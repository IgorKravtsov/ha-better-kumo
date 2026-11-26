"""Push from the relay: HA webhook endpoint and its registration in the relay."""

from __future__ import annotations

import contextlib
import hashlib
import hmac
from http import HTTPStatus
import json
import logging
from typing import Any

from aiohttp import web
from homeassistant.components import webhook
from homeassistant.const import CONF_WEBHOOK_ID
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.network import NoURLAvailableError, get_url

from .api import RelayError
from .const import (
    CONF_WEBHOOK_SECRET,
    CONF_WEBHOOK_URL,
    DOMAIN,
    EVENT_BETTER_KUMO,
    REGISTER_INTERVAL,
)
from .coordinator import BetterKumoConfigEntry, BetterKumoCoordinator

_LOGGER = logging.getLogger(__name__)


def valid_signature(secret: str, body: bytes, header: str | None) -> bool:
    """Check X-Kumo-Signature: sha256=<hex HMAC-SHA256 of the raw body>."""
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256=").lower())


def webhook_urls(hass: HomeAssistant, entry: BetterKumoConfigEntry) -> list[str]:
    """URLs the relay should try, in order: override, internal, external."""
    path = webhook.async_generate_path(entry.data[CONF_WEBHOOK_ID])
    bases: list[str] = []
    if override := entry.options.get(CONF_WEBHOOK_URL):
        bases.append(override)
    # allow_ip: internal_url is often unset, and then the detected LAN IP is the
    # only internal address there is.
    for kwargs in (
        {"allow_external": False, "allow_cloud": False, "allow_ip": True},
        {"allow_internal": False, "prefer_external": True},
    ):
        with contextlib.suppress(NoURLAvailableError):
            bases.append(get_url(hass, **kwargs))
    urls: list[str] = []
    for base in bases:
        url = base.rstrip("/") + path
        if url not in urls:
            urls.append(url)
    return urls


class WebhookReceiver:
    """Verifies, orders and applies relay deliveries for one config entry."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: BetterKumoConfigEntry,
        coordinator: BetterKumoCoordinator,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self.boot: str | None = None
        self.seq = -1
        self.registered_urls: list[str] = []
        self.last_error: str | None = None

    async def async_setup(self) -> None:
        """Start receiving and keep the registration in the relay fresh."""
        # local_only=False: the relay may reach us through an external URL
        # (ha.example.com behind a proxy) or over Tailscale (100.64/10), neither of
        # which HA counts as local. Every body is authenticated by HMAC instead.
        webhook.async_register(
            self.hass,
            DOMAIN,
            "Better Kumo",
            self.entry.data[CONF_WEBHOOK_ID],
            self._handle,
            local_only=False,
            allowed_methods=["POST"],
        )
        self.entry.async_on_unload(
            lambda: webhook.async_unregister(self.hass, self.entry.data[CONF_WEBHOOK_ID])
        )
        self.entry.async_on_unload(
            async_track_time_interval(self.hass, self._async_register_tick, REGISTER_INTERVAL)
        )
        await self.async_register()

    async def _async_register_tick(self, _now: Any) -> None:
        await self.async_register()

    async def async_register(self) -> None:
        """PUT the registration; failures leave polling as the only source."""
        urls = webhook_urls(self.hass, self.entry)
        try:
            await self.coordinator.client.put_webhook(
                self.entry.entry_id, urls, self.entry.data[CONF_WEBHOOK_SECRET]
            )
        except RelayError as err:
            if self.last_error is None:
                _LOGGER.warning(
                    "Could not register the webhook in the relay, using polling only: %s",
                    err,
                )
            self.last_error = str(err)
            return
        if self.last_error is not None:
            _LOGGER.info("Webhook registered in the relay")
        self.last_error = None
        self.registered_urls = urls

    async def async_unregister(self) -> None:
        """DELETE the registration; best effort."""
        try:
            await self.coordinator.client.delete_webhook(self.entry.entry_id)
        except RelayError as err:
            _LOGGER.debug("Could not remove the webhook from the relay: %s", err)

    async def _handle(
        self, hass: HomeAssistant, webhook_id: str, request: web.Request
    ) -> web.Response:
        body = await request.read()
        if not valid_signature(
            self.entry.data[CONF_WEBHOOK_SECRET],
            body,
            request.headers.get("X-Kumo-Signature"),
        ):
            _LOGGER.debug("Dropped a webhook body with a bad signature")
            return web.Response(status=HTTPStatus.UNAUTHORIZED)
        try:
            payload = json.loads(body)
            boot = str(payload["boot"])
            seq = int(payload["seq"])
            kind = payload["kind"]
        except (ValueError, KeyError, TypeError):
            return web.Response(status=HTTPStatus.BAD_REQUEST)
        self.handle_payload(boot, seq, kind, payload)
        return web.Response(status=HTTPStatus.OK)

    @callback
    def handle_payload(self, boot: str, seq: int, kind: str, payload: dict[str, Any]) -> None:
        """Apply one verified delivery."""
        if kind == "event":
            self.hass.bus.async_fire(
                EVENT_BETTER_KUMO,
                {**payload.get("event", {}), "config_entry_id": self.entry.entry_id},
            )
            return
        if kind not in ("desktop", "disconnected"):
            return
        if boot == self.boot and seq <= self.seq:
            _LOGGER.debug("Dropped stale %s body %s/%s", kind, boot, seq)
            return
        self.boot, self.seq = boot, seq
        if kind == "desktop" and isinstance(payload.get("desktop"), dict):
            self.coordinator.async_apply(payload["desktop"])
        elif kind == "disconnected":
            self.coordinator.async_mark_disconnected()

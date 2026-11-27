"""Push from the relay: HA webhook endpoint and its registration in the relay."""

from __future__ import annotations

import asyncio
from collections import deque
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

MAX_SEQ = 2**64 - 1


def valid_signature(secret: str, body: bytes, header: str | None) -> bool:
    """Check X-Kumo-Signature: sha256=<hex HMAC-SHA256 of the raw body>."""
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest().encode()
    given = header.removeprefix("sha256=").lower().encode("utf-8", "replace")
    return hmac.compare_digest(expected, given)


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
    """Verifies and handles relay deliveries for one config entry."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: BetterKumoConfigEntry,
        coordinator: BetterKumoCoordinator,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self._fired_events: deque[tuple[str, int]] = deque(maxlen=256)
        self._lock = asyncio.Lock()
        self._closed = False
        # Re-pairing rotates the secret; this receiver must never register the new one.
        self._secret: str = entry.data[CONF_WEBHOOK_SECRET]
        self.registered_urls: list[str] = []
        self.last_error: str | None = None

    async def async_setup(self) -> None:
        """Start receiving and keep the registration in the relay fresh."""
        # External URLs and Tailscale (100.64/10) are not "local" to HA; HMAC authenticates.
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
            async with self._lock:
                if self._closed:
                    return
                await self.coordinator.client.put_webhook(self.entry.entry_id, urls, self._secret)
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
        self._closed = True
        try:
            # The lock makes an in-flight PUT finish first, so it cannot recreate the hook.
            async with self._lock:
                await self.coordinator.client.delete_webhook(self.entry.entry_id)
        except RelayError as err:
            _LOGGER.debug("Could not remove the webhook from the relay: %s", err)

    async def _handle(
        self, hass: HomeAssistant, webhook_id: str, request: web.Request
    ) -> web.Response:
        body = await request.read()
        if not valid_signature(
            self._secret,
            body,
            request.headers.get("X-Kumo-Signature"),
        ):
            _LOGGER.debug("Dropped a webhook body with a bad signature")
            return web.Response(status=HTTPStatus.UNAUTHORIZED)
        try:
            payload = json.loads(body)
            boot, seq, kind = payload["boot"], payload["seq"], payload["kind"]
            if (
                not isinstance(boot, str)
                or not isinstance(kind, str)
                or type(seq) is not int
                or not 0 <= seq <= MAX_SEQ
                or (kind == "event" and not isinstance(payload[kind], dict))
            ):
                raise TypeError
        except (ValueError, KeyError, TypeError):
            return web.Response(status=HTTPStatus.BAD_REQUEST)
        # The endpoint stays registered until unload returns, and a request may already be in.
        if self._closed:
            return web.Response(status=HTTPStatus.GONE)
        self.handle_payload(boot, seq, kind, payload)
        return web.Response(status=HTTPStatus.OK)

    @callback
    def handle_payload(self, boot: str, seq: int, kind: str, payload: dict[str, Any]) -> None:
        """Fire an event, or read the state that a state body signals."""
        if kind in ("desktop", "disconnected"):
            # Bodies carry no revision shared with HTTP reads, so a late one could undo newer state.
            self.coordinator.async_invalidate()
        elif kind == "event":
            # The relay may deliver one body to a second URL when the first answered too late.
            if (boot, seq) in self._fired_events:
                return
            self._fired_events.append((boot, seq))
            self.hass.bus.async_fire(
                EVENT_BETTER_KUMO, {**payload["event"], "config_entry_id": self.entry.entry_id}
            )

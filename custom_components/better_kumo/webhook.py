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
        self._retired_boots: set[str] = set()
        self._held: dict[str, tuple[int, str, dict[str, Any]]] = {}
        self._boot_check: asyncio.Task[None] | None = None
        self._fired_events: deque[tuple[str, int]] = deque(maxlen=256)
        self._lock = asyncio.Lock()
        self._closed = False
        self.seq = -1
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
            self.entry.data[CONF_WEBHOOK_SECRET],
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
                or (kind in ("desktop", "event") and not isinstance(payload[kind], dict))
            ):
                raise TypeError
        except (ValueError, KeyError, TypeError):
            return web.Response(status=HTTPStatus.BAD_REQUEST)
        self.handle_payload(boot, seq, kind, payload)
        return web.Response(status=HTTPStatus.OK)

    @callback
    def handle_payload(self, boot: str, seq: int, kind: str, payload: dict[str, Any]) -> None:
        """Apply one verified delivery."""
        if kind == "event":
            # The relay may deliver one body to a second URL when the first answered too late.
            if (boot, seq) in self._fired_events:
                return
            self._fired_events.append((boot, seq))
            self.hass.bus.async_fire(
                EVENT_BETTER_KUMO, {**payload["event"], "config_entry_id": self.entry.entry_id}
            )
            return
        if kind not in ("desktop", "disconnected"):
            return
        if boot == self.boot:
            if seq <= self.seq:
                _LOGGER.debug("Dropped stale %s body %s/%s", kind, boot, seq)
                return
            self.seq = seq
            self._apply(kind, payload)
            return
        if boot in self._retired_boots:
            _LOGGER.debug("Dropped %s body from retired boot %s/%s", kind, boot, seq)
            return
        # Boot ids are random, so only the relay can tell whether an unfamiliar boot is the
        # running one or the last in-flight body of a process that already stopped.
        held = self._held.get(boot)
        if held is None or seq > held[0]:
            self._held[boot] = (seq, kind, payload)
        if self._boot_check is None or self._boot_check.done():
            self._boot_check = self.entry.async_create_background_task(
                self.hass, self._async_check_boot(), "better_kumo boot check"
            )

    async def _async_check_boot(self) -> None:
        try:
            current = await self.coordinator.client.boot()
        except RelayError as err:
            _LOGGER.debug("Could not read the relay boot id: %s", err)
            self._held.clear()
            return
        self._retired_boots.update(b for b in self._held if b != current)
        held = self._held.pop(current, None)
        self._held.clear()
        if current == self.boot:
            return
        if self.boot is not None:
            self._retired_boots.add(self.boot)
        self.boot = current
        if held is None:
            self.seq = -1
            await self.coordinator.async_request_refresh()
        else:
            self.seq = held[0]
            self._apply(held[1], held[2])

    @callback
    def _apply(self, kind: str, payload: dict[str, Any]) -> None:
        if kind == "desktop":
            self.coordinator.async_apply(payload["desktop"])
        else:
            self.coordinator.async_mark_disconnected()

"""Polling coordinator that also reads on request."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import RelayClient, RelayError, idle_seconds
from .const import CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL, DOMAIN

if TYPE_CHECKING:
    from .webhook import WebhookReceiver

_LOGGER = logging.getLogger(__name__)

type BetterKumoConfigEntry = ConfigEntry[BetterKumoCoordinator]


class BetterKumoCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Holds the last DesktopState; None data means never fetched."""

    config_entry: BetterKumoConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: BetterKumoConfigEntry, client: RelayClient
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(
                seconds=entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
            ),
        )
        self.client = client
        self.generation = 0
        self._stale = False
        self._reader: asyncio.Task[None] | None = None
        self._read_lock = asyncio.Lock()
        self.command_lock = asyncio.Lock()
        self.receiver: WebhookReceiver | None = None
        self.status = StatusCoordinator(hass, entry, client, self.update_interval)

    async def _async_update_data(self) -> dict[str, Any]:
        # HA 2025.8 does not serialize scheduled polls with async_refresh.
        async with self._read_lock:
            # This read sees every change signalled before it started.
            self._stale = False
            generation = self.generation
            try:
                data = await self.client.desktop()
            except RelayError as err:
                if generation != self.generation and self.last_update_success:
                    return self.data
                raise UpdateFailed(str(err)) from err
            # A command reply landed while this read was in flight; the read may predate it.
            if generation != self.generation:
                if not self.last_update_success:
                    raise UpdateFailed(str(self.last_exception))
                return self.data
            return data

    @callback
    def async_apply(self, data: dict[str, Any]) -> None:
        """Apply a command-returned state, keeping the poll timer running."""
        self.generation += 1
        self.data = data
        self.last_update_success = True
        self.last_exception = None
        self.async_update_listeners()

    @callback
    def async_invalidate(self) -> None:
        """Read the desktop state again soon, coalescing repeated requests."""
        self._stale = True
        if self._reader is None or self._reader.done():
            self._reader = self.config_entry.async_create_background_task(
                self.hass, self._async_read_while_stale(), "better_kumo refresh"
            )

    async def _async_read_while_stale(self) -> None:
        # After shutdown async_refresh returns without reading or yielding.
        while self._stale and not self._shutdown_requested:
            await self.async_refresh()


class StatusCoordinator(DataUpdateCoordinator[int | None]):
    """Polls GET /v1/status on its own, so neither endpoint waits for the other.

    Data is the relay computer's idle seconds, None when the relay can't tell.
    """

    config_entry: BetterKumoConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: BetterKumoConfigEntry,
        client: RelayClient,
        interval: timedelta | None,
    ) -> None:
        super().__init__(
            hass, _LOGGER, config_entry=entry, name=f"{DOMAIN} status", update_interval=interval
        )
        self.client = client
        self._read_lock = asyncio.Lock()
        # HA starts with last_update_success True, and the first read runs after setup.
        self.has_read = False

    async def _async_update_data(self) -> int | None:
        # HA 2025.8 does not serialize polls with async_refresh; an older sample must not land last.
        async with self._read_lock:
            try:
                return idle_seconds(await self.client.status())
            except RelayError as err:
                raise UpdateFailed(str(err)) from err
            finally:
                self.has_read = True

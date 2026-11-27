"""Polling coordinator that also reads on request."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
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


@dataclass(frozen=True, slots=True)
class RelayData:
    """What one read learned from the relay."""

    # None while Kumospace desktop is not connected to the relay; its entities are then unavailable.
    desktop: dict[str, Any] | None = None
    desktop_error: str | None = None
    # GET /v1/status answered, so the relay's computer is awake whether or not Kumospace runs.
    relay_ok: bool = False
    idle_seconds: int | None = None


class BetterKumoCoordinator(DataUpdateCoordinator[RelayData]):
    """Holds the last read; None data means never fetched."""

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

    async def _async_update_data(self) -> RelayData:
        # HA 2025.8 does not serialize scheduled polls with async_refresh.
        async with self._read_lock:
            # This read sees every change signalled before it started.
            self._stale = False
            generation = self.generation
            desktop, status = await asyncio.gather(
                self.client.desktop(), self.client.status(), return_exceptions=True
            )
            for result in (desktop, status):
                if isinstance(result, BaseException) and not isinstance(result, RelayError):
                    raise result
            relay_ok = isinstance(status, dict)
            idle = idle_seconds(status) if isinstance(status, dict) else None
            # A command reply landed while this read was in flight; the read may predate it.
            if generation != self.generation and self.data is not None:
                if not relay_ok:
                    return self.data
                return replace(self.data, relay_ok=True, idle_seconds=idle)
            if isinstance(desktop, RelayError):
                # Only a relay that answers neither is down; Kumospace alone being away is not.
                if not relay_ok:
                    raise UpdateFailed(str(desktop)) from desktop
                return RelayData(desktop_error=str(desktop), relay_ok=True, idle_seconds=idle)
            return RelayData(desktop=desktop, relay_ok=relay_ok, idle_seconds=idle)

    @callback
    def async_apply(self, data: dict[str, Any]) -> None:
        """Apply a command-returned state, keeping the poll timer running."""
        self.generation += 1
        self.data = replace(self.data or RelayData(), desktop=data, desktop_error=None)
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

"""Polling coordinator that also accepts pushed state."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import RelayClient, RelayError
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
        self.polls_started = 0
        # Start number of the newest poll whose result (success or failure) was applied.
        self.applied_poll = 0
        self.receiver: WebhookReceiver | None = None

    async def _async_update_data(self) -> dict[str, Any]:
        generation = self.generation
        self.polls_started += 1
        poll = self.polls_started
        try:
            data = await self.client.desktop()
        except RelayError as err:
            if generation != self.generation and self.last_update_success:
                return self.data
            self.applied_poll = poll
            raise UpdateFailed(str(err)) from err
        # A push or command landed while this poll was in flight; it is newer.
        if generation != self.generation:
            if not self.last_update_success:
                raise UpdateFailed(str(self.last_exception))
            return self.data
        self.applied_poll = poll
        return data

    @callback
    def async_apply(self, data: dict[str, Any]) -> None:
        """Apply a pushed or command-returned state.

        Unlike async_set_updated_data this keeps the poll timer running, so polling
        stays on its schedule even while pushes arrive.
        """
        self.generation += 1
        self.data = data
        self.last_update_success = True
        self.last_exception = None
        self.async_update_listeners()

    @callback
    def async_mark_disconnected(self) -> None:
        """Make entities unavailable until the next good poll or push."""
        self.generation += 1
        if self.last_update_success:
            _LOGGER.info("Kumospace desktop disconnected from the relay")
        self.last_update_success = False
        self.last_exception = UpdateFailed("Kumospace desktop disconnected")
        self.async_update_listeners()

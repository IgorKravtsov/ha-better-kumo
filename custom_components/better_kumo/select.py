"""Select for my Kumospace status."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import AVAILABILITIES
from .coordinator import BetterKumoConfigEntry
from .entity import BetterKumoEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BetterKumoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the status select."""
    async_add_entities([StatusSelect(entry.runtime_data, "status_select")])


class StatusSelect(BetterKumoEntity, SelectEntity):
    """Set available / away / focusing."""

    _attr_options = AVAILABILITIES

    @property
    def current_option(self) -> str | None:
        """Return the availability."""
        value = self.me.get("availability")
        return value if value in AVAILABILITIES else None

    async def async_select_option(self, option: str) -> None:
        """Change status; the relay keeps the current status message."""
        await self._command(self.coordinator.client.set_availability(option))

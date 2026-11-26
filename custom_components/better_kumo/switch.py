"""Microphone and camera switches."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import BetterKumoConfigEntry, BetterKumoCoordinator
from .entity import BetterKumoEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BetterKumoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add media switches."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            MediaSwitch(coordinator, "microphone", "audio"),
            MediaSwitch(coordinator, "camera", "video"),
        ]
    )


class MediaSwitch(BetterKumoEntity, SwitchEntity):
    """Turns my microphone or camera on and off."""

    def __init__(self, coordinator: BetterKumoCoordinator, key: str, field: str) -> None:
        super().__init__(coordinator, key)
        self._field = field

    @property
    def is_on(self) -> bool | None:
        """Return the media state."""
        value = self.state_data.get(self._field)
        return None if value is None else bool(value)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on."""
        await self._command(self.coordinator.client.set_media(**{self._field: True}))

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off."""
        await self._command(self.coordinator.client.set_media(**{self._field: False}))

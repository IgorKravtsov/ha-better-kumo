"""Binary sensors: in a call, someone in earshot."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import BetterKumoConfigEntry
from .entity import BetterKumoEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BetterKumoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add binary sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        [InCallSensor(coordinator, "in_call"), EarshotSensor(coordinator, "someone_in_earshot")]
    )


class InCallSensor(BetterKumoEntity, BinarySensorEntity):
    """I am in an audio/video conversation."""

    @property
    def is_on(self) -> bool | None:
        """Return inCall; None on a relay without it."""
        value = self.state_data.get("inCall")
        return None if value is None else bool(value)


class EarshotSensor(BetterKumoEntity, BinarySensorEntity):
    """Someone's audio reaches me."""

    @property
    def is_on(self) -> bool | None:
        """Return whether earshot is non-empty; None on a relay without it."""
        earshot = self.state_data.get("earshot")
        return None if earshot is None else len(earshot) > 0

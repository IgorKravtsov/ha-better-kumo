"""Sensors: status, room, people in earshot."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import MAX_LENGTH_STATE_STATE
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
    """Add sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            StatusSensor(coordinator, "status"),
            RoomSensor(coordinator, "room"),
            EarshotCountSensor(coordinator, "earshot_count"),
        ]
    )


class StatusSensor(BetterKumoEntity, SensorEntity):
    """My Kumospace availability."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = AVAILABILITIES

    @property
    def native_value(self) -> str | None:
        """Return the availability."""
        value = self.me.get("availability")
        return value if value in AVAILABILITIES else None


class RoomSensor(BetterKumoEntity, SensorEntity):
    """Name of the room I am in."""

    @property
    def native_value(self) -> str | None:
        """Return the zone name, or None outside rooms."""
        zone_id = self.me.get("zoneId")
        for zone in self.state_data.get("zones") or []:
            if zone.get("id") == zone_id and (name := zone.get("name")) is not None:
                # HA refuses to write a longer state.
                return str(name)[:MAX_LENGTH_STATE_STATE]
        return None


class EarshotCountSensor(BetterKumoEntity, SensorEntity):
    """How many people I currently hear."""

    _unrecorded_attributes = frozenset({"names"})

    @property
    def native_value(self) -> int | None:
        """Return the count, or None on a relay without earshot."""
        earshot = self.state_data.get("earshot")
        return None if earshot is None else len(earshot)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Names of the people in earshot."""
        names = {p.get("id"): p.get("name") for p in self.state_data.get("people") or []}
        return {"names": [names.get(i) or i for i in self.state_data.get("earshot") or []]}

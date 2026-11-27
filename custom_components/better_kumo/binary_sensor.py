"""Binary sensors: in a call, someone in earshot, at the computer, using the phone."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    CONF_AWAY_AFTER,
    CONF_PHONE_IN_USE_FOR,
    DEFAULT_AWAY_AFTER,
    DEFAULT_PHONE_IN_USE_FOR,
)
from .coordinator import BetterKumoConfigEntry
from .entity import BetterKumoEntity, RelayEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BetterKumoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add binary sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            InCallSensor(coordinator, "in_call"),
            EarshotSensor(coordinator, "someone_in_earshot"),
            AtComputerSensor(coordinator.status, "at_computer"),
            UsingPhoneSensor(coordinator.status, "using_phone"),
        ]
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


class AtComputerSensor(RelayEntity, BinarySensorEntity):
    """Someone used the relay's keyboard, mouse or trackpad recently."""

    # Occupancy, not presence: presence reads Home/Away, which would be mistaken for being at home.
    _attr_device_class = BinarySensorDeviceClass.OCCUPANCY

    @property
    def is_on(self) -> bool | None:
        """Return whether the idle time is under the threshold; None when the relay can't tell."""
        idle = self.relay_status.idle_seconds
        if idle is None:
            return None
        options = self.coordinator.config_entry.options
        return idle < options.get(CONF_AWAY_AFTER, DEFAULT_AWAY_AFTER)


class UsingPhoneSensor(RelayEntity, BinarySensorEntity):
    """The Better Kumo phone app was in the foreground recently, or has a call handed off."""

    # Like At computer: the person is at this device; presence would read Home/Away.
    _attr_device_class = BinarySensorDeviceClass.OCCUPANCY

    @property
    def is_on(self) -> bool | None:
        """Return whether the phone idle time is under the threshold.

        Off when no phone has reported since the relay started; None on an older relay.
        """
        status = self.relay_status
        if not status.phone_known:
            return None
        if status.phone_idle_seconds is None:
            return False
        options = self.coordinator.config_entry.options
        return status.phone_idle_seconds < options.get(
            CONF_PHONE_IN_USE_FOR, DEFAULT_PHONE_IN_USE_FOR
        )

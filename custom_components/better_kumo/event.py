"""Event entities fed by relay `event` webhook deliveries."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import OFFICE_EVENT_TYPES, signal_event
from .coordinator import BetterKumoConfigEntry, BetterKumoCoordinator
from .entity import BetterKumoEntity

_LOGGER = logging.getLogger(__name__)

MESSAGE = "message"
OTHER = "other"

# The recorder drops state attributes over 16 KiB (MAX_STATE_ATTRS_BYTES), so cap the strings:
# text at 1024 characters, every other string at 255, at most 4 UTF-8 bytes per character.
MAX_TEXT = 1024
MAX_FIELD = 255


def _clip(value: Any, limit: int = MAX_FIELD) -> str | None:
    """A string attribute cut to `limit` characters (ending in …), None when absent."""
    if value is None or value == "":
        return None
    text = value if isinstance(value, str) else str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _part(event: dict[str, Any], key: str) -> dict[str, Any]:
    value = event.get(key)
    return value if isinstance(value, dict) else {}


def _drop_none(attributes: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in attributes.items() if value is not None}


def message_attributes(event: dict[str, Any]) -> dict[str, Any]:
    """Readable attributes of a chat message."""
    person = _part(event, "person")
    return _drop_none(
        {
            "sender": _clip(person.get("name")),
            "sender_id": _clip(person.get("user_id")),
            "chat": _clip(event.get("chat")),
            "text": _clip(event.get("text"), MAX_TEXT),
            "channel": _clip(event.get("channel")),
            "channel_type": _clip(event.get("channel_type")),
            # The relay omits both flags when false.
            "mentions_me": event.get("mentions_me") is True,
            "thread": event.get("thread") is True,
            "at": event.get("at"),
        }
    )


def office_attributes(event: dict[str, Any]) -> dict[str, Any]:
    """The office event's fields, flattened into readable attributes."""
    person, room, floor = _part(event, "person"), _part(event, "room"), _part(event, "floor")
    return _drop_none(
        {
            "kind": _clip(event.get("kind")),
            "person": _clip(person.get("name")),
            "person_id": _clip(person.get("user_id")),
            "room": _clip(room.get("name")),
            "floor": _clip(floor.get("name")),
            "status": _clip(event.get("status")),
            "chat": _clip(event.get("chat")),
            "text": _clip(event.get("text"), MAX_TEXT),
            "at": event.get("at"),
        }
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BetterKumoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the Messages and Office events entities."""
    coordinator = entry.runtime_data
    async_add_entities([MessagesEvent(coordinator), OfficeEvents(coordinator)])


class BetterKumoEvent(BetterKumoEntity, EventEntity):
    """Triggered only by deduplicated `event` webhook deliveries, never by polling."""

    def __init__(self, coordinator: BetterKumoCoordinator, key: str) -> None:
        super().__init__(coordinator, key)
        self._signal = signal_event(coordinator.config_entry.entry_id)

    async def async_added_to_hass(self) -> None:
        """Listen for relay events."""
        await super().async_added_to_hass()
        self.async_on_remove(async_dispatcher_connect(self.hass, self._signal, self._handle))

    @callback
    def _handle(self, event: dict[str, Any]) -> None:
        # Runs inside the webhook handler, which must answer the relay whatever the body holds.
        try:
            if (handled := self.convert(event)) is None:
                return
            self._trigger_event(*handled)
        except Exception:
            _LOGGER.exception("Could not show relay event %r", event.get("kind"))
            return
        self.async_write_ha_state()

    def convert(self, event: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
        """The event type and attributes for this entity, or None when it is not ours."""
        raise NotImplementedError


class MessagesEvent(BetterKumoEvent):
    """Chat messages in every channel, except my own."""

    def __init__(self, coordinator: BetterKumoCoordinator) -> None:
        super().__init__(coordinator, "messages")
        self._attr_event_types = [MESSAGE]

    def convert(self, event: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
        if event.get("kind") != MESSAGE:
            return None
        return MESSAGE, message_attributes(event)


class OfficeEvents(BetterKumoEvent):
    """Office events: nudges, knocks, people moving, status changes, …"""

    def __init__(self, coordinator: BetterKumoCoordinator) -> None:
        super().__init__(coordinator, "office_events")
        self._attr_event_types = [*OFFICE_EVENT_TYPES, OTHER]

    def convert(self, event: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
        kind = event.get("kind")
        if kind == MESSAGE:
            return None
        if kind not in OFFICE_EVENT_TYPES:
            _LOGGER.debug("Unknown relay event kind %r, shown as %r", kind, OTHER)
            return OTHER, office_attributes(event)
        return kind, office_attributes(event)

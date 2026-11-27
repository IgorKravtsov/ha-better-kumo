"""Event entities: Messages and Office events, fed by `event` webhook deliveries."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any
from unittest.mock import patch

from homeassistant.components.recorder.db_schema import MAX_STATE_ATTRS_BYTES
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers.json import json_bytes
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    mock_restore_cache_with_extra_data,
)

from custom_components.better_kumo.api import RelayError
from custom_components.better_kumo.const import EVENT_BETTER_KUMO
from custom_components.better_kumo.event import MAX_FIELD, MAX_TEXT

from .conftest import WEBHOOK_SECRET, setup_entry

PATH = "/api/webhook/hook-1"
MESSAGES = "event.kumospace_messages"
OFFICE = "event.kumospace_office_events"


@pytest.fixture
async def deliver(hass: HomeAssistant, hass_client_no_auth, relay, config_entry):
    await setup_entry(hass, config_entry)
    client = await hass_client_no_auth()

    async def _deliver(seq: int, event: Any, boot: str = "b1") -> int:
        body = json.dumps({"boot": boot, "seq": seq, "kind": "event", "event": event}).encode()
        sig = "sha256=" + hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
        resp = await client.post(PATH, data=body, headers={"X-Kumo-Signature": sig})
        await hass.async_block_till_done()
        return resp.status

    return _deliver


def attrs(hass: HomeAssistant, entity_id: str) -> dict[str, Any]:
    state = hass.states.get(entity_id)
    assert state is not None
    return dict(state.attributes)


MESSAGE = {
    "seq": 9,
    "kind": "message",
    "at": 1764240000,
    "person": {"user_id": "u2", "name": "Alice"},
    "chat": "Design",
    "text": "Lunch?",
    "channel": "sendbird_group_channel_1",
    "channel_type": "group",
    "mentions_me": True,
}


async def test_entities_start_unknown(hass: HomeAssistant, deliver) -> None:
    for entity_id in (MESSAGES, OFFICE):
        assert hass.states.get(entity_id).state == STATE_UNKNOWN
    assert attrs(hass, MESSAGES)["event_types"] == ["message"]
    assert attrs(hass, OFFICE)["event_types"] == [
        "nudge",
        "knock",
        "entered_room",
        "left_room",
        "joined_floor",
        "online",
        "offline",
        "status",
        "nearby_chat",
        "other",
    ]


async def test_message_updates_messages(
    hass: HomeAssistant, deliver, config_entry: MockConfigEntry
) -> None:
    bus = async_capture_events(hass, EVENT_BETTER_KUMO)
    assert await deliver(1, MESSAGE) == 200
    assert hass.states.get(MESSAGES).state not in (STATE_UNKNOWN, STATE_UNAVAILABLE)
    a = attrs(hass, MESSAGES)
    assert a.pop("friendly_name") == "Kumospace Messages"
    a.pop("icon", None)
    assert a == {
        "event_types": ["message"],
        "event_type": "message",
        "sender": "Alice",
        "sender_id": "u2",
        "chat": "Design",
        "text": "Lunch?",
        "channel": "sendbird_group_channel_1",
        "channel_type": "group",
        "mentions_me": True,
        "thread": False,
        "at": 1764240000,
    }
    assert hass.states.get(OFFICE).state == STATE_UNKNOWN
    assert [e.data for e in bus] == [{**MESSAGE, "config_entry_id": config_entry.entry_id}]


async def test_office_event_updates_office(hass: HomeAssistant, deliver) -> None:
    event = {
        "seq": 3,
        "kind": "entered_room",
        "at": 5,
        "person": {"user_id": "u3", "name": "Bob"},
        "room": {"id": "z1", "name": "Kitchen"},
        "floor": {"id": "f1", "name": "Office"},
    }
    assert await deliver(1, event) == 200
    a = attrs(hass, OFFICE)
    assert a["event_type"] == "entered_room"
    assert {k: a[k] for k in ("kind", "person", "person_id", "room", "floor", "at")} == {
        "kind": "entered_room",
        "person": "Bob",
        "person_id": "u3",
        "room": "Kitchen",
        "floor": "Office",
        "at": 5,
    }
    assert "status" not in a
    assert "text" not in a
    assert hass.states.get(MESSAGES).state == STATE_UNKNOWN

    status = {"kind": "status", "person": {"name": "Bob"}, "status": "away", "text": "brb"}
    await deliver(2, status)
    a = attrs(hass, OFFICE)
    assert (a["event_type"], a["status"], a["text"]) == ("status", "away", "brb")
    assert a["person"] == "Bob"
    assert "room" not in a


@pytest.mark.parametrize("kind", ["wave", None, ["x"], 7])
async def test_unknown_kind_is_other(hass: HomeAssistant, deliver, kind: Any) -> None:
    bus = async_capture_events(hass, EVENT_BETTER_KUMO)
    event = {"kind": kind, "person": {"name": "Eve"}}
    assert await deliver(1, event) == 200
    a = attrs(hass, OFFICE)
    assert a["event_type"] == "other"
    assert a["person"] == "Eve"
    assert len(bus) == 1


async def test_malformed_fields_do_not_break(hass: HomeAssistant, deliver) -> None:
    event = {"kind": "knock", "person": "Alice", "room": ["x"], "text": 5}
    assert await deliver(1, event) == 200
    a = attrs(hass, OFFICE)
    assert a["event_type"] == "knock"
    assert a["text"] == "5"
    assert "person" not in a


async def test_duplicate_triggers_once(hass: HomeAssistant, deliver) -> None:
    bus = async_capture_events(hass, EVENT_BETTER_KUMO)
    changes = async_capture_events(hass, "state_changed")
    await deliver(4, MESSAGE)
    await deliver(4, MESSAGE)
    assert len(bus) == 1
    assert [c.data["entity_id"] for c in changes] == [MESSAGES]
    await deliver(4, MESSAGE, boot="b2")
    assert len(bus) == 2
    assert [c.data["entity_id"] for c in changes] == [MESSAGES, MESSAGES]


async def test_long_text_is_truncated(hass: HomeAssistant, deliver) -> None:
    bus = async_capture_events(hass, EVENT_BETTER_KUMO)
    text = "é" * (MAX_TEXT + 50)
    await deliver(1, {**MESSAGE, "text": text, "chat": "c" * 1000})
    a = attrs(hass, MESSAGES)
    assert len(a["text"]) == MAX_TEXT
    assert a["text"].endswith("…")
    assert len(a["chat"]) == MAX_FIELD
    assert bus[0].data["text"] == text


@pytest.mark.parametrize("kind", ["message", "knock"])
async def test_worst_case_attributes_fit_the_recorder(
    hass: HomeAssistant, deliver, kind: str
) -> None:
    long = "\U0001f600" * 5000
    place = {"id": long, "name": long}
    event = {
        "kind": kind,
        "at": {"nested": long},
        "person": {"user_id": long, "name": long},
        "room": place,
        "floor": place,
        **{key: long for key in ("chat", "text", "status", "channel", "channel_type")},
    }
    assert await deliver(1, event) == 200
    entity_id = MESSAGES if kind == "message" else OFFICE
    state = hass.states.get(entity_id)
    assert len(state.attributes["at"]) == MAX_FIELD
    assert len(json_bytes(dict(state.attributes))) < MAX_STATE_ATTRS_BYTES


async def test_polling_never_triggers(
    hass: HomeAssistant, deliver, config_entry: MockConfigEntry
) -> None:
    await config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(MESSAGES).state == STATE_UNKNOWN
    assert hass.states.get(OFFICE).state == STATE_UNKNOWN


async def test_availability_follows_the_relay(
    hass: HomeAssistant, deliver, config_entry: MockConfigEntry
) -> None:
    coordinator = config_entry.runtime_data
    with patch.object(coordinator.client, "desktop", side_effect=RelayError("down")):
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert hass.states.get(OFFICE).state == STATE_UNAVAILABLE
        await deliver(1, {"kind": "knock", "person": {"name": "Bob"}})
        assert hass.states.get(OFFICE).state == STATE_UNAVAILABLE
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert attrs(hass, OFFICE)["event_type"] == "knock"


async def test_last_event_is_restored(
    hass: HomeAssistant, relay, config_entry: MockConfigEntry
) -> None:
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(MESSAGES, "2026-11-27T10:00:00.000+00:00"),
                {
                    "last_event_type": "message",
                    "last_event_attributes": {"sender": "Alice", "text": "Lunch?"},
                },
            )
        ],
    )
    await setup_entry(hass, config_entry)
    state = hass.states.get(MESSAGES)
    assert state.state == "2026-11-27T10:00:00.000+00:00"
    assert (state.attributes["sender"], state.attributes["text"]) == ("Alice", "Lunch?")

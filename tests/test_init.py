"""Setup, polling, unavailability, commands and diagnostics."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.better_kumo.api import RelayError
from custom_components.better_kumo.const import CONF_POLL_INTERVAL

from .conftest import RELAY, SECRET, WEBHOOK_SECRET, desktop_state, setup_entry

ENTITIES = {
    "sensor.kumospace_status": "available",
    "select.kumospace_status": "available",
    "sensor.kumospace_room": "Kitchen",
    "sensor.kumospace_people_in_earshot": "1",
    "binary_sensor.kumospace_in_a_call": "off",
    "binary_sensor.kumospace_someone_in_earshot": "on",
    "switch.kumospace_microphone": "on",
    "switch.kumospace_camera": "off",
}


async def test_entities(
    hass: HomeAssistant, relay: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    await setup_entry(hass, config_entry)
    for entity_id, state in ENTITIES.items():
        assert hass.states.get(entity_id).state == state, entity_id
    earshot = hass.states.get("sensor.kumospace_people_in_earshot")
    assert earshot.attributes["names"] == ["Alice"]


async def test_relay_without_new_fields(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    old = desktop_state()
    for key in ("earshot", "inCall", "call"):
        del old[key]
    aioclient_mock.get(f"{RELAY}/v1/desktop", json=old)
    aioclient_mock.put(f"{RELAY}/v1/webhooks/{config_entry.entry_id}", status=404)
    aioclient_mock.delete(f"{RELAY}/v1/webhooks/{config_entry.entry_id}", status=404)
    await setup_entry(hass, config_entry)
    assert hass.states.get("binary_sensor.kumospace_in_a_call").state == "unknown"
    assert hass.states.get("sensor.kumospace_people_in_earshot").state == "unknown"
    assert hass.states.get("sensor.kumospace_status").state == "available"


@pytest.mark.parametrize(
    "mock",
    [
        {"status": 503, "text": "desktop not connected"},
        {"status": 404, "text": "desktop bridge is disabled"},
        {"exc": TimeoutError()},
        {"json": desktop_state(people=[None])},
    ],
)
async def test_unavailable_then_recovers(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock: dict,
) -> None:
    aioclient_mock.get(f"{RELAY}/v1/desktop", **mock)
    aioclient_mock.put(f"{RELAY}/v1/webhooks/{config_entry.entry_id}", status=204)
    aioclient_mock.delete(f"{RELAY}/v1/webhooks/{config_entry.entry_id}", status=204)
    await setup_entry(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED
    for entity_id in ENTITIES:
        assert hass.states.get(entity_id).state == STATE_UNAVAILABLE, entity_id

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{RELAY}/v1/desktop", json=desktop_state())
    aioclient_mock.delete(f"{RELAY}/v1/webhooks/{config_entry.entry_id}", status=204)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))
    await hass.async_block_till_done()
    assert hass.states.get("sensor.kumospace_status").state == "available"

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{RELAY}/v1/desktop", status=503)
    aioclient_mock.delete(f"{RELAY}/v1/webhooks/{config_entry.entry_id}", status=204)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=62))
    await hass.async_block_till_done()
    assert hass.states.get("sensor.kumospace_status").state == STATE_UNAVAILABLE


async def test_poll_interval_option(
    hass: HomeAssistant, relay: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(config_entry, options={CONF_POLL_INTERVAL: 2})
    await setup_entry(hass, config_entry)
    polls = sum(1 for c in relay.mock_calls if str(c[1]).endswith("/v1/desktop"))
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=3))
    await hass.async_block_till_done()
    assert sum(1 for c in relay.mock_calls if str(c[1]).endswith("/v1/desktop")) == polls + 1


async def test_select_status_sends_only_availability(
    hass: HomeAssistant, relay: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    await setup_entry(hass, config_entry)
    away = desktop_state()
    away["self"] = {**away["self"], "availability": "away"}
    relay.post(f"{RELAY}/v1/desktop/status", json=away)
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.kumospace_status", "option": "away"},
        blocking=True,
    )
    call = next(c for c in relay.mock_calls if str(c[1]).endswith("/v1/desktop/status"))
    assert call[2] == {"availability": "away"}
    assert call[3]["Authorization"] == f"Bearer {SECRET}"
    assert hass.states.get("sensor.kumospace_status").state == "away"


async def test_media_switches(
    hass: HomeAssistant, relay: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    await setup_entry(hass, config_entry)
    relay.post(f"{RELAY}/v1/desktop/media", json=desktop_state(audio=False, video=True))
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.kumospace_microphone"}, blocking=True
    )
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.kumospace_camera"}, blocking=True
    )
    bodies = [c[2] for c in relay.mock_calls if str(c[1]).endswith("/v1/desktop/media")]
    assert bodies == [{"audio": False}, {"video": True}]
    assert hass.states.get("switch.kumospace_microphone").state == "off"
    assert hass.states.get("switch.kumospace_camera").state == "on"


async def test_command_error(
    hass: HomeAssistant, relay: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    await setup_entry(hass, config_entry)
    relay.post(f"{RELAY}/v1/desktop/media", status=503)
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "switch", "turn_off", {"entity_id": "switch.kumospace_microphone"}, blocking=True
        )


async def test_unload_deletes_webhook(
    hass: HomeAssistant, relay: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    await setup_entry(hass, config_entry)
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert any(
        c[0] == "DELETE" and str(c[1]).endswith(f"/v1/webhooks/{config_entry.entry_id}")
        for c in relay.mock_calls
    )


async def test_diagnostics_redacts_secrets(
    hass: HomeAssistant,
    hass_client,
    relay: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    await hass.config.async_update(internal_url="http://homeassistant.local:8123")
    await setup_entry(hass, config_entry)
    diag = await get_diagnostics_for_config_entry(hass, hass_client, config_entry)
    text = str(diag)
    assert SECRET not in text
    assert WEBHOOK_SECRET not in text
    assert diag["webhook"]["urls"] == ["http://homeassistant.local:8123/api/webhook/hook-1"]
    assert diag["desktop"]["self"]["zoneId"] == "z1"
    assert "Alice" not in text
    assert "hi" not in str(diag["desktop"]["self"])


async def test_poll_older_than_disconnect_push_is_ignored(
    hass: HomeAssistant, relay: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    await setup_entry(hass, config_entry)
    coordinator = config_entry.runtime_data
    gate = asyncio.Event()

    async def slow_desktop():
        await gate.wait()
        return desktop_state()

    with patch.object(coordinator.client, "desktop", side_effect=slow_desktop):
        task = hass.async_create_task(coordinator.async_refresh())
        await asyncio.sleep(0)
        coordinator.async_mark_disconnected()
        gate.set()
        await task
    assert hass.states.get("sensor.kumospace_status").state == STATE_UNAVAILABLE


async def test_poll_failure_older_than_push_is_ignored(
    hass: HomeAssistant, relay: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    await setup_entry(hass, config_entry)
    coordinator = config_entry.runtime_data
    gate = asyncio.Event()

    async def slow_failure():
        await gate.wait()
        raise RelayError("relay not reachable")

    with patch.object(coordinator.client, "desktop", side_effect=slow_failure):
        task = hass.async_create_task(coordinator.async_refresh())
        await asyncio.sleep(0)
        coordinator.async_apply(desktop_state(inCall=True))
        gate.set()
        await task
    assert hass.states.get("binary_sensor.kumospace_in_a_call").state == "on"


async def test_poll_older_than_command_keeps_command_state(
    hass: HomeAssistant, relay: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    await setup_entry(hass, config_entry)
    coordinator = config_entry.runtime_data
    gate = asyncio.Event()

    async def slow_desktop():
        await gate.wait()
        return desktop_state(audio=True)

    with patch.object(coordinator.client, "desktop", side_effect=slow_desktop):
        task = hass.async_create_task(coordinator.async_refresh())
        await asyncio.sleep(0)
        coordinator.async_apply(desktop_state(audio=False))
        gate.set()
        await task
    assert hass.states.get("switch.kumospace_microphone").state == "off"


async def test_unload_waits_for_inflight_registration(
    hass: HomeAssistant, relay: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    await setup_entry(hass, config_entry)
    receiver = config_entry.runtime_data.receiver
    gate = asyncio.Event()
    order: list[str] = []

    async def slow_put(*args):
        await gate.wait()
        order.append("put")

    async def delete(*args):
        order.append("delete")

    with (
        patch.object(receiver.coordinator.client, "put_webhook", side_effect=slow_put),
        patch.object(receiver.coordinator.client, "delete_webhook", side_effect=delete),
    ):
        register = hass.async_create_task(receiver.async_register())
        await asyncio.sleep(0)
        unload = hass.async_create_task(hass.config_entries.async_unload(config_entry.entry_id))
        await asyncio.sleep(0)
        gate.set()
        await register
        assert await unload
        await receiver.async_register()
    assert order == ["put", "delete"]


@pytest.mark.parametrize("newer", ["disconnected", "desktop", "failed poll"])
async def test_command_response_older_than_push_or_poll_is_ignored(
    hass: HomeAssistant, relay: AiohttpClientMocker, config_entry: MockConfigEntry, newer: str
) -> None:
    await setup_entry(hass, config_entry)
    coordinator = config_entry.runtime_data
    gate = asyncio.Event()

    async def slow_media(**media):
        await gate.wait()
        return desktop_state(audio=False)

    async def desktop():
        if newer == "desktop":
            return desktop_state(audio=True, inCall=True)
        raise RelayError("relay returned 503: not connected")

    with (
        patch.object(coordinator.client, "set_media", side_effect=slow_media),
        patch.object(coordinator.client, "desktop", side_effect=desktop),
    ):
        task = hass.async_create_task(
            hass.services.async_call(
                "switch", "turn_off", {"entity_id": "switch.kumospace_microphone"}, blocking=True
            )
        )
        await asyncio.sleep(0)
        if newer == "disconnected":
            coordinator.async_mark_disconnected()
        elif newer == "desktop":
            coordinator.async_apply(desktop_state(audio=True, inCall=True))
        else:
            await coordinator.async_refresh()
        gate.set()
        await task
        await hass.async_block_till_done()
    mic = hass.states.get("switch.kumospace_microphone").state
    assert mic == ("on" if newer == "desktop" else STATE_UNAVAILABLE)

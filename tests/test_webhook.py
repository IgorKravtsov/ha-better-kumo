"""Webhook push: signature, state signals, events, registration."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from typing import Any
from unittest.mock import patch

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.better_kumo.api import RelayError
from custom_components.better_kumo.const import CONF_WEBHOOK_URL, EVENT_BETTER_KUMO
from custom_components.better_kumo.webhook import valid_signature

from .conftest import RELAY, WEBHOOK_SECRET, desktop_state, setup_entry

PATH = "/api/webhook/hook-1"


def desktop_down(config_entry: MockConfigEntry):
    return patch.object(
        config_entry.runtime_data.client, "desktop", side_effect=RelayError("relay returned 503")
    )


def sign(body: bytes, secret: str = WEBHOOK_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture
async def post(hass: HomeAssistant, hass_client_no_auth, relay, config_entry):
    await setup_entry(hass, config_entry)
    client = await hass_client_no_auth()

    async def _post(payload: dict[str, Any], signature: str | None = None) -> int:
        body = json.dumps(payload).encode()
        resp = await client.post(
            PATH,
            data=body,
            headers={"X-Kumo-Signature": signature or sign(body)},
        )
        await hass.async_block_till_done(wait_background_tasks=True)
        return resp.status

    return _post


def test_valid_signature() -> None:
    body = b'{"a":1}'
    assert valid_signature(WEBHOOK_SECRET, body, sign(body))
    assert valid_signature(WEBHOOK_SECRET, body, sign(body).upper().replace("SHA256=", "sha256="))
    assert not valid_signature(WEBHOOK_SECRET, body, sign(body, "other"))
    assert not valid_signature(WEBHOOK_SECRET, body, sign(body).removeprefix("sha256="))
    assert not valid_signature(WEBHOOK_SECRET, body, None)
    assert not valid_signature(WEBHOOK_SECRET, body, "sha256=\u00fc" * 8)


def desktop_reads(relay: AiohttpClientMocker) -> int:
    return sum(1 for c in relay.mock_calls if str(c[1]).endswith("/v1/desktop"))


async def test_desktop_signal_reads_the_relay(
    hass: HomeAssistant, post, relay: AiohttpClientMocker
) -> None:
    reads = desktop_reads(relay)
    # The body's snapshot is ignored; the read answers inCall=False.
    body = {"boot": "b1", "seq": 1, "kind": "desktop", "desktop": desktop_state(inCall=True)}
    assert await post(body) == 200
    assert desktop_reads(relay) == reads + 1
    assert hass.states.get("binary_sensor.kumospace_in_a_call").state == "off"


async def test_bad_signature_is_dropped(
    hass: HomeAssistant, post, relay: AiohttpClientMocker
) -> None:
    reads = desktop_reads(relay)
    payload = {"boot": "b1", "seq": 1, "kind": "desktop", "desktop": {}}
    assert await post(payload, signature="sha256=" + "0" * 64) == 401
    assert desktop_reads(relay) == reads


async def test_signals_during_a_read_coalesce(
    hass: HomeAssistant, post, config_entry: MockConfigEntry
) -> None:
    receiver = config_entry.runtime_data.receiver
    started, gate = asyncio.Event(), asyncio.Event()
    answers = [desktop_state(inCall=False), desktop_state(inCall=True)]

    async def slow_desktop() -> dict[str, Any]:
        started.set()
        await gate.wait()
        return answers.pop(0)

    with patch.object(
        config_entry.runtime_data.client, "desktop", side_effect=slow_desktop
    ) as desktop:
        # Late, duplicate and reordered bodies all mean the same thing: read again.
        for seq in (2, 2, 1, 5):
            receiver.handle_payload("b1", seq, "desktop", {})
        await started.wait()
        receiver.handle_payload("b1", 6, "disconnected", {})
        gate.set()
        await hass.async_block_till_done(wait_background_tasks=True)
    assert desktop.call_count == 2
    assert hass.states.get("binary_sensor.kumospace_in_a_call").state == "on"


async def test_disconnected_signal_and_failed_read(
    hass: HomeAssistant, post, config_entry: MockConfigEntry
) -> None:
    with desktop_down(config_entry):
        await post({"boot": "b1", "seq": 1, "kind": "disconnected"})
    assert hass.states.get("sensor.kumospace_status").state == STATE_UNAVAILABLE
    await post({"boot": "b1", "seq": 2, "kind": "desktop", "desktop": {}})
    assert hass.states.get("sensor.kumospace_status").state == "available"


async def test_late_disconnected_signal_keeps_a_reachable_desktop(
    hass: HomeAssistant, post
) -> None:
    # The relay mock is healthy: this `disconnected` was sent before a reconnect.
    await post({"boot": "b1", "seq": 1, "kind": "disconnected"})
    assert hass.states.get("sensor.kumospace_status").state == "available"


async def test_event_fires_on_the_bus(
    hass: HomeAssistant, post, config_entry: MockConfigEntry
) -> None:
    events = async_capture_events(hass, EVENT_BETTER_KUMO)
    event = {"seq": 7, "kind": "knock", "at": 1, "person": {"user_id": "u", "name": "Alice"}}
    assert await post({"boot": "b1", "seq": 3, "kind": "event", "event": event}) == 200
    assert len(events) == 1
    assert events[0].data == {**event, "config_entry_id": config_entry.entry_id}


async def test_duplicate_event_fires_once(hass: HomeAssistant, post) -> None:
    events = async_capture_events(hass, EVENT_BETTER_KUMO)
    payload = {"boot": "b1", "seq": 3, "kind": "event", "event": {"kind": "nudge"}}
    await post(payload)
    await post(payload)
    assert len(events) == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"seq": 1},
        {"boot": "b1", "seq": 1, "kind": "event"},
        {"boot": "b1", "seq": 1, "kind": "event", "event": "x"},
        {"boot": 1, "seq": 1, "kind": "disconnected"},
        {"boot": "b1", "seq": True, "kind": "disconnected"},
        {"boot": "b1", "seq": 1.5, "kind": "disconnected"},
        {"boot": "b1", "seq": 1e400, "kind": "disconnected"},
        {"boot": "b1", "seq": 1, "kind": ["desktop"]},
        {"boot": "b1", "seq": -1, "kind": "disconnected"},
        {"boot": "b1", "seq": 2**64, "kind": "event", "event": {}},
    ],
)
async def test_malformed_body(
    hass: HomeAssistant, post, relay: AiohttpClientMocker, payload: dict
) -> None:
    reads = desktop_reads(relay)
    assert await post(payload) == 400
    assert desktop_reads(relay) == reads


async def test_largest_seq_is_accepted(hass: HomeAssistant, post) -> None:
    events = async_capture_events(hass, EVENT_BETTER_KUMO)
    assert await post({"boot": "b1", "seq": 2**64 - 1, "kind": "event", "event": {}}) == 200
    assert len(events) == 1


async def test_non_ascii_signature(hass: HomeAssistant, post) -> None:
    assert await post({"boot": "b1", "seq": 1, "kind": "disconnected"}, "sha256=ü") == 401


def _put_urls(relay: AiohttpClientMocker) -> list[str]:
    puts = [c for c in relay.mock_calls if c[0] == "PUT"]
    assert puts
    assert puts[-1][2]["secret"] == WEBHOOK_SECRET
    return puts[-1][2]["urls"]


async def test_registration_url_order(
    hass: HomeAssistant, relay: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    await hass.config.async_update(
        internal_url="http://homeassistant.local:8123",
        external_url="https://ha.example.com",
    )
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry, options={CONF_WEBHOOK_URL: "http://100.64.0.2:8123"}
    )
    await setup_entry(hass, config_entry)
    assert _put_urls(relay) == [
        f"http://100.64.0.2:8123{PATH}",
        f"http://homeassistant.local:8123{PATH}",
        f"https://ha.example.com{PATH}",
    ]


async def test_registration_falls_back_to_local_ip(
    hass: HomeAssistant, relay: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    await setup_entry(hass, config_entry)
    api = hass.config.api
    assert _put_urls(relay) == [f"http://{api.local_ip}:{api.port}{PATH}"]


async def test_registration_failure_keeps_polling(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    aioclient_mock.get(f"{RELAY}/v1/desktop", json=desktop_state())
    aioclient_mock.put(f"{RELAY}/v1/webhooks/{config_entry.entry_id}", status=404)
    aioclient_mock.delete(f"{RELAY}/v1/webhooks/{config_entry.entry_id}", status=404)
    await setup_entry(hass, config_entry)
    assert hass.states.get("sensor.kumospace_status").state == "available"
    receiver = config_entry.runtime_data.receiver
    assert receiver.last_error
    await receiver.async_register()
    assert caplog.text.count("Could not register the webhook") == 1


async def test_unloading_receiver_rejects_bodies(
    hass: HomeAssistant, post, config_entry: MockConfigEntry
) -> None:
    config_entry.runtime_data.receiver._closed = True
    assert await post({"boot": "b1", "seq": 1, "kind": "disconnected"}) == 410
    assert hass.states.get("sensor.kumospace_status").state == "available"

"""Webhook push: signature, ordering, registration."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.better_kumo.const import CONF_WEBHOOK_URL, EVENT_BETTER_KUMO
from custom_components.better_kumo.webhook import valid_signature

from .conftest import RELAY, WEBHOOK_SECRET, desktop_state, setup_entry

PATH = "/api/webhook/hook-1"


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
        await hass.async_block_till_done()
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


async def test_desktop_push(hass: HomeAssistant, post) -> None:
    state = desktop_state(inCall=True)
    assert await post({"boot": "b1", "seq": 1, "kind": "desktop", "desktop": state}) == 200
    assert hass.states.get("binary_sensor.kumospace_in_a_call").state == "on"


async def test_bad_signature_is_dropped(hass: HomeAssistant, post) -> None:
    payload = {"boot": "b1", "seq": 1, "kind": "desktop", "desktop": desktop_state(inCall=True)}
    assert await post(payload, signature="sha256=" + "0" * 64) == 401
    assert hass.states.get("binary_sensor.kumospace_in_a_call").state == "off"


async def test_stale_seq_is_dropped(hass: HomeAssistant, post) -> None:
    await post({"boot": "b1", "seq": 5, "kind": "desktop", "desktop": desktop_state(inCall=True)})
    for seq in (5, 4):
        await post(
            {"boot": "b1", "seq": seq, "kind": "desktop", "desktop": desktop_state(inCall=False)}
        )
        assert hass.states.get("binary_sensor.kumospace_in_a_call").state == "on"
    await post({"boot": "b2", "seq": 1, "kind": "desktop", "desktop": desktop_state(inCall=False)})
    assert hass.states.get("binary_sensor.kumospace_in_a_call").state == "off"
    await post({"boot": "b1", "seq": 9, "kind": "desktop", "desktop": desktop_state(inCall=True)})
    assert hass.states.get("binary_sensor.kumospace_in_a_call").state == "off"


async def test_disconnected(hass: HomeAssistant, post) -> None:
    await post({"boot": "b1", "seq": 1, "kind": "disconnected"})
    assert hass.states.get("sensor.kumospace_status").state == STATE_UNAVAILABLE
    await post({"boot": "b1", "seq": 2, "kind": "desktop", "desktop": desktop_state()})
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
        {"boot": "b1", "seq": 1, "kind": "desktop"},
        {"boot": "b1", "seq": 1, "kind": "desktop", "desktop": []},
        {"boot": "b1", "seq": 1, "kind": "event", "event": "x"},
    ],
)
async def test_malformed_body(hass: HomeAssistant, post, payload: dict) -> None:
    assert await post(payload) == 400
    # A rejected body must not advance the watermark.
    await post({"boot": "b1", "seq": 1, "kind": "desktop", "desktop": desktop_state(inCall=True)})
    assert hass.states.get("binary_sensor.kumospace_in_a_call").state == "on"


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

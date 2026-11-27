"""Config and options flow tests."""

from __future__ import annotations

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_URL, CONF_WEBHOOK_ID
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
import voluptuous as vol

from custom_components.better_kumo.api import parse_pairing_link
from custom_components.better_kumo.const import (
    CONF_AWAY_AFTER,
    CONF_PHONE_IN_USE_FOR,
    CONF_POLL_INTERVAL,
    CONF_SECRET,
    CONF_WEBHOOK_SECRET,
    CONF_WEBHOOK_URL,
    DOMAIN,
)

from .conftest import RELAY, SECRET, WEBHOOK_SECRET, setup_entry

LINK = "kumowatch://relay?url=http%3A%2F%2Fkumo-mac.local%3A8787%2F&secret=s%2Bc%26r%3Dt"


def test_parse_pairing_link() -> None:
    assert parse_pairing_link(LINK) == ("http://kumo-mac.local:8787", "s+c&r=t")


@pytest.mark.parametrize(
    "link",
    [
        "https://example.com/?url=x&secret=y",
        "kumowatch://relay?url=http%3A%2F%2Fh%3A1",
        "kumowatch://relay?secret=y",
        "kumowatch://relay?url=ftp%3A%2F%2Fh&secret=y",
        "kumowatch://other?url=http%3A%2F%2Fh%3A1&secret=y",
        "kumowatch://relay?url=http%3A%2F%2Fh%3A1%2F%3Fq%3D1&secret=y",
        "kumowatch://relay?url=http%3A%2F%2Fu%3Ap%40h%3A1&secret=y",
        "kumowatch://relay?url=http%3A%2F%2Fh%3A1&secret=a%0D%0Ab",
    ],
)
def test_parse_pairing_link_rejects(link: str) -> None:
    with pytest.raises(ValueError):
        parse_pairing_link(link)


async def _start(hass: HomeAssistant):
    return await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})


async def test_pair_with_link(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get(f"{RELAY}/v1/status", json={"uid": "uid-1", "desktop": True})
    aioclient_mock.get(f"{RELAY}/v1/desktop", json={})
    result = await _start(hass)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"link": LINK})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    data = result["data"]
    assert data[CONF_URL] == RELAY
    assert data[CONF_SECRET] == "s+c&r=t"
    assert len(data[CONF_WEBHOOK_SECRET]) == 64
    int(data[CONF_WEBHOOK_SECRET], 16)
    assert data[CONF_WEBHOOK_ID]
    assert result["result"].unique_id == "uid-1"
    assert aioclient_mock.mock_calls[0][3]["Authorization"] == "Bearer s+c&r=t"


async def test_pair_with_url_and_secret(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(f"{RELAY}/v1/status", json={"uid": "uid-1"})
    aioclient_mock.get(f"{RELAY}/v1/desktop", json={})
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: RELAY + "/", CONF_SECRET: SECRET}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_URL] == RELAY


@pytest.mark.parametrize(
    ("user_input", "error"),
    [
        ({}, "missing_input"),
        ({CONF_URL: RELAY}, "missing_input"),
        ({"link": "https://example.com"}, "invalid_link"),
        ({CONF_URL: "kumo-mac.local:8787", CONF_SECRET: SECRET}, "invalid_url"),
        ({CONF_URL: RELAY + "/#x", CONF_SECRET: SECRET}, "invalid_url"),
        ({CONF_URL: RELAY + "?", CONF_SECRET: SECRET}, "invalid_url"),
        ({CONF_URL: RELAY + "/#", CONF_SECRET: SECRET}, "invalid_url"),
        ({CONF_URL: "http://kumo-mac.local:99999", CONF_SECRET: SECRET}, "invalid_url"),
        ({CONF_URL: "http://kumo-mac.local:port", CONF_SECRET: SECRET}, "invalid_url"),
        ({CONF_URL: RELAY, CONF_SECRET: "   "}, "missing_input"),
        ({"link": "   ", CONF_URL: "nope", CONF_SECRET: SECRET}, "invalid_url"),
        ({CONF_URL: RELAY, CONF_SECRET: "a\x01b"}, "invalid_secret"),
    ],
)
async def test_input_errors(hass: HomeAssistant, user_input: dict, error: str) -> None:
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}


@pytest.mark.parametrize(
    ("mock", "error"),
    [
        ({"status": 401}, "invalid_auth"),
        ({"exc": TimeoutError()}, "cannot_connect"),
        ({"json": ["not", "an", "object"]}, "cannot_connect"),
        ({"status": 307, "headers": {"Location": "https://example.com/"}}, "cannot_connect"),
    ],
)
async def test_relay_errors(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock: dict, error: str
) -> None:
    aioclient_mock.get(f"{RELAY}/v1/status", **mock)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"link": LINK})
    assert result["errors"] == {"base": error}


@pytest.mark.parametrize("url", ["http://100.64.0.1:8787", RELAY])
async def test_repairing_updates_the_entry(
    hass: HomeAssistant, relay: AiohttpClientMocker, config_entry: MockConfigEntry, url: str
) -> None:
    await setup_entry(hass, config_entry)
    relay.get(f"{url}/v1/status", json={"uid": "uid-1"})
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: url, CONF_SECRET: "new"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    # The update reloads the entry; let it finish before teardown.
    await hass.async_block_till_done()
    assert config_entry.data[CONF_URL] == url
    assert config_entry.data[CONF_SECRET] == "new"
    assert config_entry.data[CONF_WEBHOOK_SECRET] != WEBHOOK_SECRET


async def test_options(
    hass: HomeAssistant, relay: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    await setup_entry(hass, config_entry)
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    for bad in (0, 3601):
        with pytest.raises(vol.Invalid):
            await hass.config_entries.options.async_configure(
                result["flow_id"], {CONF_POLL_INTERVAL: bad}
            )
    for bad in (29, 3601):
        with pytest.raises(vol.Invalid):
            await hass.config_entries.options.async_configure(
                result["flow_id"], {CONF_POLL_INTERVAL: 5, CONF_AWAY_AFTER: bad}
            )
    for bad in (29, 3601):
        with pytest.raises(vol.Invalid):
            await hass.config_entries.options.async_configure(
                result["flow_id"], {CONF_POLL_INTERVAL: 5, CONF_PHONE_IN_USE_FOR: bad}
            )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_POLL_INTERVAL: 5, CONF_AWAY_AFTER: 60.5}
    )
    assert result["errors"] == {CONF_AWAY_AFTER: "invalid_away_after"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_POLL_INTERVAL: 5, CONF_PHONE_IN_USE_FOR: 90.5}
    )
    assert result["errors"] == {CONF_PHONE_IN_USE_FOR: "invalid_phone_in_use_for"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_POLL_INTERVAL: 5, CONF_WEBHOOK_URL: "ha.example.com"}
    )
    assert result["errors"] == {CONF_WEBHOOK_URL: "invalid_url"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_POLL_INTERVAL: 1.9}
    )
    assert result["errors"] == {CONF_POLL_INTERVAL: "invalid_interval"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_POLL_INTERVAL: "NaN"}
    )
    assert result["errors"] == {CONF_POLL_INTERVAL: "invalid_interval"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_POLL_INTERVAL: 5,
            CONF_AWAY_AFTER: 600,
            CONF_PHONE_IN_USE_FOR: 120,
            CONF_WEBHOOK_URL: "https://ha.example.com/",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert config_entry.options == {
        CONF_POLL_INTERVAL: 5,
        CONF_AWAY_AFTER: 600,
        CONF_PHONE_IN_USE_FOR: 120,
        CONF_WEBHOOK_URL: "https://ha.example.com",
    }
    assert config_entry.runtime_data.update_interval.total_seconds() == 5

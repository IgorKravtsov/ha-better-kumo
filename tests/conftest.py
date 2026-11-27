"""Fixtures for Better Kumo tests."""

from __future__ import annotations

from typing import Any

from homeassistant.const import CONF_URL, CONF_WEBHOOK_ID
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.better_kumo.const import CONF_SECRET, CONF_WEBHOOK_SECRET, DOMAIN

RELAY = "http://kumo-mac.local:8787"
SECRET = "relay-secret"
WEBHOOK_SECRET = "ab" * 32


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load custom_components in every test."""


def desktop_state(**overrides: Any) -> dict[str, Any]:
    """A DesktopState as the relay returns it."""
    state: dict[str, Any] = {
        "floorId": "f1",
        "width": 100.0,
        "height": 100.0,
        "floors": [{"id": "f1", "name": "Office"}],
        "self": {
            "id": "me",
            "x": 1.0,
            "y": 1.0,
            "zoneId": "z1",
            "availability": "available",
            "message": "hi",
        },
        "zones": [{"id": "z1", "name": "Kitchen", "x": 0, "y": 0, "w": 9, "h": 9, "open": True}],
        "people": [
            {"id": "me", "name": "Me", "self": True},
            {"id": "p2", "name": "Alice", "self": False},
            {"id": "p3", "name": "Bob", "self": False},
        ],
        "audio": True,
        "video": False,
        "earshot": ["p2"],
        "inCall": False,
        "call": None,
    }
    state.update(overrides)
    return state


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """A paired relay."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Kumospace",
        unique_id="uid-1",
        data={
            CONF_URL: RELAY,
            CONF_SECRET: SECRET,
            CONF_WEBHOOK_ID: "hook-1",
            CONF_WEBHOOK_SECRET: WEBHOOK_SECRET,
        },
    )


@pytest.fixture
def relay(
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> AiohttpClientMocker:
    """A healthy relay."""
    aioclient_mock.get(f"{RELAY}/v1/desktop", json=desktop_state())
    hook = f"{RELAY}/v1/webhooks/{config_entry.entry_id}"
    aioclient_mock.put(hook, json={"id": config_entry.entry_id, "urls": []})
    aioclient_mock.delete(hook, status=204)
    return aioclient_mock


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Add and set up an entry."""
    if hass.config_entries.async_get_entry(entry.entry_id) is None:
        entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

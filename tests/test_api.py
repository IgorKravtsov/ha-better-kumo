"""Relay error messages: built from the status and endpoint, never from a reply body."""

from __future__ import annotations

import traceback
from unittest.mock import Mock

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.better_kumo.api import RelayAuthError, RelayClient, RelayError

from .conftest import RELAY, SECRET, desktop_state, relay_status, setup_entry

LEAK = "sk_live_LEAKED_TOKEN_1234"
HTML = f"<html><body><h1>Bad gateway</h1><p>token={LEAK}</p></body></html>"


def client(hass: HomeAssistant) -> RelayClient:
    return RelayClient(async_get_clientsession(hass), RELAY, SECRET)


@pytest.mark.parametrize(
    ("method", "path", "call", "status", "message"),
    [
        (
            "get",
            "/v1/desktop",
            lambda c: c.desktop(),
            503,
            "Reading Kumospace failed: Kumospace desktop is not connected to the relay, "
            "or not in a space (HTTP 503)",
        ),
        (
            "get",
            "/v1/desktop",
            lambda c: c.desktop(),
            404,
            "Reading Kumospace failed: the relay runs with the desktop bridge disabled (HTTP 404)",
        ),
        (
            "post",
            "/v1/desktop/media",
            lambda c: c.set_media(audio=True),
            503,
            "Turning the microphone on failed: Kumospace desktop is not in a call (HTTP 503)",
        ),
        (
            "post",
            "/v1/desktop/media",
            lambda c: c.set_media(video=False),
            503,
            "Turning the camera off failed: Kumospace desktop is not in a call (HTTP 503)",
        ),
        (
            "post",
            "/v1/desktop/status",
            lambda c: c.set_availability("away"),
            503,
            "Setting the Kumospace status failed: Kumospace desktop is not connected to the "
            "relay, or not in a space (HTTP 503)",
        ),
        (
            "post",
            "/v1/desktop/status",
            lambda c: c.set_availability("away"),
            400,
            "Setting the Kumospace status failed: the relay rejected the request as invalid "
            "(HTTP 400)",
        ),
        (
            "put",
            "/v1/webhooks/hook",
            lambda c: c.put_webhook("hook", ["http://ha/api/webhook/x"], "s" * 16),
            404,
            "Registering the webhook failed: the relay does not know this endpoint; "
            "update the relay (HTTP 404)",
        ),
        (
            "get",
            "/v1/status",
            lambda c: c.status(),
            503,
            "Reading the relay status failed: the relay is temporarily unavailable (HTTP 503)",
        ),
        (
            "get",
            "/v1/status",
            lambda c: c.status(),
            502,
            "Reading the relay status failed: the relay answered with an error (HTTP 502)",
        ),
        (
            "delete",
            "/v1/webhooks/hook",
            lambda c: c.delete_webhook("hook"),
            307,
            "Removing the webhook failed: the relay answered with a redirect; "
            "use the relay's own address (HTTP 307)",
        ),
    ],
)
async def test_status_maps_to_fixed_message(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    method: str,
    path: str,
    call,
    status: int,
    message: str,
) -> None:
    getattr(aioclient_mock, method)(f"{RELAY}{path}", status=status, text=HTML)
    with pytest.raises(RelayError) as err:
        await call(client(hass))
    assert str(err.value) == message
    assert err.value.__cause__ is None


async def test_wrong_secret(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get(f"{RELAY}/v1/status", status=401, text=HTML)
    with pytest.raises(RelayAuthError) as err:
        await client(hass).status()
    assert str(err.value) == "Reading the relay status failed: wrong relay secret (HTTP 401)"


@pytest.mark.parametrize(
    ("reply", "reason"),
    [
        ({"text": HTML}, "unexpected reply from the relay: not JSON"),
        ({"content": b"\xff" + LEAK.encode()}, "unexpected reply from the relay: not UTF-8"),
        ({"text": f'["{LEAK}"]'}, "unexpected reply from the relay: not a JSON object"),
        (
            {"exc": aiohttp.ClientResponseError(None, (), message=f"Invalid header {LEAK}")},
            "relay not reachable (ClientResponseError)",
        ),
        (
            {"exc": aiohttp.ServerDisconnectedError(f"HTTP/1.1 200 {LEAK}")},
            "relay not reachable (ServerDisconnectedError)",
        ),
        ({"exc": TimeoutError()}, "relay not reachable: timed out"),
        ({"exc": aiohttp.ServerTimeoutError(LEAK)}, "relay not reachable: timed out"),
        (
            {
                "exc": aiohttp.ClientConnectorCertificateError(
                    Mock(host="relay", port=8787, ssl=True), ValueError(f"CN={LEAK}")
                )
            },
            "relay not reachable: TLS error (ClientConnectorCertificateError)",
        ),
        (
            {
                "exc": aiohttp.ClientConnectorSSLError(
                    Mock(host="relay", port=8787, ssl=True), OSError(1, f"CN={LEAK}")
                )
            },
            "relay not reachable: TLS error (ClientConnectorSSLError)",
        ),
    ],
)
async def test_bad_reply_hides_the_body(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, reply: dict, reason: str
) -> None:
    aioclient_mock.get(f"{RELAY}/v1/status", **reply)
    with pytest.raises(RelayError) as err:
        await client(hass).status()
    assert str(err.value) == f"Reading the relay status failed: {reason}"
    assert LEAK not in "".join(traceback.format_exception(err.value))


async def test_connection_error_keeps_aiohttp_text(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    refused = aiohttp.ClientConnectorError(
        Mock(host="relay", port=8787, ssl=True), ConnectionRefusedError(61, "Connection refused")
    )
    aioclient_mock.get(f"{RELAY}/v1/status", exc=refused)
    with pytest.raises(RelayError) as err:
        await client(hass).status()
    assert str(err.value) == f"Reading the relay status failed: relay not reachable: {refused}"


async def test_503_body_stays_out_of_diagnostics_and_toasts(
    hass: HomeAssistant,
    hass_client,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    aioclient_mock.get(f"{RELAY}/v1/desktop", json=desktop_state())
    aioclient_mock.get(f"{RELAY}/v1/status", json=relay_status())
    aioclient_mock.put(f"{RELAY}/v1/webhooks/{config_entry.entry_id}", status=503, text=HTML)
    aioclient_mock.delete(f"{RELAY}/v1/webhooks/{config_entry.entry_id}", status=204)
    await setup_entry(hass, config_entry)

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{RELAY}/v1/desktop", status=503, text=HTML)
    aioclient_mock.get(f"{RELAY}/v1/status", status=503, text=HTML)
    aioclient_mock.post(f"{RELAY}/v1/desktop/media", status=503, text=HTML)
    aioclient_mock.delete(f"{RELAY}/v1/webhooks/{config_entry.entry_id}", status=204)
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            "switch", "turn_off", {"entity_id": "switch.kumospace_microphone"}, blocking=True
        )
    assert str(err.value) == (
        "Better Kumo relay: Turning the microphone off failed: "
        "Kumospace desktop is not in a call (HTTP 503)"
    )
    await config_entry.runtime_data.async_refresh()
    await config_entry.runtime_data.status.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)

    diag = await get_diagnostics_for_config_entry(hass, hass_client, config_entry)
    assert diag["last_error"] == (
        "Reading Kumospace failed: Kumospace desktop is not connected to the relay, "
        "or not in a space (HTTP 503)"
    )
    assert diag["status_error"] == (
        "Reading the relay status failed: the relay is temporarily unavailable (HTTP 503)"
    )
    assert diag["webhook"]["registration_error"] == (
        "Registering the webhook failed: the relay is temporarily unavailable (HTTP 503)"
    )
    assert LEAK not in str(diag)
    assert LEAK not in caplog.text

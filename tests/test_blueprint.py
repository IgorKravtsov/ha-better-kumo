"""The talking_light blueprint, run as a real automation."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import shutil

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

BLUEPRINT = Path(__file__).parent.parent / "blueprints/automation/better_kumo/talking_light.yaml"
IN_CALL = "binary_sensor.kumospace_in_a_call"
EARSHOT = "binary_sensor.kumospace_someone_in_earshot"
SCENE = "scene.better_kumo_light_desk"


@pytest.fixture
async def calls(hass: HomeAssistant, tmp_path: Path) -> list[ServiceCall]:
    target = tmp_path / "blueprints/automation/better_kumo"
    target.mkdir(parents=True)
    shutil.copy(BLUEPRINT, target)
    hass.config.config_dir = str(tmp_path)
    hass.states.async_set(IN_CALL, "off")
    hass.states.async_set(EARSHOT, "off")

    log: list[ServiceCall] = []

    async def scene_create(call: ServiceCall) -> None:
        log.append(call)
        hass.states.async_set(f"scene.{call.data['scene_id']}", "unknown")

    async def scene_delete(call: ServiceCall) -> None:
        log.append(call)
        hass.states.async_remove(SCENE)

    async def record(call: ServiceCall) -> None:
        log.append(call)

    hass.services.async_register("scene", "create", scene_create)
    hass.services.async_register("scene", "delete", scene_delete)
    hass.services.async_register("scene", "turn_on", record)
    hass.services.async_register("light", "turn_on", record)

    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": {
                "use_blueprint": {
                    "path": "better_kumo/talking_light.yaml",
                    "input": {
                        "light": "light.desk",
                        "color": [0, 0, 255],
                        "off_delay": {"seconds": 30},
                        "in_call": IN_CALL,
                        "earshot": EARSHOT,
                    },
                }
            }
        },
    )
    await hass.async_block_till_done()
    return log


def _names(log: list[ServiceCall]) -> list[str]:
    return [f"{c.domain}.{c.service}" for c in log]


async def _wait(hass: HomeAssistant, seconds: int) -> None:
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))
    await hass.async_block_till_done()


async def test_talk_colors_then_restores(hass: HomeAssistant, calls: list[ServiceCall]) -> None:
    hass.states.async_set(IN_CALL, "on")
    await hass.async_block_till_done()
    assert _names(calls) == ["scene.create", "light.turn_on"]
    assert calls[0].data["snapshot_entities"] == ["light.desk"]
    assert calls[1].data["rgb_color"] == [0, 0, 255]

    # Earshot joining mid-call must not take a second snapshot.
    hass.states.async_set(EARSHOT, "on")
    hass.states.async_set(IN_CALL, "off")
    await hass.async_block_till_done()
    assert _names(calls) == ["scene.create", "light.turn_on"]

    hass.states.async_set(EARSHOT, "off")
    await hass.async_block_till_done()
    await _wait(hass, 10)
    assert len(calls) == 2
    await _wait(hass, 31)
    assert _names(calls)[2:] == ["scene.turn_on", "scene.delete"]
    assert calls[2].data["entity_id"] == [SCENE]


async def test_unavailable_sensors_do_not_restore(
    hass: HomeAssistant, calls: list[ServiceCall]
) -> None:
    hass.states.async_set(IN_CALL, "on")
    await hass.async_block_till_done()
    hass.states.async_set(IN_CALL, "unavailable")
    hass.states.async_set(EARSHOT, "unavailable")
    await hass.async_block_till_done()
    await _wait(hass, 60)
    assert _names(calls) == ["scene.create", "light.turn_on"]

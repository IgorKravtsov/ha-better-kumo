"""Base entity for Better Kumo."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import RelayError
from .const import DOMAIN
from .coordinator import BetterKumoCoordinator, StatusCoordinator


class KumospaceDeviceEntity[C: BetterKumoCoordinator | StatusCoordinator](CoordinatorEntity[C]):
    """An entity of the one Kumospace device of a relay."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: C, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Kumospace",
            manufacturer="Better Kumo",
        )


class BetterKumoEntity(KumospaceDeviceEntity[BetterKumoCoordinator]):
    """An entity that reads the DesktopState."""

    @property
    def available(self) -> bool:
        """Unavailable while the relay or the desktop app is down."""
        return super().available and self.coordinator.data is not None

    @property
    def state_data(self) -> dict[str, Any]:
        """The current DesktopState."""
        return self.coordinator.data or {}

    @property
    def me(self) -> dict[str, Any]:
        """The `self` part of DesktopState."""
        return self.state_data.get("self") or {}

    async def _command(self, request: Callable[[], Awaitable[dict[str, Any]]]) -> None:
        try:
            # Each reply is a full snapshot, so an older one must not land after a newer one.
            async with self.coordinator.command_lock:
                self.coordinator.async_apply(await request())
        except RelayError as err:
            raise HomeAssistantError(f"Better Kumo relay: {err}") from err
        finally:
            # The reply has no revision and a failed request may still have acted.
            self.coordinator.async_invalidate()


class RelayEntity(KumospaceDeviceEntity[StatusCoordinator]):
    """An entity about the relay's computer, following GET /v1/status alone."""

    @property
    def idle_seconds(self) -> int | None:
        """Seconds since the last input on the relay's computer."""
        return self.coordinator.data

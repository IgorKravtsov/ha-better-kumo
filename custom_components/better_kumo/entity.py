"""Base entity for Better Kumo."""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Any

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import RelayError
from .const import DOMAIN
from .coordinator import BetterKumoCoordinator


class BetterKumoEntity(CoordinatorEntity[BetterKumoCoordinator]):
    """An entity of the one Kumospace device of a relay."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: BetterKumoCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Kumospace",
            manufacturer="Better Kumo",
        )

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

    async def _command(self, request: Awaitable[dict[str, Any]]) -> None:
        try:
            state = await request
        except RelayError as err:
            raise HomeAssistantError(f"Better Kumo relay: {err}") from err
        if isinstance(state, dict):
            self.coordinator.async_apply(state)

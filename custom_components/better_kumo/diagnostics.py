"""Diagnostics for Better Kumo."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_SECRET, CONF_WEBHOOK_SECRET
from .coordinator import BetterKumoConfigEntry
from .webhook import webhook_urls

TO_REDACT = {CONF_SECRET, CONF_WEBHOOK_SECRET}
# Coworkers' names and status messages are not needed to debug the integration.
DESKTOP_REDACT = {"name", "message", "avatar", "uid"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: BetterKumoConfigEntry
) -> dict[str, Any]:
    """Return diagnostics, including the webhook URLs for manual registration."""
    coordinator = entry.runtime_data
    receiver = coordinator.receiver
    status = coordinator.status
    # HA keeps the last data after a failed read.
    read = status.data if status.last_update_success else None
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "webhook": {
            "urls": webhook_urls(hass, entry),
            "registered_urls": receiver.registered_urls if receiver else [],
            "registration_error": receiver.last_error if receiver else None,
        },
        "last_update_success": coordinator.last_update_success,
        # HA 2025.8 keeps last_exception after a later success.
        "last_error": None if coordinator.last_update_success else str(coordinator.last_exception),
        # HA keeps the last data after a failed read; report only what the last read returned.
        "desktop": async_redact_data(coordinator.data, DESKTOP_REDACT)
        if coordinator.last_update_success and coordinator.data
        else None,
        "status_ok": status.last_update_success if status.has_read else None,
        "status_error": None if status.last_update_success else str(status.last_exception),
        "idle_seconds": read.idle_seconds if read else None,
        "phone_idle_known": read.phone_known if read else None,
        "phone_idle_seconds": read.phone_idle_seconds if read else None,
    }

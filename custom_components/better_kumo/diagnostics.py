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
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "webhook": {
            "urls": webhook_urls(hass, entry),
            "registered_urls": receiver.registered_urls if receiver else [],
            "registration_error": receiver.last_error if receiver else None,
            "last_boot": receiver.boot if receiver else None,
            "last_seq": receiver.seq if receiver else None,
        },
        "last_update_success": coordinator.last_update_success,
        "last_error": str(coordinator.last_exception) if coordinator.last_exception else None,
        "desktop": async_redact_data(coordinator.data, DESKTOP_REDACT)
        if coordinator.data
        else None,
    }

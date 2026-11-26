"""Better Kumo: Kumospace presence through the Better Kumo relay."""

from __future__ import annotations

from homeassistant.const import CONF_URL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import RelayClient
from .const import CONF_SECRET
from .coordinator import BetterKumoConfigEntry, BetterKumoCoordinator
from .webhook import WebhookReceiver

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup_entry(hass: HomeAssistant, entry: BetterKumoConfigEntry) -> bool:
    """Set up a relay."""
    client = RelayClient(
        async_get_clientsession(hass), entry.data[CONF_URL], entry.data[CONF_SECRET]
    )
    coordinator = BetterKumoCoordinator(hass, entry, client)
    # Not async_config_entry_first_refresh: a relay or desktop that is down at
    # startup should give unavailable entities, not a setup retry loop.
    await coordinator.async_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Last, so a failed setup cannot leave a registration the relay keeps on disk.
    coordinator.receiver = WebhookReceiver(hass, entry, coordinator)
    await coordinator.receiver.async_setup()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: BetterKumoConfigEntry) -> bool:
    """Unload a relay and remove our webhook from it."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    if receiver := entry.runtime_data.receiver:
        await receiver.async_unregister()
    return True

"""Constants for Better Kumo."""

from datetime import timedelta

DOMAIN = "better_kumo"

CONF_WEBHOOK_SECRET = "webhook_secret"
CONF_LINK = "link"
CONF_POLL_INTERVAL = "poll_interval"
CONF_WEBHOOK_URL = "webhook_url"

DEFAULT_POLL_INTERVAL = 30
MIN_POLL_INTERVAL = 1
MAX_POLL_INTERVAL = 3600

REGISTER_INTERVAL = timedelta(minutes=10)

EVENT_BETTER_KUMO = "better_kumo_event"

AVAILABILITIES = ["available", "away", "focusing"]
CONF_SECRET = "secret"

# Event kinds shown by the Office events entity; anything else but `message` becomes `other`.
OFFICE_EVENT_TYPES = (
    "nudge",
    "knock",
    "entered_room",
    "left_room",
    "joined_floor",
    "online",
    "offline",
    "status",
    "nearby_chat",
)


def signal_event(entry_id: str) -> str:
    """Dispatcher signal carrying each new relay event of a config entry."""
    return f"{DOMAIN}_event_{entry_id}"

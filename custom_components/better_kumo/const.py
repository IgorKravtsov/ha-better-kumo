"""Constants for Better Kumo."""

from datetime import timedelta

DOMAIN = "better_kumo"

CONF_WEBHOOK_SECRET = "webhook_secret"
CONF_LINK = "link"
CONF_POLL_INTERVAL = "poll_interval"
CONF_WEBHOOK_URL = "webhook_url"
CONF_AWAY_AFTER = "away_after"
CONF_PHONE_IN_USE_FOR = "phone_in_use_for"

DEFAULT_POLL_INTERVAL = 30
MIN_POLL_INTERVAL = 1
MAX_POLL_INTERVAL = 3600

DEFAULT_AWAY_AFTER = 300
MIN_AWAY_AFTER = 30
MAX_AWAY_AFTER = 3600

# The phone app reports every 30 s while open, so the value can lag up to about 30 s.
DEFAULT_PHONE_IN_USE_FOR = 90
MIN_PHONE_IN_USE_FOR = 30
MAX_PHONE_IN_USE_FOR = 3600

REGISTER_INTERVAL = timedelta(minutes=10)

EVENT_BETTER_KUMO = "better_kumo_event"

AVAILABILITIES = ["available", "away", "focusing"]
CONF_SECRET = "secret"

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

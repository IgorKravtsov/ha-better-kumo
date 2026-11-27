"""Config and options flows for Better Kumo."""

from __future__ import annotations

import math
import secrets
from typing import Any

from homeassistant.components import webhook
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_URL, CONF_WEBHOOK_ID
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import voluptuous as vol

from .api import (
    RelayAuthError,
    RelayClient,
    RelayError,
    check_secret,
    normalize_url,
    parse_pairing_link,
)
from .const import (
    CONF_AWAY_AFTER,
    CONF_LINK,
    CONF_PHONE_IN_USE_FOR,
    CONF_POLL_INTERVAL,
    CONF_SECRET,
    CONF_WEBHOOK_SECRET,
    CONF_WEBHOOK_URL,
    DEFAULT_AWAY_AFTER,
    DEFAULT_PHONE_IN_USE_FOR,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    MAX_AWAY_AFTER,
    MAX_PHONE_IN_USE_FOR,
    MAX_POLL_INTERVAL,
    MIN_AWAY_AFTER,
    MIN_PHONE_IN_USE_FOR,
    MIN_POLL_INTERVAL,
)
from .coordinator import BetterKumoConfigEntry

USER_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_LINK): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
        vol.Optional(CONF_URL): TextSelector(TextSelectorConfig(type=TextSelectorType.URL)),
        vol.Optional(CONF_SECRET): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
    }
)


def _pairing(user_input: dict[str, Any]) -> tuple[str, str] | str:
    """Return (url, secret) from the form, or an error key."""
    link = user_input.get(CONF_LINK, "").strip()
    url = user_input.get(CONF_URL, "").strip()
    secret = user_input.get(CONF_SECRET, "").strip()
    if link:
        try:
            return parse_pairing_link(link)
        except ValueError:
            return "invalid_link"
    if not url or not secret:
        return "missing_input"
    try:
        url = normalize_url(url)
    except ValueError:
        return "invalid_url"
    try:
        return url, check_secret(secret)
    except ValueError:
        return "invalid_secret"


class BetterKumoConfigFlow(ConfigFlow, domain=DOMAIN):
    """Pair with a relay from its link, or its URL and secret."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle the only step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            parsed = _pairing(user_input)
            if isinstance(parsed, str):
                errors["base"] = parsed
            else:
                url, secret = parsed
                client = RelayClient(async_get_clientsession(self.hass), url, secret)
                try:
                    status = await client.status()
                except RelayAuthError:
                    errors["base"] = "invalid_auth"
                except RelayError:
                    errors["base"] = "cannot_connect"
                else:
                    # The relay serves exactly one Kumospace account; its uid is
                    # stable across relay address changes (LAN vs Tailscale).
                    await self.async_set_unique_id(str(status.get("uid") or url))
                    # The old relay may miss the DELETE (offline, or replaced at the same address).
                    updates = {
                        CONF_URL: url,
                        CONF_SECRET: secret,
                        CONF_WEBHOOK_SECRET: secrets.token_hex(32),
                    }
                    self._abort_if_unique_id_configured(updates=updates)
                    return self.async_create_entry(
                        title="Kumospace",
                        data={
                            CONF_URL: url,
                            CONF_SECRET: secret,
                            CONF_WEBHOOK_ID: webhook.async_generate_id(),
                            CONF_WEBHOOK_SECRET: secrets.token_hex(32),
                        },
                    )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                USER_SCHEMA, {CONF_URL: (user_input or {}).get(CONF_URL)}
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: BetterKumoConfigEntry) -> BetterKumoOptionsFlow:
        """Create the options flow."""
        return BetterKumoOptionsFlow()


class BetterKumoOptionsFlow(OptionsFlowWithReload):
    """Poll interval, webhook URL override and the idle times that mean away."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle options."""
        errors: dict[str, str] = {}
        if user_input is not None:
            options: dict[str, Any] = {}
            for key, error in (
                (CONF_POLL_INTERVAL, "invalid_interval"),
                (CONF_AWAY_AFTER, "invalid_away_after"),
                (CONF_PHONE_IN_USE_FOR, "invalid_phone_in_use_for"),
            ):
                seconds = user_input[key]
                # The selector's step=1 is only a UI hint; the API accepts 1.9 and "NaN".
                if math.isfinite(seconds) and seconds == int(seconds):
                    options[key] = int(seconds)
                else:
                    errors[key] = error
            if override := user_input.get(CONF_WEBHOOK_URL, "").strip():
                try:
                    options[CONF_WEBHOOK_URL] = normalize_url(override)
                except ValueError:
                    errors[CONF_WEBHOOK_URL] = "invalid_url"
            if not errors:
                return self.async_create_entry(data=options)
        schema = vol.Schema(
            {
                vol.Required(CONF_POLL_INTERVAL, default=DEFAULT_POLL_INTERVAL): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_POLL_INTERVAL,
                        max=MAX_POLL_INTERVAL,
                        step=1,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement="s",
                    )
                ),
                vol.Required(CONF_AWAY_AFTER, default=DEFAULT_AWAY_AFTER): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_AWAY_AFTER,
                        max=MAX_AWAY_AFTER,
                        step=1,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement="s",
                    )
                ),
                vol.Required(
                    CONF_PHONE_IN_USE_FOR, default=DEFAULT_PHONE_IN_USE_FOR
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_PHONE_IN_USE_FOR,
                        max=MAX_PHONE_IN_USE_FOR,
                        step=1,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement="s",
                    )
                ),
                vol.Optional(CONF_WEBHOOK_URL): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.URL)
                ),
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                schema, user_input or self.config_entry.options
            ),
            errors=errors,
        )

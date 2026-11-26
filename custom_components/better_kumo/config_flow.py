"""Config and options flows for Better Kumo."""

from __future__ import annotations

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

from .api import RelayAuthError, RelayClient, RelayError, normalize_url, parse_pairing_link
from .const import (
    CONF_LINK,
    CONF_POLL_INTERVAL,
    CONF_SECRET,
    CONF_WEBHOOK_SECRET,
    CONF_WEBHOOK_URL,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    MAX_POLL_INTERVAL,
    MIN_POLL_INTERVAL,
)
from .coordinator import BetterKumoConfigEntry

USER_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_LINK): TextSelector(),
        vol.Optional(CONF_URL): TextSelector(TextSelectorConfig(type=TextSelectorType.URL)),
        vol.Optional(CONF_SECRET): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
    }
)


class BetterKumoConfigFlow(ConfigFlow, domain=DOMAIN):
    """Pair with a relay from its link, or its URL and secret."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle the only step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                if link := user_input.get(CONF_LINK, "").strip():
                    url, secret = parse_pairing_link(link)
                elif user_input.get(CONF_URL) and user_input.get(CONF_SECRET):
                    url = normalize_url(user_input[CONF_URL])
                    secret = user_input[CONF_SECRET].strip()
                else:
                    raise LookupError
            except LookupError:
                errors["base"] = "missing_input"
            except ValueError:
                errors["base"] = "invalid_link" if user_input.get(CONF_LINK) else "invalid_url"
            else:
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
                    self._abort_if_unique_id_configured(
                        updates={CONF_URL: url, CONF_SECRET: secret}
                    )
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
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: BetterKumoConfigEntry) -> BetterKumoOptionsFlow:
        """Create the options flow."""
        return BetterKumoOptionsFlow()


class BetterKumoOptionsFlow(OptionsFlowWithReload):
    """Poll interval and webhook URL override."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle options."""
        errors: dict[str, str] = {}
        if user_input is not None:
            options = {CONF_POLL_INTERVAL: int(user_input[CONF_POLL_INTERVAL])}
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

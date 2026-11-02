"""Validated user/options/reauth/reconfigure flows, plus explicit legacy import."""

import uuid

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import WeatherAPIError, WeatherAuthError, WeatherClient, validate_host
from .const import (
    CONF_API_HOST,
    CONF_LEGACY_ENTRY,
    CONF_LOCATION_ENTITY,
    CONF_LOCATION_MODE,
    DAILY_CHOICES,
    DEFAULT_HOST,
    DEFAULT_INTERVAL,
    DOMAIN,
    HOURLY_CHOICES,
    LOCATION_MODES,
    NAME,
)
from .migration import legacy_entry
from .models import coordinates, parse_current, state_coordinates


def user_schema(defaults=None):
    d = defaults or {}
    return vol.Schema(
        {
            vol.Required("name", default=d.get("name", NAME)): str,
            vol.Required("api_key"): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
            vol.Required(
                CONF_API_HOST, default=d.get(CONF_API_HOST, DEFAULT_HOST)
            ): str,
            vol.Required("latitude", default=d.get("latitude", 0)): cv.latitude,
            vol.Required("longitude", default=d.get("longitude", 0)): cv.longitude,
        }
    )


def options_schema(entry):
    o = entry.options
    return vol.Schema(
        {
            vol.Required("dailysteps", default=o.get("dailysteps", 7)): vol.In(
                DAILY_CHOICES
            ),
            vol.Required("hourlysteps", default=o.get("hourlysteps", 24)): vol.In(
                HOURLY_CHOICES
            ),
            vol.Required(
                "interval", default=o.get("interval", DEFAULT_INTERVAL)
            ): vol.All(vol.Coerce(int), vol.Range(min=10, max=1440)),
            vol.Required("sugg", default=o.get("sugg", False)): bool,
            vol.Required("disaster_level", default=o.get("disaster_level", 1)): vol.All(
                vol.Coerce(int), vol.Range(min=0, max=6)
            ),
            vol.Required(
                CONF_LOCATION_MODE, default=o.get(CONF_LOCATION_MODE, "fixed")
            ): vol.In(LOCATION_MODES),
            vol.Optional(
                CONF_LOCATION_ENTITY, default=o.get(CONF_LOCATION_ENTITY, "")
            ): str,
        }
    )


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return OptionsFlow()

    async def _validate(self, data):
        try:
            host = validate_host(data.get(CONF_API_HOST, DEFAULT_HOST))
            position = coordinates((data.get("longitude"), data.get("latitude")))
            if position is None:
                return "invalid_location"
            client = WeatherClient(
                async_get_clientsession(self.hass), data["api_key"], host
            )
            parse_current(await client.request("weather/now", *position))
        except WeatherAuthError:
            return "invalid_auth"
        except WeatherAPIError:
            return "cannot_connect"
        except (ValueError, KeyError):
            return "invalid_input"
        return None

    async def async_step_user(self, user_input=None):
        compatible = []
        for entry in self.hass.config_entries.async_entries("heweather"):
            try:
                compatible.append(legacy_entry(self.hass, entry.entry_id))
            except ValueError:
                continue
        if compatible:
            return self.async_show_menu(
                step_id="user", menu_options=["configure", "migrate"]
            )
        return await self.async_step_configure(user_input)

    async def async_step_migrate(self, user_input=None):
        if user_input is not None:
            return await self.async_step_import(user_input)
        choices = {}
        for entry in self.hass.config_entries.async_entries("heweather"):
            try:
                old = legacy_entry(self.hass, entry.entry_id)
                choices[old.entry_id] = old.title
            except ValueError:
                continue
        if not choices:
            return self.async_abort(reason="invalid_legacy_entry")
        return self.async_show_form(
            step_id="migrate",
            data_schema=vol.Schema({vol.Required(CONF_LEGACY_ENTRY): vol.In(choices)}),
        )

    async def async_step_configure(self, user_input=None):
        errors = {}
        if user_input is not None:
            error = await self._validate(user_input)
            if error:
                errors["base"] = error
            else:
                # Identity must not change as an RV moves or its name changes.
                await self.async_set_unique_id(uuid.uuid4().hex)
                return self.async_create_entry(
                    title=user_input["name"], data=user_input
                )
        defaults = {
            "latitude": self.hass.config.latitude,
            "longitude": self.hass.config.longitude,
        }
        if user_input:
            defaults.update(user_input)
        return self.async_show_form(
            step_id="configure", data_schema=user_schema(defaults), errors=errors
        )

    async def async_step_import(self, user_input):
        try:
            old = legacy_entry(self.hass, user_input[CONF_LEGACY_ENTRY])
        except (ValueError, KeyError, TypeError):
            return self.async_abort(reason="invalid_legacy_entry")
        await self.async_set_unique_id(old.unique_id)
        self._abort_if_unique_id_configured()
        data = dict(old.data)
        data[CONF_API_HOST] = DEFAULT_HOST
        data[CONF_LEGACY_ENTRY] = old.entry_id
        # Preserve the old fixed-coordinate behavior; do not silently switch to a broken address sensor.
        options = dict(old.options)
        options.setdefault(CONF_LOCATION_MODE, "fixed")
        if options.get("dailysteps", 7) not in DAILY_CHOICES:
            options["dailysteps"] = 7
        if options.get("hourlysteps", 24) not in HOURLY_CHOICES:
            options["hourlysteps"] = 24
        options["interval"] = max(
            10, min(int(options.get("interval", DEFAULT_INTERVAL)), 1440)
        )
        return self.async_create_entry(title=old.title, data=data, options=options)

    async def async_step_reauth(self, entry_data):
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        entry = self._get_reauth_entry()
        errors = {}
        if user_input:
            data = {**entry.data, **user_input}
            error = await self._validate(data)
            if error:
                errors["base"] = error
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates=user_input
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required("api_key"): str}),
            errors=errors,
        )

    async def async_step_reconfigure(self, user_input=None):
        entry = self._get_reconfigure_entry()
        errors = {}
        if user_input:
            error = await self._validate(user_input)
            if error:
                errors["base"] = error
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates=user_input
                )
        return self.async_show_form(
            step_id="reconfigure", data_schema=user_schema(entry.data), errors=errors
        )


class OptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        errors = {}
        if user_input is not None:
            if user_input[CONF_LOCATION_MODE] == "entity":
                state = self.hass.states.get(user_input.get(CONF_LOCATION_ENTITY, ""))
                if state_coordinates(state) is None:
                    errors["base"] = "invalid_location"
            if not errors:
                return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(
            step_id="init", data_schema=options_schema(self.config_entry), errors=errors
        )

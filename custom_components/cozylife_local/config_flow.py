"""Config flow for CozyLife Local."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .client import CozyLifeClient
from .const import (
    CONF_CHANNELS,
    CONF_DEVICES,
    CONF_DID,
    CONF_DTP,
    CONF_IP,
    CONF_MAC,
    CONF_NAME,
    CONF_PID,
    CONF_PORT,
    DEFAULT_CHANNELS,
    DOMAIN,
    MAX_CHANNELS,
    WIFI_TCP_PORT,
)
from .discovery import discover_devices

STEP_USER = vol.Schema(
    {
        vol.Required("mode", default="discover"): vol.In(
            {"discover": "Discover on LAN", "manual": "Add by IP"}
        )
    }
)


class CannotConnect(HomeAssistantError):
    """Cannot reach device."""


async def _validate_manual(hass: HomeAssistant, ip: str, port: int) -> dict[str, Any]:
    client = CozyLifeClient(ip, port)
    try:
        await client.get_bitmask()
    except OSError as err:
        raise CannotConnect from err
    return {
        CONF_DID: f"manual-{ip.replace('.', '-')}",
        CONF_IP: ip,
        CONF_PORT: port,
        CONF_MAC: "",
        CONF_NAME: f"CozyLife {ip}",
        CONF_PID: "",
        CONF_DTP: "",
        CONF_CHANNELS: DEFAULT_CHANNELS,
    }


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._discovered: list[dict[str, Any]] = []
        self._pending_devices: list[dict[str, Any]] = []

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return CozyLifeOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=STEP_USER)
        if user_input["mode"] == "manual":
            return await self.async_step_manual()
        return await self.async_step_discover()

    async def async_step_discover(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is None:
            self._discovered = await discover_devices()
            if not self._discovered:
                return self.async_show_form(
                    step_id="discover",
                    data_schema=vol.Schema({}),
                    errors={"base": "no_devices"},
                )
            options = {
                d[CONF_DID]: f"{d.get(CONF_NAME) or d[CONF_DID]} ({d[CONF_IP]})"
                for d in self._discovered
            }
            return self.async_show_form(
                step_id="discover",
                data_schema=vol.Schema(
                    {vol.Required("selected"): cv.multi_select(options)}
                ),
            )

        selected_ids = user_input.get("selected") or []
        by_id = {d[CONF_DID]: d for d in self._discovered}
        devices = []
        for did in selected_ids:
            src = by_id[did]
            devices.append(
                {
                    CONF_DID: src[CONF_DID],
                    CONF_IP: src[CONF_IP],
                    CONF_MAC: src.get(CONF_MAC, ""),
                    CONF_NAME: src.get(CONF_NAME) or src[CONF_DID],
                    CONF_PID: src.get(CONF_PID, ""),
                    CONF_DTP: src.get(CONF_DTP, ""),
                    CONF_PORT: WIFI_TCP_PORT,
                    CONF_CHANNELS: DEFAULT_CHANNELS,
                }
            )
        if not devices:
            return self.async_show_form(
                step_id="discover",
                data_schema=vol.Schema({}),
                errors={"base": "no_selection"},
            )
        self._pending_devices = devices
        return await self.async_step_channels()

    async def async_step_channels(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        schema_dict: dict[Any, Any] = {}
        for device in self._pending_devices:
            key = f"channels_{device[CONF_DID]}"
            label_default = device.get(CONF_CHANNELS, DEFAULT_CHANNELS)
            schema_dict[
                vol.Required(key, default=label_default)
            ] = vol.All(vol.Coerce(int), vol.Range(min=1, max=MAX_CHANNELS))

        if user_input is None:
            return self.async_show_form(
                step_id="channels",
                data_schema=vol.Schema(schema_dict),
                description_placeholders={
                    "hint": "1 = plug/single rocker. 2+ = multi-gang (bitmask on DPID 1)."
                },
            )

        for device in self._pending_devices:
            key = f"channels_{device[CONF_DID]}"
            device[CONF_CHANNELS] = int(user_input[key])

        await self.async_set_unique_id("cozylife_local")
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title="CozyLife Local",
            data={CONF_DEVICES: self._pending_devices},
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        schema = vol.Schema(
            {
                vol.Required(CONF_IP): str,
                vol.Optional(CONF_PORT, default=WIFI_TCP_PORT): int,
                vol.Optional(CONF_NAME): str,
                vol.Optional(CONF_CHANNELS, default=DEFAULT_CHANNELS): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=MAX_CHANNELS)
                ),
            }
        )
        if user_input is None:
            return self.async_show_form(step_id="manual", data_schema=schema)
        try:
            device = await _validate_manual(
                self.hass,
                user_input[CONF_IP],
                int(user_input.get(CONF_PORT) or WIFI_TCP_PORT),
            )
        except CannotConnect:
            errors["base"] = "cannot_connect"
            return self.async_show_form(
                step_id="manual", data_schema=schema, errors=errors
            )
        if user_input.get(CONF_NAME):
            device[CONF_NAME] = user_input[CONF_NAME]
        device[CONF_CHANNELS] = int(
            user_input.get(CONF_CHANNELS) or DEFAULT_CHANNELS
        )
        await self.async_set_unique_id(device[CONF_DID])
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=device[CONF_NAME],
            data={CONF_DEVICES: [device]},
        )


class CozyLifeOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        devices = list(self.config_entry.data.get(CONF_DEVICES, []))
        schema_dict: dict[Any, Any] = {}
        for device in devices:
            key = f"channels_{device[CONF_DID]}"
            schema_dict[
                vol.Required(
                    key,
                    default=int(device.get(CONF_CHANNELS) or DEFAULT_CHANNELS),
                )
            ] = vol.All(vol.Coerce(int), vol.Range(min=1, max=MAX_CHANNELS))

        if user_input is None:
            return self.async_show_form(
                step_id="init",
                data_schema=vol.Schema(schema_dict),
            )

        new_devices = []
        for device in devices:
            updated = dict(device)
            key = f"channels_{device[CONF_DID]}"
            updated[CONF_CHANNELS] = int(user_input[key])
            new_devices.append(updated)

        self.hass.config_entries.async_update_entry(
            self.config_entry,
            data={**self.config_entry.data, CONF_DEVICES: new_devices},
        )
        return self.async_create_entry(title="", data={})

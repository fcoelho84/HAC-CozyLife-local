"""Config flow for CozyLife Local."""

from __future__ import annotations

import asyncio
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
    CONF_WIFI_PASSWORD,
    CONF_WIFI_SSID,
    DEFAULT_CHANNELS,
    DOMAIN,
    MAX_CHANNELS,
    WIFI_TCP_PORT,
)
from .discovery import discover_devices
from .provisioning import (
    async_provision_loose_devices,
    async_resolve_wifi_credentials,
    propagate_wifi_credentials,
)

STEP_USER = vol.Schema(
    {
        vol.Required("mode", default="discover"): vol.In(
            {"discover": "Discover on LAN", "manual": "Add by IP"}
        )
    }
)


class CannotConnect(HomeAssistantError):
    """Cannot reach device."""


def _wifi_schema_fields(
    creds: tuple[str, str] | None,
) -> dict[Any, Any]:
    default_ssid = creds[0] if creds else ""
    default_password = creds[1] if creds else ""
    return {
        vol.Optional(CONF_WIFI_SSID, default=default_ssid): str,
        vol.Optional(CONF_WIFI_PASSWORD, default=default_password): str,
    }


def _extract_wifi_input(
    user_input: dict[str, Any], fallback: tuple[str, str] | None
) -> tuple[str, str]:
    ssid = str(user_input.get(CONF_WIFI_SSID) or "").strip()
    password = str(user_input.get(CONF_WIFI_PASSWORD) or "")
    if not ssid and fallback:
        return fallback
    return ssid, password


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

    async def _discover_with_provisioning(self) -> list[dict[str, Any]]:
        found = await discover_devices()
        if found:
            return found
        provisioned = await async_provision_loose_devices(self.hass)
        if not provisioned:
            return []
        await asyncio.sleep(4.0)
        return await discover_devices()

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
            self._discovered = await self._discover_with_provisioning()
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
        creds = await async_resolve_wifi_credentials(self.hass)
        schema_dict: dict[Any, Any] = {}
        for device in self._pending_devices:
            key = f"channels_{device[CONF_DID]}"
            label_default = device.get(CONF_CHANNELS, DEFAULT_CHANNELS)
            schema_dict[
                vol.Required(key, default=label_default)
            ] = vol.All(vol.Coerce(int), vol.Range(min=1, max=MAX_CHANNELS))
        schema_dict.update(_wifi_schema_fields(creds))

        if user_input is None:
            return self.async_show_form(
                step_id="channels",
                data_schema=vol.Schema(schema_dict),
                description_placeholders={
                    "hint": "1 = plug/single rocker. 2+ = multi-gang (bitmask on DPID 1)."
                },
            )

        ssid, password = _extract_wifi_input(user_input, creds)
        if ssid:
            propagate_wifi_credentials(self.hass, ssid, password)
        for device in self._pending_devices:
            key = f"channels_{device[CONF_DID]}"
            device[CONF_CHANNELS] = int(user_input[key])

        await self.async_set_unique_id("cozylife_local")
        self._abort_if_unique_id_configured()
        data: dict[str, Any] = {CONF_DEVICES: self._pending_devices}
        if ssid:
            data[CONF_WIFI_SSID] = ssid
            data[CONF_WIFI_PASSWORD] = password
        return self.async_create_entry(
            title="CozyLife Local",
            data=data,
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        creds = await async_resolve_wifi_credentials(self.hass)
        schema_dict: dict[Any, Any] = {
            vol.Required(CONF_IP): str,
            vol.Optional(CONF_PORT, default=WIFI_TCP_PORT): int,
            vol.Optional(CONF_NAME): str,
            vol.Optional(CONF_CHANNELS, default=DEFAULT_CHANNELS): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=MAX_CHANNELS)
            ),
        }
        schema_dict.update(_wifi_schema_fields(creds))
        schema = vol.Schema(schema_dict)
        if user_input is None:
            return self.async_show_form(step_id="manual", data_schema=schema)

        ssid, password = _extract_wifi_input(user_input, creds)
        if ssid:
            propagate_wifi_credentials(self.hass, ssid, password)
        ip = str(user_input[CONF_IP]).strip()
        port = int(user_input.get(CONF_PORT) or WIFI_TCP_PORT)
        try:
            device = await _validate_manual(self.hass, ip, port)
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
        data: dict[str, Any] = {CONF_DEVICES: [device]}
        if ssid:
            data[CONF_WIFI_SSID] = ssid
            data[CONF_WIFI_PASSWORD] = password
        return self.async_create_entry(
            title=device[CONF_NAME],
            data=data,
        )


class CozyLifeOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        devices = list(self.config_entry.data.get(CONF_DEVICES, []))
        creds = await async_resolve_wifi_credentials(self.hass, self.config_entry)
        schema_dict: dict[Any, Any] = {}
        for device in devices:
            key = f"channels_{device[CONF_DID]}"
            schema_dict[
                vol.Required(
                    key,
                    default=int(device.get(CONF_CHANNELS) or DEFAULT_CHANNELS),
                )
            ] = vol.All(vol.Coerce(int), vol.Range(min=1, max=MAX_CHANNELS))
        schema_dict.update(_wifi_schema_fields(creds))

        if user_input is None:
            return self.async_show_form(
                step_id="init",
                data_schema=vol.Schema(schema_dict),
            )

        ssid, password = _extract_wifi_input(user_input, creds)
        if ssid:
            propagate_wifi_credentials(self.hass, ssid, password)
        new_devices = []
        for device in devices:
            updated = dict(device)
            key = f"channels_{device[CONF_DID]}"
            updated[CONF_CHANNELS] = int(user_input[key])
            new_devices.append(updated)

        new_data = {**self.config_entry.data, CONF_DEVICES: new_devices}
        if ssid:
            new_data[CONF_WIFI_SSID] = ssid
            new_data[CONF_WIFI_PASSWORD] = password
        self.hass.config_entries.async_update_entry(
            self.config_entry,
            data=new_data,
        )
        return self.async_create_entry(title="", data={})

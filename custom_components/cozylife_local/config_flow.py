from __future__ import annotations

import asyncio
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .client import CozyLifeClient
from .const import (
    CONF_CHANNELS,
    CONF_DEVICES,
    CONF_HOST,
    CONF_PORT,
    CONF_WIFI_PASSWORD,
    CONF_WIFI_SSID,
    DEFAULT_CHANNELS,
    DOMAIN,
    MAX_CHANNELS,
    TCP_PORT,
)
from .discovery import DiscoveredDevice, discover_devices
from .provisioning import (
    async_provision_loose_devices,
    async_resolve_wifi_credentials,
    propagate_wifi_credentials,
)


def _device_to_dict(d: DiscoveredDevice, channels: int) -> dict[str, Any]:
    return {
        "ip": d.ip,
        "port": d.port,
        "did": d.did,
        "pid": d.pid,
        "mac": d.mac,
        "model": d.model,
        CONF_CHANNELS: max(1, min(MAX_CHANNELS, int(channels))),
    }


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


class CozyLifeLocalConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._discovered: list[DiscoveredDevice] = []

    async def _discover_with_provisioning(self) -> list[DiscoveredDevice]:
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
        errors: dict[str, str] = {}
        if user_input is not None:
            mode = user_input["mode"]
            if mode == "manual":
                return await self.async_step_manual()
            found = await self._discover_with_provisioning()
            if not found:
                errors["base"] = "no_devices_found"
            else:
                self._discovered = found
                return await self.async_step_channels()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("mode", default="discover"): vol.In(
                        {
                            "discover": "Discover on network (UDP 6095)",
                            "manual": "Enter IP manually (TCP 5555)",
                        }
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_channels(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if not self._discovered:
            return self.async_abort(reason="no_devices_found")

        creds = await async_resolve_wifi_credentials(self.hass)

        if user_input is not None:
            ssid, password = _extract_wifi_input(user_input, creds)
            if ssid:
                propagate_wifi_credentials(self.hass, ssid, password)
            devices_out: list[dict[str, Any]] = []
            for d in self._discovered:
                key = f"ch_{d.did}"
                ch = int(user_input.get(key, DEFAULT_CHANNELS))
                devices_out.append(_device_to_dict(d, ch))
            uid = "cozylife_local_" + "_".join(sorted(d["did"] for d in devices_out))
            await self.async_set_unique_id(uid)
            self._abort_if_unique_id_configured()
            title = (
                f"CozyLife ({devices_out[0]['ip']})"
                if len(devices_out) == 1
                else f"CozyLife ({len(devices_out)} devices)"
            )
            data: dict[str, Any] = {CONF_DEVICES: devices_out}
            if ssid:
                data[CONF_WIFI_SSID] = ssid
                data[CONF_WIFI_PASSWORD] = password
            return self.async_create_entry(title=title, data=data)

        schema_fields: dict[Any, Any] = {}
        for d in self._discovered:
            label = f"ch_{d.did}"
            schema_fields[vol.Required(label, default=DEFAULT_CHANNELS)] = vol.All(
                vol.Coerce(int), vol.Range(min=1, max=MAX_CHANNELS)
            )
        schema_fields.update(_wifi_schema_fields(creds))

        placeholders = {
            "devices": ", ".join(
                f"{d.ip} ({d.model or d.did})" for d in self._discovered
            )
        }
        return self.async_show_form(
            step_id="channels",
            data_schema=vol.Schema(schema_fields),
            description_placeholders=placeholders,
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        creds = await async_resolve_wifi_credentials(self.hass)

        if user_input is not None:
            host = str(user_input[CONF_HOST]).strip()
            port = int(user_input.get(CONF_PORT, TCP_PORT))
            channels = int(user_input.get(CONF_CHANNELS, DEFAULT_CHANNELS))
            ssid, password = _extract_wifi_input(user_input, creds)
            if ssid:
                propagate_wifi_credentials(self.hass, ssid, password)
            client = CozyLifeClient(host, port)
            ok = await client.test_connection()
            if not ok and ssid:
                provisioned = await async_provision_loose_devices(self.hass)
                if provisioned:
                    await asyncio.sleep(4.0)
                    ok = await client.test_connection()
            if not ok:
                errors["base"] = "cannot_connect"
            else:
                did = f"manual_{host.replace('.', '_')}_{port}"
                await self.async_set_unique_id(f"cozylife_local_{did}")
                self._abort_if_unique_id_configured()
                dev = {
                    "ip": host,
                    "port": port,
                    "did": did,
                    "pid": "",
                    "mac": "",
                    "model": "manual",
                    CONF_CHANNELS: max(1, min(MAX_CHANNELS, channels)),
                }
                data: dict[str, Any] = {CONF_DEVICES: [dev]}
                if ssid:
                    data[CONF_WIFI_SSID] = ssid
                    data[CONF_WIFI_PASSWORD] = password
                return self.async_create_entry(
                    title=f"CozyLife ({host})",
                    data=data,
                )

        schema_fields: dict[Any, Any] = {
            vol.Required(CONF_HOST): str,
            vol.Optional(CONF_PORT, default=TCP_PORT): vol.Coerce(int),
            vol.Optional(CONF_CHANNELS, default=DEFAULT_CHANNELS): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=MAX_CHANNELS)
            ),
        }
        schema_fields.update(_wifi_schema_fields(creds))

        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema(schema_fields),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return CozyLifeOptionsFlow(config_entry)


class CozyLifeOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        devices: list[dict[str, Any]] = list(
            self._entry.data.get(CONF_DEVICES, [])
        )
        creds = await async_resolve_wifi_credentials(self.hass, self._entry)

        if user_input is not None:
            ssid, password = _extract_wifi_input(user_input, creds)
            if ssid:
                propagate_wifi_credentials(self.hass, ssid, password)
            new_devices: list[dict[str, Any]] = []
            for d in devices:
                did = d["did"]
                key = f"ch_{did}"
                ch = int(user_input.get(key, d.get(CONF_CHANNELS, DEFAULT_CHANNELS)))
                nd = dict(d)
                nd[CONF_CHANNELS] = max(1, min(MAX_CHANNELS, ch))
                new_devices.append(nd)
            new_data = {**self._entry.data, CONF_DEVICES: new_devices}
            if ssid:
                new_data[CONF_WIFI_SSID] = ssid
                new_data[CONF_WIFI_PASSWORD] = password
            self.hass.config_entries.async_update_entry(
                self._entry,
                data=new_data,
            )
            return self.async_create_entry(title="", data={})

        fields: dict[Any, Any] = {}
        for d in devices:
            did = d["did"]
            cur = int(d.get(CONF_CHANNELS, DEFAULT_CHANNELS))
            fields[vol.Required(f"ch_{did}", default=cur)] = vol.All(
                vol.Coerce(int), vol.Range(min=1, max=MAX_CHANNELS)
            )
        fields.update(_wifi_schema_fields(creds))
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(fields),
        )

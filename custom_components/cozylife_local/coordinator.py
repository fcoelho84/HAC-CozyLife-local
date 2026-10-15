"""Data update coordinator."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import timedelta
import logging
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .client import CozyLifeClient, channels_from_config, device_from_config
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
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    POWER_OFF,
    POWER_ON,
    RECOVERY_COOLDOWN,
    WIFI_TCP_PORT,
)
from .discovery import discover_devices
from .protocol import gang_bit_on, with_gang_bit
from .provisioning import async_provision_loose_devices

_LOGGER = logging.getLogger(__name__)


def _norm_mac(mac: Any) -> str:
    return str(mac or "").replace(":", "").replace("-", "").lower()


class CozyLifeCoordinator(DataUpdateCoordinator[dict[str, int | None]]):
    def __init__(
        self,
        hass: HomeAssistant,
        devices: list[dict[str, Any]],
        entry: ConfigEntry | None = None,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.entry = entry
        self.devices: dict[str, dict[str, Any]] = {
            d[CONF_DID]: dict(d) for d in devices
        }
        self.clients: dict[str, CozyLifeClient] = {
            did: device_from_config(cfg, hass=hass)
            for did, cfg in self.devices.items()
        }
        self._last_recovery_ts: float = 0.0
        self._suppress_reload: bool = False
        self._new_device_callbacks: list[Callable[[list[dict[str, Any]]], None]] = []

    def close_all(self) -> None:
        for client in self.clients.values():
            client.close()

    def register_new_device_callback(
        self, callback_fn: Callable[[list[dict[str, Any]]], None]
    ) -> None:
        self._new_device_callbacks.append(callback_fn)

    def consume_suppress_reload(self) -> bool:
        if not self._suppress_reload:
            return False
        self._suppress_reload = False
        return True

    def channels_for(self, did: str) -> int:
        return channels_from_config(self.devices[did])

    async def _poll_all(self) -> tuple[dict[str, int | None], list[str]]:
        result: dict[str, int | None] = {}
        failed: list[str] = []
        for did, client in list(self.clients.items()):
            previous = self.data.get(did) if self.data else None
            try:
                bitmask = await client.get_bitmask()
                result[did] = bitmask if bitmask is not None else previous
                if bitmask is None:
                    failed.append(did)
            except OSError as err:
                _LOGGER.debug("Status failed for %s: %s", did, err)
                result[did] = previous
                failed.append(did)
        return result, failed

    def _match_discovered(
        self, did: str, discovered: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        cfg = self.devices.get(did)
        if not cfg:
            return None
        for d in discovered:
            if d.get(CONF_DID) == did:
                return d
        cfg_mac = _norm_mac(cfg.get(CONF_MAC))
        if cfg_mac:
            for d in discovered:
                if _norm_mac(d.get(CONF_MAC)) == cfg_mac:
                    return d
        if did.startswith("manual") and len(discovered) == 1:
            return discovered[0]
        return None

    async def _verify_and_update_device(
        self,
        did: str,
        disc: dict[str, Any],
        result: dict[str, int | None],
    ) -> bool:
        cfg = self.devices[did]
        client = self.clients[did]
        new_ip = str(disc[CONF_IP])
        new_port = int(disc.get(CONF_PORT) or cfg.get(CONF_PORT) or WIFI_TCP_PORT)
        changed = (cfg.get(CONF_IP) != new_ip) or (cfg.get(CONF_PORT) != new_port)
        cfg[CONF_IP] = new_ip
        cfg[CONF_PORT] = new_port
        if disc.get(CONF_MAC):
            cfg[CONF_MAC] = disc[CONF_MAC]
            client.mac = str(disc[CONF_MAC])
        if disc.get(CONF_PID):
            cfg[CONF_PID] = disc[CONF_PID]
        if disc.get(CONF_DTP):
            cfg[CONF_DTP] = disc[CONF_DTP]
        client.host = new_ip
        client.port = new_port
        try:
            bitmask = await client.get_bitmask()
        except OSError:
            return changed
        if bitmask is not None:
            result[did] = bitmask
            _LOGGER.info(
                "CozyLife %s connected at DHCP IP %s:%s",
                did,
                new_ip,
                new_port,
            )
        return changed

    def _all_configured_keys(self) -> tuple[set[str], set[str]]:
        dids: set[str] = set(self.devices.keys())
        macs: set[str] = {
            _norm_mac(d.get(CONF_MAC))
            for d in self.devices.values()
            if d.get(CONF_MAC)
        }
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            for raw in entry.data.get(CONF_DEVICES, []):
                if raw.get(CONF_DID):
                    dids.add(str(raw[CONF_DID]))
                if raw.get(CONF_MAC):
                    macs.add(_norm_mac(raw[CONF_MAC]))
        return dids, macs

    async def _adopt_new_devices(
        self,
        discovered: list[dict[str, Any]],
        result: dict[str, int | None],
    ) -> list[dict[str, Any]]:
        known_dids, known_macs = self._all_configured_keys()
        adopted: list[dict[str, Any]] = []
        for disc in discovered:
            did = str(disc.get(CONF_DID) or "")
            ip = str(disc.get(CONF_IP) or "")
            if not did or not ip:
                continue
            norm_m = _norm_mac(disc.get(CONF_MAC))
            if did in known_dids or (norm_m and norm_m in known_macs):
                continue
            mac_str = str(disc.get(CONF_MAC) or "")
            client = CozyLifeClient(
                ip, WIFI_TCP_PORT, mac=mac_str, did=did, hass=self.hass
            )
            try:
                bitmask = await client.get_bitmask()
            except OSError:
                bitmask = None
            cfg = {
                CONF_DID: did,
                CONF_IP: ip,
                CONF_MAC: mac_str,
                CONF_NAME: str(disc.get(CONF_NAME) or did),
                CONF_PID: str(disc.get(CONF_PID) or ""),
                CONF_DTP: str(disc.get(CONF_DTP) or ""),
                CONF_PORT: WIFI_TCP_PORT,
                CONF_CHANNELS: DEFAULT_CHANNELS,
            }
            self.devices[did] = cfg
            self.clients[did] = client
            result[did] = bitmask
            known_dids.add(did)
            if norm_m:
                known_macs.add(norm_m)
            adopted.append(cfg)
            _LOGGER.info("Adopted CozyLife switch %s at DHCP IP %s", did, ip)
        return adopted

    def _persist_entry_devices(self) -> None:
        if self.entry is None:
            return
        new_data = dict(self.entry.data)
        new_data[CONF_DEVICES] = [dict(d) for d in self.devices.values()]
        self._suppress_reload = True
        self.hass.config_entries.async_update_entry(self.entry, data=new_data)

    async def _maybe_provision_loose(self, failed_dids: list[str]) -> None:
        now = time.monotonic()
        if now - self._last_recovery_ts < RECOVERY_COOLDOWN:
            return
        self._last_recovery_ts = now
        offline_macs = {
            str(self.devices[d].get(CONF_MAC))
            for d in failed_dids
            if d in self.devices and self.devices[d].get(CONF_MAC)
        }
        provisioned = await async_provision_loose_devices(
            self.hass, self.entry, offline_macs
        )
        if provisioned:
            await asyncio.sleep(4.0)

    async def _recover_and_discover(
        self,
        failed_dids: list[str],
        result: dict[str, int | None],
    ) -> None:
        if failed_dids:
            await self._maybe_provision_loose(failed_dids)
        discovered = await discover_devices()
        if not discovered:
            return
        entry_changed = False
        for did in failed_dids:
            disc = self._match_discovered(did, discovered)
            if disc and await self._verify_and_update_device(did, disc, result):
                entry_changed = True
        adopted = await self._adopt_new_devices(discovered, result)
        if entry_changed or adopted:
            self._persist_entry_devices()
        if adopted:
            for cb in self._new_device_callbacks:
                cb(adopted)

    async def _async_update_data(self) -> dict[str, int | None]:
        result, failed = await self._poll_all()
        now = time.monotonic()
        should_scan = bool(failed) or (
            now - self._last_recovery_ts >= RECOVERY_COOLDOWN
        )
        if should_scan:
            await self._recover_and_discover(failed, result)
            if not failed:
                self._last_recovery_ts = now
        return result

    def is_gang_on(self, did: str, gang_bit: int) -> bool | None:
        if not self.data:
            return None
        value = self.data.get(did)
        if value is None:
            return None
        channels = self.channels_for(did)
        if channels <= 1:
            return value != POWER_OFF
        return gang_bit_on(value, gang_bit)

    async def async_set_gang(self, did: str, gang_bit: int, on: bool) -> None:
        client = self.clients[did]
        channels = self.channels_for(did)
        try:
            new_value = await self._send_gang_state(
                did, client, channels, gang_bit, on
            )
        except OSError:
            await self._recover_and_discover([did], self.data or {})
            try:
                new_value = await self._send_gang_state(
                    did, client, channels, gang_bit, on
                )
            except OSError as err:
                _LOGGER.warning(
                    "Command failed for %s after reconnect retries: %s",
                    did,
                    err,
                )
                return
        if self.data is None:
            self.data = {}
        self.data[did] = new_value
        self.async_set_updated_data(dict(self.data))

    async def _send_gang_state(
        self,
        did: str,
        client: CozyLifeClient,
        channels: int,
        gang_bit: int,
        on: bool,
    ) -> int:
        if channels <= 1:
            await client.set_power(on)
            return POWER_ON if on else POWER_OFF
        current = await self._current_bitmask(did, client)
        new_value = with_gang_bit(current, gang_bit, on)
        await client.set_bitmask(new_value)
        return new_value

    async def _current_bitmask(self, did: str, client: CozyLifeClient) -> int:
        current = self.data.get(did) if self.data else None
        if current is not None:
            return current
        try:
            queried = await client.get_bitmask()
        except OSError:
            return POWER_OFF
        return queried if queried is not None else POWER_OFF

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
import logging
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .client import CozyLifeClient
from .const import (
    CONF_CHANNELS,
    CONF_DEVICES,
    DEFAULT_CHANNELS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    RECOVERY_COOLDOWN,
    TCP_PORT,
)
from .discovery import DiscoveredDevice, discover_devices
from .protocol import is_gang_on, with_gang_bit
from .provisioning import async_provision_loose_devices

_LOGGER = logging.getLogger(__name__)


@dataclass
class DeviceConfig:
    ip: str
    port: int
    did: str
    pid: str
    mac: str
    model: str
    channels: int

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> DeviceConfig:
        return cls(
            ip=str(raw["ip"]),
            port=int(raw.get("port") or TCP_PORT),
            did=str(raw.get("did") or raw["ip"]),
            pid=str(raw.get("pid") or ""),
            mac=str(raw.get("mac") or ""),
            model=str(raw.get("model") or ""),
            channels=max(1, min(8, int(raw.get(CONF_CHANNELS) or DEFAULT_CHANNELS))),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ip": self.ip,
            "port": self.port,
            "did": self.did,
            "pid": self.pid,
            "mac": self.mac,
            "model": self.model,
            CONF_CHANNELS: self.channels,
        }


def _norm_mac(mac: str) -> str:
    return mac.replace(":", "").replace("-", "").lower()


class CozyLifeCoordinator(DataUpdateCoordinator[dict[str, int | None]]):
    def __init__(
        self,
        hass: HomeAssistant,
        devices: list[DeviceConfig],
        entry: ConfigEntry | None = None,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.entry = entry
        self.devices: dict[str, DeviceConfig] = {d.did: d for d in devices}
        self.clients: dict[str, CozyLifeClient] = {
            d.did: CozyLifeClient(d.ip, d.port) for d in devices
        }
        self._last_good: dict[str, int] = {}
        self._last_recovery_ts: float = 0.0
        self._suppress_reload: bool = False
        self._new_device_callbacks: list[Callable[[list[DeviceConfig]], None]] = []

    def register_new_device_callback(
        self, callback_fn: Callable[[list[DeviceConfig]], None]
    ) -> None:
        self._new_device_callbacks.append(callback_fn)

    def consume_suppress_reload(self) -> bool:
        if not self._suppress_reload:
            return False
        self._suppress_reload = False
        return True

    def get_bitmask_cached(self, did: str) -> int | None:
        if self.data and did in self.data and self.data[did] is not None:
            return self.data[did]
        return self._last_good.get(did)

    def is_channel_on(self, did: str, gang_index: int) -> bool | None:
        bm = self.get_bitmask_cached(did)
        if bm is None:
            return None
        return is_gang_on(bm, gang_index)

    async def _poll_devices(
        self,
    ) -> tuple[dict[str, int | None], list[str]]:
        out: dict[str, int | None] = {}
        failed: list[str] = []
        for did, client in list(self.clients.items()):
            bm = await client.get_bitmask()
            if bm is not None:
                self._last_good[did] = bm
                out[did] = bm
                continue
            failed.append(did)
            out[did] = self._last_good.get(did)
        return out, failed

    def _match_discovered(
        self, did: str, discovered: list[DiscoveredDevice]
    ) -> DiscoveredDevice | None:
        cfg = self.devices.get(did)
        if not cfg:
            return None
        for d in discovered:
            if d.did == did:
                return d
        cfg_mac = _norm_mac(cfg.mac)
        if cfg_mac:
            for d in discovered:
                if _norm_mac(d.mac) == cfg_mac:
                    return d
        if did.startswith("manual_") and len(discovered) == 1:
            return discovered[0]
        return None

    async def _verify_and_update_device(
        self,
        did: str,
        disc: DiscoveredDevice,
        out: dict[str, int | None],
    ) -> bool:
        cfg = self.devices[did]
        client = self.clients[did]
        changed = (cfg.ip != disc.ip) or (cfg.port != disc.port)
        cfg.ip = disc.ip
        cfg.port = disc.port
        cfg.mac = disc.mac or cfg.mac
        cfg.pid = disc.pid or cfg.pid
        cfg.model = disc.model or cfg.model
        client.host = disc.ip
        client.port = disc.port
        bm = await client.get_bitmask()
        if bm is not None:
            self._last_good[did] = bm
            out[did] = bm
            _LOGGER.info(
                "CozyLife %s connected at DHCP IP %s:%s",
                did,
                disc.ip,
                disc.port,
            )
        return changed

    def _all_configured_keys(self) -> tuple[set[str], set[str]]:
        dids: set[str] = set(self.devices.keys())
        macs: set[str] = {
            _norm_mac(d.mac) for d in self.devices.values() if d.mac
        }
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            for raw in entry.data.get(CONF_DEVICES, []):
                if raw.get("did"):
                    dids.add(str(raw["did"]))
                if raw.get("mac"):
                    macs.add(_norm_mac(str(raw["mac"])))
        return dids, macs

    async def _adopt_new_devices(
        self,
        discovered: list[DiscoveredDevice],
        out: dict[str, int | None],
    ) -> list[DeviceConfig]:
        known_dids, known_macs = self._all_configured_keys()
        adopted: list[DeviceConfig] = []
        for disc in discovered:
            norm_m = _norm_mac(disc.mac)
            if disc.did in known_dids or (norm_m and norm_m in known_macs):
                continue
            client = CozyLifeClient(disc.ip, disc.port)
            bm = await client.get_bitmask()
            if bm is None:
                continue
            cfg = DeviceConfig(
                ip=disc.ip,
                port=disc.port,
                did=disc.did,
                pid=disc.pid,
                mac=disc.mac,
                model=disc.model,
                channels=DEFAULT_CHANNELS,
            )
            self.devices[cfg.did] = cfg
            self.clients[cfg.did] = client
            self._last_good[cfg.did] = bm
            out[cfg.did] = bm
            known_dids.add(cfg.did)
            if norm_m:
                known_macs.add(norm_m)
            adopted.append(cfg)
            _LOGGER.info(
                "Adopted CozyLife switch %s at DHCP IP %s",
                cfg.did,
                cfg.ip,
            )
        return adopted

    def _persist_entry_devices(self) -> None:
        if self.entry is None:
            return
        new_data = dict(self.entry.data)
        new_data[CONF_DEVICES] = [d.to_dict() for d in self.devices.values()]
        self._suppress_reload = True
        self.hass.config_entries.async_update_entry(self.entry, data=new_data)

    async def _maybe_provision_loose(self, failed_dids: list[str]) -> None:
        now = time.monotonic()
        if now - self._last_recovery_ts < RECOVERY_COOLDOWN:
            return
        self._last_recovery_ts = now
        offline_macs = {
            self.devices[d].mac
            for d in failed_dids
            if d in self.devices and self.devices[d].mac
        }
        provisioned = await async_provision_loose_devices(
            self.hass, self.entry, offline_macs
        )
        if provisioned:
            await asyncio.sleep(4.0)

    async def _recover_and_discover(
        self,
        failed_dids: list[str],
        out: dict[str, int | None],
    ) -> None:
        if failed_dids:
            await self._maybe_provision_loose(failed_dids)
        discovered = await discover_devices()
        if not discovered:
            return
        entry_changed = False
        for did in failed_dids:
            disc = self._match_discovered(did, discovered)
            if disc and await self._verify_and_update_device(did, disc, out):
                entry_changed = True
        adopted = await self._adopt_new_devices(discovered, out)
        if entry_changed or adopted:
            self._persist_entry_devices()
        if adopted:
            for cb in self._new_device_callbacks:
                cb(adopted)

    async def _async_update_data(self) -> dict[str, int | None]:
        out, failed = await self._poll_devices()
        now = time.monotonic()
        should_scan = bool(failed) or (
            now - self._last_recovery_ts >= RECOVERY_COOLDOWN
        )
        if should_scan:
            await self._recover_and_discover(failed, out)
            if not failed:
                self._last_recovery_ts = now
        return out

    async def async_set_channel(
        self, did: str, gang_index: int, turn_on: bool
    ) -> None:
        client = self.clients.get(did)
        if not client:
            return
        bm = await client.get_bitmask()
        if bm is None:
            bm = self.get_bitmask_cached(did)
        if bm is None:
            bm = 0
        new_bm = with_gang_bit(bm, gang_index, turn_on)
        ok = await client.set_bitmask(new_bm)
        if not ok:
            await self._recover_and_discover([did], {})
            ok = await client.set_bitmask(new_bm)
        if ok:
            self._last_good[did] = new_bm
            if self.data is not None:
                self.data[did] = new_bm
            self.async_set_updated_data(dict(self.data or {did: new_bm}))
        await self.async_request_refresh()

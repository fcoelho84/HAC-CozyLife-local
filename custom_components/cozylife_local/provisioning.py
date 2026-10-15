from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    AP_DEFAULT_IPS,
    AP_SSID_PREFIX,
    BLE_NOTIFY_UUID,
    BLE_SERVICE_UUID,
    BLE_WRITE_UUID,
    CONF_WIFI_PASSWORD,
    CONF_WIFI_SSID,
    DOMAIN,
    TCP_CONNECT_TIMEOUT,
    TCP_RECV_TIMEOUT,
    WIFI_TCP_PORT,
)
from .protocol import (
    ble_config_wifi_packets,
    config_wifi_frame,
    parse_ble_scan_record,
)

_LOGGER = logging.getLogger(__name__)


def _extract_entry_wifi(entry: ConfigEntry | None) -> tuple[str, str] | None:
    if entry is None:
        return None
    for source in (entry.options, entry.data):
        ssid = str(source.get(CONF_WIFI_SSID) or "").strip()
        password = str(source.get(CONF_WIFI_PASSWORD) or "")
        if ssid:
            return ssid, password
    return None


def get_cached_wifi_credentials(
    hass: HomeAssistant, entry: ConfigEntry | None = None
) -> tuple[str, str] | None:
    direct = _extract_entry_wifi(entry)
    if direct:
        return direct
    domain_data = hass.data.get(DOMAIN, {})
    cached = domain_data.get("wifi_credentials")
    if isinstance(cached, tuple) and cached and cached[0]:
        return cached
    for other in hass.config_entries.async_entries(DOMAIN):
        found = _extract_entry_wifi(other)
        if found:
            return found
    return None


def propagate_wifi_credentials(
    hass: HomeAssistant, ssid: str, password: str
) -> None:
    clean_ssid = ssid.strip()
    if not clean_ssid:
        return
    domain_data = hass.data.setdefault(DOMAIN, {})
    domain_data["wifi_credentials"] = (clean_ssid, password)
    for entry in hass.config_entries.async_entries(DOMAIN):
        current = _extract_entry_wifi(entry)
        if current == (clean_ssid, password):
            continue
        coord = domain_data.get(entry.entry_id)
        if coord is not None and hasattr(coord, "_suppress_reload"):
            coord._suppress_reload = True
        new_data = dict(entry.data)
        new_data[CONF_WIFI_SSID] = clean_ssid
        new_data[CONF_WIFI_PASSWORD] = password
        hass.config_entries.async_update_entry(entry, data=new_data)


def _run_nmcli(args: list[str], timeout: float = 10.0) -> str:
    if not shutil.which("nmcli"):
        return ""
    try:
        res = subprocess.run(
            ["nmcli", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return res.stdout if res.returncode == 0 else ""


def _read_profile_wifi(conn_name: str) -> tuple[str, str] | None:
    out = _run_nmcli(
        [
            "-s",
            "-g",
            "802-11-wireless.ssid,802-11-wireless-security.psk",
            "connection",
            "show",
            conn_name,
        ]
    )
    lines = [line.strip() for line in out.splitlines()]
    if not lines or not lines[0]:
        return None
    ssid = lines[0]
    if ssid.startswith(AP_SSID_PREFIX):
        return None
    password = lines[1] if len(lines) > 1 else ""
    return ssid, password


def _read_host_wifi_credentials() -> tuple[str, str] | None:
    out = _run_nmcli(["-t", "-f", "NAME,TYPE", "connection", "show"])
    for line in out.splitlines():
        parts = line.rsplit(":", 1)
        if len(parts) != 2 or parts[1] != "802-11-wireless":
            continue
        creds = _read_profile_wifi(parts[0])
        if creds:
            return creds
    return None


async def async_resolve_wifi_credentials(
    hass: HomeAssistant, entry: ConfigEntry | None = None
) -> tuple[str, str] | None:
    cached = get_cached_wifi_credentials(hass, entry)
    if cached:
        propagate_wifi_credentials(hass, cached[0], cached[1])
        return cached
    host_creds = await hass.async_add_executor_job(_read_host_wifi_credentials)
    if host_creds:
        propagate_wifi_credentials(hass, host_creds[0], host_creds[1])
    return host_creds


async def send_config_wifi_tcp(
    host: str,
    ssid: str,
    password: str,
    bssid: str = "",
    port: int = WIFI_TCP_PORT,
) -> bool:
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=TCP_CONNECT_TIMEOUT,
        )
    except (OSError, TimeoutError):
        return False
    try:
        payload = config_wifi_frame(ssid, password, bssid=bssid)
        writer.write(payload)
        await writer.drain()
        await asyncio.wait_for(reader.readline(), timeout=TCP_RECV_TIMEOUT)
        return True
    except (OSError, TimeoutError):
        return True
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass


def _normalize_mac(mac: str | None) -> str:
    if not mac:
        return ""
    return mac.replace(":", "").replace("-", "").lower()


def _extract_adv_protocol(adv: Any, offline_macs: set[str]) -> str | None:
    address = _normalize_mac(getattr(adv, "address", None))
    local_name = str(getattr(adv, "local_name", None) or "")
    uuids = [str(u).lower() for u in (getattr(adv, "service_uuids", None) or [])]
    if BLE_SERVICE_UUID in uuids or local_name.startswith("CozyLife"):
        return "00"
    if address and address in offline_macs:
        return "00"
    mfg_data = getattr(adv, "manufacturer_data", None) or {}
    for raw in mfg_data.values():
        parsed = parse_ble_scan_record(raw)
        if parsed and (not parsed["is_bind"] or _normalize_mac(parsed["mac"]) in offline_macs):
            return str(parsed.get("protocol") or "00")
    return None


async def _collect_ble_targets(
    hass: HomeAssistant, offline_macs: set[str]
) -> list[tuple[Any, str]]:
    targets: list[tuple[Any, str]] = []
    seen: set[str] = set()
    try:
        from homeassistant.components import bluetooth

        infos = bluetooth.async_discovered_service_info(hass, connectable=True)
        for info in infos:
            proto = _extract_adv_protocol(info, offline_macs)
            addr = _normalize_mac(info.address)
            if proto is not None and addr not in seen:
                seen.add(addr)
                targets.append((info.device, proto))
    except (ImportError, RuntimeError, ValueError):
        pass
    if targets:
        return targets
    try:
        from bleak import BleakScanner

        discovered = await BleakScanner.discover(timeout=3.0, return_adv=True)
        for device, adv in discovered.values():
            addr = _normalize_mac(getattr(device, "address", None))
            proto = _extract_adv_protocol(adv, offline_macs)
            if not proto and str(getattr(device, "name", "") or "").startswith("CozyLife"):
                proto = "00"
            if proto is not None and addr not in seen:
                seen.add(addr)
                targets.append((device, proto))
    except (ImportError, OSError, RuntimeError):
        pass
    return targets


async def _write_ble_wifi_config(
    ble_device: Any, ssid: str, password: str, protocol_version: str
) -> bool:
    try:
        from bleak import BleakClient
    except ImportError:
        return False
    packets = ble_config_wifi_packets(
        ssid, password, protocol_version=protocol_version
    )
    ack_event = asyncio.Event()

    def _on_notify(_sender: Any, _data: bytearray) -> None:
        ack_event.set()

    try:
        async with BleakClient(ble_device, timeout=10.0) as client:
            try:
                await client.start_notify(BLE_NOTIFY_UUID, _on_notify)
            except Exception:
                pass
            for pkt in packets:
                await client.write_gatt_char(BLE_WRITE_UUID, pkt, response=False)
                await asyncio.sleep(0.05)
            try:
                await asyncio.wait_for(ack_event.wait(), timeout=2.5)
            except TimeoutError:
                pass
            return True
    except Exception as err:
        _LOGGER.debug("BLE Wi-Fi config failed for %s: %s", ble_device, err)
        return False


async def _provision_ble_devices(
    hass: HomeAssistant, ssid: str, password: str, offline_macs: set[str]
) -> list[str]:
    targets = await _collect_ble_targets(hass, offline_macs)
    provisioned: list[str] = []
    for ble_device, proto in targets:
        ok = await _write_ble_wifi_config(ble_device, ssid, password, proto)
        if ok:
            addr = str(getattr(ble_device, "address", ble_device))
            _LOGGER.info("Provisioned CozyLife device via BLE: %s", addr)
            provisioned.append(addr)
    return provisioned


def _find_wifi_interface() -> tuple[str, str] | None:
    out = _run_nmcli(["-t", "-f", "DEVICE,TYPE,CONNECTION", "device"])
    for line in out.splitlines():
        parts = line.split(":")
        if len(parts) >= 3 and parts[1] == "wifi" and parts[0]:
            return parts[0], parts[2]
    return None


def _scan_cozylife_ssids(ifname: str) -> list[str]:
    out = _run_nmcli(
        ["-t", "-f", "SSID", "device", "wifi", "list", "ifname", ifname, "--rescan", "yes"],
        timeout=12.0,
    )
    ssids: list[str] = []
    for raw in out.splitlines():
        ssid = raw.strip()
        if ssid.startswith(AP_SSID_PREFIX) and ssid not in ssids:
            ssids.append(ssid)
    return ssids


def _get_interface_gateway(ifname: str) -> str:
    out = _run_nmcli(["-g", "IP4.GATEWAY", "device", "show", ifname])
    return out.strip()


def _connect_ap_ssid(ifname: str, ap_ssid: str) -> str | None:
    out = _run_nmcli(
        ["device", "wifi", "connect", ap_ssid, "ifname", ifname],
        timeout=15.0,
    )
    if not out:
        return None
    gw = _get_interface_gateway(ifname)
    return gw or AP_DEFAULT_IPS[0]


def _restore_wifi_connection(ifname: str, ap_ssid: str, prev_conn: str) -> None:
    _run_nmcli(["connection", "delete", ap_ssid], timeout=8.0)
    if prev_conn and prev_conn != "--":
        _run_nmcli(
            ["connection", "up", prev_conn, "ifname", ifname],
            timeout=15.0,
        )


async def _provision_single_ap(
    hass: HomeAssistant,
    ifname: str,
    prev_conn: str,
    ap_ssid: str,
    ssid: str,
    password: str,
) -> bool:
    gw_ip = await hass.async_add_executor_job(_connect_ap_ssid, ifname, ap_ssid)
    if not gw_ip:
        return False
    try:
        candidate_ips = [gw_ip, *[ip for ip in AP_DEFAULT_IPS if ip != gw_ip]]
        for ip in candidate_ips:
            if await send_config_wifi_tcp(ip, ssid, password):
                _LOGGER.info(
                    "Provisioned CozyLife AP %s via TCP (%s)", ap_ssid, ip
                )
                return True
        return False
    finally:
        await hass.async_add_executor_job(
            _restore_wifi_connection, ifname, ap_ssid, prev_conn
        )


async def _provision_ap_devices(
    hass: HomeAssistant, ssid: str, password: str
) -> list[str]:
    wifi_info = await hass.async_add_executor_job(_find_wifi_interface)
    if not wifi_info:
        return []
    ifname, prev_conn = wifi_info
    ap_ssids = await hass.async_add_executor_job(_scan_cozylife_ssids, ifname)
    provisioned: list[str] = []
    for ap_ssid in ap_ssids:
        if await _provision_single_ap(
            hass, ifname, prev_conn, ap_ssid, ssid, password
        ):
            provisioned.append(ap_ssid)
    return provisioned


async def async_provision_loose_devices(
    hass: HomeAssistant,
    entry: ConfigEntry | None = None,
    offline_macs: set[str] | None = None,
) -> list[str]:
    creds = await async_resolve_wifi_credentials(hass, entry)
    if not creds:
        return []
    ssid, password = creds
    mac_set = {_normalize_mac(m) for m in (offline_macs or set()) if m}
    ble_done = await _provision_ble_devices(hass, ssid, password, mac_set)
    ap_done = await _provision_ap_devices(hass, ssid, password)
    return [*ble_done, *ap_done]

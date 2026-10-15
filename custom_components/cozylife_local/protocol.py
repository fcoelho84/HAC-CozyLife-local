"""CozyLife LAN payload builders and parsers."""

from __future__ import annotations

import json
import time
from typing import Any

from .const import (
    ATTR_POWER,
    CMD_CONFIG_NET,
    CMD_DISCOVER,
    CMD_QUERY,
    CMD_SET,
    MESSAGE_TAIL,
    POWER_OFF,
    POWER_ON,
)


def _sn() -> str:
    return str(int(time.time() * 1000))


def frame(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, separators=(",", ":")) + MESSAGE_TAIL).encode("utf-8")


def discover_probe() -> bytes:
    return frame({"cmd": CMD_DISCOVER, "pv": 0, "sn": _sn(), "msg": {}})


def attr_msg(values: dict[str, int]) -> dict[str, Any]:
    attrs = sorted(int(k) for k in values)
    return {"data": {str(k): int(v) for k, v in values.items()}, "attr": attrs}


def set_attrs_frame(values: dict[str, int], sn: str | None = None) -> bytes:
    return frame(
        {
            "msg": attr_msg(values),
            "pv": 0,
            "cmd": CMD_SET,
            "sn": sn or _sn(),
        }
    )


def power_msg(on: bool) -> dict[str, Any]:
    return attr_msg({ATTR_POWER: POWER_ON if on else POWER_OFF})


def set_power_frame(on: bool, sn: str | None = None) -> bytes:
    return set_attrs_frame({ATTR_POWER: POWER_ON if on else POWER_OFF}, sn=sn)


def set_bitmask_frame(value: int, sn: str | None = None) -> bytes:
    return set_attrs_frame({ATTR_POWER: int(value) & 0xFF}, sn=sn)


def query_status_frame(sn: str | None = None) -> bytes:
    return frame(
        {
            "msg": {"attr": [1, 2]},
            "pv": 0,
            "cmd": CMD_QUERY,
            "sn": sn or _sn(),
        }
    )


def split_frames(buffer: str) -> list[str]:
    parts = buffer.split(MESSAGE_TAIL)
    return [p.strip() for p in parts if p.strip()]


def parse_json_object(text: str) -> dict[str, Any] | None:
    try:
        data = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if isinstance(data, dict):
        return data
    return None


def parse_discovery_message(text: str) -> dict[str, Any] | None:
    outer = parse_json_object(text)
    if not outer:
        return None
    msg = outer.get("msg")
    if not isinstance(msg, dict):
        return None
    did = msg.get("did")
    ip = msg.get("ip")
    if not did or not ip:
        return None
    return {
        "did": str(did),
        "ip": str(ip),
        "mac": str(msg.get("mac") or ""),
        "pid": str(msg.get("pid") or ""),
        "dtp": str(msg.get("dtp") or ""),
        "hv": str(msg.get("hv") or ""),
        "sv": str(msg.get("sv") or ""),
        "rssi": msg.get("rssi"),
        "name": str(msg.get("name") or did),
    }


def _dig_data(node: Any) -> dict[str, int] | None:
    if not isinstance(node, dict):
        return None
    data = node.get("data")
    if isinstance(data, dict):
        out: dict[str, int] = {}
        for key, value in data.items():
            try:
                out[str(key)] = int(value)
            except (TypeError, ValueError):
                continue
        if out:
            return out
    msg = node.get("msg")
    if isinstance(msg, dict):
        return _dig_data(msg)
    if isinstance(msg, str):
        nested = parse_json_object(msg)
        if nested:
            return _dig_data(nested)
    return None


def parse_state(text: str) -> dict[str, int] | None:
    outer = parse_json_object(text)
    if not outer:
        return None
    return _dig_data(outer)


def parse_power_value(text: str) -> int | None:
    state = parse_state(text)
    if not state or ATTR_POWER not in state:
        return None
    return state[ATTR_POWER]


def parse_power_state(text: str) -> bool | None:
    value = parse_power_value(text)
    if value is None:
        return None
    return value != POWER_OFF


def gang_bit_on(bitmask: int, gang_bit: int) -> bool:
    return (int(bitmask) & (1 << gang_bit)) != 0


def with_gang_bit(bitmask: int, gang_bit: int, on: bool) -> int:
    bit = 1 << gang_bit
    current = int(bitmask) & 0xFF
    if on:
        return current | bit
    return current & ~bit


def config_wifi_frame(
    ssid: str, password: str, bssid: str = "", sn: str | None = None
) -> bytes:
    msg = {
        "user_token": "",
        "lng": "0",
        "lat": "0",
        "ssid": ssid,
        "bssid": bssid,
        "passwd": password,
        "domain": "",
    }
    return frame({"cmd": CMD_CONFIG_NET, "pv": 0, "sn": sn or _sn(), "msg": msg})


def crc8_byte(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc = (crc ^ byte) & 0xFF
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if (crc & 0x80) else (crc << 1) & 0xFF
    return crc


def crc8_hex(hex_str: str) -> str:
    return f"{crc8_byte(bytes.fromhex(hex_str)):02x}"


def _pad_hex(hex_str: str, width: int) -> str:
    if len(hex_str) >= width:
        return hex_str[:width]
    return hex_str.ljust(width, "0")


def _encode_padded_hex(text: str, width: int) -> str:
    return _pad_hex(text.encode("utf-8").hex(), width)


def ble_config_wifi_cmd_hex(
    ssid: str,
    password: str,
    bssid: str = "",
    device_key: str = "",
    open_id: str = "",
    domain: str | None = "",
) -> str:
    len_hex = "a3" if domain is not None else "99"
    ssid_hex = "02" * 32 if not ssid else _encode_padded_hex(ssid, 64)
    passwd_hex = _encode_padded_hex(password, 128)
    clean_bssid = bssid.replace(":", "").replace("-", "").lower()
    bssid_hex = _pad_hex(clean_bssid or "000000000000", 12)
    key_hex = _encode_padded_hex(device_key, 20)
    oid_hex = _encode_padded_hex(open_id, 66)
    domain_hex = _encode_padded_hex(domain, 20) if domain is not None else ""
    return (
        "01"
        + len_hex
        + ssid_hex
        + passwd_hex
        + bssid_hex
        + key_hex
        + oid_hex
        + "00000000"
        + "00000000"
        + domain_hex
    )


def ble_fragment_cmd(
    cmd_hex: str, protocol_version: str = "00", data_len: int = 34
) -> list[bytes]:
    version = _pad_hex(protocol_version, 2)
    total = (len(cmd_hex) + data_len - 1) // data_len
    packets: list[bytes] = []
    for idx in range(total):
        chunk = cmd_hex[idx * data_len : (idx + 1) * data_len]
        sn_val = (idx | 0x80) if idx == total - 1 else idx
        pkg_sn = f"{sn_val:02x}"
        padded_data = _pad_hex(chunk, 34)
        body_hex = version + pkg_sn + padded_data
        pkt_hex = body_hex + crc8_hex(body_hex)
        packets.append(bytes.fromhex(pkt_hex))
    return packets


def ble_config_wifi_packets(
    ssid: str,
    password: str,
    bssid: str = "",
    protocol_version: str = "00",
) -> list[bytes]:
    cmd_hex = ble_config_wifi_cmd_hex(ssid, password, bssid=bssid)
    return ble_fragment_cmd(cmd_hex, protocol_version=protocol_version)


def ble_query_status_packets(protocol_version: str = "00") -> list[bytes]:
    return ble_fragment_cmd("020101", protocol_version=protocol_version)


def ble_set_bitmask_packets(
    bitmask: int, protocol_version: str = "00"
) -> list[bytes]:
    cmd_hex = f"030201{int(bitmask) & 0xFF:02x}"
    return ble_fragment_cmd(cmd_hex, protocol_version=protocol_version)


def ble_set_power_packets(
    on: bool, protocol_version: str = "00"
) -> list[bytes]:
    return ble_set_bitmask_packets(
        POWER_ON if on else POWER_OFF, protocol_version=protocol_version
    )


def parse_ble_notify_bitmask(raw: bytes | bytearray) -> int | None:
    hex_str = bytes(raw).hex().lower()
    if len(hex_str) < 12:
        return None
    payload = hex_str[4:-2]
    cmd = payload[:2]
    if cmd not in ("02", "03", "0a") or len(payload) < 8:
        return None
    if payload[4:6] != "01":
        return None
    try:
        return int(payload[6:8], 16)
    except ValueError:
        return None


def _reverse_mac_hex(mac_hex: str) -> str:
    pairs = [mac_hex[i : i + 2] for i in range(0, len(mac_hex), 2)]
    return ":".join(reversed(pairs)).lower()


def parse_ble_scan_record(raw_adv: bytes | str) -> dict[str, Any] | None:
    hex_str = raw_adv.hex() if isinstance(raw_adv, bytes) else str(raw_adv).strip()
    if len(hex_str) <= 85:
        return None
    broadcast_pkg = hex_str[:50]
    scan_pkg = hex_str[50:]
    if scan_pkg.startswith("000000000000"):
        scan_pkg = scan_pkg[12:]
    if len(scan_pkg) < 34:
        return None
    raw_did = scan_pkg[14:34].lower()
    did = raw_did.lstrip("0") or raw_did
    pid_bytes = bytes.fromhex(broadcast_pkg[26:38])
    pid = pid_bytes.decode("utf-8", errors="ignore").strip("\x00")
    return {
        "is_bind": scan_pkg[12:14] == "01",
        "did": did,
        "mac": _reverse_mac_hex(broadcast_pkg[38:50]),
        "pid": pid,
        "protocol": broadcast_pkg[22:24],
    }

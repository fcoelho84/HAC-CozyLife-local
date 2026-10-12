"""CozyLife LAN payload builders and parsers."""

from __future__ import annotations

import json
import time
from typing import Any

from .const import (
    ATTR_POWER,
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

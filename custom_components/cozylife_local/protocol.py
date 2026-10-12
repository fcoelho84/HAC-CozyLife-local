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


def power_msg(on: bool) -> dict[str, Any]:
    return {"data": {ATTR_POWER: POWER_ON if on else POWER_OFF}, "attr": [int(ATTR_POWER)]}


def set_power_frame(on: bool, sn: str | None = None) -> bytes:
    return frame(
        {
            "msg": power_msg(on),
            "pv": 0,
            "cmd": CMD_SET,
            "sn": sn or _sn(),
        }
    )


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


def _dig_power(node: Any) -> int | None:
    if not isinstance(node, dict):
        return None
    data = node.get("data")
    if isinstance(data, dict) and ATTR_POWER in data:
        try:
            return int(data[ATTR_POWER])
        except (TypeError, ValueError):
            return None
    if ATTR_POWER in node:
        try:
            return int(node[ATTR_POWER])
        except (TypeError, ValueError):
            return None
    msg = node.get("msg")
    if isinstance(msg, dict):
        return _dig_power(msg)
    if isinstance(msg, str):
        nested = parse_json_object(msg)
        if nested:
            return _dig_power(nested)
    return None


def parse_power_state(text: str) -> bool | None:
    outer = parse_json_object(text)
    if not outer:
        return None
    value = _dig_power(outer)
    if value is None:
        return None
    return value != POWER_OFF

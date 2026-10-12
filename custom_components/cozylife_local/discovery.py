"""UDP discovery for CozyLife Wi‑Fi devices."""

from __future__ import annotations

import asyncio
import logging
import socket
import time
from typing import Any

from .const import DISCOVERY_TIMEOUT, UDP_BROADCAST_ADDR, UDP_BROADCAST_PORT
from .protocol import discover_probe, parse_discovery_message, split_frames

_LOGGER = logging.getLogger(__name__)


async def discover_devices(timeout: float = DISCOVERY_TIMEOUT) -> list[dict[str, Any]]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: _discover_sync(timeout))


def _discover_sync(timeout: float) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(0.3)
        sock.sendto(discover_probe(), (UDP_BROADCAST_ADDR, UDP_BROADCAST_PORT))
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                data, _addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError as err:
                _LOGGER.debug("UDP receive error: %s", err)
                break
            text = data.decode("utf-8", errors="ignore")
            for chunk in split_frames(text):
                device = parse_discovery_message(chunk)
                if not device:
                    continue
                found[device["did"]] = device
    finally:
        sock.close()
    return list(found.values())

"""UDP discovery for CozyLife Wi‑Fi devices."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import logging
import pathlib
import socket
import time
from typing import Any

from .const import (
    DISCOVERY_TIMEOUT,
    UDP_BROADCAST_ADDR,
    UDP_BROADCAST_PORT,
    WIFI_TCP_PORT,
)
from .protocol import discover_probe, parse_discovery_message, split_frames

_LOGGER = logging.getLogger(__name__)
_UDP_BURST_COUNT = 4
_UDP_BURST_INTERVAL = 0.25


async def discover_devices(timeout: float = DISCOVERY_TIMEOUT) -> list[dict[str, Any]]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: _discover_sync(timeout))


def _local_broadcast_addresses() -> list[str]:
    targets = [UDP_BROADCAST_ADDR]
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        local_ip = sock.getsockname()[0]
    except OSError:
        local_ip = ""
    finally:
        sock.close()
    parts = local_ip.split(".")
    if len(parts) == 4:
        subnet_bcast = f"{parts[0]}.{parts[1]}.{parts[2]}.255"
        if subnet_bcast not in targets:
            targets.append(subnet_bcast)
    return targets


def _send_probe_burst(sock: socket.socket, targets: list[str]) -> None:
    payload = discover_probe()
    for addr in targets:
        try:
            sock.sendto(payload, (addr, UDP_BROADCAST_PORT))
        except OSError:
            continue


def _drain_udp_responses(
    sock: socket.socket, found: dict[str, dict[str, Any]]
) -> bool:
    try:
        data, _addr = sock.recvfrom(4096)
    except socket.timeout:
        return True
    except OSError as err:
        _LOGGER.debug("UDP receive error: %s", err)
        return False
    text = data.decode("utf-8", errors="ignore")
    for chunk in split_frames(text):
        device = parse_discovery_message(chunk)
        if device:
            found[device["did"]] = device
    return True


def _read_arp_neighbors() -> list[str]:
    arp_path = pathlib.Path("/proc/net/arp")
    if not arp_path.exists():
        return []
    try:
        lines = arp_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return []
    ips: list[str] = []
    for line in lines[1:]:
        cols = line.split()
        if len(cols) >= 4 and cols[2] != "0x0" and cols[0] not in ips:
            ips.append(cols[0])
    return ips


def _probe_tcp_discover(ip: str) -> dict[str, Any] | None:
    try:
        sock = socket.create_connection((ip, WIFI_TCP_PORT), timeout=0.6)
    except OSError:
        return None
    try:
        sock.settimeout(0.6)
        sock.sendall(discover_probe())
        data = sock.recv(4096)
    except OSError:
        return None
    finally:
        sock.close()
    text = data.decode("utf-8", errors="ignore")
    for chunk in split_frames(text):
        device = parse_discovery_message(chunk)
        if device:
            return device
    return None


def _supplement_from_arp(found: dict[str, dict[str, Any]]) -> None:
    known_ips = {d.get("ip") for d in found.values()}
    candidates = [ip for ip in _read_arp_neighbors() if ip not in known_ips]
    if not candidates:
        return
    with ThreadPoolExecutor(max_workers=min(8, len(candidates))) as pool:
        for device in pool.map(_probe_tcp_discover, candidates):
            if device and device["did"] not in found:
                found[device["did"]] = device


def _discover_sync(timeout: float) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    targets = _local_broadcast_addresses()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(0.15)
        start = time.monotonic()
        end = start + timeout
        bursts_sent = 0
        while time.monotonic() < end:
            now = time.monotonic()
            if (
                bursts_sent < _UDP_BURST_COUNT
                and now - start >= bursts_sent * _UDP_BURST_INTERVAL
            ):
                _send_probe_burst(sock, targets)
                bursts_sent += 1
            if not _drain_udp_responses(sock, found):
                break
    finally:
        sock.close()
    _supplement_from_arp(found)
    return list(found.values())

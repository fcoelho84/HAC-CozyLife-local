"""TCP client for CozyLife local control."""

from __future__ import annotations

import asyncio
import logging
import socket
from typing import Any

from .const import (
    POWER_OFF,
    TCP_CONNECT_TIMEOUT,
    TCP_RECV_TIMEOUT,
    TCP_RETRIES,
    TCP_RETRY_DELAY,
    WIFI_TCP_PORT,
)
from .protocol import (
    parse_power_value,
    query_status_frame,
    set_bitmask_frame,
    set_power_frame,
    split_frames,
)

_LOGGER = logging.getLogger(__name__)


class CozyLifeClient:
    def __init__(self, host: str, port: int = WIFI_TCP_PORT) -> None:
        self.host = host
        self.port = port
        self._lock = asyncio.Lock()

    async def set_power(self, on: bool) -> None:
        await self._exchange(set_power_frame(on))

    async def set_bitmask(self, value: int) -> None:
        await self._exchange(set_bitmask_frame(value))

    async def get_bitmask(self) -> int | None:
        text = await self._exchange(query_status_frame(), require_response=True)
        for chunk in split_frames(text):
            value = parse_power_value(chunk)
            if value is not None:
                return value
        return parse_power_value(text)

    async def get_power(self) -> bool | None:
        value = await self.get_bitmask()
        if value is None:
            return None
        return value != POWER_OFF

    async def _exchange(self, payload: bytes, require_response: bool = False) -> str:
        async with self._lock:
            return await self._exchange_with_retry(payload, require_response)

    async def _exchange_with_retry(self, payload: bytes, require_response: bool) -> str:
        loop = asyncio.get_running_loop()
        last_err: OSError | None = None
        for attempt in range(TCP_RETRIES):
            try:
                text = await loop.run_in_executor(
                    None, _exchange_sync, self.host, self.port, payload
                )
                if not require_response or text:
                    return text
                last_err = TimeoutError(f"Empty response from {self.host}:{self.port}")
            except OSError as err:
                last_err = err
            if attempt + 1 < TCP_RETRIES:
                await asyncio.sleep(TCP_RETRY_DELAY)
        if last_err is not None:
            raise last_err
        return ""


def _exchange_sync(host: str, port: int, payload: bytes) -> str:
    sock = socket.create_connection((host, port), timeout=TCP_CONNECT_TIMEOUT)
    try:
        sock.settimeout(TCP_RECV_TIMEOUT)
        sock.sendall(payload)
        chunks: list[bytes] = []
        while True:
            try:
                data = sock.recv(4096)
            except socket.timeout:
                break
            if not data:
                break
            chunks.append(data)
            if b"\r\n" in b"".join(chunks):
                break
        return b"".join(chunks).decode("utf-8", errors="ignore")
    finally:
        sock.close()


def device_from_config(raw: dict[str, Any]) -> CozyLifeClient:
    return CozyLifeClient(host=raw["ip"], port=int(raw.get("port") or WIFI_TCP_PORT))


def channels_from_config(raw: dict[str, Any]) -> int:
    try:
        channels = int(raw.get("channels") or 1)
    except (TypeError, ValueError):
        return 1
    return max(1, min(channels, 8))

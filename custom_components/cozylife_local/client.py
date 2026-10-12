"""TCP client for CozyLife local control."""

from __future__ import annotations

import asyncio
import logging
import socket
from typing import Any

from .const import WIFI_TCP_PORT
from .protocol import (
    parse_power_state,
    query_status_frame,
    set_power_frame,
    split_frames,
)

_LOGGER = logging.getLogger(__name__)
_RECV_TIMEOUT = 2.0
_CONNECT_TIMEOUT = 2.0


class CozyLifeClient:
    def __init__(self, host: str, port: int = WIFI_TCP_PORT) -> None:
        self.host = host
        self.port = port

    async def set_power(self, on: bool) -> None:
        await self._exchange(set_power_frame(on))

    async def get_power(self) -> bool | None:
        text = await self._exchange(query_status_frame())
        if not text:
            return None
        for chunk in split_frames(text):
            state = parse_power_state(chunk)
            if state is not None:
                return state
        return parse_power_state(text)

    async def _exchange(self, payload: bytes) -> str:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: _exchange_sync(self.host, self.port, payload))


def _exchange_sync(host: str, port: int, payload: bytes) -> str:
    sock = socket.create_connection((host, port), timeout=_CONNECT_TIMEOUT)
    try:
        sock.settimeout(_RECV_TIMEOUT)
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
            joined = b"".join(chunks)
            if b"\r\n" in joined:
                break
        return b"".join(chunks).decode("utf-8", errors="ignore")
    finally:
        sock.close()


def device_from_config(raw: dict[str, Any]) -> CozyLifeClient:
    return CozyLifeClient(host=raw["ip"], port=int(raw.get("port") or WIFI_TCP_PORT))

"""TCP and BLE client for CozyLife local control."""

from __future__ import annotations

import asyncio
import logging
import socket
import threading
from typing import Any

from .const import (
    BLE_NOTIFY_UUID,
    BLE_WRITE_UUID,
    POWER_OFF,
     POWER_ON,
    TCP_CONNECT_TIMEOUT,
    TCP_RECV_TIMEOUT,
    TCP_RETRIES,
    TCP_RETRY_DELAY,
    WIFI_TCP_PORT,
)
from .protocol import (
    ble_query_status_packets,
    ble_set_bitmask_packets,
    parse_ble_notify_bitmask,
    parse_ble_scan_record,
    parse_power_value,
    query_status_frame,
    set_bitmask_frame,
    set_power_frame,
    split_frames,
)

_LOGGER = logging.getLogger(__name__)


def _norm_mac(mac: Any) -> str:
    return str(mac or "").replace(":", "").replace("-", "").lower()


def _matches_ble_identity(
    address: str, adv: Any, target_mac: str, target_did: str
) -> bool:
    norm_addr = _norm_mac(address)
    if target_mac and norm_addr == target_mac:
        return True
    mfg_data = getattr(adv, "manufacturer_data", None) or {}
    for raw in mfg_data.values():
        parsed = parse_ble_scan_record(raw)
        if not parsed:
            continue
        if target_did and parsed.get("did") == target_did:
            return True
        if target_mac and _norm_mac(parsed.get("mac")) == target_mac:
            return True
    return False


class CozyLifeClient:
    def __init__(
        self,
        host: str,
        port: int = WIFI_TCP_PORT,
        mac: str = "",
        did: str = "",
        hass: Any = None,
    ) -> None:
        self._host = host
        self._port = port
        self.mac = mac
        self.did = did
        self.hass = hass
        self._lock = asyncio.Lock()
        self._sock_lock = threading.Lock()
        self._sock: socket.socket | None = None

    @property
    def host(self) -> str:
        return self._host

    @host.setter
    def host(self, value: str) -> None:
        if value != self._host:
            self._host = value
            self.close()

    @property
    def port(self) -> int:
        return self._port

    @port.setter
    def port(self, value: int) -> None:
        if value != self._port:
            self._port = value
            self.close()

    def close(self) -> None:
        with self._sock_lock:
            self._close_unlocked()

    def _close_unlocked(self) -> None:
        if self._sock is None:
            return
        try:
            self._sock.close()
        except OSError:
            pass
        self._sock = None

    def _ensure_socket_unlocked(self) -> socket.socket:
        if self._sock is not None:
            return self._sock
        sock = socket.create_connection(
            (self._host, self._port), timeout=TCP_CONNECT_TIMEOUT
        )
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        sock.settimeout(TCP_RECV_TIMEOUT)
        self._sock = sock
        return sock

    def _send_and_recv_unlocked(self, payload: bytes) -> str:
        sock = self._ensure_socket_unlocked()
        sock.settimeout(TCP_RECV_TIMEOUT)
        sock.sendall(payload)
        chunks: list[bytes] = []
        while True:
            try:
                data = sock.recv(4096)
            except socket.timeout:
                break
            if not data:
                self._close_unlocked()
                break
            chunks.append(data)
            if b"\r\n" in b"".join(chunks):
                break
        return b"".join(chunks).decode("utf-8", errors="ignore")

    def _exchange_persistent_sync(self, payload: bytes) -> str:
        with self._sock_lock:
            for attempt in range(2):
                try:
                    return self._send_and_recv_unlocked(payload)
                except OSError:
                    self._close_unlocked()
                    if attempt == 1:
                        raise
        return ""

    async def set_power(self, on: bool) -> None:
        bitmask = POWER_ON if on else POWER_OFF
        try:
            await self._exchange(set_power_frame(on))
        except OSError:
            if not await self._ble_set_bitmask(bitmask):
                raise

    async def set_bitmask(self, value: int) -> None:
        try:
            await self._exchange(set_bitmask_frame(value))
        except OSError:
            if not await self._ble_set_bitmask(value):
                raise

    async def get_bitmask(self) -> int | None:
        try:
            text = await self._exchange(
                query_status_frame(), require_response=True
            )
            for chunk in split_frames(text):
                value = parse_power_value(chunk)
                if value is not None:
                    return value
            return parse_power_value(text)
        except OSError:
            ble_val = await self._ble_query_bitmask()
            if ble_val is not None:
                return ble_val
            raise

    async def get_power(self) -> bool | None:
        value = await self.get_bitmask()
        if value is None:
            return None
        return value != POWER_OFF

    async def _exchange(
        self, payload: bytes, require_response: bool = False
    ) -> str:
        async with self._lock:
            return await self._exchange_with_retry(payload, require_response)

    async def _exchange_with_retry(
        self, payload: bytes, require_response: bool
    ) -> str:
        loop = asyncio.get_running_loop()
        last_err: OSError | None = None
        for attempt in range(TCP_RETRIES):
            try:
                text = await loop.run_in_executor(
                    None, self._exchange_persistent_sync, payload
                )
                if not require_response or text:
                    return text
                self.close()
                last_err = TimeoutError(
                    f"Empty response from {self._host}:{self._port}"
                )
            except OSError as err:
                last_err = err
            if attempt + 1 < TCP_RETRIES:
                await asyncio.sleep(TCP_RETRY_DELAY)
        if last_err is not None:
            raise last_err
        return ""

    async def _find_ble_device(self) -> Any:
        target_mac = _norm_mac(self.mac)
        target_did = str(self.did or "").strip().lower()
        if not target_mac and not target_did:
            return None
        if self.hass is not None:
            try:
                from homeassistant.components import bluetooth

                infos = bluetooth.async_discovered_service_info(
                    self.hass, connectable=True
                )
                for info in infos:
                    if _matches_ble_identity(
                        info.address, info, target_mac, target_did
                    ):
                        return info.device
            except (ImportError, RuntimeError, ValueError):
                pass
        try:
            from bleak import BleakScanner

            discovered = await BleakScanner.discover(
                timeout=2.5, return_adv=True
            )
            for device, adv in discovered.values():
                addr = str(getattr(device, "address", "") or "")
                if _matches_ble_identity(addr, adv, target_mac, target_did):
                    return device
        except (ImportError, OSError, RuntimeError):
            pass
        return None

    async def _ble_set_bitmask(self, bitmask: int) -> bool:
        ble_dev = await self._find_ble_device()
        if ble_dev is None:
            return False
        try:
            from bleak import BleakClient

            packets = ble_set_bitmask_packets(bitmask)
            async with BleakClient(ble_dev, timeout=8.0) as client:
                for pkt in packets:
                    await client.write_gatt_char(
                        BLE_WRITE_UUID, pkt, response=False
                    )
                    await asyncio.sleep(0.05)
            return True
        except Exception as err:
            _LOGGER.debug("BLE fallback set failed for %s: %s", self.did, err)
            return False

    async def _ble_query_bitmask(self) -> int | None:
        ble_dev = await self._find_ble_device()
        if ble_dev is None:
            return None
        result: list[int] = []
        done = asyncio.Event()

        def _on_notify(_sender: Any, data: bytearray) -> None:
            parsed = parse_ble_notify_bitmask(data)
            if parsed is not None:
                result.append(parsed)
                done.set()

        try:
            from bleak import BleakClient

            packets = ble_query_status_packets()
            async with BleakClient(ble_dev, timeout=8.0) as client:
                await client.start_notify(BLE_NOTIFY_UUID, _on_notify)
                for pkt in packets:
                    await client.write_gatt_char(
                        BLE_WRITE_UUID, pkt, response=False
                    )
                    await asyncio.sleep(0.05)
                try:
                    await asyncio.wait_for(done.wait(), timeout=2.5)
                except TimeoutError:
                    pass
            return result[0] if result else None
        except Exception as err:
            _LOGGER.debug("BLE fallback query failed for %s: %s", self.did, err)
            return None


CozyLifeDeviceClient = CozyLifeClient


def device_from_config(
    raw: dict[str, Any], hass: Any = None
) -> CozyLifeClient:
    return CozyLifeClient(
        host=raw["ip"],
        port=int(raw.get("port") or WIFI_TCP_PORT),
        mac=str(raw.get("mac") or ""),
        did=str(raw.get("did") or ""),
        hass=hass,
    )


def channels_from_config(raw: dict[str, Any]) -> int:
    try:
        channels = int(raw.get("channels") or 1)
    except (TypeError, ValueError):
        return 1
    return max(1, min(channels, 8))

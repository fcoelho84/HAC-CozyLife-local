"""Data update coordinator."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .client import CozyLifeClient, channels_from_config, device_from_config
from .const import CONF_DID, DEFAULT_SCAN_INTERVAL, DOMAIN, POWER_OFF, POWER_ON
from .protocol import gang_bit_on, with_gang_bit

_LOGGER = logging.getLogger(__name__)


class CozyLifeCoordinator(DataUpdateCoordinator[dict[str, int | None]]):
    def __init__(self, hass: HomeAssistant, devices: list[dict[str, Any]]) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.devices = {d[CONF_DID]: d for d in devices}
        self.clients: dict[str, CozyLifeClient] = {
            did: device_from_config(cfg) for did, cfg in self.devices.items()
        }

    def channels_for(self, did: str) -> int:
        return channels_from_config(self.devices[did])

    async def _async_update_data(self) -> dict[str, int | None]:
        result: dict[str, int | None] = {}
        for did, client in self.clients.items():
            previous = self.data.get(did) if self.data else None
            try:
                bitmask = await client.get_bitmask()
                result[did] = bitmask if bitmask is not None else previous
            except OSError as err:
                _LOGGER.debug("Status failed for %s: %s", did, err)
                result[did] = previous
        return result

    def is_gang_on(self, did: str, gang_bit: int) -> bool | None:
        if not self.data:
            return None
        value = self.data.get(did)
        if value is None:
            return None
        channels = self.channels_for(did)
        if channels <= 1:
            return value != POWER_OFF
        return gang_bit_on(value, gang_bit)

    async def async_set_gang(self, did: str, gang_bit: int, on: bool) -> None:
        client = self.clients[did]
        channels = self.channels_for(did)
        try:
            new_value = await self._send_gang_state(did, client, channels, gang_bit, on)
        except OSError as err:
            _LOGGER.warning("Command failed for %s after reconnect retries: %s", did, err)
            return
        if self.data is None:
            self.data = {}
        self.data[did] = new_value
        self.async_set_updated_data(dict(self.data))

    async def _send_gang_state(
        self, did: str, client: CozyLifeClient, channels: int, gang_bit: int, on: bool
    ) -> int:
        if channels <= 1:
            await client.set_power(on)
            return POWER_ON if on else POWER_OFF
        current = await self._current_bitmask(did, client)
        new_value = with_gang_bit(current, gang_bit, on)
        await client.set_bitmask(new_value)
        return new_value

    async def _current_bitmask(self, did: str, client: CozyLifeClient) -> int:
        current = self.data.get(did) if self.data else None
        if current is not None:
            return current
        try:
            queried = await client.get_bitmask()
        except OSError:
            return POWER_OFF
        return queried if queried is not None else POWER_OFF

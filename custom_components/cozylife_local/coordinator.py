"""Data update coordinator."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

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
        errors = 0
        for did, client in self.clients.items():
            try:
                result[did] = await client.get_bitmask()
            except OSError as err:
                errors += 1
                _LOGGER.debug("Status failed for %s: %s", did, err)
                result[did] = self.data.get(did) if self.data else None
        if errors and errors == len(self.clients):
            raise UpdateFailed("All CozyLife devices unreachable")
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
        if channels <= 1:
            await client.set_power(on)
            new_value = POWER_ON if on else POWER_OFF
        else:
            current = self.data.get(did) if self.data else None
            if current is None:
                try:
                    current = await client.get_bitmask()
                except OSError:
                    current = POWER_OFF
            if current is None:
                current = POWER_OFF
            new_value = with_gang_bit(current, gang_bit, on)
            await client.set_bitmask(new_value)
        if self.data is None:
            self.data = {}
        self.data[did] = new_value
        self.async_set_updated_data(dict(self.data))

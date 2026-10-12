"""Data update coordinator."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import CozyLifeClient, device_from_config
from .const import CONF_DEVICES, CONF_DID, DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


class CozyLifeCoordinator(DataUpdateCoordinator[dict[str, bool | None]]):
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

    async def _async_update_data(self) -> dict[str, bool | None]:
        result: dict[str, bool | None] = {}
        errors = 0
        for did, client in self.clients.items():
            try:
                result[did] = await client.get_power()
            except OSError as err:
                errors += 1
                _LOGGER.debug("Status failed for %s: %s", did, err)
                result[did] = self.data.get(did) if self.data else None
        if errors and errors == len(self.clients):
            raise UpdateFailed("All CozyLife devices unreachable")
        return result

    async def async_set_power(self, did: str, on: bool) -> None:
        client = self.clients[did]
        await client.set_power(on)
        if self.data is None:
            self.data = {}
        self.data[did] = on
        self.async_set_updated_data(dict(self.data))

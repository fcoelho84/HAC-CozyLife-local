"""Switch platform for CozyLife Local."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_DID, CONF_IP, CONF_MAC, CONF_NAME, DOMAIN
from .coordinator import CozyLifeCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: CozyLifeCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        CozyLifeSwitch(coordinator, did, cfg)
        for did, cfg in coordinator.devices.items()
    )


class CozyLifeSwitch(CoordinatorEntity[CozyLifeCoordinator], SwitchEntity):
    _attr_has_entity_name = True
    _attr_name = None

    def __init__(
        self,
        coordinator: CozyLifeCoordinator,
        did: str,
        cfg: dict[str, Any],
    ) -> None:
        super().__init__(coordinator)
        self._did = did
        self._cfg = cfg
        self._attr_unique_id = did
        connections = set()
        if cfg.get(CONF_MAC):
            connections.add(("mac", str(cfg[CONF_MAC]).lower()))
        self._attr_device_info = {
            "identifiers": {(DOMAIN, did)},
            "name": cfg.get(CONF_NAME) or did,
            "manufacturer": "CozyLife",
            "model": cfg.get("pid") or cfg.get("dtp") or "Switch",
            "connections": connections,
        }

    @property
    def is_on(self) -> bool | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get(self._did)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "did": self._did,
            "ip": self._cfg.get(CONF_IP),
            "mac": self._cfg.get(CONF_MAC),
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_power(self._did, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_power(self._did, False)

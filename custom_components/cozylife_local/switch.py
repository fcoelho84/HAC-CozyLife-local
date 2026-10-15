"""Switch platform for CozyLife Local."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_DID, CONF_IP, CONF_MAC, CONF_NAME, DOMAIN
from .coordinator import CozyLifeCoordinator


def _build_entities(
    coordinator: CozyLifeCoordinator, devices: list[dict[str, Any]]
) -> list[CozyLifeSwitch]:
    entities: list[CozyLifeSwitch] = []
    for cfg in devices:
        did = cfg[CONF_DID]
        channels = coordinator.channels_for(did)
        for gang_bit in range(channels):
            entities.append(
                CozyLifeSwitch(coordinator, did, cfg, gang_bit, channels)
            )
    return entities


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: CozyLifeCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = _build_entities(coordinator, list(coordinator.devices.values()))
    async_add_entities(entities)

    @callback
    def _on_new_devices(new_devices: list[dict[str, Any]]) -> None:
        async_add_entities(_build_entities(coordinator, new_devices))

    coordinator.register_new_device_callback(_on_new_devices)


class CozyLifeSwitch(CoordinatorEntity[CozyLifeCoordinator], SwitchEntity):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: CozyLifeCoordinator,
        did: str,
        cfg: dict[str, Any],
        gang_bit: int,
        channels: int,
    ) -> None:
        super().__init__(coordinator)
        self._did = did
        self._cfg = cfg
        self._gang_bit = gang_bit
        self._channels = channels
        gang_number = gang_bit + 1
        if channels <= 1:
            self._attr_name = None
            self._attr_unique_id = did
        else:
            self._attr_name = f"Canal {gang_number}"
            self._attr_unique_id = f"{did}_{gang_number}"
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
        return self.coordinator.is_gang_on(self._did, self._gang_bit)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = {
            "did": self._did,
            "ip": self._cfg.get(CONF_IP),
            "mac": self._cfg.get(CONF_MAC),
            "channels": self._channels,
        }
        if self._channels > 1:
            attrs["channel"] = self._gang_bit + 1
        return attrs

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_gang(self._did, self._gang_bit, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_gang(self._did, self._gang_bit, False)

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import CozyLifeCoordinator, DeviceConfig


def _build_entities_for_devices(
    coordinator: CozyLifeCoordinator, devices: list[DeviceConfig]
) -> list[CozyLifeSwitch]:
    entities: list[CozyLifeSwitch] = []
    for dev in devices:
        for gang in range(dev.channels):
            entities.append(CozyLifeSwitch(coordinator, dev, gang))
    return entities


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: CozyLifeCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = _build_entities_for_devices(
        coordinator, list(coordinator.devices.values())
    )
    async_add_entities(entities)

    @callback
    def _on_new_devices(new_devices: list[DeviceConfig]) -> None:
        async_add_entities(_build_entities_for_devices(coordinator, new_devices))

    coordinator.register_new_device_callback(_on_new_devices)


class CozyLifeSwitch(CoordinatorEntity[CozyLifeCoordinator], SwitchEntity):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: CozyLifeCoordinator,
        device: DeviceConfig,
        gang_index: int,
    ) -> None:
        super().__init__(coordinator)
        self._device = device
        self._gang = gang_index
        did = device.did
        self._attr_unique_id = f"{did}_gang_{gang_index + 1}"
        if device.channels == 1:
            self._attr_name = None
        else:
            self._attr_name = f"Gang {gang_index + 1}"

    @property
    def device_info(self) -> DeviceInfo:
        model = self._device.model or "Local switch"
        return DeviceInfo(
            identifiers={(DOMAIN, self._device.did)},
            name=f"CozyLife {self._device.ip}",
            manufacturer="CozyLife",
            model=model,
            configuration_url=f"http://{self._device.ip}/",
        )

    @property
    def available(self) -> bool:
        return (
            self.coordinator.last_update_success
            and self.coordinator.get_bitmask_cached(self._device.did) is not None
        )

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.is_channel_on(self._device.did, self._gang)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_channel(
            self._device.did, self._gang, True
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_channel(
            self._device.did, self._gang, False
        )

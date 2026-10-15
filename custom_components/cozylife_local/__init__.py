from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import CONF_DEVICES, DOMAIN
from .coordinator import CozyLifeCoordinator, DeviceConfig
from .provisioning import async_resolve_wifi_credentials

PLATFORMS: list[Platform] = [Platform.SWITCH]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    raw_list = entry.data.get(CONF_DEVICES, [])
    devices = [DeviceConfig.from_dict(x) for x in raw_list]
    await async_resolve_wifi_credentials(hass, entry)
    coordinator = CozyLifeCoordinator(hass, devices, entry=entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    coordinator: CozyLifeCoordinator | None = hass.data.get(DOMAIN, {}).get(
        entry.entry_id
    )
    if coordinator and coordinator.consume_suppress_reload():
        return
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(
        entry, PLATFORMS
    )
    if unload_ok and DOMAIN in hass.data:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok

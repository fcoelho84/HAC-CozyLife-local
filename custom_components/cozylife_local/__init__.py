"""CozyLife Local integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_DEVICES, DOMAIN, PLATFORMS
from .coordinator import CozyLifeCoordinator
from .provisioning import async_resolve_wifi_credentials


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    devices = entry.data.get(CONF_DEVICES, [])
    await async_resolve_wifi_credentials(hass, entry)
    coordinator = CozyLifeCoordinator(hass, devices, entry=entry)
    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded and DOMAIN in hass.data:
        coordinator: CozyLifeCoordinator | None = hass.data[DOMAIN].pop(
            entry.entry_id, None
        )
        if coordinator:
            coordinator.close_all()
    return unloaded


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    coordinator: CozyLifeCoordinator | None = hass.data.get(DOMAIN, {}).get(
        entry.entry_id
    )
    if coordinator and coordinator.consume_suppress_reload():
        return
    await hass.config_entries.async_reload(entry.entry_id)

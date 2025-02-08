"""
This module sets up the BM6 component for Home Assistant.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
import logging
from awesomeversion import AwesomeVersion

from homeassistant.helpers.entity_component import EntityComponent
from homeassistant.const import __version__ as HA_VERSION

from .const import (
    COMPONENT, 
    DOMAIN, 
    MIN_REQUIRED_HA_VERSION, 
    PLATFORMS
)    
from .coordinator import BM6DataUpdateCoordinator

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.config_entries import ConfigEntry


@dataclass
class BM6Data:
    """Holds global data for BM6."""

    coordinator: BM6DataUpdateCoordinator


type BM6ConfigEntry = ConfigEntry[BM6Data]

_LOGGER = logging.getLogger(__name__)


def is_ha_supported() -> bool:
    """Return True, if current HA version is supported."""
    if AwesomeVersion(HA_VERSION) >= MIN_REQUIRED_HA_VERSION:
        return True
    _LOGGER.error(
        'Unsupported HA version! Please upgrade home assistant at least to "%s"',
        MIN_REQUIRED_HA_VERSION,
    )
    return False


async def async_setup(
        hass: HomeAssistant, 
        config: dict
):
    """Set up the BM6 component."""
    _LOGGER.debug("BM6 component is set up")
    return True


async def async_setup_entry(
        hass: HomeAssistant, 
        entry: BM6ConfigEntry
) -> bool:
    """Set up BM6 from a config entry."""
    if not is_ha_supported():
        return False
    coordinator = BM6DataUpdateCoordinator(hass, entry)
    entry.runtime_data = BM6Data(coordinator)
    if hass.data.get(DOMAIN) is None:
        hass.data.setdefault(
            DOMAIN, 
            {COMPONENT: EntityComponent(_LOGGER, DOMAIN, hass)}
        )
    hass.data[DOMAIN][entry.entry_id] = entry.data
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(
        hass: HomeAssistant, 
        entry: BM6ConfigEntry
) -> bool:
    """Unload a config entry."""
    unload_ok: bool = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        del hass.data[DOMAIN][entry.entry_id]
        if len(hass.data[DOMAIN]) == 0:
            hass.data.pop(DOMAIN)
    return unload_ok


async def async_reload_entry(
        hass: HomeAssistant, 
        entry: BM6ConfigEntry
) -> None:
    """Reload the config entry when it changed."""
    await hass.config_entries.async_reload(entry.entry_id)

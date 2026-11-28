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
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError

from .const import (
    COMPONENT,
    CONF_DEVICE_ADDRESS,
    CONF_HISTORY_PAGE_COUNT,
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
    if not hass.services.has_service(DOMAIN, "download_history"):
        hass.services.async_register(
            DOMAIN, "download_history", async_handle_download_history
        )
    return True


async def async_handle_download_history(call: ServiceCall) -> None:
    """Handle the bm6.download_history service call.

    Downloads the stored history from the selected BM6 device (or all
    configured BM6 devices when no address is given) and imports it into
    long-term statistics.
    """
    hass = call.hass
    address = call.data.get(CONF_DEVICE_ADDRESS)
    page_count = call.data.get(CONF_HISTORY_PAGE_COUNT)
    if page_count is not None:
        page_count = int(page_count)
    entries = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if address is None or entry.data.get(CONF_DEVICE_ADDRESS) == address
    ]
    if not entries:
        raise ServiceValidationError(
            f"No BM6 device configured for address {address}"
            if address
            else "No BM6 devices configured"
        )
    for entry in entries:
        coordinator: BM6DataUpdateCoordinator = entry.runtime_data.coordinator
        imported = await coordinator.async_download_history(page_count=page_count)
        _LOGGER.info(
            "Downloaded BM6 history for %s: %s new records imported",
            entry.data.get(CONF_DEVICE_ADDRESS),
            imported,
        )


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

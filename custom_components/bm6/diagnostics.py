"""Diagnostics for the BM6 integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_API_KEY

from .const import CONF_DEVICE_ADDRESS

if TYPE_CHECKING:
    from . import BM6ConfigEntry
    from .coordinator import BM6DataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

TO_REDACT = [CONF_API_KEY, CONF_DEVICE_ADDRESS]


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: BM6ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    try:
        _LOGGER.debug("Getting config entry diagnostics for entry %s", entry.entry_id)
        coordinator: BM6DataUpdateCoordinator = entry.runtime_data.coordinator
        return {
            "config_entry": async_redact_data(entry.data, TO_REDACT),
            "coordinator": async_redact_data(
                coordinator.get_diagnostic_data(), TO_REDACT
            ),
        }
    except Exception as e:
        _LOGGER.error("Error getting config entry diagnostics: %s", e)
        return {"error": str(e)}


async def async_get_device_diagnostics(
    hass: HomeAssistant, entry: BM6ConfigEntry, device: DeviceEntry
) -> dict[str, Any]:
    """Return diagnostics for a device."""
    try:
        _LOGGER.debug("Getting device diagnostics for device %s", device.id)
        coordinator: BM6DataUpdateCoordinator = entry.runtime_data.coordinator
        return {
            "device": async_redact_data(device.dict_repr, TO_REDACT),
            "config_entry": async_redact_data(entry.data, TO_REDACT),
            "coordinator": async_redact_data(
                coordinator.get_diagnostic_data(), TO_REDACT
            ),
        }
    except Exception as e:
        _LOGGER.error("Error getting device diagnostics: %s", e)
        return {"error": str(e)}

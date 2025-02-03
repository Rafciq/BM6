"""Utility functions for the bm6 integration."""

from __future__ import annotations

import logging
from enum import Enum
from typing import Type

from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers import translation

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def enum_to_translated_dict(
    hass: HomeAssistant, enum_type: Type[Enum]
) -> dict[str, str]:
    """Convert an enum type to a dictionary with translations based on the current language."""
    try:
        translations = await translation.async_get_translations(
            hass, hass.config.language, "enums"
        )
        prefix = f"component.{DOMAIN}.enums.{enum_type.__name__}."
        return {
            item.value: translations.get(f"{prefix}{item.name}", item.value)
            for item in enum_type
        }
    except Exception as e:
        _LOGGER.warning("Error fetching translations: %s", e)
        return {item.value: item.value for item in enum_type}


def convert_temperature(temp_celsius: float, target_unit: UnitOfTemperature) -> float:
    """Convert a temperature from Celsius to the selected unit."""
    if target_unit == UnitOfTemperature.FAHRENHEIT:
        return (temp_celsius * 9 / 5) + 32
    elif target_unit == UnitOfTemperature.KELVIN:
        return temp_celsius + 273.15
    elif target_unit == UnitOfTemperature.CELSIUS:
        return temp_celsius
    else:
        raise ValueError(f"Unsupported temperature unit: {target_unit}")

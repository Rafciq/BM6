"""Utility functions for the bm6 integration."""

from __future__ import annotations

import logging
import re
from enum import Enum
from typing import Type

from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers import translation

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

def convert_to_snake_case(name: str) -> str:
    name_with_underscores = re.sub(r'(?<!^)(?=[A-Z])', '_', name)
    snake_case_name = name_with_underscores.lower()
    return snake_case_name

async def enum_to_translated_dict(
    hass: HomeAssistant, 
    enum_type: Type[Enum]
) -> dict[str, str]:
    """Convert an enum type to a dictionary with translations based on the current language."""
    try:
        translations = await translation.async_get_translations(
            hass, 
            hass.config.language, 
            'enum'
        )
        _LOGGER.debug("Translations: %s", translations)
        enum_name = f"component.{DOMAIN}.enum.{convert_to_snake_case(enum_type.__name__)}"
        _LOGGER.debug("Fetching translations %s for enum %s", hass.config.language, enum_name)
        result = {
            item.value: translations.get(f"{enum_name}.{item.value}", item.value)
            for item in enum_type
        }
        _LOGGER.debug("Translated enum: %s", result)
        return result
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

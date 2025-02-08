"""Utility functions for the bm6 integration."""

from __future__ import annotations

import logging

from homeassistant.const import UnitOfTemperature

_LOGGER = logging.getLogger(__name__)


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

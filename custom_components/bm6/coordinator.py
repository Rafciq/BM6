"""
This module contains the BM6DataUpdateCoordinator class, which manages fetching data from the BM6 device.
"""

from __future__ import annotations

import time
import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .utils import convert_temperature
from .battery import Battery
from .bm6_connect import BM6Connector, BM6Data, BM6DeviceError
from .const import (
    CONF_TEMPERATURE_UNIT,
    DEFAULT_FAILURES_BEFORE_UNAVAILABLE,
    DEFAULT_STALE_AFTER_SECONDS,
    DOMAIN,
    CONF_DEVICE_ADDRESS,
    CONF_UPDATE_INTERVAL,
    CONF_VOLTAGE_OFFSET,
    CONF_TEMPERATURE_OFFSET,
    KEY_BLUETOOTH_SCANNER,
    KEY_CVR_MAX,
    KEY_CVR_MIN,
    KEY_DVR_MAX,
    KEY_DVR_MIN,
    KEY_DEVICE_PERCENTAGE,
    KEY_RAPID_ACCELERATION,
    KEY_RAPID_DECELERATION,
    KEY_STATE_ALGORITHM,
    KEY_PERCENTAGE,
    KEY_RSSI,
    KEY_SOC_MAX,
    KEY_SOC_MIN,
    KEY_SOD_MAX,
    KEY_SOD_MIN,
    KEY_STATE,
    KEY_DEVICE_STATE,
    KEY_TEMPERATURE_CORRECTED,
    KEY_TEMPERATURE_DEVICE,
    KEY_TEMPERATURE_UNIT,
    KEY_VOLTAGE_CORRECTED,
    KEY_VOLTAGE_DEVICE,
)

if TYPE_CHECKING:
    from . import BM6ConfigEntry

_LOGGER = logging.getLogger(__name__)


class BM6DataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the BM6 device."""

    def __init__(self, hass: HomeAssistant, config_entry: BM6ConfigEntry) -> None:
        """Initialize the coordinator."""
        self.hass = hass
        self.config_entry = config_entry
        self.device_address = config_entry.data[CONF_DEVICE_ADDRESS]
        self._battery = Battery(config_entry.data)
        # Stale-data handling (Option 2)
        self._last_good_data: dict | None = None
        self._last_success_monotonic: float | None = None
        self._consecutive_failures: int = 0

        # Tunables (defaults in const.py)
        self._failures_before_unavailable = DEFAULT_FAILURES_BEFORE_UNAVAILABLE
        self._stale_after_seconds = DEFAULT_STALE_AFTER_SECONDS
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=config_entry.data[CONF_UPDATE_INTERVAL]),
        )

    async def _async_update_data(self) -> dict:
        """Fetch data from the BM6 device."""
        try:
            connector: BM6Connector = BM6Connector(
                hass=self.hass, address=self.device_address
            )
            data: BM6Data = await connector.get_data()
            _LOGGER.debug(
                "BM6 %s got data: RealTime=%s Adv=%s",
                self.device_address,
                getattr(data, "RealTime", None),
                getattr(data, "Advertisement", None),
            )
            voltage_corrected = (
                data.RealTime.Voltage + self.config_entry.data[CONF_VOLTAGE_OFFSET]
            )
            temperature_unit = self.config_entry.data[CONF_TEMPERATURE_UNIT]
            temperature_corrected = (
                convert_temperature(data.RealTime.Temperature, temperature_unit)
                + self.config_entry.data[CONF_TEMPERATURE_OFFSET]
            )
            self._battery.update(data.RealTime, voltage_corrected)
            _LOGGER.debug(
                "BM6 %s voltage=%r temp=%r pct=%r state=%r rssi=%r scanner=%r",
                self.device_address,
                getattr(data.RealTime, "Voltage", None),
                getattr(data.RealTime, "Temperature", None),
                getattr(data.RealTime, "Percent", None),
                getattr(data.RealTime, "State", None),
                getattr(data.Advertisement, "RSSI", None) if data.Advertisement else None,
                getattr(data.Advertisement, "Scanner", None) if data.Advertisement else None,
            )
            result = {
                KEY_VOLTAGE_DEVICE: data.RealTime.Voltage,
                KEY_VOLTAGE_CORRECTED: voltage_corrected,
                KEY_TEMPERATURE_DEVICE: data.RealTime.Temperature,
                KEY_TEMPERATURE_UNIT: temperature_unit,
                KEY_TEMPERATURE_CORRECTED: temperature_corrected,
                KEY_PERCENTAGE: self._battery.percent,
                KEY_STATE: self._battery.state.value,
                KEY_RSSI: data.Advertisement.RSSI if data.Advertisement else None,
                KEY_DVR_MIN: self._battery.range.dvr.min,
                KEY_DVR_MAX: self._battery.range.dvr.max,
                KEY_CVR_MIN: self._battery.range.cvr.min,
                KEY_CVR_MAX: self._battery.range.cvr.max,
                KEY_SOD_MIN: self._battery.range.sod.min,
                KEY_SOD_MAX: self._battery.range.sod.max,
                KEY_SOC_MIN: self._battery.range.soc.min,
                KEY_SOC_MAX: self._battery.range.soc.max,
                KEY_STATE_ALGORITHM: self._battery.info.state_algorithm.value,
                KEY_DEVICE_PERCENTAGE: data.RealTime.Percent,
                KEY_DEVICE_STATE: data.RealTime.State,
                KEY_RAPID_ACCELERATION: data.RealTime.RapidAcceleration,
                KEY_RAPID_DECELERATION: data.RealTime.RapidDeceleration,
                KEY_BLUETOOTH_SCANNER: data.Advertisement.Scanner,
            }
            # Mark fresh success
            self._last_good_data = result
            self._last_success_monotonic = time.monotonic()
            self._consecutive_failures = 0

            return result
        except BM6DeviceError as e:
            # Any BM6 read failure -- device not found, a failed GATT connection,
            # or an all-zero/invalid realtime frame -- is treated as a transient
            # miss. On a marginal link the device is often *seen* but the active
            # connection fails; previously only "not found" errors were tolerated,
            # so every failed connection flipped the sensors straight to
            # "unavailable". Route them all through the same soft-fail window.
            self._consecutive_failures += 1
            now = time.monotonic()
            last_ok = self._last_success_monotonic
            age = (now - last_ok) if last_ok is not None else None

            _LOGGER.debug(
                "BM6 %s read failed: %s (miss %s/%s, age=%s)",
                self.device_address,
                e,
                self._consecutive_failures,
                self._failures_before_unavailable,
                age,
            )

            # Soft fail: keep serving the last good reading through a few
            # transient misses so dashboards/automations do not flap.
            if (
                self._last_good_data is not None
                and self._consecutive_failures < self._failures_before_unavailable
                and age is not None
                and age < self._stale_after_seconds
            ):
                return self._last_good_data

            # Hard fail: genuinely out of range/off -- mark unavailable.
            raise UpdateFailed(
                f"BM6 {self.device_address} unavailable "
                f"(misses={self._consecutive_failures}, age={age})"
            ) from e
        except Exception as e:
            _LOGGER.error(
                "Unexpected error while reading BM6 at %s: %s", self.device_address, e
            )
            raise UpdateFailed(f"Unexpected error: {e}") from e

    def get_diagnostic_data(self) -> dict:
        """Return diagnostic data for the BM6 device."""
        return {
            "device_address": self.device_address,
            "battery": self._battery.get_diagnostic_data(),
            "data": self.data,
            "last_update_success": self.last_update_success,
            "last_exception": self.last_exception,
            "update_interval": self.update_interval.seconds,
            "microsecond": self._microsecond,
        }

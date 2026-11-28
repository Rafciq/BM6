"""
This module contains the BM6DataUpdateCoordinator class, which manages fetching data from the BM6 device.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .utils import convert_temperature
from .battery import Battery
from .bm6_connect import BM6Connector, BM6Data, BM6DeviceError
from .const import (
    CONF_HISTORY_ENABLED,
    CONF_HISTORY_PAGE_COUNT,
    CONF_TEMPERATURE_UNIT,
    CONF_VOLTAGE_OFFSET,
    CONF_TEMPERATURE_OFFSET,
    DEFAULT_HISTORY_ENABLED,
    DEFAULT_HISTORY_PAGE_COUNT,
    DEFAULT_TEMPERATURE_OFFSET,
    DEFAULT_TEMPERATURE_UNIT,
    DEFAULT_VOLTAGE_OFFSET,
    DOMAIN,
    CONF_DEVICE_ADDRESS,
    CONF_UPDATE_INTERVAL,
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
from .history import async_import_history

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
        self._history_enabled: bool = config_entry.data.get(
            CONF_HISTORY_ENABLED, DEFAULT_HISTORY_ENABLED
        )
        self._history_page_count: int = config_entry.data.get(
            CONF_HISTORY_PAGE_COUNT, DEFAULT_HISTORY_PAGE_COUNT
        )
        self._history_lock = asyncio.Lock()
        self._last_history_import: dict[str, datetime] = {}
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
            voltage_corrected = (
                data.RealTime.Voltage + self.config_entry.data[CONF_VOLTAGE_OFFSET]
            )
            temperature_unit = self.config_entry.data[CONF_TEMPERATURE_UNIT]
            temperature_corrected = (
                convert_temperature(data.RealTime.Temperature, temperature_unit)
                + self.config_entry.data[CONF_TEMPERATURE_OFFSET]
            )
            self._battery.update(data.RealTime, voltage_corrected)
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
            if self._history_enabled:
                # History runs in the background so a slow BLE download
                # never blocks the live update (or the sensor platform
                # setup, which waits on the first refresh).
                self.hass.async_create_background_task(
                    self._async_download_history_guarded(),
                    "bm6 history import",
                    eager_start=False,
                )
            return result
        except BM6DeviceError as e:
            _LOGGER.error("BM6 device error at %s: %s", self.device_address, e)
            raise UpdateFailed(f"BM6 device error: {e}") from e
        except Exception as e:
            _LOGGER.error(
                "Unexpected error while reading BM6 at %s: %s", self.device_address, e
            )
            raise UpdateFailed(f"Unexpected error: {e}") from e

    async def _async_download_history_guarded(self) -> None:
        """Download history, logging (not raising) any failure.

        Used for the automatic per-update import, which runs in the
        background and must never fail the live update.
        """
        try:
            await self.async_download_history()
        except Exception as err:  # noqa: BLE001 - history is best-effort
            _LOGGER.warning(
                "BM6 history import failed for %s: %s",
                self.device_address,
                err,
            )

    async def async_download_history(self, page_count: int | None = None) -> int:
        """Download stored history from the device and import it into statistics.

        Returns the number of new records imported. A download that finds
        no history returns 0. Raises BM6DeviceError when the device cannot
        be reached, so a manual trigger can report the failure; the
        automatic per-update import catches and logs it instead.
        """
        if self._history_lock.locked():
            _LOGGER.debug(
                "History download already in progress for %s", self.device_address
            )
            return 0
        async with self._history_lock:
            pages = (
                int(page_count)
                if page_count is not None
                else self._history_page_count
            )
            connector = BM6Connector(hass=self.hass, address=self.device_address)
            readings = await connector.get_history(page_count=pages)
            if not readings:
                _LOGGER.info(
                    "No stored history available from BM6 at %s", self.device_address
                )
                return 0
            imported, self._last_history_import = await async_import_history(
                self.hass,
                device_address=self.device_address,
                readings=readings,
                last_import=self._last_history_import,
                temperature_unit=self.config_entry.data.get(
                    CONF_TEMPERATURE_UNIT, DEFAULT_TEMPERATURE_UNIT
                ),
                voltage_offset=self.config_entry.data.get(
                    CONF_VOLTAGE_OFFSET, DEFAULT_VOLTAGE_OFFSET
                ),
                temperature_offset=self.config_entry.data.get(
                    CONF_TEMPERATURE_OFFSET, DEFAULT_TEMPERATURE_OFFSET
                ),
            )
            _LOGGER.info(
                "Imported %s BM6 history records for %s",
                imported,
                self.device_address,
            )
            return imported

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
            "history_enabled": self._history_enabled,
            "history_page_count": self._history_page_count,
            "last_history_import": {
                statistic_id: timestamp.isoformat()
                for statistic_id, timestamp in self._last_history_import.items()
            },
        }

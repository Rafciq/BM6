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
from .history import async_import_history, async_newest_archive_ts

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
        """Fetch data from the BM6 device.

        A single Bluetooth poll can fail transiently (the device does
        not answer a connect within the timeout, or the phone app is
        briefly holding the single BLE connection), which would leave
        the sensors unavailable until the next hourly poll. So the
        read is retried a few times with a short pause between
        attempts; only if every attempt fails is the update marked
        failed.
        """
        data: BM6Data | None = None
        last_error: BM6DeviceError | None = None
        for attempt in range(1, 4):
            try:
                connector: BM6Connector = BM6Connector(
                    hass=self.hass, address=self.device_address
                )
                data = await connector.get_data()
                break
            except BM6DeviceError as e:
                last_error = e
                if attempt < 3:
                    _LOGGER.warning(
                        "BM6 read attempt %s/3 failed at %s (%s); retrying",
                        attempt,
                        self.device_address,
                        e,
                    )
                    await asyncio.sleep(5)
        if data is None:
            _LOGGER.error(
                "BM6 device error at %s: %s", self.device_address, last_error
            )
            raise UpdateFailed(f"BM6 device error: {last_error}") from last_error
        try:
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

    async def _catch_up_pages(self) -> int:
        """Pages needed to reach back to the newest archived record.

        Returns 1 when the archive is current (normal hourly cycle) or
        when there is no archive yet. After the device has been out of
        range, returns enough pages to cover the whole gap, capped at
        100 (the device buffer holds about 100 pages, ~35 days).
        """
        import math

        newest = await async_newest_archive_ts(self.hass, self.device_address)
        if newest is None:
            return 1
        now = datetime.now().astimezone()
        if newest.tzinfo is None:
            newest = newest.astimezone()
        hours = max(0.0, (now - newest).total_seconds() / 3600)
        # One page = 256 records * 2 minutes = 512 minutes (~8.5 hours).
        return max(1, min(100, math.ceil(hours / 8.53)))

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
            if page_count is None:
                # Catch-up: when the device has been out of range (car
                # away, phone app holding the connection), the stored
                # history on the device covers the whole gap. Request
                # enough pages to reach back to the newest record already
                # in the archive, so the download covers the away period
                # instead of only the latest page (~8.5 hours). One page
                # holds 256 records at 2 minutes each (~8.5 hours); the
                # device buffer holds about 3.5 days, so 10 pages is the
                # most that can ever help.
                pages = max(pages, await self._catch_up_pages())
            # The device may be unreachable (not discovered, BLE busy,
            # phone app holding the single connection). That must not
            # block the gap-fill: the JSON archive is the permanent
            # record, so the import below still runs and repairs any
            # gaps from the archive. The download error is re-raised
            # afterwards so a manual trigger still reports it.
            readings: list = []
            download_error: Exception | None = None
            try:
                connector = BM6Connector(hass=self.hass, address=self.device_address)
                readings = await connector.get_history(page_count=pages)
            except Exception as err:  # noqa: BLE001 - device may be down
                download_error = err
                _LOGGER.warning(
                    "BM6 history download failed for %s: %s; "
                    "gap-fill will still run from the archive",
                    self.device_address,
                    err,
                )
            # The import always runs, even when this download found no new
            # records: it fills gaps in the stored history from the JSON
            # archive on every cycle, so a missed or partial download is
            # repaired on the next run instead of leaving a hole.
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
            if download_error is not None:
                raise download_error
            if not readings:
                _LOGGER.info(
                    "No new stored history from BM6 at %s; gap-fill ran",
                    self.device_address,
                )
                return 0
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

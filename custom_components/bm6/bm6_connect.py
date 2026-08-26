from __future__ import annotations

"""This module implements communication with BM6 device."""

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional, TYPE_CHECKING

from bleak import BleakClient
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.scanner import AdvertisementData
from Crypto.Cipher import AES

from homeassistant.core import HomeAssistant
from homeassistant.components.bluetooth import async_scanner_devices_by_address

from .const import (
    CHARACTERISTIC_UUID_NOTIFY,
    CHARACTERISTIC_UUID_WRITE,
    CRYPT_KEY,
    BLEAK_CLIENT_TIMEOUT,
    BLEAK_CONNECT_MAX_ATTEMPTS,
    GATT_DATA_REALTIME,
    GATT_DATA_VERSION,
    GATT_NOTIFY_REALTIME_PREFIX,
    GATT_NOTIFY_VERSION_PREFIX,
)

# Only import HA bluetooth typing at runtime; TYPE_CHECKING keeps editors happy
if TYPE_CHECKING:
    from habluetooth import BaseHaScanner, BluetoothScannerDevice
else:
    BaseHaScanner = object  # type: ignore
    BluetoothScannerDevice = object  # type: ignore

_LOGGER = logging.getLogger(__name__)

# Serialize GATT connections per scanner/proxy to prevent concurrent connects
_SCANNER_LOCKS: dict[str, asyncio.Lock] = {}


def _scanner_lock_key(scanner: "BluetoothScannerDevice") -> str:
    sc = getattr(scanner, "scanner", None)
    return (
        getattr(sc, "name", None)
        or getattr(sc, "source", None)
        or getattr(sc, "adapter", None)
        or "unknown_scanner"
    )


def _get_scanner_lock(key: str) -> asyncio.Lock:
    lock = _SCANNER_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _SCANNER_LOCKS[key] = lock
    return lock


def _realtime_all_zeros(rt) -> bool:
    """Return True if BM6 returned an all-zero fake realtime packet."""
    if rt is None:
        return True
    try:
        v = float(getattr(rt, "Voltage", 0) or 0)
        t = int(getattr(rt, "Temperature", 0) or 0)
        p = int(getattr(rt, "Percent", 0) or 0)
    except (TypeError, ValueError):
        return True
    return v == 0.0 and t == 0 and p == 0

PREFERRED_SCANNERS: set[str] = set()


# Try to use HA-recommended connector helper if available
try:
    from bleak_retry_connector import establish_connection  # type: ignore
except Exception:  # pragma: no cover
    establish_connection = None  # type: ignore
    _LOGGER.warning(
        "bleak-retry-connector not available; BM6 connections may be less reliable"
    )



class BM6RealTimeState(Enum):
    """Enumeration for BM6 device status."""

    BatteryOk = 0
    LowVoltage = 1
    Charging = 2


@dataclass
class BM6RealTimeData:
    """Class to store the real-time data read from the BM6 device."""

    Voltage: float = 0.0  # Battery voltage in V
    Temperature: int = 0  # Temperature in °C
    Percent: int = 0  # Percentage of power in %
    RapidAcceleration: int = 0
    RapidDeceleration: int = 0
    State: BM6RealTimeState = BM6RealTimeState.BatteryOk  # Status of the battery

    def __init__(self, data: str):
        """Initialize BM6ReadTimeData from a hex string."""
        self.Voltage = int(data[14:18], 16) / 100
        temperature_sign = int(data[6:8], 16)
        self.Temperature = int(data[8:10], 16)
        if temperature_sign == 1:
            self.Temperature = -self.Temperature
        self.Percent = int(data[12:14], 16)
        self.RapidAcceleration = int(data[18:22], 16)
        self.RapidDeceleration = int(data[22:26], 16)

        # Ensure State is an Enum, but stay resilient to unknown values
        try:
            self.State = BM6RealTimeState(int(data[10:12], 16))
        except ValueError:
            self.State = BM6RealTimeState.BatteryOk


@dataclass
class BM6Firmware:
    """Class to store the firmware version of the BM6 device."""

    Version: str = None

    def __init__(self, data: str):
        """Initialize BM6Firmware with version data."""
        self.Version = data


@dataclass
class BM6Advertisement:
    """Class to store the advertisement data of the BM6 device."""

    RSSI: int = None
    Scanner: str = None

    def __init__(
        self,
        advertisement_data: Optional[AdvertisementData],
        ha_scanner: Optional[BaseHaScanner],
    ):
        """Initialize BM6Advertisement with advertisement data."""
        self.RSSI = advertisement_data.rssi if advertisement_data else None
        self.Scanner = ha_scanner.name if ha_scanner else None


@dataclass
class BM6Data:
    """Class to store all data read from the BM6 device."""

    Firmware: Optional[BM6Firmware] = None
    RealTime: Optional[BM6RealTimeData] = None
    Advertisement: Optional[BM6Advertisement] = None

    def __init__(
        self,
        advertisement_data: Optional[AdvertisementData],
        ha_scanner: Optional[BaseHaScanner],
    ):
        """Initialize BM6Data with advertisement data."""
        self.Advertisement = BM6Advertisement(advertisement_data, ha_scanner)


class BM6DeviceError(RuntimeError):
    """Error communicating with BM6 device."""


class BM6Connector:
    """Class to manage the connection to the BM6 device."""

    def __init__(self, hass: HomeAssistant, address: str):
        """Initialize the BM6Connector."""
        self.hass = hass
        self._address: str = address
        self._scanners: list[BluetoothScannerDevice] = []
        self._data: Optional[BM6Data] = None
        self._rt_event = asyncio.Event()


    def _decrypt(self, data: bytearray) -> bytearray:
        """Decrypt the received data using AES."""
        cipher = AES.new(CRYPT_KEY, AES.MODE_CBC, 16 * b"\0")
        return cipher.decrypt(data)

    def _encrypt(self, data: bytearray) -> bytearray:
        """Encrypt the data to be sent using AES."""
        cipher = AES.new(CRYPT_KEY, AES.MODE_CBC, 16 * b"\0")
        return cipher.encrypt(data)

    def _notify_callback(
        self,
        sender: BleakGATTCharacteristic,
        data: bytearray,
    ):
        """Callback function to handle notifications from the BM6 device."""
        message = self._decrypt(data).hex()
        if message.startswith(GATT_NOTIFY_REALTIME_PREFIX):
            _LOGGER.debug(
                "BM6 realtime raw at %s: prefix=%s msg[0:40]=%s",
                self._address,
                GATT_NOTIFY_REALTIME_PREFIX,
                message[:40],
            )
        _LOGGER.debug("Received data from BM6 at %s: %s", self._address, message)

        if not self._data:
            return

        if message.startswith(GATT_NOTIFY_REALTIME_PREFIX):
            self._data.RealTime = BM6RealTimeData(message)

            self._rt_event.set()

            _LOGGER.debug(
                "Decoded real-time data from BM6 at %s: %s",
                self._address,
                self._data.RealTime,
            )

        elif message.startswith(GATT_NOTIFY_VERSION_PREFIX):
            self._data.Firmware = BM6Firmware(message)
            _LOGGER.debug(
                "Decoded firmware version from BM6 at %s: %s",
                self._address,
                self._data.Firmware,
            )

    async def _refresh_scanners_with_retry(self) -> list[BluetoothScannerDevice]:
        """BM6 advertisements can be bursty; retry before declaring 'not found'."""
        # ~20s total
        for _ in range(40):
            scanners = async_scanner_devices_by_address(
                self.hass, self._address, connectable=True
            )
            if scanners:
                return scanners
            await asyncio.sleep(0.5)
        return []

    def _sort_scanners(self, scanners: list[BluetoothScannerDevice]) -> list[BluetoothScannerDevice]:
        """Prefer configured scanners by name, then stronger RSSI."""
        def key(s: BluetoothScannerDevice):
            name = getattr(s.scanner, "name", "") or ""
            rssi = getattr(s.advertisement, "rssi", None)
            rssi_val = rssi if isinstance(rssi, int) else -999
            preferred_rank = 0 if (PREFERRED_SCANNERS and name in PREFERRED_SCANNERS) else 1
            return (preferred_rank, -rssi_val)

        return sorted(scanners, key=key)

    async def _connect_client(self, ble_device, scanner_name: str) -> BleakClient:
        """Establish a BLE connection using HA-recommended retry helper when available."""
        if establish_connection is not None:
            return await establish_connection(
                BleakClient,
                ble_device,
                self._address,
                timeout=BLEAK_CLIENT_TIMEOUT,
                max_attempts=BLEAK_CONNECT_MAX_ATTEMPTS,
            )

        # Fallback to raw BleakClient (less reliable)
        client = BleakClient(ble_device, timeout=BLEAK_CLIENT_TIMEOUT)
        await client.connect()
        return client

    async def get_data(self) -> Optional[BM6Data]:
        """Retrieve data from the BM6 device."""
        _LOGGER.debug("Get device BM6 at %s from HASS", self._address)

        scanners = await self._refresh_scanners_with_retry()
        if not scanners:
            raise BM6DeviceError(f"Bluetooth device {self._address} not found")

        self._scanners = self._sort_scanners(scanners)

        _LOGGER.debug(
            "Device BM6 at %s is seen by scanners %s",
            self._address,
            [
                {
                    "scanner": s.scanner.name,
                    "rssi": s.advertisement.rssi,
                }
                for s in self._scanners
            ],
        )

        exceptions: list[Exception] = []

        for scanner in self._scanners:
            scanner_name = getattr(scanner.scanner, "name", "unknown")
            scanner_addr = getattr(scanner.scanner, "source", None) or getattr(scanner.scanner, "adapter", None)

            _LOGGER.debug(
                "Start getting data from BM6 at %s via scanner %s (%s)",
                self._address,
                scanner_name,
                getattr(scanner, "scanner", None),
            )

            try:
                self._data = BM6Data(scanner.advertisement, scanner.scanner)

                scanner_key = _scanner_lock_key(scanner)
                lock = _get_scanner_lock(scanner_key)

                client: Optional[BleakClient] = None
                async with lock:
                    try:
                        client = await self._connect_client(scanner.ble_device, scanner_name)

                        await client.start_notify(
                            CHARACTERISTIC_UUID_NOTIFY, self._notify_callback
                        )
                        await asyncio.sleep(0.5)

                        wait_s = 6.0

                        for attempt in (1, 2):
                            self._data.RealTime = None
                            self._rt_event.clear()

                            await client.write_gatt_char(
                                CHARACTERISTIC_UUID_WRITE,
                                self._encrypt(bytearray.fromhex(GATT_DATA_REALTIME)),
                                response=True,
                            )

                            await asyncio.wait_for(self._rt_event.wait(), timeout=wait_s)

                            rt2 = self._data.RealTime if self._data else None
                            if rt2 and not _realtime_all_zeros(rt2):
                                _LOGGER.debug(
                                    "Successfully read BM6 at %s via scanner %s (late follow-up, attempt %s)",
                                    self._address,
                                    scanner_name,
                                    attempt,
                                )
                                # Fire an HA event so automations can count successes
                                try:
                                    self.hass.bus.async_fire(
                                        "bm6_success",
                                        {"address": self._address, "scanner": scanner_name},
                                    )
                                except Exception:
                                    pass

                                _LOGGER.info(
                                    "BM6 update OK %s via %s: V=%.2f T=%s P=%s",
                                    self._address,
                                    scanner_name,
                                    rt2.Voltage,
                                    rt2.Temperature,
                                    rt2.Percent,
                                )
                                return self._data


                            # We got a realtime frame but it's the known all-zero "empty" frame.
                            # Many BM6 units send the real frame a beat later. Give it a short grace window.
                            _LOGGER.debug(
                                "BM6 realtime all-zeros at %s via %s (attempt %s/2); waiting briefly for follow-up frame",
                                self._address,
                                scanner_name,
                                attempt,
                            )

                            # Wait up to 1.0s for another notify without re-writing
                            try:
                                self._rt_event.clear()
                                await asyncio.wait_for(self._rt_event.wait(), timeout=1.0)
                                rt2 = self._data.RealTime if self._data else None
                                if rt2 and not _realtime_all_zeros(rt2):
                                    _LOGGER.debug(
                                        "Successfully read BM6 at %s via scanner %s (late follow-up, attempt %s)",
                                        self._address,
                                        scanner_name,
                                        attempt,
                                    )
                                    try:
                                        self.hass.bus.async_fire(
                                            "bm6_success",
                                            {"address": self._address, "scanner": scanner_name},
                                        )
                                    except Exception:
                                        _LOGGER.debug("Failed to fire bm6_success", exc_info=True)


                                    _LOGGER.info(
                                        "BM6 update OK %s via %s: V=%.2f T=%s P=%s",
                                        self._address,
                                        scanner_name,
                                        rt2.Voltage,
                                        rt2.Temperature,
                                        rt2.Percent,
                                    )
                                    return self._data

                            except asyncio.TimeoutError:
                                pass

                            _LOGGER.debug(
                                "BM6 realtime still invalid/all-zeros at %s via %s (attempt %s/2); retrying write",
                                self._address,
                                scanner_name,
                                attempt,
                            )
                            await asyncio.sleep(0.2)


                        # You want misses to show as unavailable → raise on failure
                        raise BM6DeviceError(
                            f"Invalid realtime payload (all zeros) from {self._address} via {scanner_name} after retry"
                        )

                    finally:
                        if client is not None:
                            try:
                                await client.stop_notify(CHARACTERISTIC_UUID_NOTIFY)
                            except Exception:
                                pass
                            try:
                                await client.disconnect()
                            except Exception:
                                pass

            except Exception as e:
                try:
                    e.add_note(f"Using scanner {scanner_name}")
                except Exception:
                    pass
                exceptions.append(e)
                _LOGGER.warning(
                    "Error while reading BM6 at %s via %s: %s",
                    self._address,
                    scanner_name,
                    e,
                )

        # Only raise after trying all scanners
        raise BM6DeviceError(
            f"Error while reading BM6 at {self._address}: {exceptions}"
        ) from (exceptions[0] if exceptions else None)

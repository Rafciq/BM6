"""This module implements communication with BM6 device."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from enum import Enum
from typing import Optional
from bleak import BLEDevice, BleakClient
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.scanner import AdvertisementData
from Crypto.Cipher import AES

from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant
from homeassistant.components.bluetooth import async_ble_device_from_address

from .const import (
    CHARACTERISTIC_UUID_NOTIFY,
    CHARACTERISTIC_UUID_WRITE,
    CRYPT_KEY,
    BLEAK_CLIENT_TIMEOUT,
    GATT_DATA_REALTIME,
    GATT_DATA_VERSION,
    GATT_NOTIFY_REALTIME_PREFIX,
    GATT_NOTIFY_VERSION_PREFIX,
)

_LOGGER = logging.getLogger(__name__)


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
        self.State = int(data[10:12], 16)


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

    def __init__(self, data: Optional[AdvertisementData]):
        """Initialize BM6Advertisement with advertisement data."""
        if data:
            self.RSSI = data.rssi


@dataclass
class BM6Data:
    """Class to store all data read from the BM6 device."""

    Firmware: Optional[BM6Firmware] = None
    RealTime: Optional[BM6RealTimeData] = None
    Advertisement: Optional[BM6Advertisement] = None

    def __init__(self, advertisement_data: Optional[AdvertisementData]):
        """Initialize BM6Data with advertisement data."""
        self.Advertisement = BM6Advertisement(advertisement_data)


class BM6DeviceError(RuntimeError): ...


class BM6Connector:
    """Class to manage the connection to the BM6 device."""

    def __init__(
        self,
        hass: Optional[HomeAssistant] = None,
        address: Optional[str] = None,
        device: Optional[BLEDevice] = None,
        advertisement: Optional[AdvertisementData] = None,
    ):
        """Initialize the BM6Connector with either a HASS or a BLEDevice."""
        self.hass = hass
        self._address: Optional[str] = (
            address if address else (device.address if device else None)
        )
        self._device: Optional[BLEDevice] = device
        self._advertisement: Optional[AdvertisementData] = advertisement
        self._data: BM6Data = None
        if hass and address:
            _LOGGER.debug("Get device BM6 at %s from HASS", self._address)
            self._device = async_ble_device_from_address(
                self.hass, self._address, connectable=True
            )
            if not self._device:
                raise BM6DeviceError(f"Bluetooth device {self._address} not found")
            service_info = bluetooth.async_last_service_info(hass, self._address)
            if service_info:
                self._advertisement = service_info.advertisement
        elif device:
            if not self._device:
                raise BM6DeviceError(f"Bluetooth device {self._address} not found")
        else:
            raise ValueError("Either hass and address or device must be provided")
        if self._advertisement:
            _LOGGER.debug(
                "Advertisement data for device BM6 at %s: %s",
                self._address,
                self._advertisement,
            )
        else:
            _LOGGER.debug("No advertisement data for device BM6 at %s", self._address)
        self._data = BM6Data(self._advertisement)

    def _decrypt(self, data: bytearray) -> bytearray:
        """Decrypt the received data using AES."""
        cipher = AES.new(CRYPT_KEY, AES.MODE_CBC, 16 * b"\0")
        return cipher.decrypt(data)

    def _encrypt(self, data: bytearray) -> bytearray:
        """Encrypt the data to be sent using AES."""
        cipher = AES.new(CRYPT_KEY, AES.MODE_CBC, 16 * b"\0")
        return cipher.encrypt(data)

    async def _notify_callback(self, _sender: BleakGATTCharacteristic, data: bytearray):
        """Callback function to handle notifications from the BM6 device."""
        message = self._decrypt(data).hex()
        _LOGGER.debug("Received data from BM6 at %s: %s", self._address, message)
        if message.startswith(GATT_NOTIFY_REALTIME_PREFIX):
            self._data.RealTime = BM6RealTimeData(message)
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

    async def get_data(self) -> BM6Data:
        """Retrieve data from the BM6 device."""
        _LOGGER.debug("Start getting data from the BM6 at %s", self._address)
        try:
            async with BleakClient(
                self._device, timeout=BLEAK_CLIENT_TIMEOUT
            ) as client:
                _LOGGER.debug(
                    "Write to BM6 at %s characteristic %s",
                    self._address,
                    CHARACTERISTIC_UUID_WRITE,
                )
                await client.write_gatt_char(
                    CHARACTERISTIC_UUID_WRITE,
                    self._encrypt(bytearray.fromhex(GATT_DATA_REALTIME)),
                    response=True,
                )
                _LOGGER.debug("Wait for data from BM6 at %s", self._address)
                self._data.RealTime = None
                await client.start_notify(
                    CHARACTERISTIC_UUID_NOTIFY, self._notify_callback
                )
                while self._data is None or self._data.RealTime is None:
                    await asyncio.sleep(0.5)
                _LOGGER.debug("Finishing wait for data from BM6 at %s", self._address)
                await client.stop_notify(CHARACTERISTIC_UUID_NOTIFY)

                # The following code is commented out but can be used to get firmware version data
                # _LOGGER.debug("Write to BM6 at %s characteristic %s", device.address, CHARACTERISTIC_UUID_WRITE)
                # await client.write_gatt_char(CHARACTERISTIC_UUID_WRITE,
                #                              self._encrypt(bytearray.fromhex(GATT_DATA_VERSION)),
                #                              response=True)
                # _LOGGER.debug("Wait for data from BM6 at %s", device.address)
                # self._data.Firmware = None
                # await client.start_notify(CHARACTERISTIC_UUID_NOTIFY,
                #                           self._notify_callback)
                # while self._data is None or self._data.Firmware is None:
                #     await asyncio.sleep(0.5)
                # _LOGGER.debug("Finishing wait for data from BM6 at %s", device.address)
                # await client.stop_notify(CHARACTERISTIC_UUID_NOTIFY)
        except Exception as e:
            _LOGGER.error("Error while reading BM6 at %s: %s", self._address, e)
            raise BM6DeviceError(
                f"Error while reading BM6 at {self._address}: {e}"
            ) from e
        return self._data

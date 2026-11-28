"""This module implements communication with BM6 device."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from enum import Enum
from typing import Optional
from bleak import BleakClient
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.scanner import AdvertisementData
from Crypto.Cipher import AES

from habluetooth import BaseHaScanner, BluetoothScannerDevice
from homeassistant.core import HomeAssistant
from homeassistant.components.bluetooth import async_scanner_devices_by_address

from .const import (
    CHARACTERISTIC_UUID_NOTIFY,
    CHARACTERISTIC_UUID_WRITE,
    CRYPT_KEY,
    BLEAK_CLIENT_TIMEOUT,
    GATT_DATA_REALTIME,
    GATT_DATA_VERSION,
    GATT_NOTIFY_HISTORY_PREFIX,
    GATT_NOTIFY_REALTIME_PREFIX,
    GATT_NOTIFY_VERSION_PREFIX,
    HISTORY_IDLE_TIMEOUT,
    HISTORY_RECORD_MINUTES,
    HISTORY_START_MARKER,
    HISTORY_TIMEOUT_PER_PAGE,
    HISTORY_TRAILER,
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


@dataclass(frozen=True)
class BM6HistoryReading:
    """A single stored history record read from the BM6 device.

    Records are stored on the device newest-first, one every
    HISTORY_RECORD_MINUTES minutes. The device has no real-time clock,
    so ``ts`` is estimated by counting back from the download time
    (see default_bm6_history_reference_ts).
    """

    ts: datetime
    voltage: float
    soc: int | None
    temperature: float | None
    event_type: int
    raw_record: str
    record_index: int


def encode_bm6_history_request(page_count: int) -> bytes:
    """Encode a BM6 stored-history request.

    The 16-byte plaintext is ``D1 55 05 00 00 00 00 <page_count> 00 ...``.
    ``page_count`` is cumulative: 1 requests the latest page (256 records,
    about 8.5 hours), 2 the latest two pages, and so on. Protocol
    reverse-engineered and verified by marcomc/BMGateway
    (https://github.com/marcomc/BMGateway).
    """
    if page_count < 1 or page_count > 255:
        raise ValueError("page_count must be between 1 and 255")
    return bytes([0xD1, 0x55, 0x05, 0, 0, 0, 0, page_count, 0, 0, 0, 0, 0, 0, 0, 0])


def default_bm6_history_reference_ts(now: datetime | None = None) -> datetime:
    """Return the reference timestamp for history records.

    Timezone-aware, rounded down to an even minute, because the device
    stores one record every two minutes and has no clock of its own.
    """
    current = now or datetime.now().astimezone()
    if current.tzinfo is None:
        current = current.astimezone()
    current = current.replace(second=0, microsecond=0)
    return current - timedelta(minutes=current.minute % 2)


def parse_bm6_history_items(
    payload: bytes, reference_ts: datetime
) -> list[BM6HistoryReading]:
    """Parse BM6 stored-history records from a raw payload.

    The payload is a sequence of 4-byte records, newest first. Each record
    is parsed from its hex representation (8 characters)::

        vvv ss tt p

    ``vvv`` is the voltage in centivolts, ``ss`` the state of charge in
    percent, ``tt`` the temperature in degrees Celsius and ``p`` an
    event/record-type nibble whose meaning is not yet known. All-zero
    records (unwritten slots) are skipped. The record in slot ``i`` is
    timestamped ``reference_ts - i * HISTORY_RECORD_MINUTES``; the slot
    index (not the count of returned records) is used so timestamps stay
    correct even if an unwritten slot appears mid-page.
    Note: temperature is stored as an unsigned byte; how the device
    encodes sub-zero temperatures in history records is not yet known.
    """
    readings: list[BM6HistoryReading] = []
    for index in range(len(payload) // 4):
        record = payload[index * 4 : index * 4 + 4]
        if record == bytes(4):
            continue
        raw = record.hex()
        readings.append(
            BM6HistoryReading(
                ts=reference_ts - timedelta(minutes=index * HISTORY_RECORD_MINUTES),
                voltage=int(raw[0:3], 16) / 100,
                soc=int(raw[3:5], 16),
                temperature=float(int(raw[5:7], 16)),
                event_type=int(raw[7], 16),
                raw_record=raw,
                record_index=index,
            )
        )
    return readings


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


class BM6DeviceError(RuntimeError): ...


class BM6Connector:
    """Class to manage the connection to the BM6 device."""

    def __init__(
        self,
        hass: HomeAssistant,
        address: str
    ):
        """Initialize the BM6Connector with either a HASS or a BLEDevice."""
        self.hass = hass
        self._address: str = address
        self._scanners: list[BluetoothScannerDevice] = None
        self._data: BM6Data = None
        _LOGGER.debug("Get device BM6 at %s from HASS", self._address)
        self._scanners: list[BluetoothScannerDevice] = async_scanner_devices_by_address(
                hass, 
                self._address, 
                connectable=True
        )
        if not self._scanners:
            raise BM6DeviceError(f"Bluetooth device {self._address} not found")
        self._scanners.sort(key=lambda scanner: scanner.advertisement.rssi, reverse=True)
        _LOGGER.debug("Device BM6 at %s is seen by scanners %s",
            self._address,
            [
                {
                    "scanner": scanner.scanner.name,
                    "rssi": scanner.advertisement.rssi,
                }
                for scanner in self._scanners
            ],
        )

    def _decrypt(self, data: bytearray) -> bytearray:
        """Decrypt the received data using AES."""
        cipher = AES.new(CRYPT_KEY, AES.MODE_CBC, 16 * b"\0")
        return cipher.decrypt(data)

    def _encrypt(self, data: bytearray) -> bytearray:
        """Encrypt the data to be sent using AES."""
        cipher = AES.new(CRYPT_KEY, AES.MODE_CBC, 16 * b"\0")
        return cipher.encrypt(data)

    async def _notify_callback(
            self, 
            sender: BleakGATTCharacteristic, 
            data: bytearray
    ):
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
        exceptions: list[Exception] = []
        for scanner in self._scanners:
            _LOGGER.debug("Start getting data from the BM6 at %s via scanner %s", 
                          self._address,
                          scanner.scanner.name)
            try:
                self._data = BM6Data(
                    scanner.advertisement, 
                    scanner.scanner
                )
                async with BleakClient(
                    scanner.ble_device, 
                    timeout=BLEAK_CLIENT_TIMEOUT
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
                e.add_note = f"Using scanner {scanner.scanner.name}"
                exceptions.append(e)
                _LOGGER.warning("Error while reading BM6 at %s: %s", self._address, e)
            if not self._data.RealTime:
                if len(exceptions) > 0:
                    raise BM6DeviceError(
                        f"Error while reading BM6 at {self._address}: {exceptions}"
                    ) from exceptions[0]
                else:
                    raise BM6DeviceError(
                        f"Error while reading BM6 at {self._address}"
                    )
        return self._data if self._data else None

    async def get_history(self, page_count: int = 1) -> list[BM6HistoryReading]:
        """Download stored history records from the BM6 device.

        Opens a fresh BLE connection (the device accepts only one
        connection at a time), wakes the device with the live poll
        command, then sends the history request and collects the
        notification frames until the trailer marker is seen. Returns an
        empty list when the device reports no history or cannot be
        reached; errors are logged but not raised, so a failed history
        download never breaks a live poll.
        """
        exceptions: list[Exception] = []
        for scanner in self._scanners:
            _LOGGER.debug(
                "Start getting history from the BM6 at %s via scanner %s",
                self._address,
                scanner.scanner.name,
            )
            try:
                return await self._get_history_via_scanner(scanner, page_count)
            except Exception as err:  # noqa: BLE001 - history is best-effort
                exceptions.append(err)
                _LOGGER.warning(
                    "Error while reading history from BM6 at %s via scanner %s: %s",
                    self._address,
                    scanner.scanner.name,
                    err,
                )
        if exceptions:
            _LOGGER.warning(
                "Could not download history from BM6 at %s: %s",
                self._address,
                exceptions[-1],
            )
        return []

    async def _get_history_via_scanner(
        self, scanner: BluetoothScannerDevice, page_count: int
    ) -> list[BM6HistoryReading]:
        """Download history through one scanner. Raises on BLE failure."""
        packet_queue: asyncio.Queue[bytes] = asyncio.Queue()

        def _history_notify(
            _sender: BleakGATTCharacteristic, data: bytearray
        ) -> None:
            packet_queue.put_nowait(bytes(data))

        async with BleakClient(
            scanner.ble_device, timeout=BLEAK_CLIENT_TIMEOUT
        ) as client:
            await client.start_notify(CHARACTERISTIC_UUID_NOTIFY, _history_notify)
            try:
                # Wake the device with the live poll, as in get_data().
                await client.write_gatt_char(
                    CHARACTERISTIC_UUID_WRITE,
                    self._encrypt(bytearray.fromhex(GATT_DATA_REALTIME)),
                    response=True,
                )
                await self._drain_notification_queue(packet_queue)
                # Request the stored history pages.
                await client.write_gatt_char(
                    CHARACTERISTIC_UUID_WRITE,
                    self._encrypt(bytearray(encode_bm6_history_request(page_count))),
                    response=True,
                )
                payload = await self._collect_history_payload(packet_queue, page_count)
            finally:
                await client.stop_notify(CHARACTERISTIC_UUID_NOTIFY)
        reference_ts = default_bm6_history_reference_ts()
        readings = parse_bm6_history_items(payload, reference_ts)
        _LOGGER.debug(
            "Downloaded %s history records from BM6 at %s (payload %s bytes, first 64 hex: %s)",
            len(readings),
            self._address,
            len(payload),
            payload[:32].hex(),
        )
        if readings:
            _LOGGER.debug(
                "BM6 history first 3 records: %s",
                [
                    (r.ts.isoformat(), r.voltage, r.soc, r.temperature)
                    for r in readings[:3]
                ],
            )
        return readings

    @staticmethod
    async def _drain_notification_queue(
        packet_queue: asyncio.Queue[bytes],
    ) -> None:
        """Drain packets already queued (e.g. live-poll wake responses)."""
        while True:
            try:
                await asyncio.wait_for(packet_queue.get(), timeout=0.3)
            except TimeoutError:
                return

    async def _collect_history_payload(
        self, packet_queue: asyncio.Queue[bytes], page_count: int
    ) -> bytes:
        """Collect history frames until the trailer marker or a timeout.

        Decrypted notification frames are 16 bytes each. Frames starting
        with the history prefix (d15505), the live prefix (d15507) or the
        start marker (fffffe) are skipped; the remaining frames are
        accumulated once the header has been seen, until a frame contains
        the trailer marker (fffefe), in which case the bytes before the
        marker are kept and collection stops. Collection also stops on an
        idle timeout or when the overall deadline passes; whatever was
        collected so far is returned (possibly empty).
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + HISTORY_TIMEOUT_PER_PAGE * page_count
        payload = b""
        seen_header = False
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                encrypted = await asyncio.wait_for(
                    packet_queue.get(), timeout=min(HISTORY_IDLE_TIMEOUT, remaining)
                )
            except TimeoutError:
                break
            # Decrypt each 16-byte block independently (fresh zero IV per
            # block), as BMGateway does. Decrypting a multi-block
            # notification as one CBC operation would chain the blocks
            # and corrupt every frame after the first.
            for offset in range(0, len(encrypted), 16):
                block = bytes(encrypted[offset : offset + 16])
                if len(block) != 16:
                    continue
                frame = bytes(self._decrypt(bytearray(block)))
                frame_hex = frame.hex()
                if frame_hex.startswith(GATT_NOTIFY_HISTORY_PREFIX):
                    seen_header = True
                    continue
                if frame_hex.startswith(GATT_NOTIFY_REALTIME_PREFIX):
                    continue
                if frame_hex.startswith(HISTORY_START_MARKER):
                    seen_header = True
                    continue
                if not seen_header:
                    continue
                trailer_at = frame.find(bytes.fromhex(HISTORY_TRAILER))
                if trailer_at >= 0:
                    payload += frame[:trailer_at]
                    return payload
                payload += frame
        return payload

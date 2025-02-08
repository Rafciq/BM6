"""Constants for the BM6 integration."""

from __future__ import annotations

from homeassistant.const import Platform, UnitOfTemperature

NAME = "Battery Monitor BM6"
DOMAIN = "bm6"
VERSION = "1.0.3"
MIN_REQUIRED_HA_VERSION = "2025.1.1"

COMPONENT = "component"
PLATFORMS: set[Platform] = {Platform.SENSOR}

# BM6 Bluetooth Device parameters
# Characteristic UUIDs for the BM6 device
CHARACTERISTIC_UUID_WRITE = "FFF3"
# Characteristic UUID for notifications from the BM6 device
CHARACTERISTIC_UUID_NOTIFY = "FFF4"
# GATT Command to get real time data from the BM6 device
GATT_DATA_REALTIME = "d1550700000000000000000000000000"
# GATT Notify incoming real time data prefix from the BM6 device
GATT_NOTIFY_REALTIME_PREFIX = "d15507"
# GATT Command to get version info from the BM6 device
GATT_DATA_VERSION = "d1550100000000000000000000000000"
# GATT Notify incoming version info prefix from the BM6 device
GATT_NOTIFY_VERSION_PREFIX = "d15501"


# Encryption key for the BM6 device
CRYPT_KEY = bytearray(
    [108, 101, 97, 103, 101, 110, 100, 255, 254, 48, 49, 48, 48, 48, 48, 57]
)
# Timeout for the Bleak client
BLEAK_CLIENT_TIMEOUT = 10  # Timeout

# Configuration keys
CONF_DEVICE_ADDRESS = "device_address"
CONF_BATTERY_VOLTAGE = "battery_voltage"
CONF_BATTERY_TYPE = "battery_type"
CONF_CUSTOM_DVR_MIN = "custom_dvr_min"
CONF_CUSTOM_DVR_MAX = "custom_dvr_max"
CONF_CUSTOM_CVR_MIN = "custom_cvr_min"
CONF_CUSTOM_CVR_MAX = "custom_cvr_max"
CONF_CUSTOM_SOD_MIN = "custom_sod_min"
CONF_CUSTOM_SOD_MAX = "custom_sod_max"
CONF_CUSTOM_SOC_MIN = "custom_soc_min"
CONF_CUSTOM_SOC_MAX = "custom_soc_max"
CONF_STATE_ALGORITHM = "state_algorithm"
CONF_UPDATE_INTERVAL = "update_interval"
CONF_VOLTAGE_OFFSET = "voltage_offset"
CONF_TEMPERATURE_OFFSET = "temperature_offset"
CONF_TEMPERATURE_UNIT = "temperature_unit"
CONF_BLUETOOTH_SCANNER = "bluetooth_scanner"

# Default values
DEFAULT_UPDATE_INTERVAL = 60
DEFAULT_VOLTAGE_OFFSET = 0.0
DEFAULT_TEMPERATURE_OFFSET = 0.0
DEFAULT_TEMPERATURE_UNIT = UnitOfTemperature.CELSIUS

MIN_UPDATE_INTERVAL = 10

# Error constants
ERROR_MAX_LESS_THAN_MIN = "max_less_than_min"
ERROR_CVR_LESS_THAN_DVR = "cvr_less_than_dvr"
ERROR_SOC_LESS_THAN_SOD = "soc_less_than_sod"

# Translation keys
TRANSLATION_KEY_VOLTAGE = "voltage"
TRANSLATION_KEY_TEMPERATURE = "temperature"
TRANSLATION_KEY_PERCENTAGE = "percentage"
TRANSLATION_KEY_STATE = "state"
TRANSLATION_KEY_RSSI = "rssi"
TRANSLATION_KEY_DEVICE_PERCENTAGE = "device_percentage"
TRANSLATION_KEY_DEVICE_STATE = "device_state"
TRANSLATION_KEY_RAPID_ACCELERATION = "rapid_acceleration"
TRANSLATION_KEY_RAPID_DECELERATION = "rapid_deceleration"
TRANSLATION_KEY_BATTERY_STATE_ALGORITHM = "battery_state_algorithm"
TRANSLATION_KEY_BATTERY_VOLTAGE = "battery_voltage"
TRANSLATION_KEY_BATTERY_TYPE = "battery_type"
TRANSLATION_KEY_BLUETOOTH_SCANNER = "bluetooth_scanner"

# Keys for DataUpdateCoordinator
KEY_VOLTAGE_DEVICE = "voltage_device"
KEY_VOLTAGE_CORRECTED = "voltage_corrected"
KEY_TEMPERATURE_DEVICE = "temperature_device"
KEY_TEMPERATURE_UNIT = "temperature_unit"
KEY_TEMPERATURE_CORRECTED = "temperature_corrected"
KEY_PERCENTAGE = "percentage"
KEY_STATE = "state"
KEY_RSSI = "rssi"
KEY_STATE_ALGORITHM = "state_algorithm"
KEY_DVR_MIN = "dvr_min"
KEY_DVR_MAX = "dvr_max"
KEY_CVR_MIN = "cvr_min"
KEY_CVR_MAX = "cvr_max"
KEY_SOD_MIN = "sod_min"
KEY_SOD_MAX = "sod_max"
KEY_SOC_MIN = "soc_min"
KEY_SOC_MAX = "soc_max"
KEY_DEVICE_PERCENTAGE = "device_percentage"
KEY_DEVICE_STATE = "device_state"
KEY_RAPID_ACCELERATION = "rapid_acceleration"
KEY_RAPID_DECELERATION = "rapid_deceleration"
KEY_BLUETOOTH_SCANNER = "bluetooth_scanner"

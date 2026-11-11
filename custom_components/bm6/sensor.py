"""Sensor platform for BM6 integration."""

# TODO: Acceleration and deceleration sensors are not implemented yet.

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional, final
import logging

from homeassistant.core import HomeAssistant
from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.const import (
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    UnitOfElectricPotential,
    UnitOfTemperature,
    PERCENTAGE,
)

from .const import (
    DOMAIN,
    KEY_BLUETOOTH_SCANNER,
    KEY_CVR_MAX,
    KEY_CVR_MIN,
    KEY_DVR_MAX,
    KEY_DVR_MIN,
    KEY_STATE_ALGORITHM,
    KEY_SOC_MAX,
    KEY_SOC_MIN,
    KEY_SOD_MAX,
    KEY_SOD_MIN,
    KEY_VOLTAGE_CORRECTED,
    KEY_VOLTAGE_DEVICE,
    KEY_TEMPERATURE_CORRECTED,
    KEY_TEMPERATURE_DEVICE,
    KEY_TEMPERATURE_UNIT,
    KEY_PERCENTAGE,
    KEY_STATE,
    KEY_RSSI,
    KEY_DEVICE_PERCENTAGE,
    KEY_DEVICE_STATE,
    KEY_RAPID_ACCELERATION,
    KEY_RAPID_DECELERATION,
    TRANSLATION_KEY_BLUETOOTH_SCANNER,
    TRANSLATION_KEY_VOLTAGE,
    TRANSLATION_KEY_TEMPERATURE,
    TRANSLATION_KEY_PERCENTAGE,
    TRANSLATION_KEY_STATE,
    TRANSLATION_KEY_RSSI,
    TRANSLATION_KEY_DEVICE_PERCENTAGE,
    TRANSLATION_KEY_DEVICE_STATE,
    TRANSLATION_KEY_RAPID_ACCELERATION,
    TRANSLATION_KEY_RAPID_DECELERATION,
)
from .bm6_connect import BM6RealTimeState
from .battery import Battery, BatteryState

if TYPE_CHECKING:
    from . import BM6ConfigEntry
    from .coordinator import BM6DataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: BM6ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up BM6 sensors based on a config entry."""
    coordinator = config_entry.runtime_data.coordinator
    await coordinator.async_refresh()

    entities = [
        BM6VoltageSensor(coordinator),
        BM6TemperatureSensor(coordinator),
        BM6PercentageSensor(coordinator),
        BM6StateSensor(coordinator),
        BM6RssiSensor(coordinator),
        BM6DevicePercentageSensor(coordinator),
        BM6DeviceStateSensor(coordinator),
        BM6RapidAccelerationSensor(coordinator),
        BM6RapidDecelerationSensor(coordinator),
        BM6BluetoothScannerSensor(coordinator),
    ]
    async_add_entities(entities)


class BM6SensorEntity(CoordinatorEntity, SensorEntity):
    """Base class for BM6 sensor entities."""

    def __init__(self, coordinator: BM6DataUpdateCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_extra_state_attributes = {}
        self._device_address = coordinator.device_address

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_address)},
            name=f"BM6 {self._device_address}",
            manufacturer="BM6",
            model="Battery Monitor BM6",
        )

    @property
    def unique_id(self) -> str:
        """Return a unique ID for the sensor."""
        return f"{DOMAIN}_{self._attr_translation_key.lower()}_{self._device_address}"


@final
class BM6VoltageSensor(BM6SensorEntity):
    """Voltage sensor."""

    _attr_has_entity_name = True
    _attr_translation_key = TRANSLATION_KEY_VOLTAGE
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:flash"
    _attr_suggested_display_precision = 2

    @property
    def native_value(self):
        data = self.coordinator.data or {}
        raw = (
            data.get(KEY_VOLTAGE_CORRECTED)
            or data.get(KEY_VOLTAGE_DEVICE)
            or data.get("voltage")
            or data.get("Voltage")
        )
        if raw is None:
            return None
        try:
            return round(float(raw), 2)
        except (TypeError, ValueError):
            return None



@final
class BM6TemperatureSensor(BM6SensorEntity):
    """Temperature sensor."""

    _attr_has_entity_name = True
    _attr_translation_key = TRANSLATION_KEY_TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:thermometer"

    @property
    def state(self):
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(KEY_TEMPERATURE_CORRECTED)

    @property
    def native_value(self):
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(KEY_TEMPERATURE_DEVICE)

    @property
    def unit_of_measurement(self):
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(KEY_TEMPERATURE_UNIT)

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        return {
            key: self.coordinator.data.get(key)
            for key in [
                KEY_TEMPERATURE_DEVICE,
                KEY_TEMPERATURE_CORRECTED,
                KEY_TEMPERATURE_UNIT,
            ]
        }


@final
class BM6PercentageSensor(BM6SensorEntity):
    """Percentage sensor."""

    _attr_has_entity_name = True
    _attr_translation_key = TRANSLATION_KEY_PERCENTAGE
    _attr_unit_of_measurement = PERCENTAGE
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def state(self) -> Optional[int]:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(KEY_PERCENTAGE)

    @property
    def icon(self) -> str:
        return Battery.percent_to_icon(self.state)

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        return {
            key: self.coordinator.data.get(key)
            for key in [
                KEY_VOLTAGE_CORRECTED,
                KEY_STATE_ALGORITHM,
                KEY_DVR_MIN,
                KEY_DVR_MAX,
                KEY_CVR_MIN,
                KEY_CVR_MAX,
                KEY_SOD_MIN,
                KEY_SOD_MAX,
                KEY_SOC_MIN,
                KEY_SOC_MAX,
            ]
        }


@final
class BM6StateSensor(BM6SensorEntity):
    """State sensor."""

    _attr_has_entity_name = True
    _attr_translation_key = TRANSLATION_KEY_STATE

    @property
    def native_value(self) -> str:
        """Return the state of the sensor."""
        if self.coordinator.data is None:
            return BatteryState.Unknown.value
        state_str = self.coordinator.data.get(KEY_STATE)
        if state_str in BatteryState._value2member_map_:
            return state_str
        return BatteryState.Unknown.value

    @property
    def icon(self) -> str:
        """Return the icon of the sensor based on the BatteryState."""
        state = self.native_value
        if state == BatteryState.Unknown.value:
            return "mdi:battery-unknown"
        elif state == BatteryState.UnderVoltage.value:
            return "mdi:alert-octagon"
        elif state == BatteryState.Charging.value:
            return "mdi:battery-charging"
        elif state == BatteryState.Idle.value:
            return "mdi:battery-check"
        elif state == BatteryState.Discharging.value:
            return "mdi:battery-arrow-down"
        elif state == BatteryState.OverVoltage.value:
            return "mdi:flash-triangle"
        else:
            return "mdi:battery-unknown"


@final
class BM6RssiSensor(BM6SensorEntity):
    """Signal Strength RSSI sensor."""

    _attr_has_entity_name = True
    _attr_translation_key = TRANSLATION_KEY_RSSI
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:wifi"
    _attr_entity_registry_enabled_default = False

    @property
    def native_value(self) -> Optional[int]:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(KEY_RSSI)


@final
class BM6DevicePercentageSensor(BM6SensorEntity):
    """Device Percentage sensor."""

    _attr_has_entity_name = True
    _attr_translation_key = TRANSLATION_KEY_DEVICE_PERCENTAGE
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    @property
    def native_value(self) -> Optional[int]:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(KEY_DEVICE_PERCENTAGE)

    @property
    def icon(self) -> str:
        return Battery.percent_to_icon(self.native_value)


@final
class BM6DeviceStateSensor(BM6SensorEntity):
    """Device State sensor."""

    _attr_has_entity_name = True
    _attr_translation_key = TRANSLATION_KEY_DEVICE_STATE
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    @property
    def native_value(self) -> Optional[int]:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(KEY_DEVICE_STATE)

    @property
    def state(self) -> str:
        if self.native_value is not None:
            try:
                device_state: BM6RealTimeState = BM6RealTimeState(self.native_value)
                if device_state == BM6RealTimeState.BatteryOk:
                    return BatteryState.Ok.value
                elif device_state == BM6RealTimeState.LowVoltage:
                    return BatteryState.LowVoltage.value
                elif device_state == BM6RealTimeState.Charging:
                    return BatteryState.Charging.value
                elif isinstance(device_state, int):
                    return str(device_state)
            except ValueError:
                if isinstance(device_state, int):
                    return str(device_state)
        return BatteryState.Unknown.value

    @property
    def icon(self) -> str:
        if self.native_value is not None:
            try:
                device_state: BM6RealTimeState = BM6RealTimeState(self.native_value)
                if device_state == BM6RealTimeState.BatteryOk:
                    return "mdi:battery-check"
                elif device_state == BM6RealTimeState.LowVoltage:
                    return "mdi:alert-octagon"
                elif device_state == BM6RealTimeState.Charging:
                    return "mdi:battery-charging"
            except ValueError:
                pass
        return "mdi:battery-unknown"


@final
class BM6BluetoothScannerSensor(BM6SensorEntity):
    """Bluetooth Scanner sensor."""

    _attr_has_entity_name = True
    _attr_translation_key = TRANSLATION_KEY_BLUETOOTH_SCANNER
    _attr_icon = "mdi:bluetooth"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    @property
    def native_value(self) -> Optional[str]:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(KEY_BLUETOOTH_SCANNER)


@final
class BM6RapidAccelerationSensor(BM6SensorEntity):
    """Rapid Acceleration sensor."""

    _attr_has_entity_name = True
    _attr_translation_key = TRANSLATION_KEY_RAPID_ACCELERATION
    # _attr_unit_of_measurement = "mV/s"
    # _attr_device_class = SensorDeviceClass.ACCELERATION
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    @property
    def native_value(self) -> Optional[int]:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(KEY_RAPID_ACCELERATION)


@final
class BM6RapidDecelerationSensor(BM6SensorEntity):
    """Rapid Deceleration sensor."""

    _attr_has_entity_name = True
    _attr_translation_key = TRANSLATION_KEY_RAPID_DECELERATION
    # _attr_unit_of_measurement = "mV/s"
    # _attr_device_class = SensorDeviceClass.ACCELERATION
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    @property
    def native_value(self) -> Optional[int]:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(KEY_RAPID_DECELERATION)

"""
Config flow for BM6 integration.

This module handles the configuration flow for the BM6 integration,
including loading Bluetooth configuration, discovering devices, and
validating user input.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Any
import json
import logging
from bluetooth_adapters import AdapterDetails
from habluetooth import BaseHaScanner
import voluptuous as vol
import aiofiles

from homeassistant.core import HomeAssistant, callback
from homeassistant.config_entries import ConfigFlow, OptionsFlow
from homeassistant.data_entry_flow import FlowResult
from homeassistant.components.bluetooth.api import _get_manager
from homeassistant.components.bluetooth.manager import HomeAssistantBluetoothManager
from homeassistant.components.bluetooth import async_discovered_service_info
from homeassistant.const import UnitOfTemperature
from homeassistant.helpers import selector
from home_assistant_bluetooth import BluetoothServiceInfoBleak

from .const import (
    CONF_BLUETOOTH_SCANNER,
    CONF_TEMPERATURE_OFFSET,
    CONF_TEMPERATURE_UNIT,
    CONF_VOLTAGE_OFFSET,
    DEFAULT_TEMPERATURE_OFFSET,
    DEFAULT_TEMPERATURE_UNIT,
    DEFAULT_VOLTAGE_OFFSET,
    DOMAIN,
    CONF_DEVICE_ADDRESS,
    CONF_CUSTOM_DVR_MIN,
    CONF_CUSTOM_DVR_MAX,
    CONF_CUSTOM_CVR_MIN,
    CONF_CUSTOM_CVR_MAX,
    CONF_CUSTOM_SOD_MIN,
    CONF_CUSTOM_SOD_MAX,
    CONF_CUSTOM_SOC_MIN,
    CONF_CUSTOM_SOC_MAX,
    CONF_BATTERY_VOLTAGE,
    CONF_BATTERY_TYPE,
    CONF_STATE_ALGORITHM,
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    MIN_UPDATE_INTERVAL,
    ERROR_MAX_LESS_THAN_MIN,
    ERROR_CVR_LESS_THAN_DVR,
    ERROR_SOC_LESS_THAN_SOD,
    TRANSLATION_KEY_BATTERY_STATE_ALGORITHM,
    TRANSLATION_KEY_BATTERY_TYPE,
    TRANSLATION_KEY_BATTERY_VOLTAGE,
    TRANSLATION_KEY_BLUETOOTH_SCANNER,
)
from .battery import (
    battery_voltage_ranges,
    BatteryType,
    BatteryVoltage,
    BatteryStateAlgorithm,
    Battery,
)


if TYPE_CHECKING:
    from . import BM6ConfigEntry

_LOGGER = logging.getLogger(__name__)

# Default values
DEFAULT_BATTERY_VOLTAGE = BatteryVoltage.V12
DEFAULT_BATTERY_TYPE = BatteryType.AGM
DEFAULT_STATE_ALGORITHM = BatteryStateAlgorithm.SoC_SoD


class ConfigPage(Enum):
    """Config page for BM6 config flow."""

    MAIN = "main"
    CUSTOM_CALCULATION = "custom_calculation"
    CUSTOM_VOLTAGE = "custom_voltage"


async def build_schema(
    hass: HomeAssistant,
    data: dict[str, Any],
    devices: dict[str, Any] | None,
    is_options_flow: bool,
    config_page: ConfigPage,
) -> vol.Schema:
    """Build the schema for both config and options flow, handling custom voltage."""
    schema_fields = {}
    if config_page == ConfigPage.MAIN:
        # manager: HomeAssistantBluetoothManager = _get_manager(hass)
        # scaners: set[BaseHaScanner] = manager._connectable_scanners | manager._non_connectable_scanners
        # _LOGGER.debug("Bluetooth scanners: %s", scaners)
        if not is_options_flow:
            schema_fields[vol.Required(CONF_DEVICE_ADDRESS)] = vol.In(devices)
        schema_fields.update(
            {
                vol.Required(
                    CONF_STATE_ALGORITHM,
                    default=data.get(CONF_STATE_ALGORITHM, DEFAULT_STATE_ALGORITHM.value),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[option.value for option in BatteryStateAlgorithm],
                        translation_key=TRANSLATION_KEY_BATTERY_STATE_ALGORITHM
                )),
                vol.Required(
                    CONF_UPDATE_INTERVAL,
                    default=data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
                ): vol.All(vol.Coerce(int), vol.Range(min=MIN_UPDATE_INTERVAL)),
                vol.Required(
                    CONF_VOLTAGE_OFFSET,
                    default=data.get(CONF_VOLTAGE_OFFSET, DEFAULT_VOLTAGE_OFFSET),
                ): vol.All(vol.Coerce(float)),
                vol.Required(
                    CONF_TEMPERATURE_OFFSET,
                    default=data.get(
                        CONF_TEMPERATURE_OFFSET, DEFAULT_TEMPERATURE_OFFSET
                    ),
                ): vol.All(vol.Coerce(float)),
                vol.Required(
                    CONF_TEMPERATURE_UNIT,
                    default=data.get(CONF_TEMPERATURE_UNIT, DEFAULT_TEMPERATURE_UNIT),
                ): vol.In([UnitOfTemperature.CELSIUS, UnitOfTemperature.FAHRENHEIT]),
                # vol.Required(
                #     CONF_BLUETOOTH_GATEWAY,
                #     default=data.get(CONF_BLUETOOTH_GATEWAY, gateway_choices),
                # ): vol.All(selector.MultiSelectSelector(
                #             selector.MultiSelectSelectorConfig(
                #                 options=gateway_choices,
                #                 translation_key=TRANSLATION_KEY_BLUETOOTH_GATEWAY
                #             )),vol.Length(min=1)),
            }
        )
    elif config_page == ConfigPage.CUSTOM_CALCULATION:
        schema_fields.update(
            {
                vol.Required(
                    CONF_BATTERY_VOLTAGE,
                    default=data.get(
                        CONF_BATTERY_VOLTAGE, DEFAULT_BATTERY_VOLTAGE.value
                    ),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[option.value for option in BatteryVoltage],
                        translation_key=TRANSLATION_KEY_BATTERY_VOLTAGE
                )),
                vol.Required(
                    CONF_BATTERY_TYPE,
                    default=data.get(CONF_BATTERY_TYPE, DEFAULT_BATTERY_TYPE.value),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[option.value for option in BatteryType],
                        translation_key=TRANSLATION_KEY_BATTERY_TYPE
                )),
            }
        )
    elif config_page == ConfigPage.CUSTOM_VOLTAGE:
        default_range = battery_voltage_ranges.get(
            (
                BatteryType(data.get(CONF_BATTERY_TYPE, DEFAULT_BATTERY_TYPE.value)),
                BatteryVoltage(
                    data.get(CONF_BATTERY_VOLTAGE, DEFAULT_BATTERY_VOLTAGE.value)
                ),
            ),
            battery_voltage_ranges[(DEFAULT_BATTERY_TYPE, DEFAULT_BATTERY_VOLTAGE)],
        )
        schema_fields.update(
            {
                vol.Required(
                    CONF_CUSTOM_DVR_MIN,
                    default=data.get(CONF_CUSTOM_DVR_MIN, default_range.dvr.min),
                ): vol.All(vol.Coerce(float)),
                vol.Required(
                    CONF_CUSTOM_DVR_MAX,
                    default=data.get(CONF_CUSTOM_DVR_MAX, default_range.dvr.max),
                ): vol.All(vol.Coerce(float)),
                vol.Required(
                    CONF_CUSTOM_CVR_MIN,
                    default=data.get(CONF_CUSTOM_CVR_MIN, default_range.cvr.min),
                ): vol.All(vol.Coerce(float)),
                vol.Required(
                    CONF_CUSTOM_CVR_MAX,
                    default=data.get(CONF_CUSTOM_CVR_MAX, default_range.cvr.max),
                ): vol.All(vol.Coerce(float)),
                vol.Required(
                    CONF_CUSTOM_SOD_MIN,
                    default=data.get(CONF_CUSTOM_SOD_MIN, default_range.sod.min),
                ): vol.All(vol.Coerce(float)),
                vol.Required(
                    CONF_CUSTOM_SOD_MAX,
                    default=data.get(CONF_CUSTOM_SOD_MAX, default_range.sod.max),
                ): vol.All(vol.Coerce(float)),
                vol.Required(
                    CONF_CUSTOM_SOC_MIN,
                    default=data.get(CONF_CUSTOM_SOC_MIN, default_range.soc.min),
                ): vol.All(vol.Coerce(float)),
                vol.Required(
                    CONF_CUSTOM_SOC_MAX,
                    default=data.get(CONF_CUSTOM_SOC_MAX, default_range.soc.max),
                ): vol.All(vol.Coerce(float)),
            }
        )
    return vol.Schema(schema_fields)


def validate_custom_voltage(
    data: dict[str, Any], errors: dict[str, str]
) -> dict[str, str]:
    """Validate custom voltage settings."""
    battery_info = Battery.config_to_battery_info(data)
    if battery_info.type == BatteryType.Custom:
        if not battery_info.custom.dvr.is_valid:
            errors[CONF_CUSTOM_DVR_MAX] = ERROR_MAX_LESS_THAN_MIN
        if not battery_info.custom.cvr.is_valid:
            errors[CONF_CUSTOM_CVR_MAX] = ERROR_MAX_LESS_THAN_MIN
        if not battery_info.custom.sod.is_valid:
            errors[CONF_CUSTOM_SOD_MAX] = ERROR_MAX_LESS_THAN_MIN
        if not battery_info.custom.soc.is_valid:
            errors[CONF_CUSTOM_SOC_MAX] = ERROR_MAX_LESS_THAN_MIN
        if not battery_info.custom.is_dvr_less_cvr:
            errors[CONF_CUSTOM_DVR_MAX] = ERROR_CVR_LESS_THAN_DVR
        if not battery_info.custom.is_sod_less_soc:
            errors[CONF_CUSTOM_SOD_MAX] = ERROR_SOC_LESS_THAN_SOD
    return errors


class BM6ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for BM6."""

    VERSION = 1

    def __init__(self):
        """Initialize the config flow."""
        self._bluetooth_config: dict[str, Any] = None
        self._data: dict[str, str] = {}

    def _get_name(self, service_info: BluetoothServiceInfoBleak) -> str:
        """Get the name of the device."""
        return f"{service_info.name} {service_info.address}" if service_info.name else f"BM6 {service_info.address}"
    
    async def bluetooth_config(self) -> dict[str, Any]:
        """Return the loaded Bluetooth configuration."""
        if self._bluetooth_config:
            return self._bluetooth_config
        _LOGGER.debug("Loading Bluetooth configuration from manifest.json")
        try:
            async with aiofiles.open(
                f"custom_components/{DOMAIN}/manifest.json", encoding="utf-8"
            ) as f:
                manifest_data = await f.read()
            manifest = json.loads(manifest_data)
            self._bluetooth_config = manifest["bluetooth"]
            _LOGGER.debug("Bluetooth configuration loaded: %s", self._bluetooth_config)
        except FileNotFoundError:
            _LOGGER.error("Bluetooth configuration file not found.")
            raise
        except json.JSONDecodeError:
            _LOGGER.error("Error decoding Bluetooth configuration file.")
            raise
        except KeyError:
            _LOGGER.error("Bluetooth configuration is missing required keys.")
            raise
        except Exception as e:
            _LOGGER.error("Unexpected error loading Bluetooth configuration: %s", e)
            raise
        return self._bluetooth_config

    async def _get_devices(self) -> dict[str, Any]:
        """Get a list of devices filtered by service UUID."""
        _LOGGER.debug("Discovering Bluetooth devices")
        bluetooth_config = await self.bluetooth_config()
        devices: dict[str, Any] = {}
        all_devices: str = ""
        valid_devices: str = ""
        current_addresses = self._async_current_ids()
        for connectable in list(set(item["connectable"] for item in bluetooth_config)):
            _LOGGER.debug(
                "Discovering Bluetooth devices with connectable: %s", connectable
            )
            for service_info in async_discovered_service_info(
                self.hass, connectable=connectable
            ):
                if _LOGGER.isEnabledFor(logging.DEBUG):
                    all_devices += f"\n{self.format_device_info(service_info)}"
                if (
                    service_info.address in current_addresses
                    or service_info.address in devices
                ):
                    continue
                if await self._is_valid_device(service_info):
                    devices[service_info.address] = self._get_name(service_info)
                    valid_devices += f"\n{self.format_device_info(service_info)}"
        _LOGGER.debug("All Bluetooth devices:\n%s", all_devices)
        _LOGGER.info("BM6 Bluetooth devices:\n%s", valid_devices)
        _LOGGER.debug("Discovered devices: %s", devices)
        return devices

    async def _is_valid_device(self, service_info: BluetoothServiceInfoBleak) -> bool:
        """Check if the device matches the required UUIDs and is not already installed."""
        bluetooth_config = await self.bluetooth_config()
        _LOGGER.debug(
            "Checking if device %s exist in %s", service_info, bluetooth_config
        )
        is_valid: bool = any(
            any(item.get("service_data_uuid") == uuid for item in bluetooth_config)
            for uuid in service_info.service_uuids
        ) or any(
            item.get("manufacturer_id")
            and item.get("manufacturer_data_start")
            and service_info.manufacturer_data.get(item.get("manufacturer_id"))
            and bytes(item["manufacturer_data_start"])
            == service_info.manufacturer_data.get(item["manufacturer_id"])[
                : len(item["manufacturer_data_start"])
            ]
            for item in bluetooth_config
        )
        _LOGGER.debug("Device %s is valid: %s", service_info.address, is_valid)
        return is_valid

    @staticmethod
    def format_device_info(service_info: BluetoothServiceInfoBleak):
        """Format the device information for logging."""
        return (
            f"\nMAC:{service_info.address} Name:'{service_info.name}'\n"
            f"\tUUID:{','.join(f'({uuid})' for uuid in service_info.service_uuids)}\n"
            f"\tManufacturer:{','.join(f'({item})' for item in service_info.manufacturer_data.items())}\n"
            f"\tData:{','.join(f'({item})' for item in service_info.service_data.items())}\n"
        )

    async def async_step_bluetooth(
        self, 
        discovery_info: BluetoothServiceInfoBleak
    ) -> FlowResult:
        """Handle Bluetooth discovery."""
        _LOGGER.debug("Starting Bluetooth step with discovery info: %s", discovery_info)
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        self.context["title_placeholders"] = {"name": self._get_name(discovery_info)}
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm discovery."""
        _LOGGER.debug("Starting Bluetooth confirm step with input: %s", user_input)
        self._set_confirm_only()
        schema = await build_schema(
            self.hass,
            self._data,
            [self.context["unique_id"]],
            is_options_flow=False,
            config_page=ConfigPage.MAIN,
        )
        _LOGGER.debug("Showing user form with schema: %s", schema)
        return self.async_show_form(
            step_id="user",
            data_schema=schema,
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        _LOGGER.debug("Starting user step with input: %s", user_input)
        errors: dict[str, str] = {}
        if user_input is not None:
            self._data.update(user_input)
            _LOGGER.debug("Updated data: %s", self._data)
            device_address = user_input[CONF_DEVICE_ADDRESS]
            await self.async_set_unique_id(device_address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            #            self.context["title_placeholders"] = {"name": discovery.title}
            if (
                BatteryStateAlgorithm(user_input[CONF_STATE_ALGORITHM])
                != BatteryStateAlgorithm.By_Device
            ):
                return await self.async_step_custom_calculation()
            else:
                return self.async_create_entry(
                    title=f"BM6 {user_input[CONF_DEVICE_ADDRESS]}",
                    description="BM6 battery monitor",
                    data=self._data,
                )
        if self.context.get("unique_id"):
            self._data[CONF_DEVICE_ADDRESS] = self.context["unique_id"]
            devices = {self.context["unique_id"]: self.context["title_placeholders"]["name"]}
        else:
            _LOGGER.debug("Getting devices...")
            devices = await self._get_devices()
            if not devices:
                _LOGGER.warning("No devices found")
                return self.async_abort(reason="no_devices_found")
        schema = await build_schema(
            self.hass,
            self._data,
            devices,
            is_options_flow=False,
            config_page=ConfigPage.MAIN,
        )
        _LOGGER.debug("Showing user form with schema: %s", schema)
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_custom_calculation(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the custom calculation step."""
        _LOGGER.debug("Starting custom calculation step with input: %s", user_input)
        errors: dict[str, str] = {}
        if user_input is not None:
            self._data.update(user_input)
            _LOGGER.debug("Updated data: %s", self._data)
            if BatteryType(user_input[CONF_BATTERY_TYPE]) == BatteryType.Custom:
                return await self.async_step_custom_voltage()
            else:
                return self.async_create_entry(
                    title=f"BM6 {self._data[CONF_DEVICE_ADDRESS]}",
                    description="BM6 battery monitor",
                    data=self._data,
                )
        schema = await build_schema(
            self.hass,
            self._data,
            devices=None,
            is_options_flow=False,
            config_page=ConfigPage.CUSTOM_CALCULATION,
        )
        _LOGGER.debug("Showing custom calculation form with schema: %s", schema)
        return self.async_show_form(
            step_id="custom_calculation", data_schema=schema, errors=errors
        )

    async def async_step_custom_voltage(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the custom voltage step."""
        _LOGGER.debug("Starting custom voltage step with input: %s", user_input)
        errors: dict[str, str] = {}
        if user_input is not None:
            self._data.update(user_input)
            _LOGGER.debug("Updated data: %s", self._data)
            errors = validate_custom_voltage(self._data, errors)
            if not errors:
                return self.async_create_entry(
                    title=f"BM6 {self._data[CONF_DEVICE_ADDRESS]}",
                    description="BM6 battery monitor",
                    data=self._data,
                )
        schema = await build_schema(
            self.hass,
            self._data,
            devices=None,
            is_options_flow=False,
            config_page=ConfigPage.CUSTOM_VOLTAGE,
        )
        _LOGGER.debug("Showing custom voltage form with schema: %s", schema)
        return self.async_show_form(
            step_id="custom_voltage", data_schema=schema, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return BM6OptionsFlow(config_entry)


class BM6OptionsFlow(OptionsFlow):
    """Handle BM6 options."""

    VERSION = 1

    def __init__(self, config_entry: BM6ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry
        self._data: dict[str, str] = dict(config_entry.data)

    async def async_step_user(self, _user_input=None):
        return await self.async_step_init()

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle options flow."""
        _LOGGER.debug("Starting options flow init step with input: %s", user_input)
        errors: dict[str, str] = {}
        if user_input is not None:
            self._data.update(user_input)
            _LOGGER.debug("Updated data: %s", self._data)
            if (
                BatteryStateAlgorithm(user_input[CONF_STATE_ALGORITHM])
                != BatteryStateAlgorithm.By_Device
            ):
                return await self.async_step_custom_calculation()
            else:
                self.hass.config_entries.async_update_entry(
                    self._config_entry, data=self._data
                )
                return self.async_create_entry(
                    title=f"BM6 {self._data[CONF_DEVICE_ADDRESS]}",
                    description="BM6 battery monitor",
                    data=self._data,
                )
        schema = await build_schema(
            self.hass,
            self._data,
            devices=None,
            is_options_flow=True,
            config_page=ConfigPage.MAIN,
        )
        _LOGGER.debug("Showing options form with schema: %s", schema)
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)

    async def async_step_custom_calculation(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the custom calculation step."""
        _LOGGER.debug("Starting custom calculation step with input: %s", user_input)
        errors: dict[str, str] = {}
        if user_input is not None:
            self._data.update(user_input)
            _LOGGER.debug("Updated data: %s", self._data)
            if BatteryType(user_input[CONF_BATTERY_TYPE]) == BatteryType.Custom:
                return await self.async_step_custom_voltage()
            else:
                self.hass.config_entries.async_update_entry(
                    self._config_entry, data=self._data
                )
                return self.async_create_entry(
                    title=f"BM6 {self._data[CONF_DEVICE_ADDRESS]}",
                    description="BM6 battery monitor",
                    data=self._data,
                )
        schema = await build_schema(
            self.hass,
            self._data,
            devices=None,
            is_options_flow=True,
            config_page=ConfigPage.CUSTOM_CALCULATION,
        )
        _LOGGER.debug("Showing custom calculation form with schema: %s", schema)
        return self.async_show_form(
            step_id="custom_calculation", data_schema=schema, errors=errors
        )

    async def async_step_custom_voltage(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the custom voltage step."""
        _LOGGER.debug("Starting custom voltage step with input: %s", user_input)
        errors: dict[str, str] = {}
        if user_input is not None:
            self._data.update(user_input)
            _LOGGER.debug("Updated data: %s", self._data)
            errors = validate_custom_voltage(self._data, errors)
            if not errors:
                self.hass.config_entries.async_update_entry(
                    self._config_entry, data=self._data
                )
                return self.async_create_entry(
                    title=f"BM6 {self._data[CONF_DEVICE_ADDRESS]}",
                    description="BM6 battery monitor",
                    data=self._data,
                )
        schema = await build_schema(
            self.hass,
            self._data,
            devices=None,
            is_options_flow=True,
            config_page=ConfigPage.CUSTOM_VOLTAGE,
        )
        _LOGGER.debug("Showing custom voltage form with schema: %s", schema)
        return self.async_show_form(
            step_id="custom_voltage", data_schema=schema, errors=errors
        )

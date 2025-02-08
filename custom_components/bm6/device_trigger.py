"""Provides device triggers for BM6 devices."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Callable
import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.trigger import TriggerActionType, TriggerInfo
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.entity_registry import RegistryEntry
from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.components.homeassistant.triggers import state as state_trigger
from homeassistant.const import (
    CONF_DEVICE,
    CONF_DEVICE_ID,
    CONF_DOMAIN,
    CONF_ENTITY_ID,
    CONF_PLATFORM,
    CONF_TYPE,
    CONF_STATE,
)

from .const import CONF_STATE_ALGORITHM, DOMAIN, TRANSLATION_KEY_STATE
from .battery import BatteryState, BatteryStateAlgorithm

_LOGGER = logging.getLogger(__name__)


@dataclass
class TriggerConfig:
    """BM6 trigger configuration."""

    battery_state: BatteryState
    battery_state_alg: list[BatteryStateAlgorithm]
    translation_key: str
    change_what: str


TRIGGER_START_OK = "start_ok"
TRIGGER_START_LOW_VOLTAGE = "start_low_voltage"
TRIGGER_UNDER_VOLTAGE = "under_voltage"
TRIGGER_START_DISCHARGING = "start_discharging"
TRIGGER_START_IDLE = "start_idle"
TRIGGER_START_CHARGING = "start_charging"
TRIGGER_OVER_VOLTAGE = "over_voltage"
TRIGGER_STATE_CHANGED = "state_changed"

TRIGGER_TYPES: dict[str, TriggerConfig] = {
    TRIGGER_START_OK: TriggerConfig(
        translation_key=TRANSLATION_KEY_STATE,
        battery_state=BatteryState.Ok,
        battery_state_alg=[BatteryStateAlgorithm.By_Device],
        change_what=state_trigger.CONF_TO,
    ),
    TRIGGER_START_LOW_VOLTAGE: TriggerConfig(
        translation_key=TRANSLATION_KEY_STATE,
        battery_state=BatteryState.LowVoltage,
        battery_state_alg=[BatteryStateAlgorithm.By_Device],
        change_what=state_trigger.CONF_TO,
    ),
    TRIGGER_UNDER_VOLTAGE: TriggerConfig(
        translation_key=TRANSLATION_KEY_STATE,
        battery_state=BatteryState.UnderVoltage,
        battery_state_alg=[
            BatteryStateAlgorithm.CVR_DVR,
            BatteryStateAlgorithm.SoC_SoD,
        ],
        change_what=state_trigger.CONF_TO,
    ),
    TRIGGER_START_DISCHARGING: TriggerConfig(
        translation_key=TRANSLATION_KEY_STATE,
        battery_state=BatteryState.Discharging,
        battery_state_alg=[
            BatteryStateAlgorithm.CVR_DVR,
            BatteryStateAlgorithm.SoC_SoD,
        ],
        change_what=state_trigger.CONF_TO,
    ),
    TRIGGER_START_IDLE: TriggerConfig(
        translation_key=TRANSLATION_KEY_STATE,
        battery_state=BatteryState.Idle,
        battery_state_alg=[
            BatteryStateAlgorithm.CVR_DVR,
            BatteryStateAlgorithm.SoC_SoD,
        ],
        change_what=state_trigger.CONF_TO,
    ),
    TRIGGER_START_CHARGING: TriggerConfig(
        translation_key=TRANSLATION_KEY_STATE,
        battery_state=BatteryState.Charging,
        battery_state_alg=[
            BatteryStateAlgorithm.By_Device,
            BatteryStateAlgorithm.CVR_DVR,
            BatteryStateAlgorithm.SoC_SoD,
        ],
        change_what=state_trigger.CONF_TO,
    ),
    TRIGGER_OVER_VOLTAGE: TriggerConfig(
        translation_key=TRANSLATION_KEY_STATE,
        battery_state=BatteryState.OverVoltage,
        battery_state_alg=[
            BatteryStateAlgorithm.CVR_DVR,
            BatteryStateAlgorithm.SoC_SoD,
        ],
        change_what=state_trigger.CONF_TO,
    ),
    TRIGGER_STATE_CHANGED: TriggerConfig(
        translation_key=TRANSLATION_KEY_STATE,
        battery_state=BatteryState.Unknown,
        battery_state_alg=[
            BatteryStateAlgorithm.By_Device,
            BatteryStateAlgorithm.CVR_DVR,
            BatteryStateAlgorithm.SoC_SoD,
        ],
        change_what=state_trigger.CONF_NOT_TO,
    ),
}

TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend(
    {
        vol.Required(CONF_ENTITY_ID): cv.entity_id,
        vol.Required(CONF_TYPE): vol.In(TRIGGER_TYPES.keys()),
    }
)


async def async_get_triggers(
    hass: HomeAssistant, device_id: str
) -> list[dict[str, Any]]:
    """List device triggers for BM6 devices."""
    try:
        _LOGGER.debug("Getting triggers for device %s", device_id)
        triggers = []
        registry = er.async_get(hass)
        entries: list[RegistryEntry] = er.async_entries_for_device(registry, device_id)
        for entry in entries:
            if entry.platform != DOMAIN:
                continue
            config_entry = hass.config_entries.async_get_entry(entry.config_entry_id)
            _LOGGER.debug(
                "Getting config for entry %s: %s", entry.entity_id, config_entry.data
            )
            available_triggers = [
                trigger_type
                for trigger_type, trigger_config in TRIGGER_TYPES.items()
                if BatteryStateAlgorithm(config_entry.data[CONF_STATE_ALGORITHM])
                in trigger_config.battery_state_alg
                and trigger_config.translation_key == entry.translation_key
            ]
            _LOGGER.debug("Available trigger types: %s", available_triggers)
            for trigger_type in available_triggers:
                base_trigger = {
                    CONF_PLATFORM: CONF_DEVICE,
                    CONF_DEVICE_ID: device_id,
                    CONF_DOMAIN: DOMAIN,
                    CONF_ENTITY_ID: entry.entity_id,
                }
                triggers.append({**base_trigger, CONF_TYPE: trigger_type})
        _LOGGER.debug("Triggers: %s", triggers)
        return triggers
    except Exception as e:
        _LOGGER.error("Error getting triggers for device %s: %s", device_id, e)
        return []


async def async_validate_trigger_config(
    hass: HomeAssistant, config: ConfigType
) -> ConfigType:
    """Validate trigger config."""
    _LOGGER.debug("Validating trigger config %s", config)
    return TRIGGER_SCHEMA(config)


async def async_attach_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action: TriggerActionType,
    trigger_info: TriggerInfo,
) -> Callable[[], None]:
    """Attach a trigger."""
    try:
        _LOGGER.debug("Attaching trigger %s", config)
        state_config = state_trigger.TRIGGER_STATE_SCHEMA(
            {
                state_trigger.CONF_PLATFORM: CONF_STATE,
                CONF_ENTITY_ID: config[CONF_ENTITY_ID],
            }
        )
        trigger_type = TRIGGER_TYPES[config[CONF_TYPE]]
        if not trigger_type:
            raise ValueError(f"Unknown trigger type {config[CONF_TYPE]}")
        state_config[trigger_type.change_what] = trigger_type.battery_state.value
        state_config = await state_trigger.async_validate_trigger_config(
            hass, state_config
        )
        return await state_trigger.async_attach_trigger(
            hass, state_config, action, trigger_info, platform_type=CONF_DEVICE
        )
    except Exception as e:
        _LOGGER.error("Error attaching trigger: %s", e)
        return lambda: None

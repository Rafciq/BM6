"""Mocked integration tests for BM6 watchdog coordinator and sensor entities.

Run in an environment with Home Assistant and pytest installed:
    python -m pytest -q tests/test_watchdog_integration.py

No physical BM6 device, Bluetooth adapter, or live HA instance is needed.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import is_callback
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.bm6 import coordinator as coordinator_module
from custom_components.bm6 import sensor as sensor_module


@pytest.fixture
def bm6_coordinator():
    """Construct the real coordinator while mocking battery calculations and HA."""
    entry = SimpleNamespace(
        data={
            "device_address": "AA:BB:CC:DD:EE:FF",
            "update_interval": 60,
            "voltage_offset": 0.0,
            "temperature_offset": 0.0,
            "temperature_unit": "°C",
        }
    )
    # Mirror the unload registration used by Home Assistant ConfigEntry.
    entry.async_on_unload = MagicMock()
    hass = MagicMock()
    hass.loop = MagicMock()
    with patch.object(coordinator_module, "Battery") as battery_cls:
        battery = battery_cls.return_value
        battery.percent = 95
        battery.state.value = "idle"
        battery.info.state_algorithm.value = "by_device"
        for name in ("dvr", "cvr", "sod", "soc"):
            setattr(battery.range, name, SimpleNamespace(min=11.0, max=14.0))
        # HA's coordinator constructor reports framework usage via a global frame
        # helper, which is not initialized in this isolated mocked test.
        with patch("homeassistant.helpers.frame.report_usage"):
            instance = coordinator_module.BM6DataUpdateCoordinator(hass, entry)
    return instance


def sample_reading(voltage=12.8):
    return SimpleNamespace(
        RealTime=SimpleNamespace(
            Voltage=voltage, Temperature=8.0, Percent=95, State=0,
            RapidAcceleration=0, RapidDeceleration=0,
        ),
        Advertisement=SimpleNamespace(RSSI=-80, Scanner="hci0"),
    )


@pytest.mark.asyncio
async def test_successful_read_updates_timestamp(bm6_coordinator):
    with patch.object(coordinator_module, "BM6Connector") as connector:
        connector.return_value.get_data = AsyncMock(return_value=sample_reading())
        result = await bm6_coordinator._async_update_data()
    assert result["voltage_device"] == 12.8
    assert bm6_coordinator.last_successful_reading is not None
    assert bm6_coordinator.last_successful_reading.tzinfo is not None


@pytest.mark.asyncio
async def test_identical_voltage_still_refreshes_timestamp(bm6_coordinator):
    with patch.object(coordinator_module, "BM6Connector") as connector:
        connector.return_value.get_data = AsyncMock(return_value=sample_reading())
        await bm6_coordinator._async_update_data()
        previous = bm6_coordinator.last_successful_reading
        await bm6_coordinator._async_update_data()
    assert bm6_coordinator.last_successful_reading >= previous


@pytest.mark.asyncio
async def test_failed_poll_preserves_last_good_timestamp(bm6_coordinator):
    previous = datetime.now(timezone.utc) - timedelta(minutes=10)
    bm6_coordinator.last_successful_reading = previous
    with patch.object(coordinator_module, "BM6Connector") as connector:
        connector.return_value.get_data = AsyncMock(side_effect=RuntimeError("BLE disconnected"))
        with pytest.raises(UpdateFailed):
            await bm6_coordinator._async_update_data()
    assert bm6_coordinator.last_successful_reading == previous


@pytest.mark.asyncio
async def test_invalid_payload_does_not_refresh_timestamp(bm6_coordinator):
    previous = datetime.now(timezone.utc) - timedelta(minutes=10)
    bm6_coordinator.last_successful_reading = previous
    with patch.object(coordinator_module, "BM6Connector") as connector:
        connector.return_value.get_data = AsyncMock(return_value=SimpleNamespace(RealTime=None))
        with pytest.raises(UpdateFailed):
            await bm6_coordinator._async_update_data()
    assert bm6_coordinator.last_successful_reading == previous


def test_new_coordinator_starts_unknown(bm6_coordinator):
    assert bm6_coordinator.last_successful_reading is None


def test_timestamp_sensor_survives_failed_coordinator(bm6_coordinator):
    timestamp = datetime.now(timezone.utc)
    bm6_coordinator.last_successful_reading = timestamp
    bm6_coordinator.last_update_success = False
    entity = sensor_module.BM6LastSuccessfulReadingSensor(bm6_coordinator)
    assert entity.available
    assert entity.native_value == timestamp
    assert entity.unique_id.endswith("last_successful_reading_AA:BB:CC:DD:EE:FF")


def test_health_sensor_unknown_before_first_read(bm6_coordinator):
    entity = sensor_module.BM6ReadingHealthSensor(bm6_coordinator)
    assert entity.available
    assert entity.native_value == "unknown"


def test_health_sensor_stale_and_recovers(bm6_coordinator):
    entity = sensor_module.BM6ReadingHealthSensor(bm6_coordinator)
    bm6_coordinator.last_successful_reading = datetime.now(timezone.utc) - timedelta(minutes=6)
    assert entity.native_value == "stale"
    bm6_coordinator.last_successful_reading = datetime.now(timezone.utc)
    assert entity.native_value == "healthy"


@pytest.mark.asyncio
async def test_health_timer_updates_and_unregisters(bm6_coordinator):
    """Check the actual entity lifecycle and cleanup registration."""
    entity = sensor_module.BM6ReadingHealthSensor(bm6_coordinator)
    cancel = MagicMock()
    with patch.object(sensor_module, "async_track_time_interval", return_value=cancel) as track:
        # Avoid needing an entity platform or event bus for this unit-level lifecycle test.
        with patch.object(sensor_module.BM6SensorEntity, "async_added_to_hass", new_callable=AsyncMock):
            await entity.async_added_to_hass()
        assert track.call_args.args[2] == timedelta(seconds=30)
        with patch.object(entity, "async_write_ha_state") as write:
            callback = track.call_args.args[1]
            assert is_callback(callback), "Health timer must run in HA event loop"
            callback(datetime.now(timezone.utc))
            write.assert_called_once()
        # Home Assistant invokes async_on_remove callbacks during the actual
        # entity removal, before async_will_remove_from_hass(). Reproduce that
        # removal phase without requiring a full entity platform setup.
        entity._call_on_remove_callbacks()
        cancel.assert_called_once()

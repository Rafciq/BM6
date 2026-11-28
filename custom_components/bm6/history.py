"""Import BM6 stored history into Home Assistant long-term statistics.

The BM6 monitor stores voltage, state of charge and temperature records
on-device (one record every two minutes). This module resolves the three
history sensors (voltage, percentage, temperature) for a device, converts
the records to the sensors' units and imports them as external statistics
so they appear in the Home Assistant history graphs alongside the live
readings.

Home Assistant long-term statistics are hourly: each point must start
exactly on the hour. The two-minute records are therefore aggregated
into one hourly point per sensor (mean, min and max), which is what the
history graphs display.
"""

from __future__ import annotations

import inspect
import logging
from datetime import datetime
from typing import Callable

from homeassistant.components.recorder.statistics import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
    async_add_external_statistics,
)
from homeassistant.const import PERCENTAGE, UnitOfElectricPotential
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .bm6_connect import BM6HistoryReading
from .const import (
    DOMAIN,
    TRANSLATION_KEY_PERCENTAGE,
    TRANSLATION_KEY_TEMPERATURE,
    TRANSLATION_KEY_VOLTAGE,
)
from .utils import convert_temperature

_LOGGER = logging.getLogger(__name__)


def _hour_start(timestamp: datetime) -> datetime:
    """Truncate a timestamp to the top of its hour."""
    return timestamp.replace(minute=0, second=0, microsecond=0)


async def async_import_history(
    hass: HomeAssistant,
    *,
    device_address: str,
    readings: list[BM6HistoryReading],
    last_import: dict[str, datetime],
    temperature_unit,
    voltage_offset: float,
    temperature_offset: float,
) -> tuple[int, dict[str, datetime]]:
    """Import BM6 history readings into long-term statistics.

    Returns a tuple of (number of hourly points imported, updated
    last-import watermarks keyed by statistic id). Records are
    aggregated by hour (mean/min/max) because long-term statistics
    require hour-aligned timestamps. Only hours newer than the watermark
    for a sensor are imported, so repeated imports are idempotent. A
    failure to import (for example the recorder not being ready yet) is
    logged and the unchanged watermarks are returned, so the next update
    retries.
    """
    if not readings:
        return 0, last_import

    statistic_ids = _resolve_statistic_ids(hass, device_address)
    if not statistic_ids:
        _LOGGER.debug(
            "BM6 history sensors for %s are not registered yet; skipping import",
            device_address,
        )
        return 0, last_import

    new_last_import = dict(last_import)
    imported = 0
    for translation_key, entity_id in statistic_ids.items():
        # External statistics use a colon-separated statistic id
        # (sensor:<object_id>), not the dotted entity id. The source must
        # match the domain part of the statistic id.
        statistic_id = entity_id.replace(".", ":", 1)
        source = statistic_id.split(":", 1)[0]
        value_fn, unit, unit_class = _sensor_value_and_unit(
            translation_key, temperature_unit, voltage_offset, temperature_offset
        )
        watermark = new_last_import.get(entity_id)
        # Aggregate the two-minute records into hourly buckets.
        hourly: dict[datetime, list[float]] = {}
        for reading in sorted(readings, key=lambda item: item.ts):
            hour = _hour_start(reading.ts)
            if watermark is not None and hour <= watermark:
                continue
            value = value_fn(reading)
            if value is None:
                continue
            hourly.setdefault(hour, []).append(value)
        if not hourly:
            continue
        points: list[StatisticData] = []
        for hour in sorted(hourly):
            values = hourly[hour]
            mean = sum(values) / len(values)
            points.append(
                StatisticData(
                    start=hour,
                    state=mean,
                    mean=mean,
                    min=min(values),
                    max=max(values),
                )
            )
        metadata = StatisticMetaData(
            statistic_id=statistic_id,
            source=source,
            name=None,
            unit_of_measurement=unit,
            unit_class=unit_class,
            mean_type=StatisticMeanType.ARITHMETIC,
            has_mean=True,
            has_sum=False,
        )
        try:
            result = async_add_external_statistics(hass, metadata, points)
            if inspect.isawaitable(result):
                await result
        except Exception as err:  # noqa: BLE001 - recorder may not be ready
            _LOGGER.warning(
                "Could not import BM6 history for %s: %s", entity_id, err
            )
            continue
        imported += len(points)
        new_last_import[entity_id] = max(point["start"] for point in points)
    return imported, new_last_import


def _resolve_statistic_ids(
    hass: HomeAssistant, device_address: str
) -> dict[str, str]:
    """Resolve sensor entity ids for the history sensors by unique id.

    The statistic id for each sensor is its entity id, so the imported
    statistics are shown as the sensor's own history. Sensors that are
    not registered yet (for example on the very first refresh) are
    skipped and picked up on the next import.
    """
    registry = er.async_get(hass)
    statistic_ids: dict[str, str] = {}
    for translation_key in (
        TRANSLATION_KEY_VOLTAGE,
        TRANSLATION_KEY_PERCENTAGE,
        TRANSLATION_KEY_TEMPERATURE,
    ):
        unique_id = f"{DOMAIN}_{translation_key.lower()}_{device_address}"
        entity_id = registry.async_get_entity_id("sensor", DOMAIN, unique_id)
        if entity_id is None:
            _LOGGER.debug(
                "BM6 sensor %s is not registered yet; skipping it for history import",
                unique_id,
            )
            continue
        statistic_ids[translation_key] = entity_id
    return statistic_ids


def _sensor_value_and_unit(
    translation_key: str,
    temperature_unit,
    voltage_offset: float,
    temperature_offset: float,
) -> tuple[Callable[[BM6HistoryReading], float | None], str, str | None]:
    """Return (value extractor, unit, unit class) for a history sensor.

    Values are converted to the sensor's state: voltage gets the
    configured offset, temperature is converted from the device's
    Celsius to the configured unit and then offset, and percentage is
    the device-reported state of charge. The temperature unit must match
    the sensor's configured unit, otherwise the recorder rejects the
    statistics for a unit mismatch with the sensor's own statistics.
    """
    if translation_key == TRANSLATION_KEY_VOLTAGE:
        return (
            lambda reading: reading.voltage + voltage_offset,
            UnitOfElectricPotential.VOLT,
            "voltage",
        )
    if translation_key == TRANSLATION_KEY_PERCENTAGE:
        return (lambda reading: reading.soc, PERCENTAGE, None)
    if translation_key == TRANSLATION_KEY_TEMPERATURE:
        return (
            lambda reading: (
                None
                if reading.temperature is None
                else convert_temperature(reading.temperature, temperature_unit)
                + temperature_offset
            ),
            temperature_unit,
            "temperature",
        )
    raise ValueError(f"Unknown history sensor: {translation_key}")

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

from sqlalchemy import text

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

    # Save the raw two-minute records first: long-term statistics are
    # hourly-only, so the full resolution would otherwise be lost when
    # the records are aggregated below. The JSON file keeps every record
    # keyed by timestamp, so re-downloads merge without duplicating.
    await _save_raw_history(hass, device_address, readings)

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
        # External statistics use the colon-format statistic id
        # (sensor:<object_id>), but a sensor's history graph only shows
        # its own statistics (dotted entity id). Copy the imported rows
        # into the sensor's own statistics so the history is visible on
        # the sensor's graph. Rows for hours the sensor already has are
        # skipped, so this is idempotent.
        await _copy_to_sensor_statistics(hass, statistic_id, entity_id)
        imported += len(points)
        new_last_import[entity_id] = max(point["start"] for point in points)
    # Long-term statistics are hourly-only, so the two-minute detail
    # never reaches the history graph through them. Write the raw
    # two-minute records as state rows as well, so the graph shows the
    # full resolution for the recent period (states are kept for the
    # recorder's purge window; the hourly statistics remain the
    # long-term record). Best-effort: a failure here never blocks the
    # statistics import above.
    try:
        await _write_history_states(
            hass,
            statistic_ids,
            readings,
            temperature_unit,
            voltage_offset,
            temperature_offset,
        )
    except Exception as err:  # noqa: BLE001 - state backfill is best-effort
        _LOGGER.warning("Could not write BM6 history states: %s", err)
    return imported, new_last_import


def _state_string(translation_key: str, value: float) -> str:
    """Format a history value the way the live sensor reports it."""
    if translation_key == TRANSLATION_KEY_VOLTAGE:
        return f"{value:.2f}"
    if translation_key == TRANSLATION_KEY_PERCENTAGE:
        return str(int(value))
    if translation_key == TRANSLATION_KEY_TEMPERATURE:
        return f"{value:.1f}"
    raise ValueError(f"Unknown history sensor: {translation_key}")


async def _write_history_states(
    hass: HomeAssistant,
    statistic_ids: dict[str, str],
    readings: list[BM6HistoryReading],
    temperature_unit,
    voltage_offset: float,
    temperature_offset: float,
) -> None:
    """Write two-minute history records as recorder state rows.

    The history graph for the recent period reads the recorder's
    ``states`` table, which accepts any resolution, unlike long-term
    statistics (hourly-only). Each record becomes a state row for its
    sensor, so the graph shows the full two-minute detail. Rows are
    skipped where the sensor already has a state within 60 seconds
    (live polls and earlier backfills), so repeated imports never
    duplicate. The sensor's latest attributes are reused for the rows.
    """
    from homeassistant.components.recorder import get_instance

    if not readings:
        return
    ordered = sorted(readings, key=lambda item: item.ts)
    earliest = ordered[0].ts.timestamp() - 3600

    def _write() -> dict[str, int]:
        instance = get_instance(hass)
        counts: dict[str, int] = {}
        with instance.engine.begin() as connection:
            for translation_key, entity_id in statistic_ids.items():
                value_fn, _unit, _unit_class = _sensor_value_and_unit(
                    translation_key,
                    temperature_unit,
                    voltage_offset,
                    temperature_offset,
                )
                row = connection.execute(
                    text(
                        "SELECT metadata_id FROM states_meta "
                        "WHERE entity_id = :eid"
                    ),
                    {"eid": entity_id},
                ).fetchone()
                if row is None:
                    continue
                metadata_id = row[0]
                row = connection.execute(
                    text(
                        "SELECT attributes_id FROM states "
                        "WHERE metadata_id = :mid "
                        "ORDER BY last_updated_ts DESC LIMIT 1"
                    ),
                    {"mid": metadata_id},
                ).fetchone()
                if row is None:
                    continue
                attributes_id = row[0]
                existing = [
                    ts
                    for (ts,) in connection.execute(
                        text(
                            "SELECT last_updated_ts FROM states "
                            "WHERE metadata_id = :mid "
                            "AND last_updated_ts >= :earliest"
                        ),
                        {"mid": metadata_id, "earliest": earliest},
                    ).fetchall()
                    if ts is not None
                ]
                inserted = 0
                prev_state: str | None = None
                for reading in ordered:
                    value = value_fn(reading)
                    if value is None:
                        continue
                    state_str = _state_string(translation_key, value)
                    epoch = reading.ts.timestamp()
                    if any(abs(e - epoch) <= 60 for e in existing):
                        continue
                    changed_ts = epoch if state_str != prev_state else None
                    connection.execute(
                        text(
                            "INSERT INTO states (metadata_id, state, "
                            "attributes_id, last_changed_ts, "
                            "last_updated_ts, last_reported_ts, origin_idx, "
                            "old_state_id, context_id, event_id) VALUES "
                            "(:mid, :state, :attrs, :changed, :updated, "
                            ":updated, 0, NULL, NULL, NULL)"
                        ),
                        {
                            "mid": metadata_id,
                            "state": state_str,
                            "attrs": attributes_id,
                            "changed": changed_ts,
                            "updated": epoch,
                        },
                    )
                    existing.append(epoch)
                    prev_state = state_str
                    inserted += 1
                counts[entity_id] = inserted
        return counts

    counts = await get_instance(hass).async_add_executor_job(_write)
    total = sum(counts.values())
    if total:
        _LOGGER.debug(
            "Wrote %s BM6 history state rows (%s)",
            total,
            counts,
        )


async def _copy_to_sensor_statistics(
    hass: HomeAssistant, external_statistic_id: str, entity_id: str
) -> None:
    """Copy external statistics rows into the sensor's own statistics.

    The recorder's ``async_add_external_statistics`` stores rows under
    the colon-format statistic id, which the sensor's history graph does
    not display. This copies those rows into the sensor's own statistics
    (dotted entity id) so the imported history appears on the graph.
    Only hours the sensor does not already have are copied.
    """
    from homeassistant.components.recorder import get_instance

    def _copy() -> int:
        instance = get_instance(hass)
        with instance.engine.begin() as connection:
            # Resolve both metadata ids.
            row = connection.execute(
                text(
                    "SELECT id FROM statistics_meta WHERE statistic_id = :sid"
                ),
                {"sid": external_statistic_id},
            ).fetchone()
            if row is None:
                return 0
            external_meta_id = row[0]
            row = connection.execute(
                text(
                    "SELECT id FROM statistics_meta WHERE statistic_id = :sid"
                ),
                {"sid": entity_id},
            ).fetchone()
            if row is None:
                return 0
            own_meta_id = row[0]
            result = connection.execute(
                text(
                    "INSERT INTO statistics "
                    "(created, start, mean, min, max, last_reset, state, "
                    "sum, metadata_id, created_ts, start_ts, last_reset_ts, "
                    "mean_weight) "
                    "SELECT created, start, mean, min, max, last_reset, "
                    "state, sum, :dst, created_ts, start_ts, last_reset_ts, "
                    "mean_weight FROM statistics WHERE metadata_id = :src "
                    "AND start_ts NOT IN "
                    "(SELECT start_ts FROM statistics WHERE metadata_id = :dst)"
                ),
                {"src": external_meta_id, "dst": own_meta_id},
            )
            copied = result.rowcount
            # Remove the external rows now that they are copied: the
            # colon-format statistic is not shown on the sensor's graph,
            # so leaving it would only accumulate invisible duplicates.
            connection.execute(
                text("DELETE FROM statistics WHERE metadata_id = :src"),
                {"src": external_meta_id},
            )
            connection.execute(
                text("DELETE FROM statistics_meta WHERE id = :src"),
                {"src": external_meta_id},
            )
            return copied

    try:
        copied = await get_instance(hass).async_add_executor_job(_copy)
        if copied:
            _LOGGER.debug(
                "Copied %s history rows into %s own statistics",
                copied,
                entity_id,
            )
    except Exception as err:  # noqa: BLE001 - visibility is best-effort
        _LOGGER.warning(
            "Could not copy BM6 history into %s own statistics: %s",
            entity_id,
            err,
        )


async def _save_raw_history(
    hass: HomeAssistant,
    device_address: str,
    readings: list[BM6HistoryReading],
) -> None:
    """Save the raw two-minute records to a JSON file.

    Long-term statistics are hourly-only, so the full resolution is
    lost when the records are aggregated for import. This keeps every
    record at its original two-minute resolution in
    ``bm6_history_<address>.json`` under the Home Assistant config
    directory, keyed by timestamp so re-downloads merge without
    duplicating. Temperatures are stored in Celsius as read from the
    device; voltage is the raw value before any configured offset.
    """
    import json

    filename = f"bm6_history_{device_address.replace(':', '').lower()}.json"
    path = hass.config.path(filename)

    def _write() -> int:
        records: dict[str, dict] = {}
        try:
            with open(path, encoding="utf-8") as f:
                existing = json.load(f)
            if isinstance(existing, dict):
                records.update(existing.get("records", {}))
        except (OSError, ValueError):
            pass
        added = 0
        for reading in readings:
            key = reading.ts.isoformat()
            if key in records:
                continue
            records[key] = {
                "voltage": reading.voltage,
                "soc": reading.soc,
                "temperature_c": reading.temperature,
                "event": reading.event_type,
            }
            added += 1
        if not added and records:
            return 0
        payload = {
            "device_address": device_address,
            "resolution_minutes": 2,
            "records": dict(sorted(records.items())),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=1)
        return added

    try:
        added = await hass.async_add_executor_job(_write)
        if added:
            _LOGGER.debug(
                "Saved %s new raw BM6 history records to %s",
                added,
                filename,
            )
    except Exception as err:  # noqa: BLE001 - raw export is best-effort
        _LOGGER.warning("Could not save raw BM6 history to %s: %s", filename, err)


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

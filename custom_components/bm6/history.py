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

import bisect
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

    Every import also fills gaps: the accumulated JSON archive (every
    record ever downloaded) is checked against the stored hourly
    statistics and the two-minute state rows, and any missing hour or
    state is backfilled from the archive. So a missed or partial
    download, a failed import, or a deleted row is repaired on the next
    run instead of leaving a permanent hole. The gap-fill runs even when
    this download returned no new records.
    """
    # Save the raw two-minute records first: long-term statistics are
    # hourly-only, so the full resolution would otherwise be lost when
    # the records are aggregated below. The JSON file keeps every record
    # keyed by timestamp, so re-downloads merge without duplicating.
    if readings:
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
    if readings:
        for translation_key, entity_id in statistic_ids.items():
            # External statistics use a colon-separated statistic id
            # (sensor:<object_id>), not the dotted entity id. The source
            # must match the domain part of the statistic id.
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
            # (sensor:<object_id>), but a sensor's history graph only
            # shows its own statistics (dotted entity id). Copy the
            # imported rows into the sensor's own statistics so the
            # history is visible on the sensor's graph. Rows for hours
            # the sensor already has are skipped, so this is idempotent.
            await _copy_to_sensor_statistics(hass, statistic_id, entity_id)
            imported += len(points)
            new_last_import[entity_id] = max(point["start"] for point in points)
    # Fill gaps from the accumulated JSON archive on every import, even
    # when this download returned no new records: any hour missing from
    # the sensor's own long-term statistics, and any two-minute record
    # missing a state row, is backfilled from the archive. Best-effort:
    # a failure here never blocks the import above.
    try:
        filled = await _fill_history_gaps(
            hass,
            device_address,
            statistic_ids,
            temperature_unit,
            voltage_offset,
            temperature_offset,
        )
        if filled:
            _LOGGER.info(
                "Filled %s gaps in BM6 history for %s", filled, device_address
            )
    except Exception as err:  # noqa: BLE001 - gap-fill is best-effort
        _LOGGER.warning("Could not fill BM6 history gaps: %s", err)
    return imported, new_last_import


async def _fill_history_gaps(
    hass: HomeAssistant,
    device_address: str,
    statistic_ids: dict[str, str],
    temperature_unit,
    voltage_offset: float,
    temperature_offset: float,
) -> int:
    """Fill gaps in stored BM6 history from the JSON archive.

    The JSON file under the config directory accumulates every
    two-minute record ever downloaded, so it is the permanent archive
    even after the device's own ~3.5-day buffer has rolled past. On
    every import this checks the archive against the stored data and
    backfills anything missing, for each sensor:

    * hourly long-term statistics rows for hours in the archive range
      that have no row (a missed or failed import leaves these behind
      because the watermark only moves forward);
    * two-minute state rows for records that have no state within 60
      seconds (the recent-history graph reads these).

    Returns the number of rows filled (hourly points plus state rows).
    """
    archive = await _load_raw_history_readings(hass, device_address)
    if not archive:
        return 0
    filled = 0
    filled += await _fill_hourly_stat_gaps(
        hass,
        archive,
        statistic_ids,
        temperature_unit,
        voltage_offset,
        temperature_offset,
    )
    filled += await _fill_state_gaps(
        hass,
        archive,
        statistic_ids,
        temperature_unit,
        voltage_offset,
        temperature_offset,
    )
    return filled


async def async_newest_archive_ts(
    hass: HomeAssistant, device_address: str
) -> datetime | None:
    """Return the newest record timestamp in the JSON archive.

    Used to size a catch-up download: after the device has been out of
    range, the download requests enough pages to reach back to this
    timestamp instead of only the latest page. Returns None when the
    archive does not exist yet or cannot be read.
    """
    import json

    filename = f"bm6_history_{device_address.replace(':', '').lower()}.json"
    path = hass.config.path(filename)

    def _newest() -> datetime | None:
        try:
            with open(path, encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, ValueError):
            return None
        records = payload.get("records", {}) if isinstance(payload, dict) else {}
        if not records:
            return None
        try:
            return datetime.fromisoformat(max(records.keys()))
        except ValueError:
            return None

    return await hass.async_add_executor_job(_newest)


async def _load_raw_history_readings(
    hass: HomeAssistant, device_address: str
) -> list[BM6HistoryReading]:
    """Load the accumulated JSON history archive as readings.

    Returns the records sorted oldest-first, or an empty list when the
    archive does not exist yet (first import) or cannot be read.
    """
    import json

    filename = f"bm6_history_{device_address.replace(':', '').lower()}.json"
    path = hass.config.path(filename)

    def _load() -> list[BM6HistoryReading]:
        try:
            with open(path, encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, ValueError):
            return []
        records = payload.get("records", {}) if isinstance(payload, dict) else {}
        readings: list[BM6HistoryReading] = []
        for key, rec in records.items():
            try:
                ts = datetime.fromisoformat(key)
            except ValueError:
                continue
            readings.append(
                BM6HistoryReading(
                    ts=ts,
                    voltage=float(rec["voltage"]),
                    soc=rec.get("soc"),
                    temperature=rec.get("temperature_c"),
                    event_type=int(rec.get("event", 0)),
                    raw_record="",
                    record_index=0,
                )
            )
        readings.sort(key=lambda item: item.ts)
        return readings

    return await hass.async_add_executor_job(_load)


async def _fill_hourly_stat_gaps(
    hass: HomeAssistant,
    archive: list[BM6HistoryReading],
    statistic_ids: dict[str, str],
    temperature_unit,
    voltage_offset: float,
    temperature_offset: float,
) -> int:
    """Insert hourly statistics rows for hours missing from own stats.

    Groups the archive records by hour and, for each sensor, inserts a
    row (mean/min/max, state = last value in the hour) for any hour in
    the archive range that the sensor's own statistics do not have.
    Rows are written directly into the sensor's own statistics (dotted
    entity id) in the same shape the recorder's own compilation uses.
    """
    import time

    from homeassistant.components.recorder import get_instance

    def _fill() -> int:
        instance = get_instance(hass)
        filled = 0
        now = time.time()
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
                        "SELECT id FROM statistics_meta "
                        "WHERE statistic_id = :sid"
                    ),
                    {"sid": entity_id},
                ).fetchone()
                if row is None:
                    continue
                meta_id = row[0]
                existing = {
                    ts
                    for (ts,) in connection.execute(
                        text(
                            "SELECT start_ts FROM statistics "
                            "WHERE metadata_id = :mid"
                        ),
                        {"mid": meta_id},
                    ).fetchall()
                    if ts is not None
                }
                hourly: dict[float, list[float]] = {}
                last_value: dict[float, float] = {}
                for reading in archive:
                    hour = _hour_start(reading.ts).timestamp()
                    value = value_fn(reading)
                    if value is None:
                        continue
                    hourly.setdefault(hour, []).append(value)
                    last_value[hour] = value
                for hour in sorted(hourly):
                    if hour in existing:
                        continue
                    values = hourly[hour]
                    mean = sum(values) / len(values)
                    try:
                        connection.execute(
                            text(
                                "INSERT INTO statistics "
                                "(created_ts, metadata_id, start_ts, mean, "
                                "min, max, last_reset_ts, state, sum, "
                                "mean_weight) VALUES "
                                "(:now, :mid, :start, :mean, :mn, :mx, "
                                "NULL, :state, NULL, NULL)"
                            ),
                            {
                                "now": now,
                                "mid": meta_id,
                                "start": hour,
                                "mean": mean,
                                "mn": min(values),
                                "mx": max(values),
                                "state": last_value[hour],
                            },
                        )
                    except Exception:  # noqa: BLE001 - race with recorder
                        continue
                    filled += 1
        return filled

    return await get_instance(hass).async_add_executor_job(_fill)


async def _fill_state_gaps(
    hass: HomeAssistant,
    archive: list[BM6HistoryReading],
    statistic_ids: dict[str, str],
    temperature_unit,
    voltage_offset: float,
    temperature_offset: float,
) -> int:
    """Insert state rows for archive records missing one.

    Reuses the same writer as the normal import (same 60-second
    clash check, same state formatting, same attributes reuse), but
    runs over the whole archive instead of only the latest download,
    so any two-minute record that never got a state row gets one.

    The device itself occasionally skips a two-minute recording slot
    (a four-minute step between two records instead of two), which
    leaves a short break in the graph that no re-download can repair
    because the slot was never recorded. For a single missing slot
    the value is interpolated linearly from its two neighbours and
    written as a state row, so the graph is continuous; the archive
    itself is left untouched, so interpolated points are never
    mistaken for measured records. Larger gaps are left alone.
    """
    effective = _interpolate_single_slot_gaps(archive)
    return await _write_history_states(
        hass,
        statistic_ids,
        effective,
        temperature_unit,
        voltage_offset,
        temperature_offset,
    )


def _interpolate_single_slot_gaps(
    readings: list[BM6HistoryReading],
) -> list[BM6HistoryReading]:
    """Insert interpolated readings for single missing two-minute slots.

    The BM6 records one sample every two minutes. When two consecutive
    archive records are four minutes apart, exactly one slot was never
    recorded; the value at the midpoint is estimated linearly from its
    neighbours. Only single-slot gaps are interpolated. The returned
    list is sorted oldest-first and includes the original readings.
    """
    from datetime import timedelta

    ordered = sorted(readings, key=lambda item: item.ts)
    if len(ordered) < 2:
        return ordered
    filled: list[BM6HistoryReading] = [ordered[0]]
    for prev, nxt in zip(ordered, ordered[1:]):
        step = (nxt.ts - prev.ts).total_seconds()
        if step == 240:
            mid_ts = prev.ts + timedelta(minutes=2)
            soc = None
            if prev.soc is not None and nxt.soc is not None:
                soc = round((prev.soc + nxt.soc) / 2)
            temperature = None
            if prev.temperature is not None and nxt.temperature is not None:
                temperature = (prev.temperature + nxt.temperature) / 2
            filled.append(
                BM6HistoryReading(
                    ts=mid_ts,
                    voltage=(prev.voltage + nxt.voltage) / 2,
                    soc=soc,
                    temperature=temperature,
                    event_type=prev.event_type,
                    raw_record="",
                    record_index=0,
                )
            )
        filled.append(nxt)
    return filled


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
) -> int:
    """Write two-minute history records as recorder state rows.

    The history graph for the recent period reads the recorder's
    ``states`` table, which accepts any resolution, unlike long-term
    statistics (hourly-only). Each record becomes a state row for its
    sensor, so the graph shows the full two-minute detail. Rows are
    skipped where the sensor already has a state within 60 seconds
    (live polls and earlier backfills), so repeated imports never
    duplicate. The sensor's latest attributes are reused for the rows.
    Returns the number of state rows written.
    """
    from homeassistant.components.recorder import get_instance

    if not readings:
        return 0
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
                existing = sorted(
                    ts
                    for (ts,) in connection.execute(
                        text(
                            "SELECT last_updated_ts FROM states "
                            "WHERE metadata_id = :mid "
                            "AND last_updated_ts >= :earliest "
                            "AND state != 'unavailable' "
                            "AND state != 'unknown'"
                        ),
                        {"mid": metadata_id, "earliest": earliest},
                    ).fetchall()
                    if ts is not None
                )
                inserted = 0
                prev_state: str | None = None
                for reading in ordered:
                    value = value_fn(reading)
                    if value is None:
                        continue
                    state_str = _state_string(translation_key, value)
                    epoch = reading.ts.timestamp()
                    # Skip when a state already exists within 60 s.
                    # bisect keeps the check logarithmic so a full
                    # archive pass stays fast as the archive grows.
                    # "unavailable"/"unknown" rows are not counted here:
                    # they are not measurements, so a brief unavailable
                    # blip must not block a real or interpolated value
                    # from being written at that time.
                    idx = bisect.bisect_left(existing, epoch - 60)
                    if idx < len(existing) and existing[idx] <= epoch + 60:
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
                    bisect.insort(existing, epoch)
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
    return total


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

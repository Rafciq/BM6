"""Pure freshness evaluation for BM6 successful Bluetooth readings."""

from __future__ import annotations

from datetime import datetime, timezone


def reading_health(last_success: datetime | None, now: datetime | None = None) -> str:
    """Return unknown before first read; healthy/delayed/stale thereafter.

    Thresholds are deliberately conservative for a 60-second poll interval.
    """
    if last_success is None:
        return "unknown"
    current = now if now is not None else datetime.now(timezone.utc)
    age = max(0.0, (current - last_success).total_seconds())
    if age >= 300:
        return "stale"
    if age >= 180:
        return "delayed"
    return "healthy"

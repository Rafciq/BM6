"""Unit tests for BM6 watchdog freshness classification (no HA required)."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import importlib.util
import unittest

MODULE_PATH = Path(__file__).resolve().parents[1] / "custom_components" / "bm6" / "watchdog.py"
spec = importlib.util.spec_from_file_location("bm6_watchdog", MODULE_PATH)
watchdog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watchdog)


class TestReadingHealth(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)

    def health_at(self, age_seconds):
        return watchdog.reading_health(self.now - timedelta(seconds=age_seconds), self.now)

    def test_unknown_before_first_read(self):
        self.assertEqual(watchdog.reading_health(None, self.now), "unknown")

    def test_fresh_readings(self):
        for age in (0, 60, 179):
            self.assertEqual(self.health_at(age), "healthy")

    def test_delayed_boundary(self):
        for age in (180, 240, 299):
            self.assertEqual(self.health_at(age), "delayed")

    def test_stale_boundary(self):
        for age in (300, 600, 3600):
            self.assertEqual(self.health_at(age), "stale")

    def test_recovery_after_failure(self):
        old = self.now - timedelta(minutes=10)
        self.assertEqual(watchdog.reading_health(old, self.now), "stale")
        recovered = self.now - timedelta(seconds=5)
        self.assertEqual(watchdog.reading_health(recovered, self.now), "healthy")

    def test_future_timestamp_is_healthy(self):
        self.assertEqual(self.health_at(-30), "healthy")


if __name__ == "__main__":
    unittest.main()

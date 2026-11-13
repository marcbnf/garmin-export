"""Export engine tests, offline (fake API). Run: python -m unittest discover tests"""

import shutil
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from fake_api import FakeApi

from garmin_export import api, updates
from garmin_export.cache import cache_size, clear_cache
from garmin_export.exporter import ExportOptions, run_export
from garmin_export.output import downsample_timeseries, strip_empty
from garmin_export.sections import SECTIONS

FAST = dict(delay=0.0)


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.out = Path(tempfile.mkdtemp())
        api.CANCEL.clear()
        self._wait = api.CANCEL.wait
        api.CANCEL.wait = lambda timeout=None: False  # no 60 s pause on 429

    def tearDown(self):
        api.CANCEL.wait = self._wait
        api.CANCEL.clear()
        shutil.rmtree(self.out)

    def export(self, fake=None, progress=None, **opts):
        return run_export(fake or FakeApi(), self.out, ExportOptions(**{**FAST, **opts}), progress)

    def test_full_export_contains_every_section(self):
        fractions = []
        ex = self.export(days=5, max_activities=3, progress=lambda f, _: fractions.append(f))
        self.assertEqual(len(ex.written_files), 1)
        self.assertFalse(ex.interrupted)
        self.assertEqual(ex.errors, [])
        text = ex.written_files[0].read_text()
        for section in SECTIONS:
            self.assertIn(f"\n{section.name}\n", text)
        self.assertIn("Activity 100: Run 0", text)
        self.assertEqual(fractions, sorted(fractions), "progress must never go backwards")
        self.assertEqual(fractions[-1], 1.0)

    def test_404_is_no_data_and_429_is_retried(self):
        text = self.export(days=3).written_files[0].read_text()
        golf = text.split("\nGolf\n", 1)[1]
        self.assertTrue(golf.lstrip().startswith("No data available."))
        self.assertIn('"valueInML": 1500', text)

    def test_cache_skips_recent_days_and_keys_sections_by_range(self):
        self.export(days=5)
        today = date.today()
        days = sorted(p.stem for p in (self.out / ".cache/daily").glob("2*.json"))
        self.assertEqual(days, [str(today - timedelta(days=d)) for d in (4, 3, 2)])
        profile = [p.name for p in (self.out / ".cache/sections").glob("profile@*")]
        self.assertEqual(profile, [f"profile@{today - timedelta(days=5)}_{today}.json"])

        # A different range re-fetches sections and replaces the old version
        fake = FakeApi()
        self.export(fake, days=10)
        profile = [p.name for p in (self.out / ".cache/sections").glob("profile@*")]
        self.assertEqual(profile, [f"profile@{today - timedelta(days=10)}_{today}.json"])
        self.assertGreater(fake.calls, 0)

    def test_second_run_same_day_uses_cache(self):
        self.export(days=5)
        fake = FakeApi()
        self.export(fake, days=5)
        # Only today/yesterday (13 endpoints each, minus the special-cased HR) are fetched again
        self.assertLess(fake.calls, 40)

    def test_split_writes_part_files(self):
        ex = self.export(days=5, split=True)
        self.assertTrue(ex.written_files)
        self.assertTrue(all("_split_part" in p.name for p in ex.written_files))

    def test_compact_downsamples_heart_rate(self):
        text = self.export(days=3, compact=True, use_cache=False).written_files[0].read_text()
        hr = text[text.index("heartRateValues"):]
        points = hr[:hr.index("]]")].count("],") + 1
        self.assertLessEqual(points, 30)

    def test_cancel_saves_partial_export(self):
        def stop_in_daily_health(_, label):
            if label.startswith("Daily Health 2"):
                api.CANCEL.set()
        ex = self.export(days=20, use_cache=False, progress=stop_in_daily_health)
        self.assertTrue(ex.interrupted)
        self.assertIn("interrupted by user", ex.written_files[0].read_text())

    def test_update_starts_from_previous_export(self):
        self.export(days=5)
        ex = self.export(update=True)
        self.assertTrue(ex.written_files[0].name.endswith("_update.txt"))
        self.assertEqual(ex.start_date, date.today() - timedelta(days=1))

    def test_section_selection(self):
        ex = self.export(days=3, sections=["activities", "Daily Health"])
        text = ex.written_files[0].read_text()
        self.assertEqual([s.name for s in ex.sections], ["Daily Health", "Activities"])
        self.assertIn("\nActivities\n", text)
        self.assertNotIn("\nProfile\n", text)
        self.assertIn("selected Garmin Connect", text)
        with self.assertRaises(ValueError):
            self.export(sections=["nope"])

    def test_custom_date_range(self):
        start, end = date(2025, 3, 1), date(2025, 3, 11)
        ex = self.export(start=start, end=end, sections=["daily_health", "trends"])
        self.assertEqual((ex.start_date, ex.today, ex.days), (start, end, 10))
        text = ex.written_files[0].read_text()
        self.assertIn("Date range: 2025-03-01 to 2025-03-11 (10 days)", text)
        self.assertIn("\n2025-03-11\n", text)
        self.assertNotIn(f"\n{date.today()}\n", text)
        # Past days of a custom range are cached like any past day
        self.assertTrue((self.out / ".cache/daily/2025-03-05.json").exists())
        with self.assertRaises(ValueError):
            self.export(start=end, end=start)

    def test_end_in_future_is_clamped_to_today(self):
        ex = self.export(start=date.today() - timedelta(days=2), end=date.today() + timedelta(days=30),
                         sections=["profile"])
        self.assertEqual(ex.today, date.today())

    def test_cache_size_and_clear(self):
        self.export(days=5)
        files, size = cache_size(self.out)
        self.assertGreater(files, 0)
        self.assertGreater(size, 0)
        clear_cache(self.out)
        self.assertEqual(cache_size(self.out), (0, 0))
        self.assertTrue(list(self.out.glob("garmin_export_*.txt")), "exports are kept")


class UpdateCheckTests(unittest.TestCase):
    def test_version_comparison(self):
        self.assertTrue(updates.is_newer("v0.2.0", "0.1.1"))
        self.assertTrue(updates.is_newer("v0.1.10", "0.1.9"))
        self.assertFalse(updates.is_newer("v0.1.1", "0.1.1"))
        self.assertFalse(updates.is_newer("v0.1.0", "0.1.1"))
        self.assertFalse(updates.is_newer("nightly", "0.1.1"))

    def test_offline_is_silent(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("offline")):
            self.assertIsNone(updates.newer_release())


class OutputTests(unittest.TestCase):
    def test_strip_empty(self):
        # Empty containers go away in dicts; in lists only None is dropped (keeps positions)
        self.assertEqual(strip_empty({"a": None, "b": [], "c": {"d": ""}, "e": [1, None, {}], "f": 0}),
                         {"e": [1, {}], "f": 0})

    def test_downsample_both_formats(self):
        rows = [[i, float(i)] for i in range(240)]
        self.assertEqual(len(downsample_timeseries(rows)), 24)
        dicts = [{"t": i, "v": i} for i in range(240)]
        out = downsample_timeseries(dicts)
        self.assertEqual(len(out), 24)
        self.assertEqual(out[0], {"t": 4.5, "v": 4.5})


if __name__ == "__main__":
    unittest.main()

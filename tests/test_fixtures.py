"""Export tests on Garmin-shaped data (tests/fixtures/<name>/).

Each fixture folder holds cache files: daily/<YYYY-MM-DD>.json (the 13 daily
endpoints) and activities/<id>.json. garmin_sample is synthetic; add your own
anonymized fixtures with tools/anonymize_cache.py. The test copies them into
an export cache and runs real exports over their date range, so the output
code sees the structures Garmin actually returns.
"""

import json
import os
import re
import shutil
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from garmin_export import api
from garmin_export.exporter import ExportOptions, run_export

os.environ["GARMIN_EXPORT_KEYRING"] = "0"
FIXTURES = Path(__file__).parent / "fixtures"


def _lists(obj):
    """Lists directly inside obj (or inside the objects of a list obj)."""
    items = obj if isinstance(obj, list) and all(isinstance(i, dict) for i in obj) else [obj]
    return [v for i in items if isinstance(i, dict) for v in i.values() if isinstance(v, list)]


class FixtureApi:
    """Serves the activity list from the fixture; everything else has no data."""

    def __init__(self, activities):
        self.activities = activities

    def get_activities(self, start, limit):
        return self.activities[:limit]

    def get_activities_by_date(self, *args):
        return self.activities

    def __getattr__(self, name):
        if name.startswith("get_"):
            return lambda *a, **k: None
        raise AttributeError(name)


class FixtureExportTests(unittest.TestCase):
    def fixtures(self):
        found = [d for d in sorted(FIXTURES.iterdir()) if (d / "daily").is_dir()]
        self.assertTrue(found, "no fixture folders")
        return found

    def run_on(self, fixture: Path, **opts):
        out = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, out)
        shutil.copytree(fixture / "daily", out / ".cache" / "daily")
        if (fixture / "activities").is_dir():
            shutil.copytree(fixture / "activities", out / ".cache" / "activities")
        days = sorted(date.fromisoformat(p.stem) for p in (fixture / "daily").glob("*.json"))
        acts = [json.loads(p.read_text())["summary"]
                for p in sorted((fixture / "activities").glob("*.json"))]
        options = ExportOptions(delay=0.0, start=days[0] - timedelta(days=1), end=days[-1],
                                sections=["daily_health", "activities"], **opts)
        ex = run_export(FixtureApi(acts), out, options)
        return ex, days, acts, ex.written_files[0].read_text(encoding="utf-8")

    def setUp(self):
        api.CANCEL.clear()

    def test_full_export(self):
        for fixture in self.fixtures():
            with self.subTest(fixture.name):
                ex, days, acts, text = self.run_on(fixture)
                self.assertEqual(ex.errors, [])
                self.assertEqual(ex.cache.misses, 0, "everything comes from the fixture")
                for d in days:
                    self.assertIn(f"\n{d}\n", text)
                for a in acts:
                    self.assertIn(f"Activity {a['activityId']}:", text)
                self.assertIn("heartRateValues", text)

    def test_compact_export(self):
        for fixture in self.fixtures():
            with self.subTest(fixture.name):
                ex, days, acts, text = self.run_on(fixture, compact=True)
                self.assertEqual(ex.errors, [])
                blocks = [json.loads(line) for line in text.splitlines() if line.startswith(("{", "["))]
                daily = next(b for b in blocks if isinstance(b, dict) and str(days[-1]) in b)
                self.assertEqual(sorted(daily), sorted(str(d) for d in days))
                for ds, day in daily.items():
                    for key in ("Heart Rate", "Stress", "Respiration", "Sleep", "Heart Rate Variability",
                                "Body Battery", "Intensity Minutes"):
                        for series in _lists(day.get(key)):
                            self.assertLessEqual(len(series), 24, f"{ds} {key} not downsampled")
                activities = next(b for b in blocks if isinstance(b, list))
                self.assertEqual(len(activities), len(acts))
                self.assertTrue(all("details" not in a for a in activities), "time-series dropped")

    def test_no_personal_data_in_fixtures(self):
        email = re.compile(r"[^@\s\"]+@(?!example\.com)[^@\s\"]+\.[a-z]{2,}", re.I)
        for f in FIXTURES.rglob("*.json"):
            text = f.read_text(encoding="utf-8")
            self.assertIsNone(email.search(text), f"email address in {f}")
            for coord in re.findall(r'"(?:start|end)?(?:Latitude|Longitude|lat|lon)": (-?[\d.]+)', text):
                self.assertEqual(float(coord), 0.0, f"GPS coordinate in {f}")


if __name__ == "__main__":
    unittest.main()

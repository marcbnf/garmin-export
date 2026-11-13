"""Offline stand-in for garminconnect.Garmin (no network)."""

import os

from garminconnect import GarminConnectConnectionError, GarminConnectNotFoundError

# Tests must never read or write the real OS credential store
os.environ["GARMIN_EXPORT_KEYRING"] = "0"


class _Response:
    def __init__(self, status_code: int):
        self.status_code = status_code


def http_error(status: int) -> GarminConnectConnectionError:
    """Error shaped like the ones garminconnect >= 0.3 raises for API calls."""
    cls = GarminConnectNotFoundError if status == 404 else GarminConnectConnectionError
    err = cls(f"API Error {status}")
    err.response = _Response(status)
    return err


class FakeApi:
    """Returns plausible data for every get_* method, with a few special cases:

    - golf: 404 (feature not used)
    - hydration: one 429 on the first call, then data (tests the retry)
    - heart rate: 600 points per day (tests compact downsampling)
    """

    def __init__(self, n_activities: int = 3):
        self.n_activities = n_activities
        self.calls = 0
        self._rate_limited_once = False

    def get_activities(self, start, limit):
        return [{"activityId": 100 + i, "activityName": f"Run {i}",
                 "activityType": {"typeKey": "running"},
                 "startTimeLocal": "2026-01-01 08:00:00"} for i in range(self.n_activities)]

    def get_activities_by_date(self, *args):
        return self.get_activities(0, self.n_activities)

    def get_golf_summary(self, *args):
        raise http_error(404)

    def get_hydration_data(self, ds):
        if not self._rate_limited_once:
            self._rate_limited_once = True
            raise http_error(429)
        return {"valueInML": 1500, "date": ds}

    def get_heart_rates(self, ds):
        return {"heartRateValues": [[i * 60000, 60 + i % 10] for i in range(600)]}

    def __getattr__(self, name):
        if not name.startswith("get_"):
            raise AttributeError(name)

        def call(*args, **kwargs):
            self.calls += 1
            return {"endpoint": name, "args": [str(a) for a in args]}
        return call

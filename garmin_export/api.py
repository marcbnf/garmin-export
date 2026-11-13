"""Rate-limited, cancellable calls to the Garmin Connect API."""

import logging
import threading
import time
from datetime import date, timedelta
from typing import Any

from garminconnect import GarminConnectTooManyRequestsError

log = logging.getLogger(__name__)

# Set by the GUI's "Stop" button; checked before every API call.
CANCEL = threading.Event()


class ExportCancelled(KeyboardInterrupt):
    """Raised when CANCEL is set. Subclasses KeyboardInterrupt so the exporter
    handles it exactly like Ctrl-C (saves a partial export)."""


class RateLimiter:
    """Adaptive, thread-safe pacer. Only slows down when Garmin pushes back.

    Concurrent fetches (daily health uses 4 threads) all go through one lock,
    so the overall request rate stays bounded.
    """

    def __init__(self, base_delay: float = 0.15):
        self._lock = threading.Lock()
        self.reset(base_delay)

    def reset(self, base_delay: float = 0.15):
        with self._lock:
            self.base_delay = base_delay
            self.current_delay = base_delay
            self.call_count = 0
            self.last_call = 0.0
            self.consecutive_ok = 0

    def wait(self):
        with self._lock:
            elapsed = time.time() - self.last_call
            if elapsed < self.current_delay:
                time.sleep(self.current_delay - elapsed)
            self.last_call = time.time()
            self.call_count += 1
            # Light breather every 250 calls
            if self.call_count % 250 == 0:
                log.info(f"  Pacing break after {self.call_count} API calls...")
                time.sleep(2)

    def on_success(self):
        with self._lock:
            self.consecutive_ok += 1
            if self.consecutive_ok > 10 and self.current_delay > self.base_delay:
                self.current_delay = max(self.base_delay, self.current_delay * 0.9)

    def on_rate_limit(self):
        with self._lock:
            self.consecutive_ok = 0
            self.current_delay = min(self.current_delay * 2, 10.0)
            log.warning(f"  Rate limited -- delay now {self.current_delay:.1f}s, waiting 60s...")
        CANCEL.wait(60)  # interruptible sleep

    def on_error(self):
        with self._lock:
            self.consecutive_ok = 0
            self.current_delay = min(self.current_delay * 1.2, 5.0)


# One pacer for the whole process: there is only one Garmin account to protect.
limiter = RateLimiter()


def http_status(exc: BaseException) -> int | None:
    """HTTP status attached to a garminconnect error (``exc.response``), if any."""
    return getattr(getattr(exc, "response", None), "status_code", None)


def is_rate_limited(exc: BaseException) -> bool:
    # garminconnect >= 0.3 raises GarminConnectConnectionError with a 429
    # response for API calls; TooManyRequestsError only during login.
    return isinstance(exc, GarminConnectTooManyRequestsError) or http_status(exc) == 429


def safe_call(fn, *args, label: str = "", **kwargs) -> Any | None:
    """Call a Garmin API method with pacing, one retry on 429, and no exceptions.

    Returns None on any error (missing data is normal for many accounts).
    Raises ExportCancelled if the user asked to stop.
    """
    for attempt in (1, 2):
        if CANCEL.is_set():
            raise ExportCancelled()
        limiter.wait()
        try:
            result = fn(*args, **kwargs)
            limiter.on_success()
            return result
        except Exception as e:
            if is_rate_limited(e) and attempt == 1:
                limiter.on_rate_limit()
                continue  # retry once after backing off
            status = http_status(e)
            if status in (400, 404):
                # Endpoint not available for this account/date -- expected
                log.debug(f"  Not available ({status}) [{label}]")
            elif is_rate_limited(e):
                log.warning(f"  Still rate limited, skipping [{label}]")
            else:
                log.warning(f"  API error [{label}]: {e}")
                limiter.on_error()
            return None
    return None


def chunked_date_call(fn, start: date, end: date, label: str, chunk_days: int = 365):
    """Call a date-range API in yearly chunks and merge the results.

    Some Garmin endpoints reject ranges longer than ~1 year with a 400.
    """
    all_results = []
    chunk_start = start
    while chunk_start < end:
        chunk_end = min(chunk_start + timedelta(days=chunk_days), end)
        result = safe_call(fn, chunk_start.isoformat(), chunk_end.isoformat(),
                           label=f"{label}_{chunk_start}")
        if result is not None:
            if isinstance(result, list):
                all_results.extend(result)
            else:
                all_results.append(result)
        chunk_start = chunk_end + timedelta(days=1)
    return all_results if all_results else None

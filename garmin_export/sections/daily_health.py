"""Daily Health: 13 per-day endpoints, fetched 4 at a time."""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import TYPE_CHECKING

from ..api import limiter, safe_call
from ..output import compact_daily

if TYPE_CHECKING:
    from ..exporter import GarminExporter

log = logging.getLogger(__name__)


def export(ex: GarminExporter) -> None:
    weeks = ex.days / 7
    months = ex.days / 30.44
    log.info(f"  {ex.days} days to process ({months:.1f} months / {weeks:.0f} weeks)")
    log.info(f"  Date range: {ex.start_date} to {ex.today}")
    log.info("  13 API calls per day, fetching 4 at a time")

    ex.doc.append("\nDaily Health\n")

    # Endpoint keys in display order
    endpoint_keys = [
        "summary", "heart_rate", "rhr", "sleep", "stress", "spo2",
        "respiration", "hrv", "body_battery", "bb_events",
        "intensity_min", "events", "lifestyle",
    ]

    display_names = {
        "summary": "Daily Summary", "heart_rate": "Heart Rate",
        "rhr": "Resting Heart Rate", "sleep": "Sleep", "stress": "Stress",
        "spo2": "Blood Oxygen (SpO2)", "respiration": "Respiration",
        "hrv": "Heart Rate Variability", "body_battery": "Body Battery",
        "bb_events": "Body Battery Events", "intensity_min": "Intensity Minutes",
        "events": "All Day Events", "lifestyle": "Lifestyle Logging",
    }

    def _fetch_endpoint(key, ds):
        """Fetch a single endpoint for a given date. Runs in a thread."""
        api = ex.api
        if key == "summary":
            return safe_call(api.get_user_summary, ds, label=f"summary_{ds}")
        elif key == "heart_rate":
            return safe_call(api.get_heart_rates, ds, label=f"hr_{ds}")
        elif key == "rhr":
            return safe_call(api.get_rhr_day, ds, label=f"rhr_{ds}")
        elif key == "sleep":
            return safe_call(api.get_sleep_data, ds, label=f"sleep_{ds}")
        elif key == "stress":
            return safe_call(api.get_all_day_stress, ds, label=f"stress_{ds}")
        elif key == "spo2":
            return safe_call(api.get_spo2_data, ds, label=f"spo2_{ds}")
        elif key == "respiration":
            return safe_call(api.get_respiration_data, ds, label=f"resp_{ds}")
        elif key == "hrv":
            return safe_call(api.get_hrv_data, ds, label=f"hrv_{ds}")
        elif key == "body_battery":
            return safe_call(api.get_body_battery, ds, ds, label=f"bb_{ds}")
        elif key == "bb_events":
            return safe_call(api.get_body_battery_events, ds, label=f"bbe_{ds}")
        elif key == "intensity_min":
            return safe_call(api.get_intensity_minutes_data, ds, label=f"im_{ds}")
        elif key == "events":
            return safe_call(api.get_all_day_events, ds, label=f"events_{ds}")
        elif key == "lifestyle":
            return safe_call(api.get_lifestyle_logging_data, ds, label=f"ll_{ds}")

    t_start = time.time()
    cached_days = 0
    if ex.compact:
        all_days = {}

    for i in range(ex.days):
        d = ex.today - timedelta(days=i)
        ds = d.isoformat()

        # Check cache first
        day_data = ex.cache.get_day(ds)
        if day_data is not None:
            cached_days += 1
        else:
            # Fetch all 13 endpoints concurrently (4 threads)
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = {key: pool.submit(_fetch_endpoint, key, ds) for key in endpoint_keys}
            # Collect in endpoint order so the output is deterministic
            day_data = {}
            for key, future in futures.items():
                try:
                    day_data[key] = future.result()
                except Exception:
                    day_data[key] = None

            ex.cache.put_day(ds, day_data)

        # Write to markdown
        if ex.compact:
            write_data = compact_daily(day_data)
            merged = {display_names.get(k, k): v for k, v in write_data.items() if v is not None}
            if merged:
                all_days[ds] = merged
        else:
            ex.doc.append(f"{ds}\n")
            for key in endpoint_keys:
                ex.doc.section(display_names[key], day_data.get(key))

        # Progress reporting -- frequent early on, then every 25 days
        done = i + 1
        ex.progress(done / max(1, ex.days), f"Daily Health {done}/{ex.days}")
        report_interval = 5 if done <= 25 else 25
        if done % report_interval == 0 or done == 1 or done == ex.days:
            elapsed = time.time() - t_start
            d_display = d.isoformat()
            if done > cached_days and (done - cached_days) > 0:
                fetched = done - cached_days
                per_day = elapsed / fetched
                remaining_fetch = max(0, ex.days - done)
                eta_sec = remaining_fetch * per_day
                eta_min = eta_sec / 60
                log.info(f"  {done}/{ex.days} days ({d_display}) | "
                         f"{cached_days} cached | "
                         f"{limiter.call_count} calls | ~{eta_min:.0f}m remaining")
            else:
                log.info(f"  {done}/{ex.days} days ({d_display}) | "
                         f"{cached_days} cached (all from cache so far)")

    if ex.compact:
        if all_days:
            ex.doc.append('Schema: "Object keyed by ISO date (YYYY-MM-DD). Each day contains up to 13 endpoints: Daily Summary, Heart Rate, Resting Heart Rate, Sleep, Stress, Blood Oxygen (SpO2), Respiration, Heart Rate Variability, Body Battery, Body Battery Events, Intensity Minutes, All Day Events, Lifestyle Logging. High-frequency time-series downsampled to ~24 hourly data points."\n')
            ex.doc.append(f"{ex.doc.json(all_days)}\n")
        else:
            ex.doc.nodata()

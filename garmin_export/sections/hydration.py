"""Hydration: per-day fluid intake, fetched 4 days at a time."""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import TYPE_CHECKING

from ..api import safe_call

if TYPE_CHECKING:
    from ..exporter import GarminExporter

log = logging.getLogger(__name__)


def export(ex: GarminExporter) -> None:
    ex.doc.append("\nHydration\n")
    log.info(f"  {ex.days} days to check")

    # Collect all days, split cached vs uncached
    days_list = []
    cached_results = {}
    uncached_dates = []
    for i in range(ex.days):
        d = ex.today - timedelta(days=i)
        ds = d.isoformat()
        days_list.append(ds)
        cached = ex.cache.get_day(f"hydration_{ds}")
        if cached is not None:
            cached_results[ds] = cached
        else:
            uncached_dates.append(ds)

    log.info(f"  {len(cached_results)} cached, {len(uncached_dates)} to fetch")

    # Fetch uncached days concurrently
    fetched_results = {}
    if uncached_dates:
        api = ex.api
        def _fetch_hydration(ds):
            return ds, safe_call(api.get_hydration_data, ds, label=f"hydration_{ds}")

        t_start = time.time()
        done = 0
        with ThreadPoolExecutor(max_workers=4) as pool:
            for ds, result in pool.map(lambda ds: _fetch_hydration(ds), uncached_dates):
                data = {"hydration": result}
                ex.cache.put_day(f"hydration_{ds}", data)
                fetched_results[ds] = data
                done += 1
                if done % 50 == 0 or done == len(uncached_dates):
                    elapsed = time.time() - t_start
                    remaining = len(uncached_dates) - done
                    eta = (elapsed / done * remaining / 60) if done else 0
                    log.info(f"    {done}/{len(uncached_dates)} fetched | ~{eta:.0f}m remaining")

    # Write markdown in chronological order
    if ex.compact:
        all_days = {}
        for ds in days_list:
            day_data = cached_results.get(ds) or fetched_results.get(ds)
            if day_data and day_data.get("hydration"):
                all_days[ds] = day_data["hydration"]
        if all_days:
            ex.doc.append('Schema: "Object keyed by ISO date (YYYY-MM-DD). Each day contains fluid intake data: cups consumed, goal, intake records with timestamps."\n')
            ex.doc.append(f"{ex.doc.json(all_days)}\n")
        else:
            ex.doc.nodata()
    else:
        has_data = False
        for ds in days_list:
            day_data = cached_results.get(ds) or fetched_results.get(ds)
            if day_data and day_data.get("hydration"):
                has_data = True
                ex.doc.append(f"{ds}\n")
                ex.doc.section("Hydration", day_data["hydration"])
        if not has_data:
            ex.doc.nodata()

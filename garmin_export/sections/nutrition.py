"""Nutrition: per-day food logs, meals, settings (3 calls/day)."""

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
    ex.doc.append("\nNutrition\n")
    log.info(f"  {ex.days} days to check")

    # Collect all days, split cached vs uncached
    days_list = []
    cached_results = {}
    uncached_dates = []
    for i in range(ex.days):
        d = ex.today - timedelta(days=i)
        ds = d.isoformat()
        days_list.append(ds)
        cached = ex.cache.get_day(f"nutrition_{ds}")
        if cached is not None:
            cached_results[ds] = cached
        else:
            uncached_dates.append(ds)

    log.info(f"  {len(cached_results)} cached, {len(uncached_dates)} to fetch")

    # Fetch uncached days concurrently (3 API calls per day)
    fetched_results = {}
    if uncached_dates:
        api = ex.api
        def _fetch_nutrition(ds):
            fl = safe_call(api.get_nutrition_daily_food_log, ds, label=f"food_{ds}")
            ml = safe_call(api.get_nutrition_daily_meals, ds, label=f"meals_{ds}")
            st = safe_call(api.get_nutrition_daily_settings, ds, label=f"nutr_set_{ds}")
            return ds, {"food_log": fl, "meals": ml, "settings": st}

        t_start = time.time()
        done = 0
        with ThreadPoolExecutor(max_workers=4) as pool:
            for ds, data in pool.map(lambda ds: _fetch_nutrition(ds), uncached_dates):
                ex.cache.put_day(f"nutrition_{ds}", data)
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
            if day_data and any(day_data.get(k) for k in ("food_log", "meals", "settings")):
                merged = {k: v for k, v in day_data.items() if v is not None}
                all_days[ds] = merged
        if all_days:
            ex.doc.append('Schema: "Object keyed by ISO date (YYYY-MM-DD). Each day may contain: food_log (daily intake totals), meals (individual meal entries), settings (nutrition goals/targets)."\n')
            ex.doc.append(f"{ex.doc.json(all_days)}\n")
        else:
            ex.doc.nodata()
    else:
        has_data = False
        for ds in days_list:
            day_data = cached_results.get(ds) or fetched_results.get(ds)
            if day_data and any(day_data.get(k) for k in ("food_log", "meals", "settings")):
                has_data = True
                ex.doc.append(f"{ds}\n")
                ex.doc.section("Food Log", day_data.get("food_log"))
                ex.doc.section("Meals", day_data.get("meals"))
                ex.doc.section("Nutrition Settings", day_data.get("settings"))
        if not has_data:
            ex.doc.nodata()

"""Activities: list, then summary/splits/zones/weather/time-series per activity."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from ..api import limiter, safe_call

if TYPE_CHECKING:
    from ..exporter import GarminExporter

log = logging.getLogger(__name__)


def export(ex: GarminExporter) -> None:
    ex.doc.append("\nActivities\n")

    if ex.fetch_all or ex.update_mode:
        activities = safe_call(
            ex.api.get_activities_by_date,
            ex.start_date.isoformat(), ex.today.isoformat(), None,
            label="activities_all",
        ) or []
    elif ex.custom_range:
        activities = (safe_call(
            ex.api.get_activities_by_date,
            ex.start_date.isoformat(), ex.today.isoformat(), None,
            label="activities_range",
        ) or [])[:ex.max_activities]
    else:
        activities = safe_call(
            ex.api.get_activities, 0, ex.max_activities,
            label="activities_list",
        ) or []
        if not activities:
            activities = safe_call(
                ex.api.get_activities_by_date,
                ex.start_date.isoformat(), ex.today.isoformat(), "",
                label="activities_by_date",
            ) or []

    ex.doc.append(f"Total activities found: {len(activities)}\n")
    log.info(f"  {len(activities)} activities found, 10 API calls each = up to {len(activities) * 10:,} calls")

    t_start = time.time()
    cached_acts = 0
    if ex.compact:
        all_activities = []

    for i, act in enumerate(activities):
        aid = act.get("activityId", i)
        name = act.get("activityName") or "Unnamed"
        atype = (act.get("activityType", {}).get("typeKey", "?")
                 if isinstance(act.get("activityType"), dict)
                 else str(act.get("activityType", "?")))
        start = act.get("startTimeLocal", "")

        if not ex.compact:
            ex.doc.append(f"Activity {aid}: {name}\n")
            ex.doc.append(f"Type: {atype} | Date: {start}\n")

        # Check cache
        act_data = ex.cache.get_activity(aid)
        if act_data is not None:
            cached_acts += 1
        else:
            # Fetch from API and cache
            act_data = {"summary": act}
            act_data["detail"] = safe_call(ex.api.get_activity, aid, label=f"act_{aid}")
            act_data["splits"] = safe_call(ex.api.get_activity_splits, aid, label=f"splits_{aid}")
            act_data["split_summaries"] = safe_call(ex.api.get_activity_split_summaries, aid, label=f"ss_{aid}")
            act_data["typed_splits"] = safe_call(ex.api.get_activity_typed_splits, aid, label=f"typed_splits_{aid}")
            act_data["weather"] = safe_call(ex.api.get_activity_weather, aid, label=f"wx_{aid}")
            act_data["hr_zones"] = safe_call(ex.api.get_activity_hr_in_timezones, aid, label=f"hrz_{aid}")
            act_data["power_zones"] = safe_call(ex.api.get_activity_power_in_timezones, aid, label=f"pwrz_{aid}")
            act_data["exercise_sets"] = safe_call(ex.api.get_activity_exercise_sets, aid, label=f"sets_{aid}")
            act_data["details"] = safe_call(ex.api.get_activity_details, aid, label=f"details_{aid}")
            ex.cache.put_activity(aid, act_data)

        if ex.compact:
            act_display_keys = ["summary", "detail", "splits", "split_summaries",
                                "typed_splits", "weather", "hr_zones", "power_zones",
                                "exercise_sets"]
            merged = {}
            for k in act_display_keys:
                v = act_data.get(k)
                if v is not None:
                    merged[k] = v
            if merged:
                all_activities.append(merged)
        else:
            ex.doc.section("Activity Summary", act_data.get("summary"))
            ex.doc.section("Full Activity Detail", act_data.get("detail"))
            ex.doc.section("Splits", act_data.get("splits"))
            ex.doc.section("Split Summaries", act_data.get("split_summaries"))
            ex.doc.section("Typed Splits", act_data.get("typed_splits"))
            ex.doc.section("Weather", act_data.get("weather"))
            ex.doc.section("HR Zones", act_data.get("hr_zones"))
            ex.doc.section("Power Zones", act_data.get("power_zones"))
            ex.doc.section("Exercise Sets", act_data.get("exercise_sets"))
            ex.doc.section("Time-Series Details", act_data.get("details"))

        done = i + 1
        ex.progress(done / len(activities), f"Activities {done}/{len(activities)}")
        if done % 10 == 0 or done == len(activities):
            elapsed = time.time() - t_start
            if done > cached_acts:
                fetched = done - cached_acts
                per_act = elapsed / fetched
                remaining = max(0, len(activities) - done)
                eta_min = (remaining * per_act) / 60
                log.info(f"  {done}/{len(activities)} activities | {cached_acts} cached | "
                         f"{limiter.call_count} calls | ~{eta_min:.0f}m remaining")
            else:
                log.info(f"  {done}/{len(activities)} activities | {cached_acts} cached")

    if ex.compact:
        if all_activities:
            ex.doc.append('Schema: "Array of activity objects. Each contains: summary (overview, stats), detail (full activity record), splits, split_summaries, typed_splits, weather, hr_zones, power_zones, exercise_sets. Time-series details omitted for size."\n')
            ex.doc.append(f"{ex.doc.json(all_activities)}\n")
        else:
            ex.doc.nodata()

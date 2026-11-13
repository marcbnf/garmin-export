"""Trends: weekly aggregates, daily steps, floors, progress summaries."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..api import chunked_date_call, safe_call

if TYPE_CHECKING:
    from ..exporter import GarminExporter


def export(ex: GarminExporter) -> None:
    ex.doc.append("\nTrends\n")

    cached = ex.cache.get_section(ex.section_key("trends"))
    if cached is not None and not ex.update_mode:
        data = cached
    else:
        start_s = ex.start_date.isoformat()
        today_s = ex.today.isoformat()

        data = {}
        data["daily_steps"] = safe_call(ex.api.get_daily_steps, start_s, today_s, label="daily_steps")
        data["weekly_steps"] = safe_call(ex.api.get_weekly_steps, today_s, 52, label="weekly_steps")
        data["weekly_stress"] = safe_call(ex.api.get_weekly_stress, today_s, 52, label="weekly_stress")
        data["weekly_im"] = chunked_date_call(ex.api.get_weekly_intensity_minutes,
                                                ex.start_date, ex.today, "weekly_im")
        data["floors"] = safe_call(ex.api.get_floors, start_s, label="floors")

        for metric in ("distance", "duration", "elevationGain", "calories"):
            result = safe_call(
                ex.api.get_progress_summary_between_dates,
                start_s, today_s, metric, True,
                label=f"progress_{metric}",
            )
            data[f"progress_{metric}"] = result

        # Body battery range endpoint returns 400 for any date span;
        # per-day body battery is already captured in daily health sections.
        data["bb_range"] = None
        ex.cache.put_section(ex.section_key("trends"), data)

    if ex.compact:
        compact_data = {k: v for k, v in data.items() if k != "bb_range"}
        ex.doc.append('Schema: "daily_steps, weekly_steps (52 weeks), weekly_stress (52 weeks), weekly_im (intensity minutes), floors, progress_distance, progress_duration, progress_elevationGain, progress_calories."\n')
        ex.doc.append(f"{ex.doc.json(compact_data)}\n")
    else:
        ex.doc.section("Daily Steps", data.get("daily_steps"))
        ex.doc.section("Weekly Steps (52 weeks)", data.get("weekly_steps"))
        ex.doc.section("Weekly Stress (52 weeks)", data.get("weekly_stress"))
        ex.doc.section("Weekly Intensity Minutes", data.get("weekly_im"))
        ex.doc.section("Floors", data.get("floors"))
        for metric in ("distance", "duration", "elevationGain", "calories"):
            ex.doc.section(f"Progress: {metric}", data.get(f"progress_{metric}"))

"""Goals and Records: personal records, badges, goals."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..api import safe_call

if TYPE_CHECKING:
    from ..exporter import GarminExporter


def export(ex: GarminExporter) -> None:
    ex.doc.append("\nGoals and Records\n")

    cached = ex.cache.get_section(ex.section_key("goals"))
    if cached is not None and not ex.update_mode:
        data = cached
    else:
        data = {}
        data["personal_records"] = safe_call(ex.api.get_personal_record, label="personal_records")
        data["badges"] = safe_call(ex.api.get_earned_badges, label="badges")
        data["active_goals"] = safe_call(ex.api.get_goals, "active", 0, 100, label="active_goals")
        data["past_goals"] = safe_call(ex.api.get_goals, "past", 0, 100, label="past_goals")
        ex.cache.put_section(ex.section_key("goals"), data)

    if ex.compact:
        ex.doc.append('Schema: "personal_records: lifetime bests by activity type. badges: earned achievement badges. active_goals: current goals. past_goals: completed/expired goals."\n')
        ex.doc.append(f"{ex.doc.json(data)}\n")
    else:
        ex.doc.section("Personal Records", data.get("personal_records"))
        ex.doc.section("Earned Badges", data.get("badges"))
        ex.doc.section("Active Goals", data.get("active_goals"))
        ex.doc.section("Past Goals", data.get("past_goals"))

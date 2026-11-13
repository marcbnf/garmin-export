"""Gear: equipment list, per-item stats, activity type defaults."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..api import safe_call

if TYPE_CHECKING:
    from ..exporter import GarminExporter


def export(ex: GarminExporter) -> None:
    ex.doc.append("\nGear\n")

    cached = ex.cache.get_section(ex.section_key("gear"))
    if cached is not None and not ex.update_mode:
        data = cached
    else:
        # Need user profile number for gear endpoints
        profile = safe_call(ex.api.get_user_profile, label="gear_profile")
        profile_num = None
        if profile and isinstance(profile, dict):
            profile_num = str(profile.get("profileNumber") or profile.get("userProfileNumber", ""))

        data = {}
        if profile_num:
            data["gear_list"] = safe_call(ex.api.get_gear, profile_num, label="gear_list")
            data["gear_defaults"] = safe_call(ex.api.get_gear_defaults, profile_num, label="gear_defaults")
        else:
            data["gear_list"] = None
            data["gear_defaults"] = None

        gear_details = []
        if data["gear_list"] and isinstance(data["gear_list"], list):
            for item in data["gear_list"]:
                uuid = item.get("uuid") or item.get("gearUUID")
                if not uuid:
                    continue
                g = {"_uuid": uuid}
                g["stats"] = safe_call(ex.api.get_gear_stats, uuid, label=f"gear_stats_{uuid}")
                gear_details.append(g)

        data["gear_details"] = gear_details
        ex.cache.put_section(ex.section_key("gear"), data)

    if not data.get("gear_list"):
        ex.doc.nodata()
    elif ex.compact:
        ex.doc.append('Schema: "gear_list: equipment items. gear_defaults: per-activity-type defaults. gear_details: array of {_uuid, stats} per item. Empty if no gear."\n')
        ex.doc.append(f"{ex.doc.json(data)}\n")
    else:
        ex.doc.section("Gear List", data.get("gear_list"))
        ex.doc.section("Gear Defaults", data.get("gear_defaults"))
        for g in data.get("gear_details", []):
            ex.doc.section(f"Gear Stats: {g.get('_uuid', '?')}", g.get("stats"))

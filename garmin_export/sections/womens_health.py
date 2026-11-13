"""Women's Health: menstrual calendar, pregnancy summary."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..api import chunked_date_call, safe_call

if TYPE_CHECKING:
    from ..exporter import GarminExporter


def export(ex: GarminExporter) -> None:
    ex.doc.append("\nWomen's Health\n")

    cached = ex.cache.get_section(ex.section_key("womens_health"))
    if cached is not None and not ex.update_mode:
        data = cached
    else:
        data = {}
        data["pregnancy"] = safe_call(ex.api.get_pregnancy_summary, label="pregnancy")

        # Menstrual calendar endpoint returns 400 on accounts without the
        # feature enabled. Try a single recent-range call first; only chunk
        # the full history if it succeeds.
        probe = safe_call(ex.api.get_menstrual_calendar_data,
                          ex.today.isoformat(),
                          ex.today.isoformat(),
                          label="menstrual_probe")
        if probe is not None:
            data["menstrual_calendar"] = chunked_date_call(
                ex.api.get_menstrual_calendar_data,
                ex.start_date, ex.today, "menstrual_cal")
        else:
            data["menstrual_calendar"] = None
        ex.cache.put_section(ex.section_key("womens_health"), data)

    if not any(data.get(k) for k in ("pregnancy", "menstrual_calendar")):
        ex.doc.nodata()
    elif ex.compact:
        ex.doc.append('Schema: "pregnancy: pregnancy tracking summary. menstrual_calendar: cycle history. Features require opt-in on Garmin device. Empty if not enabled."\n')
        ex.doc.append(f"{ex.doc.json(data)}\n")
    else:
        ex.doc.section("Pregnancy Summary", data.get("pregnancy"))
        ex.doc.section("Menstrual Calendar", data.get("menstrual_calendar"))

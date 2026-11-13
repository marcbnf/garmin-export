"""Golf: round summaries, scorecards, shot data."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..api import safe_call

if TYPE_CHECKING:
    from ..exporter import GarminExporter


def export(ex: GarminExporter) -> None:
    ex.doc.append("\nGolf\n")

    cached = ex.cache.get_section(ex.section_key("golf"))
    if cached is not None and not ex.update_mode:
        data = cached
    else:
        data = {}
        summary = safe_call(ex.api.get_golf_summary, label="golf_summary")
        data["summary"] = summary

        scorecards = []
        if summary and isinstance(summary, list):
            for item in summary:
                sc_id = item.get("scorecardId") or item.get("id")
                if not sc_id:
                    continue
                sc = {"_id": sc_id}
                sc["detail"] = safe_call(ex.api.get_golf_scorecard, sc_id, label=f"golf_sc_{sc_id}")
                sc["shots"] = safe_call(ex.api.get_golf_shot_data, sc_id, label=f"golf_shots_{sc_id}")
                scorecards.append(sc)

        data["scorecards"] = scorecards
        ex.cache.put_section(ex.section_key("golf"), data)

    if not data.get("summary") and not data.get("scorecards"):
        ex.doc.nodata()
    elif ex.compact:
        ex.doc.append('Schema: "summary: round list. scorecards: array of {_id, detail, shots} per round. Empty if no golf data."\n')
        ex.doc.append(f"{ex.doc.json(data)}\n")
    else:
        ex.doc.section("Golf Summary", data.get("summary"))
        for sc in data.get("scorecards", []):
            ex.doc.section(f"Scorecard {sc.get('_id', '?')}", sc.get("detail"))
            ex.doc.section(f"Shot Data {sc.get('_id', '?')}", sc.get("shots"))

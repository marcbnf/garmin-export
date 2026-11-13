"""Body Composition: weight, body fat, weigh-ins (yearly chunks)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..api import chunked_date_call

if TYPE_CHECKING:
    from ..exporter import GarminExporter


def export(ex: GarminExporter) -> None:
    ex.doc.append("\nBody Composition\n")

    cached = ex.cache.get_section(ex.section_key("body_comp"))
    if cached is not None and not ex.update_mode:
        data = cached
    else:
        data = {}
        data["body_comp"] = chunked_date_call(ex.api.get_body_composition,
                                               ex.start_date, ex.today, "body_comp")
        data["weigh_ins"] = chunked_date_call(ex.api.get_weigh_ins,
                                               ex.start_date, ex.today, "weigh_ins")
        ex.cache.put_section(ex.section_key("body_comp"), data)

    if ex.compact:
        ex.doc.append('Schema: "body_comp: weight/BMI/body fat percentage history in yearly chunks. weigh_ins: individual scale readings with timestamps."\n')
        ex.doc.append(f"{ex.doc.json(data)}\n")
    else:
        ex.doc.section("Body Composition", data.get("body_comp"))
        ex.doc.section("Weigh-Ins", data.get("weigh_ins"))

"""Training Plans: list, then detail per plan."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..api import safe_call

if TYPE_CHECKING:
    from ..exporter import GarminExporter


def export(ex: GarminExporter) -> None:
    ex.doc.append("\nTraining Plans\n")

    cached = ex.cache.get_section(ex.section_key("training_plans"))
    if cached is not None and not ex.update_mode:
        data = cached
    else:
        data = {}
        plans = safe_call(ex.api.get_training_plans, label="training_plans")
        data["plans"] = plans

        plan_details = []
        if plans and isinstance(plans, list):
            for item in plans:
                pid = item.get("trainingPlanId") or item.get("id")
                if not pid:
                    continue
                p = {"_id": pid}
                # Try standard plan first, then adaptive
                detail = safe_call(ex.api.get_training_plan_by_id, pid, label=f"plan_{pid}")
                if detail is None:
                    detail = safe_call(ex.api.get_adaptive_training_plan_by_id, pid,
                                       label=f"adaptive_plan_{pid}")
                p["detail"] = detail
                plan_details.append(p)

        data["plan_details"] = plan_details
        ex.cache.put_section(ex.section_key("training_plans"), data)

    if not data.get("plans"):
        ex.doc.nodata()
    elif ex.compact:
        ex.doc.append('Schema: "plans: training plan list. plan_details: array of {_id, detail} per plan. Empty if no plans."\n')
        ex.doc.append(f"{ex.doc.json(data)}\n")
    else:
        ex.doc.section("Training Plans", data.get("plans"))
        for p in data.get("plan_details", []):
            ex.doc.section(f"Plan: {p.get('_id', '?')}", p.get("detail"))

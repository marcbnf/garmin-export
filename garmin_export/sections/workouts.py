"""Workouts: list, then detail per workout."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..api import safe_call

if TYPE_CHECKING:
    from ..exporter import GarminExporter


def export(ex: GarminExporter) -> None:
    ex.doc.append("\nWorkouts\n")

    cached = ex.cache.get_section(ex.section_key("workouts"))
    if cached is not None and not ex.update_mode:
        data = cached
    else:
        data = {}
        workout_list = safe_call(ex.api.get_workouts, 0, 1000, label="workouts")
        data["workout_list"] = workout_list

        workout_details = []
        if workout_list and isinstance(workout_list, list):
            for item in workout_list:
                wid = item.get("workoutId") or item.get("id")
                if not wid:
                    continue
                w = {"_id": wid}
                w["detail"] = safe_call(ex.api.get_workout_by_id, wid, label=f"workout_{wid}")
                workout_details.append(w)

        data["workout_details"] = workout_details
        ex.cache.put_section(ex.section_key("workouts"), data)

    if not data.get("workout_list"):
        ex.doc.nodata()
    elif ex.compact:
        ex.doc.append('Schema: "workout_list: saved workout definitions. workout_details: array of {_id, detail} per workout. Empty if no workouts."\n')
        ex.doc.append(f"{ex.doc.json(data)}\n")
    else:
        ex.doc.section("Workout List", data.get("workout_list"))
        for w in data.get("workout_details", []):
            ex.doc.section(f"Workout: {w.get('_id', '?')}", w.get("detail"))

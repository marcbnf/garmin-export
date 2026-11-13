"""Training Metrics: VO2 max, readiness, status, FTP, hill/endurance scores..."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..api import chunked_date_call, safe_call

if TYPE_CHECKING:
    from ..exporter import GarminExporter


def export(ex: GarminExporter) -> None:
    ex.doc.append("\nTraining Metrics\n")

    cached = ex.cache.get_section(ex.section_key("training"))
    if cached is not None and not ex.update_mode:
        data = cached
    else:
        today_s = ex.today.isoformat()

        items = [
            ("training_readiness", "Training Readiness",
             safe_call(ex.api.get_training_readiness, today_s, label="training_readiness")),
            ("morning_readiness", "Morning Training Readiness",
             safe_call(ex.api.get_morning_training_readiness, today_s, label="morning_readiness")),
            ("training_status", "Training Status",
             safe_call(ex.api.get_training_status, today_s, label="training_status")),
            ("max_metrics", "VO2 Max and Max Metrics",
             safe_call(ex.api.get_max_metrics, today_s, label="max_metrics")),
            ("fitness_age", "Fitness Age",
             safe_call(ex.api.get_fitnessage_data, today_s, label="fitness_age")),
            ("lactate_threshold", "Lactate Threshold",
             safe_call(ex.api.get_lactate_threshold, label="lactate_threshold")),
            ("cycling_ftp", "Cycling FTP",
             safe_call(ex.api.get_cycling_ftp, label="cycling_ftp")),
            ("intensity_min", "Intensity Minutes",
             safe_call(ex.api.get_intensity_minutes_data, today_s, label="intensity_min")),
            ("hill_score", "Hill Score",
             chunked_date_call(ex.api.get_hill_score, ex.start_date, ex.today, "hill_score")),
            ("endurance_score", "Endurance Score",
             chunked_date_call(ex.api.get_endurance_score, ex.start_date, ex.today, "endurance_score")),
            ("running_tolerance", "Running Tolerance",
             chunked_date_call(ex.api.get_running_tolerance, ex.start_date, ex.today, "running_tolerance")),
            ("race_predictions", "Race Predictions",
             safe_call(ex.api.get_race_predictions, label="race_predictions")),
        ]

        data = {}
        for key, title, result in items:
            data[key] = result
            data[f"_title_{key}"] = title
        ex.cache.put_section(ex.section_key("training"), data)

    if ex.compact:
        compact_data = {k: v for k, v in data.items() if not k.startswith("_title_")}
        ex.doc.append('Schema: "Training metrics: training_readiness, morning_readiness, training_status, max_metrics (VO2 max), fitness_age, lactate_threshold, cycling_ftp, intensity_min, hill_score (history), endurance_score (history), running_tolerance (history), race_predictions."\n')
        ex.doc.append(f"{ex.doc.json(compact_data)}\n")
    else:
        for key in ["training_readiness", "morning_readiness", "training_status",
                    "max_metrics", "fitness_age", "lactate_threshold", "cycling_ftp",
                    "intensity_min", "hill_score", "endurance_score", "running_tolerance",
                    "race_predictions"]:
            ex.doc.section(data.get(f"_title_{key}", key), data.get(key))

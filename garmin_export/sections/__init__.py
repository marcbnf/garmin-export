"""Export sections, in output order.

Each module exposes ``export(ex: GarminExporter) -> None``, which fetches its
data (through ``ex.api``, ``ex.cache``) and appends it to ``ex.doc``.
To add a section: create a module here and register it in SECTIONS.
"""

from collections.abc import Callable, Iterable
from typing import NamedTuple

from . import (
    activities,
    body_composition,
    daily_health,
    gear,
    goals,
    golf,
    hydration,
    nutrition,
    profile,
    training_metrics,
    training_plans,
    trends,
    womens_health,
    workouts,
)


class Section(NamedTuple):
    name: str           # section title in the output (also used by split mode)
    export: Callable    # export(ex) -> None
    description: str    # one-line summary for the table of contents

    @property
    def key(self) -> str:
        """Short id used on the command line and in settings (module name)."""
        return self.export.__module__.rsplit(".", 1)[-1]


SECTIONS: list[Section] = [
    Section("Profile", profile.export,
            "User info, settings, device details, alarms, supported activity types"),
    Section("Daily Health", daily_health.export,
            "Per-day: steps, heart rate, sleep, stress, body battery, SpO2, HRV, respiration, intensity minutes, all-day events"),
    Section("Activities", activities.export,
            "Per-activity: summary, splits, HR/power zones, exercise sets, weather, time-series data"),
    Section("Body Composition", body_composition.export,
            "Weight, BMI, body fat, muscle/bone mass, body water, weigh-ins (yearly chunks)"),
    Section("Training Metrics", training_metrics.export,
            "VO2 max, fitness age, training readiness/status, lactate threshold, cycling FTP, hill/endurance scores, race predictions"),
    Section("Goals and Records", goals.export,
            "Personal records, earned badges, active and past goals"),
    Section("Trends", trends.export,
            "Weekly aggregates (steps, stress, intensity minutes), daily steps, floors, progress summaries"),
    Section("Golf", golf.export,
            "Round summaries, scorecards, shot data"),
    Section("Gear", gear.export,
            "Equipment list, per-item stats, activity type defaults"),
    Section("Training Plans", training_plans.export,
            "Active and past training plans with full details"),
    Section("Workouts", workouts.export,
            "Saved workout definitions with full structure"),
    Section("Hydration", hydration.export,
            "Per-day fluid intake"),
    Section("Nutrition", nutrition.export,
            "Per-day food logs, meals, nutrition settings"),
    Section("Women's Health", womens_health.export,
            "Menstrual calendar, pregnancy summary"),
]


def select_sections(keys: Iterable[str] | None) -> list[Section]:
    """Sections whose key or name is in keys (all when keys is None), in output order.

    Raises ValueError for an unknown section.
    """
    if keys is None:
        return list(SECTIONS)
    wanted = {_norm(k) for k in keys if k.strip()}
    known = {_norm(s.key) for s in SECTIONS} | {_norm(s.name) for s in SECTIONS}
    unknown = sorted(wanted - known)
    if unknown:
        raise ValueError(f"Unknown section(s): {', '.join(unknown)}. "
                         f"Valid: {', '.join(s.key for s in SECTIONS)}")
    return [s for s in SECTIONS if _norm(s.key) in wanted or _norm(s.name) in wanted]


def _norm(name: str) -> str:
    return name.strip().lower().replace("'", "").replace(" ", "_").replace("-", "_")

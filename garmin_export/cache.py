"""On-disk JSON cache that makes long exports resumable."""

import json
import logging
import shutil
from datetime import date, timedelta
from pathlib import Path

log = logging.getLogger(__name__)


class ExportCache:
    """JSON file cache for day-, activity- and section-level API results.

    Lives in ``{output_dir}/.cache/``:
      daily/        YYYY-MM-DD.json, hydration_YYYY-MM-DD.json, nutrition_YYYY-MM-DD.json
      activities/   {activityId}.json
      sections/     {name}@{start}_{end}.json

    Past days and activities never change, so they are kept forever. Today
    and yesterday are never cached (data still syncing). Sections are keyed
    by their date range, so they are re-fetched once per day while an
    interrupted export still resumes instantly.
    """

    RECENT_DAYS = 1  # don't cache days newer than today - RECENT_DAYS

    def __init__(self, out_dir: Path, enabled: bool = True):
        self.enabled = enabled
        self.cache_dir = out_dir / ".cache"
        self.daily_dir = self.cache_dir / "daily"
        self.activity_dir = self.cache_dir / "activities"
        self.section_dir = self.cache_dir / "sections"
        self.hits = 0
        self.misses = 0
        if not enabled:
            return
        for d in (self.daily_dir, self.activity_dir, self.section_dir):
            d.mkdir(parents=True, exist_ok=True)
        self._log_contents()

    def _log_contents(self):
        daily = [f.name for f in self.daily_dir.glob("*.json")]
        counts = {
            "daily health": sum(1 for n in daily if n[0].isdigit()),
            "hydration": sum(1 for n in daily if n.startswith("hydration_")),
            "nutrition": sum(1 for n in daily if n.startswith("nutrition_")),
            "activities": len(list(self.activity_dir.glob("*.json"))),
            "sections": len(list(self.section_dir.glob("*.json"))),
        }
        parts = [f"{n} {label}" for label, n in counts.items() if n]
        if parts:
            log.info(f"Cache: {', '.join(parts)}")

    # -- generic read/write -------------------------------------------------
    def _get(self, path: Path) -> dict | None:
        if not self.enabled:
            return None
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                self.hits += 1
                return data
            except (json.JSONDecodeError, OSError):
                pass
        self.misses += 1
        return None

    def _put(self, path: Path, data) -> None:
        path.write_text(json.dumps(data, default=str, ensure_ascii=False), encoding="utf-8")

    # -- per day ------------------------------------------------------------
    def get_day(self, ds: str) -> dict | None:
        """ds is "YYYY-MM-DD" or "<prefix>_YYYY-MM-DD"."""
        return self._get(self.daily_dir / f"{ds}.json")

    def put_day(self, ds: str, data: dict):
        day = date.fromisoformat(ds[-10:])
        if self.enabled and day < date.today() - timedelta(days=self.RECENT_DAYS):
            self._put(self.daily_dir / f"{ds}.json", data)

    # -- per activity -------------------------------------------------------
    def get_activity(self, activity_id) -> dict | None:
        return self._get(self.activity_dir / f"{activity_id}.json")

    def put_activity(self, activity_id, data: dict):
        if self.enabled:
            self._put(self.activity_dir / f"{activity_id}.json", data)

    # -- per section --------------------------------------------------------
    def get_section(self, key: str) -> dict | None:
        """key is "{name}@{start}_{end}" (see GarminExporter.section_key)."""
        return self._get(self.section_dir / f"{key}.json")

    def put_section(self, key: str, data: dict):
        if not self.enabled:
            return
        # Drop older versions of this section (other date ranges)
        base = key.split("@")[0]
        for old in self.section_dir.glob(f"{base}@*.json"):
            old.unlink(missing_ok=True)
        self._put(self.section_dir / f"{key}.json", data)

    def summary(self) -> str:
        total = self.hits + self.misses
        if total == 0:
            return "Cache: no lookups"
        return f"Cache: {self.hits} hits, {self.misses} misses ({self.hits / total:.0%} hit rate)"


def cache_dir(out_dir: Path) -> Path:
    return Path(out_dir).expanduser() / ".cache"


def cache_size(out_dir: Path) -> tuple[int, int]:
    """(number of files, total bytes) of the cache in out_dir."""
    files = [p for p in cache_dir(out_dir).rglob("*.json") if p.is_file()]
    return len(files), sum(p.stat().st_size for p in files)


def clear_cache(out_dir: Path) -> None:
    """Delete the cache of out_dir (the next export fetches everything again)."""
    shutil.rmtree(cache_dir(out_dir), ignore_errors=True)

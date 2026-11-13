"""Export orchestration: date range, sections, progress and output files."""

import logging
import re
import traceback
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from garminconnect import Garmin

from .api import CANCEL, limiter, safe_call
from .cache import ExportCache
from .output import Document
from .sections import SECTIONS, Section, select_sections
from .split import write_split

log = logging.getLogger(__name__)

# progress(fraction 0..1, label) -- called from the export thread
ProgressFn = Callable[[float, str], None]


@dataclass
class ExportOptions:
    days: int = 30              # days of daily data (ignored with fetch_all/update)
    max_activities: int = 100   # ignored with fetch_all/update
    fetch_all: bool = False     # complete history, auto-detected start date
    update: bool = False        # only new data since the latest export in out_dir
    compact: bool = False       # smaller output (implied by split and update)
    split: bool = False         # several files under NotebookLM's word limit
    use_cache: bool = True
    delay: float = 0.15         # base delay between API calls (seconds)
    sections: Sequence[str] | None = None  # section keys/names to export (None: all)
    start: date | None = None  # custom range (ignored with fetch_all/update);
    end: date | None = None    # end defaults to today, start to end - days


class GarminExporter:
    """Runs the selected sections and writes the export file(s).

    Sections read: api, cache, doc, compact, split, update_mode, fetch_all,
    start_date, today (last day of the range), days, custom_range, max_activities, and call
    section_key()/progress().
    """

    def __init__(self, api: Garmin, out_dir: Path, options: ExportOptions,
                 progress: ProgressFn | None = None):
        self.api = api
        self.out_dir = out_dir
        self.options = options
        self.update_mode = options.update
        self.fetch_all = options.fetch_all
        self.max_activities = options.max_activities
        self.split = options.split
        self.compact = options.compact or options.split or options.update
        self.doc = Document(compact=self.compact, split=self.split)
        self.cache = ExportCache(out_dir, enabled=options.use_cache)
        self.sections: list[Section] = select_sections(options.sections)
        if not self.sections:
            raise ValueError("No section selected")
        # "today" is the last day of the range: the real today unless a custom end is set
        end = options.end if not (options.update or options.fetch_all) else None
        # Custom range: activities are listed by date (not "the latest N")
        self.custom_range = bool(options.start or options.end) and not (options.update or options.fetch_all)
        self.today = min(end, date.today()) if end else date.today()
        self.errors: list[str] = []
        self.written_files: list[Path] = []
        self.interrupted = False
        self.update_base_date: str | None = None  # end date of the base export
        self._progress_fn = progress
        self._section_idx = 0

        if self.update_mode:
            base_end = self._find_latest_export_end_date()
            if base_end:
                self.update_base_date = base_end.isoformat()
                # Overlap by 1 day to catch late-arriving data
                self.start_date = base_end - timedelta(days=1)
                log.info(f"Update mode: last export ends {base_end}, fetching from {self.start_date}")
            else:
                log.warning("No previous export found -- falling back to the days setting")
                self.start_date = self.today - timedelta(days=options.days)
        elif self.fetch_all:
            self.start_date = self._detect_start_date()
        elif options.start:
            self.start_date = options.start
        else:
            self.start_date = self.today - timedelta(days=options.days)
        if self.start_date >= self.today:
            raise ValueError(f"Empty date range: {self.start_date} to {self.today}")
        self.days = (self.today - self.start_date).days

    # -- helpers used by sections ------------------------------------------
    def progress(self, within: float, label: str) -> None:
        """Report overall progress: current section + fraction within it."""
        if self._progress_fn:
            self._progress_fn(min(1.0, (self._section_idx + within) / len(self.sections)), label)

    def section_key(self, name: str) -> str:
        """Section cache key: section content depends on the date range."""
        return f"{name}@{self.start_date}_{self.today}"

    # -- date range ---------------------------------------------------------
    def _find_latest_export_end_date(self) -> date | None:
        """Find the newest export in out_dir and parse its end date."""
        ts_pattern = re.compile(r'garmin_export_(\d{4}-\d{2}-\d{2}_\d{6})')
        timestamps: dict[str, Path] = {}
        for p in sorted(self.out_dir.glob("garmin_export_*.txt"), reverse=True):
            m = ts_pattern.search(p.name)
            if m:
                timestamps.setdefault(m.group(1), p)  # ignore _partNofM duplicates
        if not timestamps:
            return None

        newest_ts = max(timestamps)
        newest_file = timestamps[newest_ts]
        try:
            with open(newest_file, encoding="utf-8") as f:
                header = f.read(2000)
            m = re.search(r'Date range:\s*(\S+)\s+to\s+(\S+)', header)
            if m:
                end_date = date.fromisoformat(m.group(2))
                log.info(f"Found latest export: {newest_file.name} (ends {end_date})")
                return end_date
        except (OSError, ValueError) as e:
            log.warning(f"Could not parse export header: {e}")

        # Fallback: date from the filename timestamp
        try:
            d = datetime.strptime(newest_ts, "%Y-%m-%d_%H%M%S").date()
            log.info(f"Using filename date as fallback: {d}")
            return d
        except ValueError:
            return None

    def _detect_start_date(self) -> date:
        """Oldest activity minus a week (to catch earlier health data); else 5 years."""
        log.info("Detecting account history range...")
        oldest = safe_call(self.api.get_activities_by_date,
                           "2000-01-01", self.today.isoformat(), None, "asc",
                           label="oldest_activity")
        if oldest and isinstance(oldest, list):
            start_str = (oldest[0].get("startTimeLocal") or "")[:10]
            try:
                d = date.fromisoformat(start_str) - timedelta(days=7)
                log.info(f"Oldest activity found: {start_str}")
                log.info(f"Will export from: {d}")
                return d
            except ValueError:
                pass
        fallback = self.today - timedelta(days=365 * 5)
        log.info(f"Could not detect oldest data, defaulting to {fallback}")
        return fallback

    # -- run ----------------------------------------------------------------
    def run(self) -> None:
        now = datetime.now()
        filename = f"garmin_export_{now.strftime('%Y-%m-%d_%H%M%S')}{'_update' if self.update_mode else ''}.txt"

        log.info(f"Date range: {self.start_date} to {self.today} ({self.days} days)")
        if self.update_mode:
            log.info(f"Mode: update (new data since {self.update_base_date})")
        if self.compact:
            log.info("Mode: compact (smaller output for LLM upload)")
        if self.split:
            log.info("Mode: split (multiple files, <500K words each)")
        if self.fetch_all and not self.update_mode:
            log.info("Mode: full history; max activities: unlimited")
        else:
            log.info(f"Max activities: {self.max_activities}")
        if len(self.sections) < len(SECTIONS):
            log.info(f"Sections: {', '.join(s.name for s in self.sections)}")

        self._write_header(now)

        for idx, section in enumerate(self.sections):
            self._section_idx = idx
            self.progress(0.0, section.name)
            log.info(f"Exporting {section.name}...")
            try:
                section.export(self)
                log.info(f"  Done: {section.name}")
            except KeyboardInterrupt:  # Ctrl-C or ExportCancelled
                log.info(f"  Interrupted during {section.name} -- saving partial export")
                self.errors.append(f"{section.name}: interrupted by user (partial data)")
                self.interrupted = True
                break
            except Exception as e:
                self.errors.append(f"{section.name}: {e}")
                log.error(f"  Failed: {section.name}: {e}")
                log.debug(traceback.format_exc())

        if self.errors:
            self.doc.append("\nErrors During Export\n")
            self.doc.lines += [f"- {err}" for err in self.errors]
            self.doc.append("")

        self._write_files(filename)

        log.info(f"API calls: {limiter.call_count}")
        log.info(self.cache.summary())
        if self.errors:
            log.warning(f"{len(self.errors)} section(s) had errors")

    def _write_header(self, now: datetime) -> None:
        doc = self.doc
        date_range = f"Date range: {self.start_date} to {self.today} ({self.days} days)"
        if self.update_mode:
            doc.append("Garmin Connect Data Export -- Update\n")
            doc.append(f"Exported: {now.isoformat()}")
            doc.append(f"Update for data since: {self.update_base_date}")
            doc.append(date_range)
            doc.append("This file contains only new data. Upload alongside your base export files.\n")
        else:
            doc.append("Garmin Connect Data Export\n")
            doc.append(f"Exported: {now.isoformat()}")
            doc.append(date_range)
            doc.append(f"Max activities: {self.max_activities}")
        if self.compact:
            doc.append("Format: compact (nulls stripped, single-line JSON, "
                       "activity time-series omitted, daily data downsampled to hourly)\n")
        else:
            doc.append("Format: full (complete JSON, all fields)\n")

        # Table of contents -- helps LLMs navigate very large files
        doc.append("Table of Contents\n")
        if self.update_mode:
            doc.append(f"This file contains NEW data since {self.update_base_date}.")
            doc.append("Upload alongside your base export files for complete coverage.")
        else:
            doc.append("This file contains a complete export of Garmin Connect health and fitness data."
                       if len(self.sections) == len(SECTIONS) else
                       "This file contains an export of selected Garmin Connect health and fitness data.")
        if self.compact:
            doc.append("Each section contains one JSON block with a schema description.")
            doc.append("All data is raw JSON from the Garmin Connect API. In compact mode, each section is a single JSON block.")
        else:
            doc.append("Each section has subsections with titled JSON blocks.")
            doc.append("All data is raw JSON from the Garmin Connect API.")
        doc.append("Sections with no data contain a note: No data available.\n")
        for i, section in enumerate(self.sections, 1):
            doc.append(f"  {i}. {section.name} -- {section.description}")
        doc.append("")

    def _write_files(self, filename: str) -> None:
        if self.split:
            self.written_files = write_split(self.doc, self.out_dir, filename,
                                             [s.name for s in self.sections])
            total_kb = sum(p.stat().st_size for p in self.written_files) / 1024
            log.info(f"Export complete: {len(self.written_files)} files in {self.out_dir}")
            log.info(f"Total size: {total_kb:.0f} KB")
        else:
            out_path = self.out_dir / filename
            out_path.write_text(self.doc.text(), encoding="utf-8")
            self.written_files = [out_path]
            log.info(f"Export complete: {out_path}")
            log.info(f"File size: {out_path.stat().st_size / 1024:.0f} KB")


def run_export(api: Garmin, out_dir: Path, options: ExportOptions,
               progress: ProgressFn | None = None) -> GarminExporter:
    """Run a full export. Shared by the CLI and the GUI.

    Returns the exporter (see .written_files, .errors, .interrupted).
    """
    limiter.reset(options.delay)
    out_dir = Path(out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    log.info("Cache: enabled (resume interrupted exports)" if options.use_cache else "Cache: disabled")

    exporter = GarminExporter(api, out_dir, options, progress=progress)
    exporter.run()
    if progress:
        progress(1.0, "Done")
    return exporter


__all__ = ["CANCEL", "ExportOptions", "GarminExporter", "ProgressFn", "run_export"]

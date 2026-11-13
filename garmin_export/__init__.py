"""Garmin Export -- export Garmin Connect data to LLM-friendly plain text.

Package layout:
    app.py        entry point: GUI by default, command line with --no-gui
    gui.py        Tkinter desktop interface
    cli.py        command-line mode
    auth.py       Garmin Connect login and saved tokens
    api.py        rate-limited, cancellable API calls
    cache.py      on-disk cache (resumable exports)
    output.py     text/JSON document writer, compact mode
    split.py      split output into NotebookLM-sized files
    exporter.py   orchestration (date range, sections, progress, files)
    sections/     one module per exported section
    updates.py    new-release check (GitHub)
"""

from pathlib import Path


def _read_version() -> str:
    """Version from the VERSION file: at the repository root when run from
    source, next to this package in the frozen app (build.py bundles it)."""
    here = Path(__file__).resolve().parent
    for f in (here / "VERSION", here.parent / "VERSION"):
        try:
            return f.read_text(encoding="utf-8").strip()
        except OSError:
            continue
    return "0.0.0"


__version__ = _read_version()
APP_NAME = "Garmin Export"
GITHUB_REPO = "marcbnf/garmin-export"

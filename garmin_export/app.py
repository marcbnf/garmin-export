"""Single entry point: desktop app by default, command line with --no-gui."""

import argparse
import sys
from datetime import date

from . import APP_NAME, __version__
from .auth import DEFAULT_TOKENSTORE

EPILOG = """
Without --no-gui, the desktop app opens and the other options are ignored.

Command-line examples:
  main.py --no-gui --login              # just log in & save tokens
  main.py --no-gui                      # last 30 days, 100 activities
  main.py --no-gui --all --split        # everything, split for NotebookLM
  main.py --no-gui --update             # only new data since the last export
  main.py --no-gui --days 90 --activities 500
  main.py --no-gui --from 2025-01-01 --to 2025-12-31
  main.py --no-gui --sections activities,daily_health

Authentication (command line):
  Enter credentials when prompted, or set GARMIN_EMAIL / GARMIN_PASSWORD
  (environment or .env file). Tokens are saved afterwards in the system
  credential store (Keychain, Credential Manager, Secret Service), or in
  the --tokenstore folder when none is available (or GARMIN_EXPORT_KEYRING=0).
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="garmin-export",
        description=f"{APP_NAME}: export Garmin Connect health & fitness data to plain text with JSON.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=EPILOG,
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--no-gui", action="store_true", help="Run in the terminal instead of opening the app")

    g = p.add_argument_group("command-line options (with --no-gui)")
    g.add_argument("--all", action="store_true", help="Export complete history (auto-detects how far back to go)")
    g.add_argument("--days", type=int, default=30, help="Days of daily health data (default: 30)")
    g.add_argument("--from", dest="start", type=_iso_date, metavar="YYYY-MM-DD",
                   help="First day to export (instead of --days)")
    g.add_argument("--to", dest="end", type=_iso_date, metavar="YYYY-MM-DD",
                   help="Last day to export (default: today)")
    g.add_argument("--sections", type=_section_list, metavar="LIST",
                   help="Comma-separated sections to export (default: all). See --list-sections")
    g.add_argument("--list-sections", action="store_true", help="List the section names and exit")
    g.add_argument("--activities", type=int, default=100, help="Max activities to export (default: 100)")
    g.add_argument("--output", type=str, default="export", help="Output directory (default: export)")
    g.add_argument("--tokenstore", type=str, default=None,
                   help=f"Token storage path (default: $GARMINTOKENS or {DEFAULT_TOKENSTORE})")
    g.add_argument("--delay", type=float, default=0.15, help="Base delay between API calls in seconds (default: 0.15)")
    g.add_argument("--no-cache", action="store_true", help="Disable caching (re-fetch everything)")
    g.add_argument("--clear-cache", action="store_true",
                   help="Delete the cache in the output directory, then exit")
    g.add_argument("--compact", action="store_true",
                   help="Smaller output: strip nulls, single-line JSON, drop activity time-series, downsample daily data")
    g.add_argument("--split", action="store_true",
                   help="Split output into files under 500K words each (for NotebookLM). Implies --compact")
    g.add_argument("--update", action="store_true",
                   help="Export only new data since the last export. Implies --compact")
    g.add_argument("--login", action="store_true", help="Just authenticate and save tokens, then exit")
    g.add_argument("--verbose", action="store_true", help="Debug logging")
    return p


def _iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid date {value!r} (expected YYYY-MM-DD)") from None


def _section_list(value: str) -> list[str]:
    from .sections import select_sections
    keys = [k for k in value.split(",") if k.strip()]
    try:
        select_sections(keys)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from None
    return keys


def _attach_windows_console() -> None:
    """The packaged Windows app has no console; reuse the parent terminal's."""
    if sys.platform != "win32" or sys.stdout is not None:
        return
    try:
        import ctypes
        if ctypes.windll.kernel32.AttachConsole(-1):  # ATTACH_PARENT_PROCESS
            sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
            sys.stderr = open("CONOUT$", "w", encoding="utf-8", buffering=1)
            sys.stdin = open("CONIN$", encoding="utf-8")
    except Exception:
        pass


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.no_gui:
        _attach_windows_console()
        from . import cli
        sys.exit(cli.run(args))

    try:
        from . import gui
    except ImportError as e:  # Python built without Tkinter
        sys.exit(f"The desktop app needs Tkinter ({e}). Install it, or use --no-gui.")
    gui.run()

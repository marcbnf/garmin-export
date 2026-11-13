"""Command-line mode (``--no-gui``)."""

import argparse
import logging
import os
import traceback
from getpass import getpass
from pathlib import Path

from garminconnect import Garmin, GarminConnectAuthenticationError

from .auth import (
    DEFAULT_TOKENSTORE,
    ResendCode,
    friendly_login_error,
    has_tokens,
    login,
    save_tokens,
    token_location,
)
from .cache import cache_size, clear_cache
from .exporter import ExportOptions, run_export
from .sections import SECTIONS

log = logging.getLogger("garmin_export")


def run(args: argparse.Namespace) -> int:
    """Run the CLI with parsed arguments; returns the process exit code."""
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    # --verbose also shows garminconnect's own messages (login strategy, MFA steps)
    logging.getLogger("garminconnect").setLevel(logging.DEBUG if args.verbose else logging.WARNING)
    tokenstore = args.tokenstore or os.getenv("GARMINTOKENS", DEFAULT_TOKENSTORE)

    if args.list_sections:
        for s in SECTIONS:
            print(f"  {s.key:<18} {s.name} -- {s.description}")
        return 0
    if args.clear_cache:
        files, size = cache_size(Path(args.output))
        clear_cache(Path(args.output))
        log.info(f"Cache cleared: {files} files, {size / 1e6:.1f} MB")
        return 0
    if args.start and args.end and args.start >= args.end:
        log.error("--from must be before --to")
        return 2

    try:
        api = _interactive_login(tokenstore)
    except KeyboardInterrupt:
        return 130
    except Exception as e:
        log.error(f"Authentication failed: {friendly_login_error(e)}")
        log.debug(traceback.format_exc())
        return 1

    if args.login:
        log.info(f"Login successful -- session saved in {token_location(tokenstore)}. "
                 "You can now run exports.")
        return 0

    options = ExportOptions(
        days=args.days, max_activities=args.activities, fetch_all=args.all,
        update=args.update, compact=args.compact, split=args.split,
        use_cache=not args.no_cache, delay=args.delay,
        sections=args.sections, start=args.start, end=args.end,
    )
    try:
        exporter = run_export(api, Path(args.output), options)
    except KeyboardInterrupt:
        log.info("Interrupted -- cached data is saved, re-run to continue")
        return 130
    except ValueError as e:  # e.g. empty date range
        log.error(str(e))
        return 2
    finally:
        save_tokens(api, tokenstore)  # tokens may have been refreshed during the export
    log.info("Reminder: the export contains personal health data. Share it only with "
             "services you trust.")
    return 130 if exporter.interrupted else 0


def _interactive_login(tokenstore: str) -> Garmin:
    """Saved tokens first, then .env / environment, then prompts (3 attempts)."""
    def ask_mfa(hint: str) -> str:
        print(f"\n  {hint}")
        print("  (No code? Press Enter to have Garmin send a new one.)")
        code = input("  Verification code: ").strip()
        if not code:
            raise ResendCode()
        return code

    if has_tokens(tokenstore):
        try:
            return login(tokenstore, prompt_mfa=ask_mfa)
        except Exception as e:
            log.info(f"Saved tokens not usable ({friendly_login_error(e)}), need fresh login")

    _load_env_file()
    email = os.getenv("GARMIN_EMAIL")
    password = os.getenv("GARMIN_PASSWORD")

    for attempt in range(1, 4):
        if not email or not password:
            print("\n  Garmin Connect login (credentials go only to Garmin; tokens are saved locally)\n")
            email = email or input("  Garmin email: ").strip()
            password = password or getpass("  Garmin password: ")
        try:
            return login(tokenstore, email, password, prompt_mfa=ask_mfa)
        except GarminConnectAuthenticationError as e:
            log.error(f"Login failed: {friendly_login_error(e)}")
            if attempt == 3:
                raise
            email = password = None  # ask again
    raise RuntimeError("unreachable")


def _load_env_file() -> None:
    """Load GARMIN_EMAIL / GARMIN_PASSWORD from ./.env if present (key=value lines)."""
    env_path = Path(".env")
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip().strip("\"'")
            if key and val:
                os.environ.setdefault(key, val)

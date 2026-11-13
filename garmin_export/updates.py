"""Check GitHub for a newer release (used by the desktop app, opt-out)."""

import json
import logging
import re
import ssl
import urllib.request

from . import GITHUB_REPO, __version__

log = logging.getLogger(__name__)

RELEASES_URL = f"https://github.com/{GITHUB_REPO}/releases/latest"
API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"


def parse_version(text: str) -> tuple[int, ...] | None:
    """"v1.2.3" -> (1, 2, 3); None if it is not a plain version number."""
    m = re.fullmatch(r"v?(\d+(?:\.\d+)*)", text.strip())
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def is_newer(latest: str, current: str = __version__) -> bool:
    new, cur = parse_version(latest), parse_version(current)
    return bool(new and cur and new > cur)


def _ssl_context() -> ssl.SSLContext:
    # Frozen apps may not find the system CA bundle: use certifi's when present
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def latest_release(timeout: float = 5.0) -> str | None:
    """Tag of the latest GitHub release, or None (offline, rate limited...).

    Sends no personal data: an anonymous GET with a generic User-Agent.
    """
    req = urllib.request.Request(API_URL, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "garmin-export-update-check",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as r:
            return str(json.load(r).get("tag_name") or "") or None
    except Exception as e:
        log.debug(f"Update check failed: {e}")
        return None


def newer_release() -> str | None:
    """Tag of a release newer than this version, else None."""
    tag = latest_release()
    return tag if tag and is_newer(tag) else None

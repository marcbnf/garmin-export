"""Garmin Connect authentication and saved tokens.

garminconnect >= 0.3 has its own SSO client (no garth). After a login, the
tokens (a refresh token that grants lasting access to the account) are kept:

- in the OS credential store (macOS Keychain, Windows Credential Manager,
  Secret Service on Linux) through ``keyring``, when one is available;
- otherwise in ``<tokenstore>/garmin_tokens.json`` (owner-only file).

Set ``GARMIN_EXPORT_KEYRING=0`` to always use the file. A token file left by
an earlier version is moved into the credential store at the next login.
"""

import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

from .api import http_status

log = logging.getLogger(__name__)

DEFAULT_TOKENSTORE = "~/.garminconnect"
TOKEN_FILE = "garmin_tokens.json"

KEYRING_SERVICE = "garmin-export"
# Windows Credential Manager stores at most 2560 bytes (UTF-16) per entry,
# and the token JSON is larger: store it in chunks.
KEYRING_CHUNK = 1000
MFA_ATTEMPTS = 3
# Login strategies that never explicitly ask Garmin to send an email/SMS
# code; skipped when the user asks for a new code (the widget flow does).
NO_RESEND_STRATEGIES = {"mobile+cffi", "mobile+requests"}

# prompt_mfa(hint) -> code. It may raise ResendCode to ask for a new code.
MfaPrompt = Callable[[str], str]

__all__ = [
    "DEFAULT_TOKENSTORE", "Garmin", "GarminConnectAuthenticationError", "ResendCode",
    "forget_tokens", "friendly_login_error", "has_tokens", "login", "save_tokens",
    "token_location", "token_path",
]


class ResendCode(Exception):
    """Raised by a prompt_mfa callback: no code arrived, request a new one."""


# ---------------------------------------------------------------------------
# Token storage
# ---------------------------------------------------------------------------
def token_path(tokenstore: str = DEFAULT_TOKENSTORE) -> Path:
    return Path(tokenstore).expanduser() / TOKEN_FILE


def _keyring() -> Any:
    """The keyring module when a real OS credential store is usable, else None."""
    if os.environ.get("GARMIN_EXPORT_KEYRING", "1").lower() in ("0", "false", "no"):
        return None
    try:
        import keyring
        from keyring.backends import fail

        backend = keyring.get_keyring()
    except Exception as e:  # not installed / broken install
        log.debug(f"Credential store unavailable: {e}")
        return None
    if isinstance(backend, fail.Keyring) or getattr(backend, "priority", 0) < 1:
        log.debug(f"No usable credential store ({type(backend).__module__}); using a token file")
        return None  # e.g. headless Linux without Secret Service
    return keyring


def _keyring_read() -> str | None:
    kr = _keyring()
    if kr is None:
        return None
    try:
        count = kr.get_password(KEYRING_SERVICE, "tokens.count")
        if not count:
            return None
        parts = [kr.get_password(KEYRING_SERVICE, f"tokens.{i}") for i in range(int(count))]
        if any(p is None for p in parts):
            return None
        return "".join(p for p in parts if p)
    except Exception as e:
        log.debug(f"Could not read tokens from the credential store: {e}")
        return None


def _keyring_write(payload: str) -> bool:
    kr = _keyring()
    if kr is None:
        return False
    chunks = [payload[i:i + KEYRING_CHUNK] for i in range(0, len(payload), KEYRING_CHUNK)]
    try:
        _keyring_delete()
        for i, chunk in enumerate(chunks):
            kr.set_password(KEYRING_SERVICE, f"tokens.{i}", chunk)
        kr.set_password(KEYRING_SERVICE, "tokens.count", str(len(chunks)))  # written last
        return True
    except Exception as e:
        log.warning(f"Could not save tokens in the credential store ({e}); using a file instead")
        return False


def _keyring_delete() -> None:
    kr = _keyring()
    if kr is None:
        return
    try:
        count = int(kr.get_password(KEYRING_SERVICE, "tokens.count") or 0)
    except Exception:
        count = 0
    for name in ["tokens.count"] + [f"tokens.{i}" for i in range(max(count, 8))]:
        try:
            kr.delete_password(KEYRING_SERVICE, name)
        except Exception:
            pass  # not there


def has_tokens(tokenstore: str = DEFAULT_TOKENSTORE) -> bool:
    return token_path(tokenstore).is_file() or _keyring_read() is not None


def token_location(tokenstore: str = DEFAULT_TOKENSTORE) -> str:
    """Where tokens are (or will be) kept, for display."""
    in_file = token_path(tokenstore).is_file() and _keyring_read() is None
    return str(token_path(tokenstore)) if in_file or not _keyring() else "the system credential store"


def save_tokens(api: Garmin, tokenstore: str = DEFAULT_TOKENSTORE) -> None:
    """Persist the client's current tokens (call after login and after an export,
    since tokens are refreshed in memory during long exports)."""
    try:
        payload = api.client.dumps()
    except Exception as e:
        log.debug(f"No tokens to save: {e}")
        return
    if _keyring_write(payload):
        token_path(tokenstore).unlink(missing_ok=True)  # migrated
    else:
        api.client.dump(str(Path(tokenstore).expanduser()))


def forget_tokens(tokenstore: str = DEFAULT_TOKENSTORE) -> None:
    """Log out locally: delete the saved tokens (credential store and file)."""
    _keyring_delete()
    token_path(tokenstore).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------
def login(tokenstore: str = DEFAULT_TOKENSTORE,
          email: str | None = None,
          password: str | None = None,
          prompt_mfa: MfaPrompt | None = None) -> Garmin:
    """Authenticate to Garmin Connect and save the tokens.

    Uses saved tokens when they exist and are valid; otherwise logs in with
    email/password. If Garmin asks for a 2FA code, calls prompt_mfa(hint),
    where hint explains where the code comes from (see mfa_hint()). The
    callback may raise ResendCode: the login restarts through Garmin's web
    flow, which explicitly asks Garmin to send a new email/SMS code.
    Raises garminconnect exceptions on failure.
    """
    store = Path(tokenstore).expanduser()
    saved = _keyring_read() or (str(store) if token_path(tokenstore).is_file() else None)

    if saved:
        try:
            api = Garmin(email=None, password=None)
            api.login(saved)
            log.info("Authenticated with Garmin Connect (saved session)")
            save_tokens(api, tokenstore)  # refreshed tokens / file -> credential store
            return api
        except Exception as e:
            if not (email and password):
                raise
            log.info(f"Saved session not usable ({friendly_login_error(e)}), logging in again")

    if not (email and password):
        raise GarminConnectAuthenticationError("Username and password are required")

    api = _credential_login(email, password, prompt_mfa, resend=False)
    log.info("Authenticated with Garmin Connect")
    save_tokens(api, tokenstore)
    return api


def _credential_login(email: str, password: str, prompt_mfa: MfaPrompt | None,
                      resend: bool) -> Garmin:
    api = Garmin(email=email, password=password, return_on_mfa=True)
    if resend:
        api.client.skip_strategies = set(NO_RESEND_STRATEGIES)
    status, state = api.login()
    if status != "needs_mfa":
        return api
    if prompt_mfa is None:
        raise GarminConnectAuthenticationError("Garmin asks for a verification code (2FA)")

    method = str((state or {}).get("method") or "")
    flow = str((state or {}).get("flow") or "")
    log.info(f"Garmin asks for a verification code (method: {method or 'unknown'}, "
             f"login flow: {flow or 'unknown'})")
    hint = mfa_hint(method)
    for attempt in range(1, MFA_ATTEMPTS + 1):
        try:
            code = prompt_mfa(hint).strip()
        except ResendCode:
            if resend:
                raise GarminConnectAuthenticationError(
                    "No verification code received. Check your Garmin account's 2FA "
                    "settings at connect.garmin.com, then try again.") from None
            log.info("Requesting a new verification code (web login flow)")
            return _credential_login(email, password, prompt_mfa, resend=True)
        if not code:
            raise GarminConnectAuthenticationError("No verification code entered.")
        try:
            api.resume_login(state, code)
            return api
        except GarminConnectAuthenticationError as e:
            if attempt == MFA_ATTEMPTS:
                raise
            log.warning(f"Verification code rejected ({e}); try again")
            hint = "That code was not accepted. Enter the latest code Garmin sent you."
    raise RuntimeError("unreachable")


def mfa_hint(method: str) -> str:
    """Tell the user where to find the 2FA code for Garmin's method name."""
    m = method.lower()
    if "email" in m:
        return ("Garmin sent a verification code to your account's email address. "
                "It can take a minute or two: check your spam folder.")
    if "sms" in m or "phone" in m or "text" in m:
        return "Garmin sent a verification code by SMS to your phone."
    if "totp" in m or "app" in m or "auth" in m:
        return ("Garmin does not send a code for this method: open your authenticator app "
                "and enter the current code for Garmin.")
    return ("Enter the verification code from Garmin (email, SMS, or your authenticator app). "
            "If you use email, check your spam folder.")


def friendly_login_error(exc: BaseException) -> str:
    """Short, actionable message for a login failure."""
    if isinstance(exc, GarminConnectTooManyRequestsError):
        return "Too many login attempts. Wait a few minutes and try again."
    msg = str(exc)
    low = msg.lower()
    status = http_status(exc) or http_status(exc.__cause__ or exc)
    if status == 401 or "401" in msg or "incorrect email or password" in low:
        return "Wrong email or password."
    if status == 403 or "403" in msg:
        return "Access denied (403). Your account may be locked -- try logging in at connect.garmin.com first."
    if "garth-format" in low:
        return "Old login tokens found. Log in again with your email and password."
    if "username and password are required" in low:
        return "Email and password are required."
    if isinstance(exc, GarminConnectConnectionError) and ("connect" in low or "timeout" in low):
        return "Could not reach Garmin servers. Check your internet connection."
    # Drop the wall of URLs some errors contain
    if "https://" in msg:
        msg = msg[:msg.index("https://")].rstrip(": ")
    return msg.splitlines()[0] if msg else "Unknown login error."

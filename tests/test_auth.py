"""Login tests: 2FA prompts, code resend, token storage (credential store / file).

Never touches the real OS credential store: auth._keyring is replaced by an
in-memory stand-in (or disabled).
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from garminconnect import GarminConnectAuthenticationError

from garmin_export import auth

os.environ["GARMIN_EXPORT_KEYRING"] = "0"  # safety net for any unpatched call

TOKENS = json.dumps({"di_token": "t" * 1500, "di_refresh_token": "r" * 700, "di_client_id": "c"})


class MemoryKeyring:
    """Stand-in for the keyring module; enforces the Windows per-entry limit."""

    def __init__(self):
        self.items = {}

    def get_password(self, service, name):
        return self.items.get((service, name))

    def set_password(self, service, name, value):
        if len(value.encode("utf-16-le")) > 2560:
            raise ValueError("entry too large for Windows Credential Manager")
        self.items[(service, name)] = value

    def delete_password(self, service, name):
        if (service, name) not in self.items:
            raise KeyError(name)
        del self.items[(service, name)]


def fake_garmin(method="email", flow="ios", good_code="123456", log=None):
    """Garmin stand-in following garminconnect's return_on_mfa / resume_login API."""
    log = log if log is not None else []

    class FakeGarmin:
        def __init__(self, email=None, password=None, return_on_mfa=False, **_):
            self.email, self.password = email, password
            self.client = SimpleNamespace(skip_strategies=set(), dumps=lambda: TOKENS,
                                          dump=self._dump)

        def _dump(self, path):
            Path(path).mkdir(parents=True, exist_ok=True)
            (Path(path) / auth.TOKEN_FILE).write_text(TOKENS)

        def login(self, tokenstore=None):
            if tokenstore:
                log.append("tokens")
                if tokenstore.startswith("{") or (Path(tokenstore) / auth.TOKEN_FILE).is_file():
                    return None, None
            if not (self.email and self.password):
                raise GarminConnectAuthenticationError("Username and password are required")
            web = "mobile+cffi" in self.client.skip_strategies
            log.append("web-login" if web else "login")
            return "needs_mfa", {"method": method, "flow": "widget" if web else flow}

        def resume_login(self, state, code):
            log.append(f"code:{code}")
            if code != good_code:
                raise GarminConnectAuthenticationError("Invalid MFA code")
            return None, None
    return FakeGarmin


class MfaTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(auth, "_keyring", lambda: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def login_with(self, method, answers=("123456",), log=None):
        hints, answers = [], list(answers)

        def prompt(hint):
            hints.append(hint)
            answer = answers.pop(0)
            if isinstance(answer, BaseException):
                raise answer
            return answer
        with mock.patch.object(auth, "Garmin", fake_garmin(method, log=log)), \
                tempfile.TemporaryDirectory() as store:  # no saved session
            auth.login(store, "a@b.c", "pw", prompt_mfa=prompt)
        return hints

    def test_email(self):
        self.assertIn("email", self.login_with("email")[0])
        self.assertIn("spam", self.login_with("EMAIL")[0])

    def test_sms(self):
        self.assertIn("SMS", self.login_with("sms")[0])

    def test_authenticator_app(self):
        hint = self.login_with("totp")[0]
        self.assertIn("authenticator app", hint)
        self.assertIn("does not send", hint)

    def test_unknown_method(self):
        self.assertIn("authenticator app", self.login_with("")[0])

    def test_method_is_logged(self):
        with self.assertLogs("garmin_export.auth", "INFO") as logs:
            self.login_with("email")
        self.assertTrue(any("method: email" in line and "flow: ios" in line for line in logs.output))

    def test_wrong_code_can_be_retyped(self):
        log = []
        hints = self.login_with("email", answers=("000000", "123456"), log=log)
        self.assertEqual(log, ["login", "code:000000", "code:123456"])
        self.assertIn("not accepted", hints[1])

    def test_resend_uses_web_flow(self):
        log = []
        self.login_with("email", answers=(auth.ResendCode(), "123456"), log=log)
        self.assertEqual(log, ["login", "web-login", "code:123456"])

    def test_resend_twice_gives_up(self):
        with self.assertRaises(GarminConnectAuthenticationError):
            self.login_with("email", answers=(auth.ResendCode(), auth.ResendCode()))

    def test_empty_code_cancels(self):
        with self.assertRaises(GarminConnectAuthenticationError):
            self.login_with("email", answers=("",))


class TokenStorageTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = tmp.name
        self.log = []
        patcher = mock.patch.object(auth, "Garmin", fake_garmin(log=self.log))
        patcher.start()
        self.addCleanup(patcher.stop)

    def use_keyring(self, kr):
        patcher = mock.patch.object(auth, "_keyring", lambda: kr)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_keyring_round_trip_in_chunks(self):
        kr = MemoryKeyring()
        self.use_keyring(kr)
        auth.login(self.store, "a@b.c", "pw", prompt_mfa=lambda h: "123456")
        self.assertGreater(int(kr.items[(auth.KEYRING_SERVICE, "tokens.count")]), 1)
        self.assertEqual(auth._keyring_read(), TOKENS)
        self.assertTrue(auth.has_tokens(self.store))
        self.assertFalse(auth.token_path(self.store).exists())

        # Next start: saved session, no password, no 2FA
        auth.login(self.store)
        self.assertEqual(self.log[-1], "tokens")

        auth.forget_tokens(self.store)
        self.assertEqual(kr.items, {})
        self.assertFalse(auth.has_tokens(self.store))

    def test_old_token_file_moves_to_keyring(self):
        self.use_keyring(None)
        auth.login(self.store, "a@b.c", "pw", prompt_mfa=lambda h: "123456")
        self.assertTrue(auth.token_path(self.store).is_file())

        kr = MemoryKeyring()
        self.use_keyring(kr)
        auth.login(self.store)
        self.assertEqual(auth._keyring_read(), TOKENS)
        self.assertFalse(auth.token_path(self.store).exists())

    def test_file_fallback_without_keyring(self):
        self.use_keyring(None)
        auth.login(self.store, "a@b.c", "pw", prompt_mfa=lambda h: "123456")
        self.assertTrue(auth.has_tokens(self.store))
        self.assertIn(self.store, auth.token_location(self.store))
        auth.forget_tokens(self.store)
        self.assertFalse(auth.has_tokens(self.store))

    def test_no_saved_session_needs_credentials(self):
        self.use_keyring(MemoryKeyring())
        with self.assertRaises(GarminConnectAuthenticationError):
            auth.login(self.store)

    def test_keyring_disabled_by_environment(self):
        with mock.patch.dict(os.environ, {"GARMIN_EXPORT_KEYRING": "0"}):
            self.assertIsNone(auth._keyring())


if __name__ == "__main__":
    unittest.main()

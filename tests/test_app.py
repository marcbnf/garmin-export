"""Entry point and GUI tests. GUI tests are skipped when no display is available."""

import io
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from fake_api import FakeApi

from garmin_export import __version__, api, app


class EntryPointTests(unittest.TestCase):
    def test_help_mentions_no_gui(self):
        buf = io.StringIO()
        with redirect_stdout(buf), self.assertRaises(SystemExit) as cm:
            app.main(["--help"])
        self.assertEqual(cm.exception.code, 0)
        self.assertIn("--no-gui", buf.getvalue())

    def test_version_comes_from_version_file(self):
        version_file = Path(__file__).resolve().parent.parent / "VERSION"
        self.assertEqual(__version__, version_file.read_text().strip())
        self.assertRegex(__version__, r"^\d+\.\d+\.\d+$")

    def test_no_gui_runs_cli(self):
        with mock.patch("garmin_export.cli.run", return_value=0) as run, \
                self.assertRaises(SystemExit) as cm:
            app.main(["--no-gui", "--days", "7"])
        self.assertEqual(cm.exception.code, 0)
        self.assertEqual(run.call_args.args[0].days, 7)

    def test_default_opens_gui(self):
        with mock.patch("garmin_export.gui.run") as run:
            app.main([])
        run.assert_called_once()

    def test_cli_export_end_to_end(self):
        out = Path(tempfile.mkdtemp())
        try:
            with mock.patch("garmin_export.cli._interactive_login", return_value=FakeApi()), \
                    mock.patch.object(api.CANCEL, "wait", lambda timeout=None: False):
                with self.assertRaises(SystemExit) as cm:
                    app.main(["--no-gui", "--days", "3", "--delay", "0", "--output", str(out)])
            self.assertEqual(cm.exception.code, 0)
            self.assertEqual(len(list(out.glob("garmin_export_*.txt"))), 1)
        finally:
            shutil.rmtree(out)

    def test_cli_options_reach_the_export(self):
        with mock.patch("garmin_export.cli._interactive_login", return_value=FakeApi()), \
                mock.patch("garmin_export.cli.run_export") as run_export, \
                self.assertRaises(SystemExit):
            app.main(["--no-gui", "--from", "2025-01-01", "--to", "2025-02-01",
                      "--sections", "activities,daily-health"])
        options = run_export.call_args.args[2]
        self.assertEqual(str(options.start), "2025-01-01")
        self.assertEqual(str(options.end), "2025-02-01")
        self.assertEqual(options.sections, ["activities", "daily-health"])

    def test_cli_rejects_bad_values(self):
        for argv in (["--sections", "nope"], ["--from", "2025-02-30"]):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as cm:
                app.main(["--no-gui", *argv])
            self.assertEqual(cm.exception.code, 2)

    def test_list_sections(self):
        buf = io.StringIO()
        with redirect_stdout(buf), self.assertRaises(SystemExit) as cm:
            app.main(["--no-gui", "--list-sections"])
        self.assertEqual(cm.exception.code, 0)
        self.assertIn("daily_health", buf.getvalue())


def _tk_root():
    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        return root
    except Exception as e:  # no Tk or no display (headless CI)
        raise unittest.SkipTest(f"GUI not available: {e}") from e


class GuiTests(unittest.TestCase):
    """Drives the real window: login with 2FA, export, then stop mid-export."""

    def test_login_export_stop(self):
        from garmin_export import gui

        root = _tk_root()
        out = Path(tempfile.mkdtemp())
        tokens = {"saved": False}
        mfa_codes = []
        notices = []

        def fake_login(store, email=None, password=None, prompt_mfa=None):
            mfa_codes.append(prompt_mfa("Garmin sent a code by email."))  # simulate a 2FA request
            tokens["saved"] = True
            return FakeApi()

        patches = [
            mock.patch.object(gui, "login", fake_login),
            mock.patch.object(gui, "has_tokens", lambda *a: tokens["saved"]),
            mock.patch.object(gui, "save_settings", lambda s: None),
            mock.patch.object(gui, "load_settings", lambda: dict(gui.DEFAULTS)),
            mock.patch.object(gui, "ask_mfa_code", lambda *a, **k: "123456"),
            mock.patch.object(gui.messagebox, "showinfo", lambda *a, **k: notices.append(a)),
            mock.patch.object(api.CANCEL, "wait", lambda timeout=None: False),
        ]
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        self.addCleanup(shutil.rmtree, out)

        window = gui.App(root, check_updates=False)
        window.v_output.set(str(out))
        window.v_delay.set(0.0)
        results = {}

        def when_idle(then):
            root.after(100, lambda: when_idle(then) if window._busy() else then())

        def step_login():
            window.v_email.set("someone@example.com")
            window.v_password.set("secret")
            window.on_login()
            when_idle(step_export)

        def step_export():
            results["login"] = window.v_status.get()
            results["password_cleared"] = window.v_password.get() == ""
            window.v_days.set(5)
            window.on_run()
            when_idle(step_long_export)

        def step_long_export():
            results["export"] = window.v_status.get()
            results["show_files"] = bool(window.btn_show.winfo_manager()) and bool(window.last_files)
            window.v_days.set(400)
            window.v_no_cache.set(True)
            window.on_run()
            root.after(300, step_stop)

        def step_stop():
            results["button_while_running"] = window.btn_run["text"]
            window.on_run()  # "Stop"
            when_idle(step_done)

        def step_done():
            results["stopped"] = window.v_status.get()
            root.quit()

        root.after(100, step_login)
        root.after(60_000, root.quit)  # safety net
        root.mainloop()
        root.destroy()

        self.assertEqual(mfa_codes, ["123456"])
        self.assertIn("Logged in", results["login"])
        self.assertTrue(results["password_cleared"])
        self.assertIn("Export complete", results["export"])
        self.assertEqual(str(results["button_while_running"]), "Stop")
        self.assertIn("Export stopped", results["stopped"])
        self.assertEqual(len(notices), 1, "privacy note is shown before the first export only")
        self.assertTrue(results["show_files"])


if __name__ == "__main__":
    unittest.main()

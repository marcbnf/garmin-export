"""Desktop app (Tkinter).

Log in to Garmin Connect (with 2FA support), choose what to export, follow
progress, stop cleanly. The export runs in a background thread; the UI
talks to it through a queue polled every POLL_MS.
"""

import json
import logging
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinter.scrolledtext import ScrolledText
from typing import Any

from . import APP_NAME, __version__, updates
from .api import CANCEL, ExportCancelled
from .auth import (
    DEFAULT_TOKENSTORE,
    GarminConnectAuthenticationError,
    ResendCode,
    forget_tokens,
    friendly_login_error,
    has_tokens,
    login,
    save_tokens,
    token_location,
)
from .cache import cache_size, clear_cache
from .exporter import ExportOptions, GarminExporter, run_export
from .sections import SECTIONS

log = logging.getLogger("garmin_export")
MAX_LOG_LINES = 5000
ICON_PATH = Path(__file__).resolve().parent / "assets" / "icon.png"
POLL_MS = 100
RESEND = "\0resend"  # ask_mfa_code() result: the user wants a new code

PRIVACY_NOTE = (
    "The export file contains your personal health data (heart rate, sleep, "
    "locations of your activities, body measurements...).\n\n"
    "It stays on this computer. If you upload it to an AI service (NotebookLM, "
    "ChatGPT, Claude...), that service receives this data: check its privacy "
    "policy first, and don't share the file publicly."
)


# ---------------------------------------------------------------------------
# Settings (persisted between launches -- never the password)
# ---------------------------------------------------------------------------
def config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home())
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "GarminExport"


def default_output_dir() -> Path:
    docs = Path.home() / "Documents"
    return (docs if docs.is_dir() else Path.home()) / "Garmin Export"


DEFAULTS: dict[str, Any] = {
    "email": "",
    "mode": "recent",  # recent | range | all | update
    "days": 30,
    "start": "",       # range mode, YYYY-MM-DD
    "end": "",
    "activities": 100,
    "sections": [s.key for s in SECTIONS],
    "compact": False,
    "split": False,
    "no_cache": False,
    "delay": 0.15,
    "verbose": False,
    "check_updates": True,
    "privacy_ack": False,
    "output_dir": str(default_output_dir()),
}


def load_settings() -> dict:
    settings = dict(DEFAULTS)
    try:
        saved = json.loads((config_dir() / "settings.json").read_text(encoding="utf-8"))
        settings.update({k: v for k, v in saved.items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    known = {s.key for s in SECTIONS}
    settings["sections"] = [k for k in settings["sections"] if k in known] or list(DEFAULTS["sections"])
    return settings


def save_settings(settings: dict) -> None:
    try:
        config_dir().mkdir(parents=True, exist_ok=True)
        (config_dir() / "settings.json").write_text(json.dumps(settings, indent=2), encoding="utf-8")
    except OSError:
        pass


def open_in_file_manager(path: Path, select: bool = False) -> None:
    """Open a folder, or show a file selected in its folder (select=True)."""
    if sys.platform == "win32":
        if select:
            subprocess.Popen(["explorer", "/select,", str(path)])
        else:
            os.startfile(path)  # type: ignore[attr-defined]  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)] if select else ["open", str(path)])
    else:  # xdg-open can't select a file: open its folder
        subprocess.Popen(["xdg-open", str(path.parent if select else path)])


def parse_date(text: str) -> date | None:
    try:
        return date.fromisoformat(text.strip())
    except ValueError:
        return None


class QueueLogHandler(logging.Handler):
    """Forwards log records from the worker thread to the UI queue."""

    def __init__(self, q: queue.Queue):
        super().__init__()
        self.q = q
        self.setFormatter(logging.Formatter("%(asctime)s  %(message)s", datefmt="%H:%M:%S"))

    def emit(self, record):
        try:
            self.q.put(("log", self.format(record)))
        except Exception:
            self.handleError(record)


# ---------------------------------------------------------------------------
# Dialogs
# ---------------------------------------------------------------------------
class MfaDialog(simpledialog.Dialog):
    """Verification code prompt with a "send a new code" button."""

    def __init__(self, parent, hint: str):
        self.hint = hint
        self.result: str | None = None
        super().__init__(parent, "Two-step verification")

    def body(self, master):
        ttk.Label(master, text=self.hint, wraplength=380, justify="left").grid(
            row=0, column=0, sticky="w", pady=(0, 8))
        ttk.Label(master, text="Verification code:").grid(row=1, column=0, sticky="w")
        self.entry = ttk.Entry(master, width=16)
        self.entry.grid(row=2, column=0, sticky="w", pady=(2, 0))
        return self.entry

    def buttonbox(self):
        box = ttk.Frame(self)
        ttk.Button(box, text="No code? Send a new one", command=self.resend).pack(side="left", padx=(0, 20))
        ttk.Button(box, text="Cancel", command=self.cancel).pack(side="right")
        ttk.Button(box, text="OK", command=self.ok, default="active").pack(side="right", padx=(0, 6))
        self.bind("<Return>", self.ok)
        self.bind("<Escape>", self.cancel)
        box.pack(fill="x", padx=10, pady=(0, 10))

    def apply(self):
        self.result = self.entry.get().strip()

    def resend(self):
        self.result = RESEND
        self.cancel()  # closes the dialog, keeps result


def ask_mfa_code(parent, hint: str) -> str | None:
    """Code typed by the user, RESEND, or None (cancelled)."""
    return MfaDialog(parent, hint).result


class SectionsDialog(simpledialog.Dialog):
    """Checklist of the sections to export."""

    def __init__(self, parent, selected: list[str]):
        self.vars = {s.key: tk.BooleanVar(value=s.key in selected) for s in SECTIONS}
        self.result: list[str] | None = None
        super().__init__(parent, "Sections to export")

    def body(self, master):
        half = (len(SECTIONS) + 1) // 2
        for i, s in enumerate(SECTIONS):
            ttk.Checkbutton(master, text=s.name, variable=self.vars[s.key]).grid(
                row=i % half, column=i // half, sticky="w", padx=(0, 24), pady=1)
        links = ttk.Frame(master)
        links.grid(row=half, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Button(links, text="All", command=lambda: self._set_all(True)).pack(side="left")
        ttk.Button(links, text="None", command=lambda: self._set_all(False)).pack(side="left", padx=(4, 0))
        return None

    def _set_all(self, value: bool):
        for v in self.vars.values():
            v.set(value)

    def validate(self):
        if not any(v.get() for v in self.vars.values()):
            messagebox.showwarning(APP_NAME, "Select at least one section.", parent=self)
            return False
        return True

    def apply(self):
        self.result = [k for k, v in self.vars.items() if v.get()]


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------
class App:
    def __init__(self, root: tk.Tk, check_updates: bool | None = None):
        self.root = root
        self.q: queue.Queue = queue.Queue()
        self.task: str | None = None  # "login" / "export" while a job runs (until its "done" is handled)
        self.api: Any = None  # logged-in Garmin client, reused between exports
        self.settings = load_settings()
        self.sections: list[str] = list(self.settings["sections"])
        self.last_files: list[Path] = []
        self.closing = False

        handler = QueueLogHandler(self.q)
        for name in ("garmin_export", "garminconnect"):
            lg = logging.getLogger(name)
            lg.addHandler(handler)
            lg.propagate = False
        logging.getLogger("garminconnect").setLevel(logging.WARNING)
        log.setLevel(logging.INFO)

        root.title(f"{APP_NAME} {__version__}")
        self._set_window_icon()
        root.minsize(640, 660)
        root.geometry("780x780")
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        if sys.platform.startswith("linux"):
            ttk.Style().theme_use("clam")

        self._build_vars()
        self._build_ui()
        self._refresh_account()
        self._refresh_options()
        self._refresh_sections()
        self._refresh_cache()
        root.after(POLL_MS, self._poll)
        if self.settings["check_updates"] if check_updates is None else check_updates:
            threading.Thread(target=self._check_updates, daemon=True).start()

    def _set_window_icon(self):
        """Title bar / taskbar icon (the packaged app also embeds .icns/.ico)."""
        try:
            self._icon = tk.PhotoImage(file=str(ICON_PATH))  # keep a reference
            self.root.iconphoto(True, self._icon)
        except tk.TclError:
            pass  # missing file or Tk without PNG support: keep the default icon

    # ----- state ---------------------------------------------------------
    def _build_vars(self):
        s = self.settings
        today = date.today()
        self.v_email = tk.StringVar(value=s["email"])
        self.v_password = tk.StringVar()
        self.v_mode = tk.StringVar(value=s["mode"])
        self.v_days = tk.IntVar(value=s["days"])
        self.v_start = tk.StringVar(value=s["start"] or str(today - timedelta(days=s["days"])))
        self.v_end = tk.StringVar(value=s["end"] or str(today))
        self.v_activities = tk.IntVar(value=s["activities"])
        self.v_compact = tk.BooleanVar(value=s["compact"])
        self.v_split = tk.BooleanVar(value=s["split"])
        self.v_no_cache = tk.BooleanVar(value=s["no_cache"])
        self.v_delay = tk.DoubleVar(value=s["delay"])
        self.v_verbose = tk.BooleanVar(value=s["verbose"])
        self.v_check_updates = tk.BooleanVar(value=s["check_updates"])
        self.v_output = tk.StringVar(value=s["output_dir"])
        self.v_status = tk.StringVar(value="Ready.")
        self.v_progress_label = tk.StringVar()
        self.v_sections = tk.StringVar()
        self.v_cache = tk.StringVar()
        for v in (self.v_mode, self.v_split):
            v.trace_add("write", lambda *_: self._refresh_options())
        self.v_output.trace_add("write", lambda *_: self._refresh_cache())

    def _collect_settings(self) -> dict:
        def num(var, default):
            try:
                return var.get()
            except tk.TclError:  # field left empty / invalid
                return default
        return {
            "email": self.v_email.get().strip(),
            "mode": self.v_mode.get(),
            "days": max(1, int(num(self.v_days, 30))),
            "start": self.v_start.get().strip(),
            "end": self.v_end.get().strip(),
            "activities": max(1, int(num(self.v_activities, 100))),
            "sections": list(self.sections),
            "compact": self.v_compact.get(),
            "split": self.v_split.get(),
            "no_cache": self.v_no_cache.get(),
            "delay": round(float(num(self.v_delay, 0.15)), 2),
            "verbose": self.v_verbose.get(),
            "check_updates": self.v_check_updates.get(),
            "privacy_ack": self.settings.get("privacy_ack", False),
            "output_dir": self.v_output.get().strip(),
        }

    # ----- layout --------------------------------------------------------
    def _build_ui(self):
        main = ttk.Frame(self.root, padding=12)
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(4, weight=1)

        # New release banner (hidden until the update check finds one)
        self.frm_update = ttk.Frame(main)
        self.frm_update.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self.lbl_update = ttk.Label(self.frm_update, foreground="#0e7c86", cursor="hand2")
        self.lbl_update.pack(side="left")
        self.lbl_update.bind("<Button-1>", lambda _: webbrowser.open(updates.RELEASES_URL))
        self.frm_update.grid_remove()

        # Account
        acc = ttk.LabelFrame(main, text="Garmin account", padding=10)
        acc.grid(row=1, column=0, sticky="ew")
        acc.columnconfigure(1, weight=1)
        self.lbl_account = ttk.Label(acc)
        self.lbl_account.grid(row=0, column=0, columnspan=2, sticky="w")
        self.btn_logout = ttk.Button(acc, text="Log out", command=self.on_logout)
        self.btn_logout.grid(row=0, column=2, sticky="e")

        self.frm_creds = ttk.Frame(acc)
        self.frm_creds.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        self.frm_creds.columnconfigure(1, weight=1)
        ttk.Label(self.frm_creds, text="Email").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(self.frm_creds, textvariable=self.v_email).grid(row=0, column=1, sticky="ew")
        ttk.Label(self.frm_creds, text="Password").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(4, 0))
        pwd = ttk.Entry(self.frm_creds, textvariable=self.v_password, show="•")
        pwd.grid(row=1, column=1, sticky="ew", pady=(4, 0))
        pwd.bind("<Return>", lambda _: self.on_login())
        self.btn_login = ttk.Button(self.frm_creds, text="Log in", command=self.on_login)
        self.btn_login.grid(row=1, column=2, padx=(8, 0), pady=(4, 0))
        ttk.Label(self.frm_creds, foreground="gray",
                  text="Credentials are sent only to Garmin and never stored. "
                       "You will be asked for a verification code (2FA) if needed.",
                  wraplength=560).grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))

        # Export options
        opt = ttk.LabelFrame(main, text="Export", padding=10)
        opt.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        opt.columnconfigure(1, weight=1)

        modes = ttk.Frame(opt)
        modes.grid(row=0, column=0, columnspan=3, sticky="w")
        for text, val in (("Recent period", "recent"), ("Date range", "range"),
                          ("Full history", "all"), ("Incremental update", "update")):
            ttk.Radiobutton(modes, text=text, value=val, variable=self.v_mode).pack(side="left", padx=(0, 14))

        period = ttk.Frame(opt)
        period.grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self.frm_days = ttk.Frame(period)
        ttk.Label(self.frm_days, text="Days:").pack(side="left")
        ttk.Spinbox(self.frm_days, from_=1, to=3650, width=6, textvariable=self.v_days).pack(side="left", padx=(4, 16))
        self.frm_range = ttk.Frame(period)
        ttk.Label(self.frm_range, text="From:").pack(side="left")
        ttk.Entry(self.frm_range, width=11, textvariable=self.v_start).pack(side="left", padx=(4, 8))
        ttk.Label(self.frm_range, text="To:").pack(side="left")
        ttk.Entry(self.frm_range, width=11, textvariable=self.v_end).pack(side="left", padx=(4, 4))
        ttk.Label(self.frm_range, text="(YYYY-MM-DD)", foreground="gray").pack(side="left", padx=(0, 16))
        self.frm_acts = ttk.Frame(period)
        ttk.Label(self.frm_acts, text="Max activities:").pack(side="left")
        ttk.Spinbox(self.frm_acts, from_=1, to=100000, width=7, textvariable=self.v_activities).pack(side="left", padx=4)
        self.lbl_mode_help = ttk.Label(period, foreground="gray", wraplength=640)

        secs = ttk.Frame(opt)
        secs.grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Label(secs, text="Sections:").pack(side="left")
        ttk.Label(secs, textvariable=self.v_sections).pack(side="left", padx=(4, 8))
        ttk.Button(secs, text="Choose…", command=self.on_choose_sections).pack(side="left")

        checks = ttk.Frame(opt)
        checks.grid(row=3, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self.chk_compact = ttk.Checkbutton(checks, text="Compact (smaller file)", variable=self.v_compact)
        self.chk_compact.pack(side="left", padx=(0, 14))
        ttk.Checkbutton(checks, text="Split for NotebookLM", variable=self.v_split).pack(side="left", padx=(0, 14))
        ttk.Checkbutton(checks, text="Ignore cache", variable=self.v_no_cache).pack(side="left")

        adv = ttk.Frame(opt)
        adv.grid(row=4, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Label(adv, text="Delay between requests (s):").pack(side="left")
        ttk.Spinbox(adv, from_=0.05, to=5.0, increment=0.05, width=6, textvariable=self.v_delay).pack(side="left", padx=(4, 16))
        ttk.Checkbutton(adv, text="Verbose log", variable=self.v_verbose).pack(side="left", padx=(0, 14))
        ttk.Checkbutton(adv, text="Check for new versions", variable=self.v_check_updates).pack(side="left")

        ttk.Label(opt, text="Folder:").grid(row=5, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(opt, textvariable=self.v_output).grid(row=5, column=1, sticky="ew", padx=6, pady=(8, 0))
        dirs = ttk.Frame(opt)
        dirs.grid(row=5, column=2, pady=(8, 0))
        ttk.Button(dirs, text="Browse…", command=self.on_choose_dir).pack(side="left")
        ttk.Button(dirs, text="Open", command=self.on_open_dir).pack(side="left", padx=(4, 0))

        cache = ttk.Frame(opt)
        cache.grid(row=6, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Label(cache, textvariable=self.v_cache, foreground="gray").pack(side="left")
        self.btn_clear_cache = ttk.Button(cache, text="Clear cache…", command=self.on_clear_cache)
        self.btn_clear_cache.pack(side="left", padx=(8, 0))

        # Run / progress
        run = ttk.Frame(main)
        run.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        run.columnconfigure(1, weight=1)
        self.btn_run = ttk.Button(run, text="Start export", command=self.on_run)
        self.btn_run.grid(row=0, column=0, sticky="w")
        self.progress = ttk.Progressbar(run, maximum=1000)
        self.progress.grid(row=0, column=1, sticky="ew", padx=10)
        ttk.Label(run, textvariable=self.v_progress_label, width=24).grid(row=0, column=2, sticky="e")
        self.btn_show = ttk.Button(run, text="Show files", command=self.on_show_files)
        self.btn_show.grid(row=0, column=3, padx=(6, 0))
        self.btn_show.grid_remove()

        # Log
        logf = ttk.LabelFrame(main, text="Log", padding=6)
        logf.grid(row=4, column=0, sticky="nsew", pady=(10, 0))
        logf.columnconfigure(0, weight=1)
        logf.rowconfigure(0, weight=1)
        self.txt_log = ScrolledText(logf, height=10, wrap="word", state="disabled",
                                    font=("Menlo" if sys.platform == "darwin" else "Consolas"
                                          if sys.platform == "win32" else "Monospace", 10))
        self.txt_log.grid(row=0, column=0, sticky="nsew")
        tools = ttk.Frame(logf)
        tools.grid(row=1, column=0, sticky="e", pady=(4, 0))
        ttk.Button(tools, text="Copy", command=self.on_copy_log).pack(side="left")
        ttk.Button(tools, text="Clear", command=self.on_clear_log).pack(side="left", padx=(4, 0))

        # Status bar
        ttk.Separator(self.root).pack(fill="x")
        ttk.Label(self.root, textvariable=self.v_status, padding=(12, 4)).pack(fill="x")

    # ----- UI refresh ----------------------------------------------------
    def _busy(self) -> bool:
        return self.task is not None

    def _refresh_account(self):
        if has_tokens():
            self.lbl_account.config(text=f"✔  Logged in (session saved in {token_location()})",
                                    foreground="#2e8b57")
            self.btn_logout.grid()
            self.frm_creds.grid_remove()
        else:
            self.lbl_account.config(text="Not logged in", foreground="#c77700")
            self.btn_logout.grid_remove()
            self.frm_creds.grid()

    def _refresh_options(self):
        mode = self.v_mode.get()
        for w in (self.frm_days, self.frm_range, self.frm_acts, self.lbl_mode_help):
            w.pack_forget()
        if mode == "recent":
            self.frm_days.pack(side="left")
            self.frm_acts.pack(side="left")
        elif mode == "range":
            self.frm_range.pack(side="left")
            self.frm_acts.pack(side="left")
        else:
            self.lbl_mode_help.config(text={
                "all": "Goes back to your very first activity. Can take several hours; "
                       "if interrupted, just start again: the cache resumes where it stopped.",
                "update": "Exports only the data added since the latest export "
                          "in the output folder (compact format).",
            }[mode])
            self.lbl_mode_help.pack(side="left")
        forced = self.v_split.get() or mode == "update"
        self.chk_compact.state(["disabled"] if forced else ["!disabled"])
        if forced:
            self.v_compact.set(True)

    def _refresh_sections(self):
        n = len(self.sections)
        if n == len(SECTIONS):
            text = f"all ({n})"
        else:
            names = [s.name for s in SECTIONS if s.key in self.sections]
            text = ", ".join(names) if n <= 3 else f"{n} of {len(SECTIONS)}"
        self.v_sections.set(text)

    def _refresh_cache(self):
        folder = self.v_output.get().strip()
        files, size = cache_size(Path(folder)) if folder else (0, 0)
        if files:
            self.v_cache.set(f"Cache: {files} items, {size / 1e6:.1f} MB "
                             "(makes the next exports faster)")
            self.btn_clear_cache.pack(side="left", padx=(8, 0))
        else:
            self.v_cache.set("Cache: empty")
            self.btn_clear_cache.pack_forget()

    def _set_busy(self, busy: bool, task: str = ""):
        self.btn_run.config(text="Stop" if busy and task == "export" else "Start export",
                            state="disabled" if busy and task != "export" else "normal")
        self.btn_login.state(["disabled"] if busy else ["!disabled"])
        self.btn_logout.state(["disabled"] if busy else ["!disabled"])
        self.btn_clear_cache.state(["disabled"] if busy else ["!disabled"])
        if busy:
            self.btn_show.grid_remove()
        else:
            self.v_progress_label.set("")

    def log(self, line: str):
        t = self.txt_log
        t.config(state="normal")
        t.insert("end", line + "\n")
        excess = int(t.index("end-1c").split(".")[0]) - MAX_LOG_LINES
        if excess > 0:
            t.delete("1.0", f"{excess + 1}.0")
        t.see("end")
        t.config(state="disabled")

    # ----- worker plumbing -----------------------------------------------
    def _start(self, task: str, target: Callable[[], Any]):
        CANCEL.clear()
        verbose = self.v_verbose.get()
        log.setLevel(logging.DEBUG if verbose else logging.INFO)
        # Verbose also shows garminconnect's own messages (login strategy, MFA steps)
        logging.getLogger("garminconnect").setLevel(logging.DEBUG if verbose else logging.WARNING)
        self.settings = self._collect_settings()
        save_settings(self.settings)
        self._set_busy(True, task)
        self.progress["value"] = 0
        self.task = task
        threading.Thread(target=self._guard, args=(task, target), daemon=True).start()

    def _guard(self, task, target):
        """Run `target` in the worker thread and always report completion."""
        try:
            result = target()
            self.q.put(("done", task, True, result))
        except ExportCancelled:
            self.q.put(("done", task, False, "Stopped."))
        except Exception as e:  # noqa: BLE001 -- reported to the user
            log.debug("Worker error", exc_info=True)
            self.q.put(("done", task, False, friendly_login_error(e)
                        if task == "login" or self.api is None else str(e)))

    def _ask_mfa(self, hint: str) -> str:
        """Called from the worker thread when Garmin asks for a 2FA code."""
        done = threading.Event()
        box: dict = {}
        self.q.put(("mfa", hint, done, box))
        done.wait()
        code = box.get("code")
        if code == RESEND:
            raise ResendCode()
        if not code:
            raise GarminConnectAuthenticationError("No verification code entered.")
        return code

    def _login(self) -> Any:
        email, password = self.v_email.get().strip(), self.v_password.get()
        self.api = login(DEFAULT_TOKENSTORE, email, password, prompt_mfa=self._ask_mfa)
        return self.api

    def _check_updates(self):
        tag = updates.newer_release()
        if tag:
            self.q.put(("update", tag))

    def _poll(self):
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "log":
                    self.log(msg[1])
                elif kind == "progress":
                    _, frac, label = msg
                    self.progress["value"] = frac * 1000
                    self.v_progress_label.set(label)
                elif kind == "mfa":
                    _, hint, done, box = msg
                    self.v_status.set("Waiting for the verification code…")
                    code = ask_mfa_code(self.root, hint)
                    box["code"] = code if code == RESEND else (code or "").strip()
                    if code == RESEND:
                        self.v_status.set("Asking Garmin for a new code…")
                    done.set()
                elif kind == "update":
                    self.lbl_update.config(text=f"⬆  {APP_NAME} {msg[1]} is available "
                                                f"(you have {__version__}). Click to download.")
                    self.frm_update.grid()
                elif kind == "done":
                    self._on_done(*msg[1:])
        except queue.Empty:
            pass
        self.root.after(POLL_MS, self._poll)

    def _on_done(self, task: str, ok: bool, result):
        self.task = None
        self._set_busy(False)
        if task == "login":
            if ok:
                self.v_password.set("")
                self.v_status.set("Logged in. Your session is saved on this computer.")
            else:
                self.api = None
                self.v_status.set(f"Login failed: {result}")
            self._refresh_account()
        else:
            exporter: GarminExporter | None = result if ok else None
            if exporter is None:
                self.v_status.set(f"Export failed: {result}")
            elif exporter.interrupted:
                self.v_status.set("Export stopped. Partial file saved; start again to resume (cache).")
            else:
                n = len(exporter.written_files)
                errs = f" ({len(exporter.errors)} section(s) with errors, see log)" if exporter.errors else ""
                self.v_status.set(f"Export complete: {n} file(s) in {self.settings['output_dir']}{errs}")
                self.progress["value"] = 1000
            if exporter is not None and exporter.written_files:
                self.last_files = list(exporter.written_files)
                self.btn_show.grid()
            self._refresh_account()
            self._refresh_cache()
        if self.closing:
            self.root.destroy()

    # ----- actions -------------------------------------------------------
    def on_login(self):
        if self._busy():
            return
        if not self.v_email.get().strip() or not self.v_password.get():
            self.v_status.set("Enter your Garmin email and password.")
            return
        self.v_status.set("Logging in to Garmin Connect…")
        self._start("login", self._login)

    def on_logout(self):
        if self._busy():
            return
        if messagebox.askyesno(APP_NAME, "Delete the Garmin session saved on this computer?"):
            forget_tokens()
            self.api = None
            self.v_status.set("Logged out.")
            self._refresh_account()

    def _export_options(self, s: dict) -> ExportOptions | None:
        """ExportOptions from the settings, or None (and a status message) if invalid."""
        start = end = None
        if s["mode"] == "range":
            start, end = parse_date(s["start"]), parse_date(s["end"])
            if not start or not end:
                self.v_status.set("Enter the dates as YYYY-MM-DD.")
                return None
            if start >= end:
                self.v_status.set("The start date must be before the end date.")
                return None
            if start > date.today():
                self.v_status.set("The start date is in the future.")
                return None
        return ExportOptions(
            days=s["days"], max_activities=s["activities"],
            fetch_all=s["mode"] == "all", update=s["mode"] == "update",
            compact=s["compact"], split=s["split"], use_cache=not s["no_cache"],
            delay=s["delay"], sections=s["sections"], start=start, end=end,
        )

    def on_run(self):
        if self._busy():
            CANCEL.set()
            self.v_status.set("Stopping… (saving partial export)")
            self.btn_run.state(["disabled"])
            return
        s = self._collect_settings()
        if not s["output_dir"]:
            self.v_status.set("Choose an output folder.")
            return
        if not has_tokens() and not (s["email"] and self.v_password.get()):
            self.v_status.set("Log in to your Garmin account first.")
            return
        options = self._export_options(s)
        if options is None:
            return
        if not self.settings.get("privacy_ack"):
            messagebox.showinfo(f"{APP_NAME}: your data", PRIVACY_NOTE, parent=self.root)
            self.settings["privacy_ack"] = True

        def progress(frac, label):
            self.q.put(("progress", frac, label))

        def job():
            api = self.api or self._login()
            try:
                return run_export(api, Path(s["output_dir"]), options, progress=progress)
            finally:
                save_tokens(api)  # tokens may have been refreshed during the export

        self.on_clear_log()
        self.v_status.set("Exporting…")
        self._start("export", job)

    def on_choose_sections(self):
        chosen = SectionsDialog(self.root, self.sections).result
        if chosen:
            self.sections = chosen
            self._refresh_sections()

    def on_choose_dir(self):
        d = filedialog.askdirectory(initialdir=self.v_output.get() or str(Path.home()), parent=self.root)
        if d:
            self.v_output.set(d)

    def on_open_dir(self):
        p = Path(self.v_output.get()).expanduser()
        p.mkdir(parents=True, exist_ok=True)
        open_in_file_manager(p)

    def on_show_files(self):
        files = [f for f in self.last_files if f.exists()]
        if files:
            open_in_file_manager(files[0], select=True)

    def on_clear_cache(self):
        if self._busy():
            return
        folder = Path(self.v_output.get()).expanduser()
        files, size = cache_size(folder)
        if messagebox.askyesno(APP_NAME, f"Delete the cache of this folder ({files} items, "
                                         f"{size / 1e6:.1f} MB)?\n\nExport files are kept. "
                                         "The next export downloads everything again."):
            clear_cache(folder)
            self._refresh_cache()
            self.v_status.set("Cache cleared.")

    def on_copy_log(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.txt_log.get("1.0", "end-1c"))

    def on_clear_log(self):
        self.txt_log.config(state="normal")
        self.txt_log.delete("1.0", "end")
        self.txt_log.config(state="disabled")

    def on_close(self):
        save_settings(self._collect_settings())
        if not self._busy():
            self.root.destroy()
            return
        if messagebox.askyesno(APP_NAME, "An export is running. Stop it and quit?\n"
                                         "(a partial file will be saved)"):
            self.closing = True
            CANCEL.set()
            self.v_status.set("Stopping…")


def run() -> None:
    """Open the desktop app (blocks until the window is closed)."""
    if sys.platform == "win32":
        try:  # crisp text on high-DPI screens
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    root = tk.Tk()
    App(root)
    root.mainloop()

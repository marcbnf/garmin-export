# AGENTS.md -- AI context for garmin-export

This file is for AI agents working on or consuming output from this project.
For human setup instructions, see [README.md](README.md).

## What this project does

A cross-platform desktop app (macOS, Windows, Linux; MIT license), pure
Python, packaged as `garmin_export/`. One entry point, `main.py`: it opens
the Tkinter app by default and runs in the terminal with `--no-gui`.
Releases are frozen with PyInstaller (one-folder build per OS: `.app` on
macOS, installer + portable zip on Windows, AppImage + tar.gz on Linux), so
end users need no Python install.

The export engine downloads all available health
and fitness data from a user's Garmin Connect account and writes it as one
plain text file with raw JSON data blocks. The output is designed for LLM
consumption -- every API response is dumped as complete, unfiltered JSON.
No markdown formatting is used; plain text headers separate sections.

This format was specifically chosen for compatibility with NotebookLM and
other LLM tools. Research found that .md files have known parsing bugs in
NotebookLM's RAG indexer, and content inside code fences (```json) gets
skipped. Plain .txt with raw JSON avoids both issues.

No official Garmin API key exists for personal use. The script authenticates
through Garmin's SSO (same flow as the website) via the
[python-garminconnect](https://github.com/cyberjunky/python-garminconnect)
library (>= 0.3, which has its own SSO client -- `garth` is no longer used).

## Output file structure

Each export produces a single file: `export/garmin_export_YYYY-MM-DD_HHMMSS.txt`
Update exports use: `export/garmin_export_YYYY-MM-DD_HHMMSS_update.txt`

The output uses plain text section headers and raw JSON (no markdown headings,
no code fences, no bold/italic). This is intentional -- NotebookLM's RAG
indexer has bugs with .md files and skips content inside code fences.

```
Garmin Connect Data Export            -- title + metadata (date range, export time)

Table of Contents                     -- numbered list with section names and descriptions;
                                         notes that all data is raw JSON
Profile                               -- section cache keyed by date range
Daily Health                          -- per-day: 13 endpoints fetched concurrently (4 threads)
  2026-03-22                          -- one per day, newest first
    Steps / Heart Rate / Sleep        -- each sub-section has a plain text title + raw JSON
Activities                            -- per-activity cache; list then detail per item
  Activity {id}                       -- summary, splits, zones, weather, time-series
Body Composition                      -- chunked yearly API calls, section cache
Training Metrics                      -- VO2max, FTP, hill/endurance scores, etc.
Goals and Records                     -- PRs, badges, goals
Trends                                -- weekly aggregates, daily steps, floors, progress
Golf                                  -- list then scorecard + shots per round
Gear                                  -- needs userProfileNumber; list then stats per item
Training Plans                        -- list then detail per plan
Workouts                              -- list then detail per workout
Hydration                             -- per-day, fetched concurrently (4 threads)
Nutrition                             -- per-day (3 calls/day), fetched concurrently
Women's Health                        -- chunked date range + pregnancy summary

Errors During Export                  -- only if any sections failed
```

Empty sections contain: "No data available."

## Architecture

### Package layout

```
main.py                 entry point -> garmin_export.app.main()
VERSION                 the version (e.g. 0.2.0): single source for the app, build.py and CI
garmin_export/
  __init__.py           __version__ (read from VERSION), APP_NAME, GITHUB_REPO
  __main__.py           python -m garmin_export
  app.py                argparse; --no-gui -> cli.run(args), else gui.run()
  cli.py                terminal mode: interactive login (.env, prompts, 3 attempts), run_export
  gui.py                Tkinter app (see GUI below)
  auth.py               login(), save_tokens(), has_tokens(), forget_tokens(), ResendCode,
                        friendly_login_error(); tokens in the OS credential store (keyring)
  api.py                CANCEL, ExportCancelled, RateLimiter/limiter, safe_call(), chunked_date_call()
  cache.py              ExportCache
  output.py             Document (text + JSON blocks, compact/split formatting), strip_empty,
                        downsample_timeseries, compact_daily
  split.py              write_split(): NotebookLM-sized files
  exporter.py           ExportOptions, GarminExporter, run_export()
  sections/__init__.py  SECTIONS registry: Section(name, export, description), in output order
  sections/<name>.py    one module per section, each exposing export(ex); Section.key = module name
  updates.py            GitHub latest-release check (GUI, opt-out)
tests/                  unittest suite, offline (tests/fake_api.py stands in for Garmin)
  fixtures/<name>/      Garmin-shaped cache files (daily/, activities/) for test_fixtures.py
tools/                  make_icon.py (Pillow), anonymize_cache.py (cache -> fixture)
packaging/              windows/installer.iss, linux/garmin-export.desktop, homebrew/garmin-export.rb
```

Dependency direction: `app -> cli/gui -> exporter -> sections -> api/output/cache`.
`auth` and `api` don't import the exporter. No module-level mutable state
except `api.CANCEL` and the process-wide `api.limiter`.

### Key pieces

| Item | Purpose |
|------|---------|
| `RateLimiter` (`api.limiter`) | Thread-safe adaptive pacer. Starts at 0.15s delay, doubles on 429, decays on success. Forced 2s pause every 250 calls. `reset(delay)` at each export. |
| `safe_call()` | Wraps every API call: pacing, 429 retry, returns `None` on any error. Raises `ExportCancelled` when `CANCEL` is set. |
| `ExportCache` | JSON file cache under `<output>/.cache/` (see Caching strategy). |
| `ExportOptions` | Dataclass of export settings (days, max_activities, fetch_all, update, compact, split, use_cache, delay, sections, start, end). Built by both CLI and GUI. |
| `GarminExporter` | Orchestrator. Computes the date range, iterates `SECTIONS`, writes the file(s), handles Ctrl-C / cancel (saves partial export), reports progress. |
| `Document` | Output buffer: `append()`, `section(title, data)`, `nodata()`, `json(data)`. Formatting follows compact/split mode. |
| `login()` | Non-interactive auth (saved tokens, then email/password, `prompt_mfa(hint)` callback that may raise `ResendCode`). Saves tokens. Used by the GUI; `cli._interactive_login()` adds prompts/.env. |
| `select_sections(keys)` | Sections by key (`daily_health`) or name (`Daily Health`), in output order; `ValueError` if unknown. |
| `run_export(api, out_dir, options, progress)` | Shared by CLI and GUI; returns the exporter (`written_files`, `errors`, `interrupted`). |

### Section contract

`export(ex: GarminExporter) -> None`. A section reads `ex.api`, `ex.cache`,
`ex.start_date`, `ex.today` (last day of the range: the real today unless a
custom end date is set), `ex.days`, `ex.compact`, `ex.update_mode`,
`ex.fetch_all`, `ex.custom_range`, `ex.max_activities`; caches via `ex.section_key(name)` (or
per day/activity); writes via `ex.doc`; may call `ex.progress(fraction,
label)`. Output must start with `"\n{Section name}\n"` (split mode relies on it).
Compact mode writes one `Schema: "..."` line followed by one JSON block.

### GUI (`garmin_export/gui.py`)

- Tkinter + ttk, no extra dependency. All UI text and docs are in English.
- Worker thread runs `login()` / `run_export()`; UI polls a `queue.Queue`
  every 100 ms. Messages: `log`, `progress`, `mfa`, `update`, `done`.
- Busy state is `App.task` ("login"/"export"/None), cleared only when the
  `done` message is handled -- not the thread's liveness, which ends earlier.
- Logging: a `QueueLogHandler` is attached to the `garmin_export` and
  `garminconnect` loggers (propagation off). `logging.basicConfig` is only
  called by the CLI, never at import.
- 2FA: `prompt_mfa` runs in the worker, posts an `mfa` message and blocks on
  a `threading.Event` until `MfaDialog` answers (code, cancel, or RESEND ->
  `ResendCode` raised in the worker).
- Modes: recent / range (From-To) / all / update; `SectionsDialog` picks the
  sections; cache size + "Clear cache…"; "Show files" reveals the export;
  privacy note shown once before the first export (`privacy_ack` setting);
  update banner from `updates.newer_release()` in a daemon thread.
  `App(root, check_updates=False)` in tests.
- Stop button / window close: sets `api.CANCEL`; next `safe_call` raises
  `ExportCancelled` (a `KeyboardInterrupt` subclass), so the partial export
  is written exactly as with Ctrl-C.
- Settings (never the password) in `settings.json` under the OS config dir
  (`~/Library/Application Support/GarminExport`, `%APPDATA%\GarminExport`,
  `~/.config/GarminExport`).

### Command line (`--no-gui`)

Same options as the original script (`--all`, `--days`, `--split`, `--update`,
`--login`...) plus `--from/--to`, `--sections`, `--list-sections`,
`--clear-cache`. An empty 2FA answer raises `ResendCode`. In the packaged Windows app (GUI subsystem, no console),
`app._attach_windows_console()` attaches to the parent terminal.

### Concurrency model

- Daily health: `ThreadPoolExecutor(max_workers=4)` -- 13 endpoints per day fetched in parallel,
  collected in endpoint order (deterministic output)
- Hydration: 4 days fetched concurrently (1 API call per day)
- Nutrition: 4 days concurrently (3 API calls per day)
- Activities: sequential (each activity has ~8 detail calls)
- `RateLimiter.wait()` uses a `threading.Lock` so concurrent threads are still paced

### Caching strategy

| Cache type | Key | Location | Lifetime |
|------------|-----|----------|----------|
| Daily health | `YYYY-MM-DD` | `.cache/daily/YYYY-MM-DD.json` | forever, except today/yesterday (never cached) |
| Hydration | `hydration_YYYY-MM-DD` | `.cache/daily/` | same |
| Nutrition | `nutrition_YYYY-MM-DD` | `.cache/daily/` | same |
| Activity | `{activityId}` | `.cache/activities/{activityId}.json` | forever |
| Section | `{name}@{start}_{end}` | `.cache/sections/` | until the date range changes (i.e. daily); older versions deleted on write |

Past days and finished activities don't change, so they're cached forever.
Today and yesterday are still syncing (sleep spans midnight), so they're
never cached. Sections (profile, training metrics, trends, gear...) depend
on the date range and change over time, so their key includes the range.
Re-running the same export the same day hits the cache everywhere, which
keeps interrupted `--all` exports fully resumable.

### Chunked date-range calls

Several Garmin endpoints reject date ranges longer than about one year with
HTTP 400. The `_chunked_date_call()` helper breaks ranges into 365-day
segments, calls each, and merges the list results. Used for: endurance score,
running tolerance, weekly intensity minutes, hill score, body composition,
weigh-ins, and menstrual calendar.

### Rate limiting details

| Parameter | Value |
|-----------|-------|
| Base delay | 0.15s (configurable via `--delay`) |
| On HTTP 429 | Double delay (max 10s), wait 60s (interruptible), retry once |
| On success streak (10+) | Gradually reduce delay toward base |
| Every 250 calls | Forced 2s pause |
| On general error | 1.2x delay bump |

### Authentication

- `garminconnect` >= 0.3 handles Garmin SSO itself (no `garth`)
- Tokens (`api.client.dumps()`, ~2.3 KB JSON with the DI refresh token) are
  kept by `save_tokens()` in the OS credential store via `keyring`, service
  `garmin-export`, split in 1000-char entries `tokens.0..n` + `tokens.count`
  (Windows Credential Manager caps an entry at 2560 bytes UTF-16). Fallback
  when no real backend (headless Linux) or `GARMIN_EXPORT_KEYRING=0`:
  `<tokenstore>/garmin_tokens.json` via `client.dump()` (0600). A leftover
  token file is migrated to the credential store at the next login.
  `Garmin.login()` accepts the token JSON inline. Save again after each
  export: tokens are refreshed in memory. Tests must never hit the real
  store: they set `GARMIN_EXPORT_KEYRING=0` / patch `auth._keyring`.
- Old garth-format files (`oauth1_token.json`/`oauth2_token.json`) are
  rejected: user must log in again
- 2FA uses the public `return_on_mfa=True` / `resume_login(state, code)` API;
  `state["method"]`/`state["flow"]` tell where the code comes from (no
  private attributes). Up to 3 code attempts. `ResendCode` restarts the
  login with `client.skip_strategies = {"mobile+cffi", "mobile+requests"}`:
  the mobile flow never explicitly asks Garmin to send an email/SMS code,
  the widget flow does (`/sso/verifyMFA/mfaCode`)
- CLI: `.env` file or `GARMIN_EMAIL`/`GARMIN_PASSWORD`, else interactive prompts;
  `--login` authenticates without exporting
- `friendly_login_error()` turns exceptions into short messages

### Error handling with garminconnect >= 0.3

API errors are raised as `GarminConnectConnectionError` (404 as its subclass
`GarminConnectNotFoundError`) with the HTTP response attached as `.response`.
A 429 on an API call is **not** a `GarminConnectTooManyRequestsError` (that
one is only raised during login/token refresh). `safe_call()` therefore reads
`exc.response.status_code` to detect 400/404 (expected, logged at debug) and
429 (back off + retry).

### Compact mode (`--compact`)

Reduces output file size from ~170 MB to roughly 10-20 MB for LLM upload.
Applied at write time only -- the cache always stores full data.

What it does:
- `_strip_empty()` recursively removes None, empty strings, empty lists/dicts
- `_json()` uses `indent=None` (single-line) instead of `indent=2`
- `_compact_daily()` downsamples high-frequency daily time-series (heart rate,
  stress, sleep, respiration, HRV, body battery, intensity minutes) to at
  most 24 points (bucket size rounded up). Lists of objects that hold the
  series (body battery: `[{"bodyBatteryValuesArray": [...]}]`) are
  downsampled inside each object.
  `_downsample_timeseries()` handles both list-of-dicts (keyed by timestamp
  field) and list-of-lists (`[[timestamp, value], ...]`) formats, since
  Garmin returns different formats for different endpoints
- Activity time-series (`details` key) is omitted entirely; summaries, splits,
  and zones are kept
- Each section becomes a single JSON block with a schema description
- Output filename gets a `_compact` suffix

### Split mode (`--split`)

Splits output into multiple files for NotebookLM's 500K word limit.
Implies `--compact`. Applied at write time after the full export is built.

What it does:
- `_SPLIT_WORD_LIMIT = 480000` (safety margin under 500K)
- Splits on section boundaries; oversized sections split by JSON keys/items
- Each file gets its own header with a list of sections it contains
- Output filenames: `..._split_part1of6.txt`, `..._split_part2of6.txt`, etc.

### Update mode (`--update`)

Exports only new data since the last export. Designed for incremental use
after an initial `--all --split` base export.

How it works:
- Scans the output directory for the most recent export file(s)
- Parses the end date from that file's metadata
- Sets start_date to 1 day before that end date (overlap for late-arriving data)
- If no previous export is found, falls back to `--days` default (30 days)

What it includes (all sections, section cache bypassed so new data is always fetched):
- Profile, Daily Health, Activities, Body Composition, Training Metrics, Goals and Records,
  Trends, Golf, Gear, Training Plans, Workouts, Hydration, Nutrition, Women's Health

Implies `--compact`. Output filename gets a `_update` suffix.

### NotebookLM compatibility notes

Based on extensive research (Reddit, official docs, community reports):
- Official supported formats: txt, md, csv, pdf, docx, pptx, Google Docs/Sheets
- Limits: 500K words per source, 200MB file size, 50 sources per notebook
- Known bug: .md files have parsing issues in NotebookLM's RAG indexer
- Content inside code fences (```json) gets skipped by the indexer
- Plain .txt is fastest and most reliable for the AI to process
- NotebookLM uses RAG (chunks documents, retrieves per query) -- it does NOT
  read entire files at once

## Lessons learned

1. **No official Garmin API for personal use.** The `python-garminconnect`
   library reverse-engineers the web API. Endpoints can change without notice.

2. **Rate limits are real but undocumented.** HTTP 429 responses start after
   sustained bursts. The adaptive rate limiter with exponential backoff handles
   this well. Starting conservative (0.15s) and ramping down is safer than
   starting fast.

3. **Some endpoints have a roughly one-year date range limit.** They return
   400 (not 429) for longer ranges. Chunking into yearly segments solved this.
   The body battery range endpoint (`/bodyBattery/reports/daily`) returns 400
   for any date span and was removed entirely -- per-day data covers it.

4. **Cache the past, never the present.** Historical days and activities do
   not change, so caching them permanently makes multi-hour `--all` exports
   resumable and re-exports near-instant (about 1-2 KB per cached day). But
   today/yesterday are incomplete, and range-based sections (VO2 max, trends,
   gear...) evolve: an earlier version cached them forever under a fixed key,
   freezing stale data and even reusing a 30-day section in a full-history
   export. Their keys now include the date range.

5. **Concurrent fetching needs thread-safe rate limiting.** Four threads hit
   the API simultaneously, but all go through one `RateLimiter` with a lock.
   This gives roughly 3-4x speedup while still respecting pacing.

6. **Graceful Ctrl-C matters for long exports.** The script catches
   `KeyboardInterrupt` per-section, saves whatever was exported so far, and
   notes the interruption. Cache is already persisted per-item, so nothing
   is lost.

7. **Garmin endpoints vary in structure.** Some are list+detail (activities,
   golf, gear, workouts), some are per-day (daily health, hydration,
   nutrition), some are single-call (profile, goals), and some need chunked
   date ranges (trends, training metrics, body comp). Each pattern needs
   its own caching strategy.

8. **Not all accounts have all data.** Golf, women's health, nutrition, and
   hydration may return empty or 404. The `safe_call()` wrapper catches all
   errors and returns `None`. Sections with no data get a "No data available"
   note instead of crashing.

9. **Output size can be large.** A 3-year `--all` export produces around
   170 MB of text. This is fine for LLMs with large context windows or
   RAG systems that chunk by section headers.

10. **The Table of Contents is critical for AI parsing.** It tells the model
    what sections exist, what each one contains, and that all data is raw JSON.
    Without it, models may struggle to navigate a 170 MB file.

11. **Plain text beats markdown for LLM tools.** NotebookLM has known bugs
    with .md files and skips content inside code fences. Reddit users
    universally recommend .txt for best results.

12. **Garmin time-series data comes in two formats.** Some endpoints return
    list-of-dicts (`[{"timestamp": ..., "value": ...}]`) while others
    (heart rate, stress, body battery) return list-of-lists
    (`[[timestamp, value], ...]`). Downsampling must handle both formats
    or it silently skips data, leaving hundreds of points instead of ~24
    hourly samples in compact mode.

13. **Incremental exports need overlap.** When resuming from the last
    export's end date, starting 1 day earlier catches data that arrived
    late (e.g., sleep data that spans midnight, or syncs that happen
    after the export ran). The slight duplication is worth the safety.

14. **Library upgrades change exception types.** garminconnect 0.3 dropped
    garth and `requests.HTTPError`; code that matched on those silently
    stopped detecting 404s and 429s. Check `exc.response.status_code`.
    Hence the upper bound in requirements.txt; upgrade garminconnect on its own.

15. **Test on real data shapes.** The fake API returns flat dicts; Garmin-shaped
    fixtures revealed that compact mode left body battery (480 points, inside a
    list of objects) and 25-47-point series untouched. Add an anonymized
    fixture (`tools/anonymize_cache.py`) when an endpoint's format is in doubt.

## Build and release

- `build.py [label] [version]`: PyInstaller build of `main.py`
  (`--windowed --onedir` everywhere: fast startup, no self-extracting exe
  that antivirus tools flag), then packages into `dist/`: macOS `.zip` of the
  `.app`; Windows portable `.zip` + Inno Setup `-setup.exe`
  (`packaging/windows/installer.iss`, per-user install, skipped without
  ISCC); Linux `.tar.gz` + AppImage (AppDir with AppRun/.desktop/icon,
  skipped without appimagetool). Exits if a `v*` version differs from
  `VERSION` (bundled into the app with `--add-data`). Bundles keyring backends (`--copy-metadata keyring
  --collect-submodules keyring.backends`) and certifi
- No universal2 macOS build: curl_cffi/cffi publish no universal2 wheels
- Icon: `garmin_export/assets/icon.icns` (macOS `--icon`), `icon.ico` (Windows
  `--icon`), `icon.png` (bundled with `--add-data`, set by the GUI via
  `iconphoto`; Linux has no embedded icon). Regenerate with `tools/make_icon.py`
  (Pillow); the generated files are committed
- macOS signing: ad hoc by default (Gatekeeper blocks first launch on other
  Macs). If `MACOS_SIGN_IDENTITY` is set, PyInstaller signs every binary with
  that Developer ID + hardened runtime + `ENTITLEMENTS` (unsigned executable
  memory, no library validation), the bundle is re-signed after the Info.plist
  edit, and with `APPLE_ID`/`APPLE_TEAM_ID`/`APPLE_APP_PASSWORD` the zip is
  notarized (`notarytool --wait`) and stapled, then re-zipped. CI imports the
  certificate from the `MACOS_CERT_P12`/`MACOS_CERT_PASSWORD` secrets into a
  temporary keychain. Archives are zipped with `ditto` to keep symlinks/signature
- `.github/workflows/release.yml`: matrix (windows-latest, macos-latest arm64,
  macos-15-intel, ubuntu-22.04) on every push/PR; installs
  `requirements-dev.txt` constrained by `requirements-lock.txt`; ruff + mypy
  (Linux job, non-blocking); tests; installs Inno Setup (choco) and
  appimagetool (both optional, non-blocking). Releases need no tag: the
  `version` job reads `VERSION`; on a push to main with no release
  `v<VERSION>` yet (`gh release view`), builds get that version and the
  `release` job publishes it (softprops creates the tag on `github.sha`)
  with all packages, generated release notes, and `garmin-export.rb`
  (Homebrew cask filled with the macOS zips' sha256). Same VERSION = build only
- No Dependabot (the owner found its branches/PRs confusing): bump
  `requirements-lock.txt` / action versions by hand
- PyInstaller cannot cross-compile, hence one runner per OS/arch
- Tkinter comes with the setup-python / python.org builds. Homebrew Python needs `brew install python-tk`

## Dependencies

Runtime (see `requirements.txt`, bounded; exact versions in `requirements-lock.txt`):

- `garminconnect` >= 0.3.20, < 0.4 -- Garmin Connect API wrapper with its own SSO
  client (pulls in `curl_cffi`, `requests`, `ua-generator`)
- `keyring` 25 -- OS credential store for the session tokens

Tkinter (standard library) for the GUI. `requirements-dev.txt`: pyinstaller,
ruff, mypy, pillow (icon). Tool config in `pyproject.toml` (not a package).
Python 3.10+ required (garminconnect's minimum).

## Files

See "Package layout" above. Also: `build.py` (PyInstaller build + packages),
`requirements.txt` / `requirements-dev.txt` / `requirements-lock.txt`,
`pyproject.toml` (ruff, mypy), `.github/workflows/release.yml` (lint, tests,
builds on every push, automatic release when VERSION changes), `README.md`,
`LICENSE` (MIT), `.gitignore` (blocks `export/`, `.env`, `.garminconnect/`,
Python/build artifacts).

## Testing

`python -m unittest discover -s tests` -- fully offline, ~3 s. Covers the
full export, 404/429 handling, cache rules, split, compact downsampling,
cancel, update mode, section selection, custom date ranges, cache clearing,
version comparison, login (2FA hints, wrong code, resend, keyring chunks,
file fallback and migration), the entry point (`--no-gui` dispatch, CLI
options end to end), the GUI (login with 2FA, export, stop; skipped without
a display) and exports on Garmin-shaped fixtures (`test_fixtures.py`).
`ruff check .` and `mypy` must pass (CI).
When refactoring, also compare outputs byte for byte against the previous
version with the same `FakeApi` (full, compact and split modes).

# Garmin Export

Desktop app (macOS, Windows, Linux) that downloads your health and fitness
data from Garmin Connect and saves it as a plain text file with raw JSON
blocks. The format is designed for AI tools (NotebookLM, ChatGPT, Claude),
and it also works as a local backup.

- Pure **Python**: Tkinter interface, data fetched with
  [python-garminconnect](https://github.com/cyberjunky/python-garminconnect).
  It logs in through the same SSO as the Garmin website, so no API key is
  needed.
- Nothing to install: each release ships a standalone executable built
  with PyInstaller.

## Installation

Download the archive for your system from the [Releases](../../releases)
page:

| System | File |
|--------|------|
| macOS Apple Silicon (M1 and later) | `garmin-export-vX.Y.Z-macos-arm64.zip` |
| macOS Intel | `garmin-export-vX.Y.Z-macos-x86_64.zip` |
| Windows 10/11 (installer) | `garmin-export-vX.Y.Z-windows-x86_64-setup.exe` |
| Windows 10/11 (portable) | `garmin-export-vX.Y.Z-windows-x86_64.zip` |
| Linux x86_64 | `garmin-export-vX.Y.Z-linux-x86_64.AppImage` or `.tar.gz` |

**macOS**: unzip the archive and move `Garmin Export.app` to Applications.
Unless the release was notarized (see
[Signing the macOS app](#signing-the-macos-app-optional)), macOS blocks the
first launch with "Apple could not verify…". To allow it, either:

- open the app once, click **Done**, then go to **System Settings →
  Privacy & Security**, scroll down and click **Open Anyway** next to
  "Garmin Export" (on macOS 14 and earlier, right-click the app → **Open**
  also works); or
- run this once in Terminal:

  ```
  xattr -dr com.apple.quarantine "/Applications/Garmin Export.app"
  ```

**Windows**: run the installer (no administrator rights needed; it adds
a Start menu entry), or unzip the portable archive and run
`GarminExport\GarminExport.exe`. If SmartScreen shows a warning, click
"More info" and then "Run anyway".

**Linux**: make the AppImage executable (`chmod +x garmin-export-*.AppImage`)
and run it. Or `tar xzf garmin-export-*.tar.gz` and run
`GarminExport/GarminExport`; a `.desktop` file and an icon are included.

**Homebrew (macOS)**: each release includes a `garmin-export.rb` cask. Once
it is published in a tap (see [Homebrew cask](#homebrew-cask)):
`brew install --cask <owner>/tap/garmin-export`.

The app tells you when a new version is available (an anonymous request to
GitHub at startup; it can be turned off in the options).

## Usage

1. **Log in** with your Garmin email and password. They are sent only to
   Garmin's servers and are never stored. If two-step verification is on,
   a dialog asks for the code; if no code arrives, click **No code? Send a
   new one** (the app then asks Garmin explicitly to send it). The session
   is saved in the system credential store (macOS Keychain, Windows
   Credential Manager, Secret Service on Linux), or in
   `~/.garminconnect/garmin_tokens.json` when there is none. *Log out*
   deletes it.
2. **Choose what to export**:
   - *Recent period*: the last N days and up to M activities.
   - *Date range*: from one date to another (YYYY-MM-DD).
   - *Full history*: goes back to your very first activity.
   - *Incremental update*: only the data added since the latest export in
     the output folder.
   - *Sections*: all by default; **Choose…** to export only some of them
     (e.g. Activities and Daily Health).
3. **Options**:
   - *Compact*: a file about 10 times smaller. Empty fields are removed and
     time series are downsampled.
   - *Split for NotebookLM*: several files under 500,000 words each
     (NotebookLM's limit per source).
   - *Ignore cache*: downloads everything again. **Clear cache…** deletes
     it (its size is shown below the folder).
4. Click **Start export**. The log shows progress. **Stop** ends the export
   cleanly and saves a partial file. Start again later to resume where it
   stopped. When it is done, **Show files** opens the folder with the
   export selected.

By default, files are written to `Documents/Garmin Export/`.

> **Your data**: the export contains personal health data (heart rate,
> sleep, activity locations, body measurements). It stays on your computer.
> If you upload it to an AI service, that service receives this data: check
> its privacy policy, and don't share the file publicly.

### Recommended workflow

Run a *Full history* export with *Split for NotebookLM* once. After that,
use *Incremental update* to add new data.

## Exported data

Profile, daily health (steps, heart rate, sleep, stress, Body Battery,
SpO2, HRV, respiration…), activities (summary, splits, zones, weather,
time series), body composition, training metrics (VO2 max, FTP, scores…),
records and badges, trends, golf, gear, training plans, workouts,
hydration, nutrition and women's health.

The output is plain text with section titles and raw JSON, with no
markdown and no code fences. NotebookLM's RAG indexer handles `.md` files
poorly and skips content inside code fences.

## Cache and rate limits

Data is cached in `<output folder>/.cache/`, so an interrupted export
resumes where it stopped:

- past days and activities are kept forever;
- today and yesterday are never cached, because their data may still be
  syncing;
- account-wide sections (profile, training metrics, trends…) are kept for
  a given date range, so they are downloaded again once a day.

The app paces itself and slows down when Garmin returns HTTP 429. The base
delay can be changed in the interface.

## Development

Requirements: Python 3.10 or later, with Tkinter. The python.org
installers include it. With Homebrew: `brew install python-tk`. On
Debian/Ubuntu: `apt install python3-tk`.

```
pip install -r requirements-dev.txt -c requirements-lock.txt
python main.py                       # the desktop app
python main.py --no-gui --help       # the command line
python -m unittest discover -s tests # offline tests (fake Garmin API)
ruff check . && mypy                 # lint and type check (also run by the CI)
```

`requirements.txt` lists the runtime dependencies with version bounds;
`requirements-lock.txt` pins the exact versions used for release builds.
To upgrade, change a version there, push, and check that the CI passes.

### Tests on real data shapes

`tests/test_fixtures.py` runs exports on every folder under
`tests/fixtures/`. `garmin_sample` is synthetic data in Garmin's formats.
To test with the structures your own account returns, anonymize part of an
export cache (names, emails, ids and GPS coordinates are removed;
measurements are kept). Folders named `private*` are git-ignored:

```
python tools/anonymize_cache.py "~/Documents/Garmin Export" tests/fixtures/private-mine
```

### Project layout

```
main.py                    single entry point: app by default, --no-gui for the terminal
garmin_export/
  app.py                   argument parsing, dispatch to the GUI or the CLI
  gui.py                   desktop interface (Tkinter)
  cli.py                   command-line mode (prompts, .env)
  auth.py                  Garmin Connect login and saved tokens
  api.py                   rate-limited, cancellable API calls
  cache.py                 on-disk cache (resumable exports)
  exporter.py              orchestration: date range, sections, progress, files
  output.py                text/JSON writer, compact mode
  split.py                 split output into NotebookLM-sized files
  sections/                one module per exported section (profile, daily_health, ...)
  updates.py               new-release check (GitHub)
  assets/                  app icon (icns, ico, png)
tests/                     offline tests with a fake Garmin API
  fixtures/                Garmin-shaped data for test_fixtures.py
tools/make_icon.py         regenerates the app icon
tools/anonymize_cache.py   turns an export cache into a test fixture
packaging/                 Windows installer (Inno Setup), Linux .desktop, Homebrew cask
build.py                   PyInstaller build + packages in dist/
```

To add a section, create a module in `garmin_export/sections/` with an
`export(ex)` function and register it in `sections/__init__.py`.

### Command line

The same program runs in the terminal with `--no-gui`:

```
python main.py --no-gui --login            # log in only
python main.py --no-gui                    # last 30 days, 100 activities
python main.py --no-gui --all --split      # full history, split for NotebookLM
python main.py --no-gui --update           # new data since the last export
python main.py --no-gui --from 2025-01-01 --to 2025-06-30
python main.py --no-gui --sections activities,daily_health
python main.py --no-gui --list-sections    # section names for --sections
python main.py --no-gui --clear-cache      # delete the cache of --output
```

Credentials can be typed when prompted, or provided through the
`GARMIN_EMAIL` / `GARMIN_PASSWORD` environment variables or a `.env` file.
Set `GARMIN_EXPORT_KEYRING=0` to keep the session in a file
(`--tokenstore`, default `~/.garminconnect`) instead of the credential
store, e.g. for scheduled exports on a server.
The packaged app accepts the same options, for example
`"Garmin Export.app/Contents/MacOS/Garmin Export" --no-gui --update` or
`GarminExport.exe --no-gui --update`.

### Building the app locally

```
pip install -r requirements-dev.txt -c requirements-lock.txt
python build.py
```

PyInstaller only builds for the system it runs on, so each system needs
its own machine. The CI takes care of that. On Windows, `build.py` also
makes the installer when [Inno Setup 6](https://jrsoftware.org/isinfo.php)
is installed; on Linux, the AppImage when
[appimagetool](https://github.com/AppImage/appimagetool) is on the `PATH`
(or in `APPIMAGETOOL`).

There is no single "universal" macOS build: `curl_cffi`, which
garminconnect uses to log in, has no universal2 wheels, so arm64 and Intel
are built separately.

### Publishing a release

1. Write the new version in the `VERSION` file at the root (e.g. `0.2.1`).
2. Commit and push to `main`.

That's all. The `.github/workflows/release.yml` workflow builds the app on
Windows, macOS (arm64 and Intel) and Linux at every push. When `VERSION`
holds a version that has no release yet, it publishes the release
`v0.2.1` with every package attached and generated release notes (the
tag is created automatically; no `git tag` needed). Pushes that keep the
same `VERSION` only build (the packages are in the run's artifacts).

### Homebrew cask

Each release also includes `garmin-export.rb`, a Homebrew cask with that
release's checksums (template: `packaging/homebrew/garmin-export.rb`). To
offer `brew install --cask`, create a public repository named
`homebrew-tap` and copy the file to `Casks/garmin-export.rb` at each
release.

### Signing the macOS app (optional)

Without an Apple Developer account, the macOS app is only signed ad hoc and
users must allow it once (see Installation). With a paid Apple Developer
account, the CI can sign it with a Developer ID certificate and have Apple
notarize it, so it opens with no warning. Add these repository secrets
(Settings → Secrets and variables → Actions):

| Secret | Value |
|--------|-------|
| `MACOS_CERT_P12` | Your "Developer ID Application" certificate + private key exported as `.p12`, base64-encoded (`base64 -i cert.p12 \| pbcopy`) |
| `MACOS_CERT_PASSWORD` | Password of that `.p12` file |
| `MACOS_SIGN_IDENTITY` | Certificate name, e.g. `Developer ID Application: Jane Doe (AB12CD34EF)` |
| `APPLE_ID` | Apple ID email of the developer account |
| `APPLE_TEAM_ID` | 10-character team ID |
| `APPLE_APP_PASSWORD` | App-specific password from [account.apple.com](https://account.apple.com) |

`build.py` reads the same variables locally. When they are not set,
signing falls back to ad hoc and notarization is skipped.

## Security

The Garmin session (a refresh token that gives lasting access to your
account) is kept in the system credential store when available, otherwise
in a file readable only by you. *Log out* deletes it.

The `.gitignore` excludes your credentials (`.env`), your login tokens
(`.garminconnect/`, `garmin_tokens.json`) and your exported data
(`export/`, any `garmin_export_*.txt` file and `.cache/` folder, wherever
`--output` points). Never commit them.

## License

[MIT](LICENSE)

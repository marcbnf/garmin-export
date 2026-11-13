#!/usr/bin/env python3
"""
Build the desktop app with PyInstaller and package it into dist/.

    pip install -r requirements-dev.txt -c requirements-lock.txt
    python build.py [label] [version]

label   : archive suffix, e.g. macos-arm64 (default: <os>-<arch> of this machine)
version : e.g. v1.2.0 (default: "dev")

Output (all in dist/):
    macOS   garmin-export-<version>-<label>.zip         "Garmin Export.app"
    Windows garmin-export-<version>-<label>.zip         GarminExport folder (portable)
            garmin-export-<version>-<label>-setup.exe   installer (needs Inno Setup 6)
    Linux   garmin-export-<version>-<label>.tar.gz      GarminExport folder
            garmin-export-<version>-<label>.AppImage    (needs appimagetool)

Every OS uses PyInstaller's one-folder mode: fast startup, and no
self-extracting executable (which antivirus tools often flag on Windows).

macOS signing (optional, environment variables):
    MACOS_SIGN_IDENTITY   "Developer ID Application: Name (TEAMID)" in the keychain.
                          Without it, the app is only signed ad hoc and Gatekeeper
                          blocks it on other Macs until the user allows it.
    APPLE_ID, APPLE_TEAM_ID, APPLE_APP_PASSWORD
                          With MACOS_SIGN_IDENTITY: notarize with Apple and staple
                          the ticket, so the app opens with no warning.
Optional tools:
    ISCC          path to Inno Setup's ISCC.exe (default: PATH, then Program Files)
    APPIMAGETOOL  path to appimagetool (default: PATH)
"""

import os
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "garmin_export" / "assets"
PACKAGING = ROOT / "packaging"
APP_NAME = "Garmin Export"
EXE_NAME = "GarminExport"
BUNDLE_ID = "io.github.garmin-export"

# Hardened-runtime exceptions a PyInstaller-frozen Python needs (Developer ID only)
ENTITLEMENTS = {
    "com.apple.security.cs.allow-unsigned-executable-memory": True,
    "com.apple.security.cs.disable-library-validation": True,
}


def default_label() -> str:
    os_name = {"darwin": "macos", "win32": "windows"}.get(sys.platform, "linux")
    arch = platform.machine().lower()
    arch = {"amd64": "x86_64", "aarch64": "arm64"}.get(arch, arch)
    return f"{os_name}-{arch}"


def app_version() -> str:
    """The VERSION file at the repository root (single source of the version)."""
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        sys.exit(f"VERSION must look like 1.2.3, got {version!r}")
    return version


def run(*cmd: str, secret: str = "", env: dict | None = None) -> None:
    shown = " ".join(cmd).replace(secret, "***") if secret else " ".join(cmd)
    print("+", shown, flush=True)
    subprocess.run(cmd, check=True, cwd=ROOT, env={**os.environ, **(env or {})})


def main() -> None:
    label = sys.argv[1] if len(sys.argv) > 1 else default_label()
    version = sys.argv[2] if len(sys.argv) > 2 else "dev"
    if version.startswith("v") and version.lstrip("v") != app_version():
        sys.exit(f"Version mismatch: tag {version} but the VERSION file says "
                 f"{app_version()}. Update VERSION before tagging.")
    work, dist = ROOT / "build", ROOT / "dist"
    shutil.rmtree(work, ignore_errors=True)
    dist.mkdir(exist_ok=True)
    base = f"garmin-export-{version}-{label}"

    common = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--onedir",
        "--name", APP_NAME if sys.platform == "darwin" else EXE_NAME,
        "--collect-data", "garminconnect",
        # keyring finds its OS backends through entry points
        "--copy-metadata", "keyring", "--collect-submodules", "keyring.backends",
        # CA bundle for the update check
        "--collect-data", "certifi",
        # window icon used at runtime by the GUI, and the version
        "--add-data", f"{ASSETS / 'icon.png'}{os.pathsep}garmin_export/assets",
        "--add-data", f"{ROOT / 'VERSION'}{os.pathsep}garmin_export",
        # optional garminconnect extras we don't use
        "--exclude-module", "pydantic", "--exclude-module", "pandas",
        "--distpath", str(work / "dist"), "--workpath", str(work / "pyi"),
        "--specpath", str(work),
    ]

    if sys.platform == "darwin":
        outputs = [build_macos(common, work, dist / f"{base}.zip", version)]
    elif sys.platform == "win32":
        outputs = build_windows(common, work, dist, base, version)
    else:
        outputs = build_linux(common, work, dist, base)

    for out in outputs:
        print(f"\nBuilt {out} ({out.stat().st_size / 1e6:.1f} MB)")


# ---------------------------------------------------------------------------
# Windows: portable zip + Inno Setup installer
# ---------------------------------------------------------------------------
def build_windows(common: list[str], work: Path, dist: Path, base: str, version: str) -> list[Path]:
    run(*common, "--icon", str(ASSETS / "icon.ico"), "main.py")
    folder = work / "dist" / EXE_NAME
    out = dist / f"{base}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(folder.rglob("*")):
            if f.is_file():
                z.write(f, f"{base}/{EXE_NAME}/{f.relative_to(folder).as_posix()}")
        for f in (ROOT / "LICENSE", ROOT / "README.md"):
            z.write(f, f"{base}/{f.name}")
    outputs = [out]

    iscc = find_tool("ISCC", "iscc", [
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Inno Setup 6" / "ISCC.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Inno Setup 6" / "ISCC.exe",
    ])
    if iscc:
        run(iscc, f"/DAppVersion={version}", f"/DRootDir={ROOT}", f"/DSourceDir={folder}",
            f"/DOutputDir={dist}", f"/DOutputName={base}-setup",
            str(PACKAGING / "windows" / "installer.iss"))
        outputs.append(dist / f"{base}-setup.exe")
    else:
        print("Inno Setup not found: skipping the installer (portable zip only)")
    return outputs


# ---------------------------------------------------------------------------
# Linux: tar.gz of the folder + AppImage
# ---------------------------------------------------------------------------
def build_linux(common: list[str], work: Path, dist: Path, base: str) -> list[Path]:
    run(*common, "main.py")
    folder = work / "dist" / EXE_NAME
    out = dist / f"{base}.tar.gz"
    with tarfile.open(out, "w:gz") as t:
        t.add(folder, f"{base}/{EXE_NAME}")
        for f in (ROOT / "LICENSE", ROOT / "README.md", PACKAGING / "linux" / "garmin-export.desktop",
                  ASSETS / "icon.png"):
            t.add(f, f"{base}/{'garmin-export.png' if f.name == 'icon.png' else f.name}")
    outputs = [out]

    tool = find_tool("APPIMAGETOOL", "appimagetool", [])
    if tool:
        outputs.append(build_appimage(tool, folder, work / "AppDir", dist / f"{base}.AppImage"))
    else:
        print("appimagetool not found: skipping the AppImage (tar.gz only)")
    return outputs


def build_appimage(tool: str, folder: Path, appdir: Path, out: Path) -> Path:
    shutil.rmtree(appdir, ignore_errors=True)
    shutil.copytree(folder, appdir / "usr" / "bin")
    shutil.copy(PACKAGING / "linux" / "garmin-export.desktop", appdir / "garmin-export.desktop")
    shutil.copy(ASSETS / "icon.png", appdir / "garmin-export.png")
    icons = appdir / "usr" / "share" / "icons" / "hicolor" / "256x256" / "apps"
    icons.mkdir(parents=True)
    shutil.copy(ASSETS / "icon.png", icons / "garmin-export.png")
    apprun = appdir / "AppRun"
    apprun.write_text('#!/bin/sh\nHERE="$(dirname "$(readlink -f "$0")")"\n'
                      f'exec "$HERE/usr/bin/{EXE_NAME}" "$@"\n')
    apprun.chmod(0o755)
    out.unlink(missing_ok=True)
    # APPIMAGE_EXTRACT_AND_RUN: appimagetool is itself an AppImage; CI has no FUSE
    run(tool, str(appdir), str(out), env={"ARCH": platform.machine(), "APPIMAGE_EXTRACT_AND_RUN": "1"})
    return out


def find_tool(env_var: str, name: str, candidates: list[Path]) -> str | None:
    if os.environ.get(env_var):
        return os.environ[env_var]
    found = shutil.which(name)
    if found:
        return found
    return next((str(p) for p in candidates if p.is_file()), None)


# ---------------------------------------------------------------------------
# macOS: .app bundle, signed (ad hoc or Developer ID), optionally notarized
# ---------------------------------------------------------------------------
def build_macos(common: list[str], work: Path, out: Path, version: str) -> Path:
    identity = os.environ.get("MACOS_SIGN_IDENTITY", "").strip()
    entitlements = work / "entitlements.plist"
    work.mkdir(parents=True, exist_ok=True)
    entitlements.write_bytes(plistlib.dumps(ENTITLEMENTS))

    signing = ["--codesign-identity", identity, "--osx-entitlements-file", str(entitlements)] if identity else []
    run(*common, "--osx-bundle-identifier", BUNDLE_ID,
        "--icon", str(ASSETS / "icon.icns"), *signing, "main.py")
    app = work / "dist" / f"{APP_NAME}.app"
    set_plist_version(app, version)

    # Info.plist changed: re-sign the bundle
    if identity:
        # PyInstaller already signed every binary inside with the identity
        run("codesign", "--force", "--timestamp", "--options", "runtime",
            "--entitlements", str(entitlements), "--sign", identity, str(app))
        run("codesign", "--verify", "--deep", "--strict", str(app))
    else:
        print("MACOS_SIGN_IDENTITY not set: ad-hoc signature only (Gatekeeper will warn)")
        run("codesign", "--force", "--deep", "--sign", "-", str(app))

    zip_app(app, out)
    if identity and notarize(out):
        run("xcrun", "stapler", "staple", str(app))
        zip_app(app, out)  # re-zip with the stapled ticket
    return out


def notarize(archive: Path) -> bool:
    apple_id = os.environ.get("APPLE_ID", "")
    team_id = os.environ.get("APPLE_TEAM_ID", "")
    password = os.environ.get("APPLE_APP_PASSWORD", "")
    if not (apple_id and team_id and password):
        print("APPLE_ID / APPLE_TEAM_ID / APPLE_APP_PASSWORD not set: skipping notarization")
        return False
    run("xcrun", "notarytool", "submit", str(archive), "--apple-id", apple_id,
        "--team-id", team_id, "--password", password, "--wait", secret=password)
    return True


def zip_app(app: Path, out: Path) -> None:
    out.unlink(missing_ok=True)
    # ditto keeps the bundle's symlinks and signature intact
    run("ditto", "-c", "-k", "--norsrc", "--keepParent", str(app), str(out))
    with zipfile.ZipFile(out, "a") as z:
        z.write(ROOT / "LICENSE", "LICENSE")
        z.write(ROOT / "README.md", "README.md")


def set_plist_version(app: Path, version: str) -> None:
    plist = app / "Contents" / "Info.plist"
    data = plistlib.loads(plist.read_bytes())
    v = version.lstrip("v") if version[:1] == "v" else app_version()
    data.update({
        "CFBundleShortVersionString": v,
        "CFBundleVersion": v,
        "CFBundleDisplayName": APP_NAME,
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "11.0",
    })
    plist.write_bytes(plistlib.dumps(data))


if __name__ == "__main__":
    main()

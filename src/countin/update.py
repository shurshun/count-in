"""Checks GitHub releases for a newer version, downloads it and swaps the installed bundle on restart."""

import hashlib
import json
import os
import shutil
import ssl
import re
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from importlib.metadata import PackageNotFoundError, version

import truststore

REPO = "shurshun/count-in"
TIMEOUT = 10.0


@dataclass
class Release:
    version: str
    url: str  # the asset for this platform, or the release page
    sha256: str = ""  # the asset's digest from the API; empty means no self-update


def current_version() -> str:
    try:
        return version("countin")
    except PackageNotFoundError:
        return "0"


def parse(v: str) -> tuple[tuple[int, ...], int]:
    """'v1.2.3' -> ((1, 2, 3), 1). A suffix (rc1, dev) sorts before the final release; trailing zeros do not count."""
    m = re.match(r"[vV]?(\d+(?:\.\d+)*)(.*)", v.strip())
    if not m:
        return (), 0
    nums = [int(x) for x in m.group(1).split(".")]
    while nums and nums[-1] == 0:
        nums.pop()
    return tuple(nums), 0 if m.group(2) else 1


def asset_suffix() -> str:
    return "windows-x64.zip" if sys.platform == "win32" else "macos-arm64.zip"


def newer_release(data: dict, current: str) -> Release | None:
    """The latest release from the GitHub API response when it is newer than current."""
    tag = data.get("tag_name", "")
    if parse(tag) <= parse(current):
        return None
    page = f"https://github.com/{REPO}/releases/latest"
    asset = next((a for a in data.get("assets", []) if a.get("name", "").endswith(asset_suffix())), {})
    url = asset.get("browser_download_url", "")

    # Only links into this repository get opened or downloaded.
    if not url.startswith(f"https://github.com/{REPO}/"):
        return Release(tag.lstrip("vV"), page)
    digest = asset.get("digest") or ""
    return Release(tag.lstrip("vV"), url, digest.removeprefix("sha256:") if digest.startswith("sha256:") else "")


def check() -> Release | None:
    """Blocking call, run it off the UI thread. Raises on network, TLS and API errors.

    TLS trusts the operating system's certificate store (Keychain, Windows store) through truststore.
    The bundled Python's own OpenSSL would look in a folder that exists only where python.org Python is installed.
    """
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/latest",
                                 headers={"Accept": "application/vnd.github+json", "User-Agent": "Count-In"})
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=_tls()) as r:
        return newer_release(json.load(r), current_version())


def _tls() -> ssl.SSLContext:
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


def installed_bundle() -> Path | None:
    """The folder that an update replaces: Count-In.app on macOS, the Count-In folder on Windows.

    None when the app cannot replace itself: a development run, a read-only location, or macOS
    App Translocation (an app started from Downloads without moving it runs from a random read-only path).
    """
    if not getattr(sys, "frozen", False):
        return None
    exe = Path(sys.executable).resolve()
    root = exe.parents[2] if sys.platform == "darwin" else exe.parent
    if sys.platform == "darwin" and (root.suffix != ".app" or "/AppTranslocation/" in str(root)):
        return None
    return root if os.access(root.parent, os.W_OK) else None


def can_self_update(r: Release) -> bool:
    return bool(r.sha256) and installed_bundle() is not None


def staging_dir(root: Path) -> Path:
    """Next to the bundle, so the final move stays on one volume and is a rename."""
    return root.parent / ".count-in-update"


def download(r: Release, root: Path, progress=lambda done, total: None) -> Path:
    """Downloads and unpacks the release next to root; returns the new bundle. Blocking, raises on any error.

    The archive streams to disk and gets hashed on the way; a digest mismatch deletes it.
    """
    stage = staging_dir(root)
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir()
    archive = stage / "update.zip"
    req = urllib.request.Request(r.url, headers={"User-Agent": "Count-In"})
    h = hashlib.sha256()
    with urllib.request.urlopen(req, timeout=60, context=_tls()) as resp, open(archive, "wb") as f:
        total, done = int(resp.headers.get("Content-Length") or 0), 0
        while chunk := resp.read(1 << 20):
            f.write(chunk)
            h.update(chunk)
            done += len(chunk)
            progress(done, total)
    if h.hexdigest() != r.sha256:
        shutil.rmtree(stage, ignore_errors=True)
        raise ValueError("downloaded update does not match its sha256")
    out = stage / "new"

    # ditto keeps the symlinks and executable bits of the .app; zipfile drops them.
    if sys.platform == "darwin":
        subprocess.run(["ditto", "-x", "-k", str(archive), str(out)], check=True)
    else:
        with zipfile.ZipFile(archive) as z:
            z.extractall(out)
    archive.unlink()
    new = out / root.name
    if not new.is_dir():
        raise ValueError(f"{root.name} is missing from the update")
    return new


MAC_SWAP = """#!/bin/sh
# Waits for the app to quit, swaps the bundle, starts the new one. A failed move puts the old one back.
pid="$1"; root="$2"; new="$3"; stage="$4"; relaunch="$5"
exec >>"$6" 2>&1; set -x
while kill -0 "$pid" 2>/dev/null; do sleep 0.2; done
rm -rf "$root.old"
if mv "$root" "$root.old"; then
  if mv "$new" "$root"; then rm -rf "$root.old"; else mv "$root.old" "$root"; fi
fi
rm -rf "$stage" "$0"
[ "$relaunch" = 1 ] && open "$root"
"""

WIN_SWAP = r"""@echo on
rem `timeout` needs a console, which a detached helper lacks, so ping does the waiting.
rem Waits for the app to quit, swaps the folder, starts the new one. A failed move puts the old one back.
set "pid=%~1" & set "root=%~2" & set "new=%~3" & set "stage=%~4" & set "relaunch=%~5"
rem System tools by full path: Git for Windows puts its own find.exe ahead of them on PATH.
set "sys=%SystemRoot%\System32"
:wait
"%sys%\tasklist.exe" /FI "PID eq %pid%" /NH 2>nul | "%sys%\find.exe" "%pid%" >nul && ("%sys%\PING.EXE" -n 2 127.0.0.1 >nul & goto wait)
if exist "%root%.old" rmdir /s /q "%root%.old"
rem Antivirus or a running countin.exe can hold the folder for a moment.
set tries=0
:move
move "%root%" "%root%.old" >nul 2>&1 && goto swap
set /a tries+=1
if %tries% lss 20 ("%sys%\PING.EXE" -n 2 127.0.0.1 >nul & goto move)
goto start
:swap
move "%new%" "%root%" >nul 2>&1 && (rmdir /s /q "%root%.old") || move "%root%.old" "%root%" >nul
:start
rmdir /s /q "%stage%" 2>nul
if "%relaunch%"=="1" start "" "%root%\Count-In.exe"
(goto) 2>nul & del "%~f0"
"""


HELPER_LOG = Path(tempfile.gettempdir()) / "count-in-update.log"


def install_on_exit(root: Path, new: Path, relaunch: bool, pid: int | None = None) -> None:
    """Starts a detached helper that replaces root with new once process pid (default: this one) exits."""
    pid = pid or os.getpid()
    win = sys.platform == "win32"
    fd, script = tempfile.mkstemp(suffix=".cmd" if win else ".sh")
    with os.fdopen(fd, "w", newline="\r\n" if win else "\n") as f:
        f.write(WIN_SWAP if win else MAC_SWAP)
    args = [str(pid), str(root), str(new), str(staging_dir(root)), "1" if relaunch else "0", str(HELPER_LOG)]

    # The helper runs outside the bundle, so nothing in it holds the folder being moved.
    if win:
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        with open(HELPER_LOG, "a") as log:
            subprocess.Popen(["cmd", "/c", script, *args], cwd=tempfile.gettempdir(), creationflags=flags,
                             stdin=subprocess.DEVNULL, stdout=log, stderr=log)
    else:
        subprocess.Popen(["/bin/sh", script, *args], cwd="/", start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

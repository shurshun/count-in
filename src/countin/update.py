"""Checks GitHub releases for a newer version of the app."""

import json
import platform
import re
import sys
import urllib.request
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version

REPO = "shurshun/count-in"
TIMEOUT = 10.0


@dataclass
class Release:
    version: str
    url: str  # the asset for this platform, or the release page


def current_version() -> str:
    try:
        return version("countin")
    except PackageNotFoundError:
        return "0"


def parse(v: str) -> tuple[int, ...]:
    """'v1.2.3' -> (1, 2, 3); anything after the numeric part (rc, dev) is ignored."""
    parts = []
    for p in v.lstrip("vV").split("."):
        digits = re.match(r"\d*", p).group()
        if not digits:
            break
        parts.append(int(digits))
        if digits != p:
            break
    return tuple(parts)


def asset_suffix() -> str:
    if sys.platform == "win32":
        return "windows-x64.zip"
    return f"macos-{'arm64' if platform.machine() == 'arm64' else 'x64'}.zip"


def newer_release(data: dict, current: str) -> Release | None:
    """The latest release from the GitHub API response when it is newer than current."""
    tag = data.get("tag_name", "")
    if data.get("draft") or data.get("prerelease") or parse(tag) <= parse(current):
        return None
    url = next((a["browser_download_url"] for a in data.get("assets", []) if a["name"].endswith(asset_suffix())),
               data.get("html_url", f"https://github.com/{REPO}/releases/latest"))
    return Release(tag.lstrip("vV"), url)


def check() -> Release | None:
    """Blocking call, run it off the UI thread. Network errors and timeouts return None."""
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/latest",
                                 headers={"Accept": "application/vnd.github+json", "User-Agent": "Count-In"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return newer_release(json.load(r), current_version())
    except Exception:
        return None

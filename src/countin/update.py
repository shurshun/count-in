"""Checks GitHub releases for a newer version of the app."""

import json
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
    url = next((a.get("browser_download_url", "") for a in data.get("assets", [])
                if a.get("name", "").endswith(asset_suffix())), page)

    # The browser opens only links into this repository.
    return Release(tag.lstrip("vV"), url if url.startswith(f"https://github.com/{REPO}/") else page)


def check() -> Release | None:
    """Blocking call, run it off the UI thread. Network errors and timeouts return None."""
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/latest",
                                 headers={"Accept": "application/vnd.github+json", "User-Agent": "Count-In"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return newer_release(json.load(r), current_version())
    except Exception:
        return None

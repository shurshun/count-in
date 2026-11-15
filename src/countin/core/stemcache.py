"""Whole-song stems kept on disk, so a song opened again skips the separation.

Each song gets a folder named after a hash of its file and the separation settings, with one 16-bit FLAC per
stem (about 200 MB for a six-minute song; 16 bits keep the stems' noise near -96 dB). The oldest songs go when
the cache passes LIMIT.
"""

import hashlib
import os
import shutil
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

LIMIT = 3 * 1024**3  # bytes


def folder() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "Count-In" / "stems"
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Count-In" / "cache" / "stems"
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "count-in" / "stems"


def key(song: Path) -> str:
    """The song file's content and the separation settings: a re-encoded or edited file gets new stems."""
    from . import stems

    h = hashlib.sha256(f"{stems.MODEL}/{stems.OVERLAP}".encode())
    with open(song, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()[:32]


def load(k: str, names: tuple[str, ...], length: int) -> dict[str, np.ndarray] | None:
    """The cached stems, or None when any is missing or does not match the song's length."""
    d = folder() / k
    out = {}
    for name in names:
        f = d / f"{name}.flac"
        if not f.is_file():
            return None
        y, _sr = sf.read(f, dtype="float32", always_2d=True)
        if len(y) != length:
            return None
        out[name] = y
    os.utime(d)  # recently used: pruned last
    return out


def save(k: str, stems: dict[str, np.ndarray], sr: int) -> None:
    """Writes the stems into a temporary folder and renames it, so a half-written entry never loads."""
    d = folder() / k
    part = d.with_name(d.name + ".part")
    shutil.rmtree(part, ignore_errors=True)
    part.mkdir(parents=True)
    for name, y in stems.items():
        sf.write(part / f"{name}.flac", np.clip(y, -1.0, 1.0), sr, subtype="PCM_16")
    shutil.rmtree(d, ignore_errors=True)
    part.rename(d)
    prune()


def prune(limit: int = LIMIT) -> None:
    """Drops the least recently used songs until the cache fits in limit bytes."""
    root = folder()
    if not root.is_dir():
        return
    entries = [(e.stat().st_mtime, sum(f.stat().st_size for f in e.iterdir()), e)
               for e in root.iterdir() if e.is_dir() and not e.name.endswith(".part")]
    total = sum(size for _t, size, _e in entries)
    for _t, size, e in sorted(entries):
        if total <= limit:
            break
        shutil.rmtree(e, ignore_errors=True)
        total -= size


def size() -> tuple[int, int]:
    """(bytes, songs) the cache holds."""
    root = folder()
    if not root.is_dir():
        return 0, 0
    songs = [e for e in root.iterdir() if e.is_dir()]
    return sum(f.stat().st_size for e in songs for f in e.iterdir()), len(songs)


def clear() -> None:
    shutil.rmtree(folder(), ignore_errors=True)

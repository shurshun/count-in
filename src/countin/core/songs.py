"""What the user set for each song (tempo, the "1", pauses, levels, speed, loop, markers), kept by the file's
content in the data folder, so a song reopens as it was left."""

import hashlib
import json
import os
from pathlib import Path

from .assets import data_dir


def folder() -> Path:
    return data_dir().parent / "songs-editor-dev"


def key(song: Path) -> str:
    h = hashlib.sha256()
    with open(song, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()[:32]


def load(k: str) -> dict | None:
    dest = folder()
    for directory in (dest, dest.with_name("songs-tempo-dev"), dest.with_name("songs")):
        try:
            state = json.loads((directory / f"{k}.json").read_text("utf-8"))
            if isinstance(state, dict):
                return state
        except (OSError, ValueError):
            continue
    return None


def save(k: str, state: dict) -> None:
    """Written to a temporary file and renamed, so a crash never leaves half a file."""
    d = folder()
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f"{k}.json.part"
    tmp.write_text(json.dumps(state, ensure_ascii=False), "utf-8")
    os.replace(tmp, d / f"{k}.json")

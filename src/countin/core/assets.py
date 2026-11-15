"""Model weights live outside the app bundle, so app updates stay small.

Search order for each file:
1. the folder in COUNTIN_ASSETS;
2. an `assets` folder next to the app (Count-In.app on macOS, Count-In.exe on Windows), for a copy carried on a
   flash drive;
3. the user data folder, where downloads and copied folders go;
4. `models/` in the project root, for development.

Missing files get downloaded from this project's `assets-v1` release (Windows also takes the ONNX core there) (a prerelease, so the update check never
sees it), falling back to the original publishers.
"""

import hashlib
import os
import shutil
import ssl
import sys
import urllib.request
from pathlib import Path

import truststore

RELEASE = "https://github.com/shurshun/count-in/releases/download/assets-v1/"

# name -> (size in bytes, sha256, fallback URL)
FILES = {
    "final0.ckpt": (
        81058141, "8c328b45f59d8dd3dff219253ff6a8d6482be57d0133a29140e2febbf8eb8331",
        "https://cloud.cp.jku.at/public.php/dav/files/7ik4RrBKTS273gp/final0.ckpt"),
    "separator/BS-Roformer-SW.ckpt": (
        699412152, "24e7d35ee9c64415673d3fd33e06a67cac2c103c5df6267ba1576459c775916e",
        "https://github.com/nomadkaraoke/python-audio-separator/releases/download/model-configs/BS-Roformer-SW.ckpt"),
    "separator/BS-Roformer-SW.yaml": (
        4653, "b558996f1e25eb48798bd6502505a5de94c4f966d6edfb1a0420f06cc40b501a",
        "https://github.com/nomadkaraoke/python-audio-separator/releases/download/model-configs/BS-Roformer-SW.yaml"),
}

# Windows separates on the GPU through DirectML with this ONNX export of the RoFormer core (`accel.export_onnx`);
# a Mac builds its Core ML model from the weights above and needs no download.
if sys.platform == "win32":
    FILES["separator/BS-Roformer-SW-core.onnx"] = (
        712562453, "09593a8ef0027cd1f281c6a3cf6241a474249c573a665606102de35611b7bccb",
        RELEASE + "BS-Roformer-SW-core.onnx")
TOTAL = sum(size for size, _sha, _url in FILES.values())


def data_dir() -> Path:
    """Where downloads go: Application Support on macOS, %APPDATA% on Windows."""
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "Count-In" / "assets"


def app_dir() -> Path | None:
    """The folder holding the app: next to Count-In.app on macOS, next to Count-In.exe on Windows."""
    if not getattr(sys, "frozen", False):
        return None
    exe = Path(sys.executable).resolve()
    for parent in exe.parents:
        if parent.suffix == ".app":
            return parent.parent
    return exe.parent


def search_dirs() -> list[Path]:
    dirs = []
    if os.environ.get("COUNTIN_ASSETS"):
        dirs.append(Path(os.environ["COUNTIN_ASSETS"]))
    if (a := app_dir()) is not None:
        dirs.append(a / "assets")
    dirs.append(data_dir())
    dirs.append(Path(__file__).resolve().parents[3] / "models")
    return dirs


def _complete(folder: Path, name: str) -> bool:
    p = folder / name
    return p.is_file() and p.stat().st_size == FILES[name][0]


def path(name: str) -> Path:
    """The first complete copy of an asset file or folder ("separator"); the data folder when none exists."""
    names = [n for n in FILES if n == name or n.startswith(name + "/")]
    for d in search_dirs():
        if all(_complete(d, n) for n in names) if names else (d / name).exists():  # unpinned: any copy
            return d / name
    return data_dir() / name


def missing() -> list[str]:
    return [n for n in FILES if not any(_complete(d, n) for d in search_dirs())]


def missing_bytes() -> int:
    return sum(FILES[n][0] for n in missing())


def has_all(folder: Path) -> bool:
    return all(_complete(folder, n) for n in FILES)


def download(progress=None, cancelled=lambda: False) -> None:
    """Downloads the missing files into the data folder, checking each sha256. progress(done, total) in bytes."""
    todo = missing()
    total, done = sum(FILES[n][0] for n in todo), 0
    for name in todo:
        size, sha, fallback = FILES[name]
        dest = data_dir() / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        errors = []
        for url in (RELEASE + name.split("/")[-1], fallback):
            h = hashlib.sha256()
            got = 0
            try:
                # The bundled Python has no CA file, so TLS checks against the system's certificates.
                tls = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                with urllib.request.urlopen(url, timeout=60, context=tls) as r, open(part, "wb") as f:
                    while chunk := r.read(1 << 20):
                        if cancelled():
                            raise InterruptedError
                        f.write(chunk)
                        h.update(chunk)
                        got += len(chunk)
                        if progress:
                            progress(done + got, total)
            except InterruptedError:
                part.unlink(missing_ok=True)
                raise
            except OSError as e:
                part.unlink(missing_ok=True)
                errors.append(f"{url}: {e}")
                continue
            if h.hexdigest() == sha:
                part.replace(dest)
                break
            part.unlink(missing_ok=True)
            errors.append(f"{url}: checksum mismatch")
        else:
            raise RuntimeError(f"could not download {name} ({'; '.join(errors)})")
        done += size


def copy_from(folder: Path, progress=None) -> None:
    """Copies the asset files from a chosen folder (a flash drive, a backup) into the data folder."""
    names = [n for n in FILES if _complete(folder, n)]
    if not names:
        raise FileNotFoundError(f"no Count-In assets in {folder}")
    total, done = sum(FILES[n][0] for n in names), 0
    for n in names:
        dest = data_dir() / n
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(folder / n, dest)
        done += FILES[n][0]
        if progress:
            progress(done, total)

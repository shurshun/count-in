"""Audio I/O through ffmpeg. All paths go as argv lists, so spaces and Cyrillic are safe on every OS."""

import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# Output format -> file extension.
FORMATS = {"mp3": ".mp3", "flac": ".flac", "alac": ".m4a", "wav": ".wav"}

# Hide console windows spawned from the GUI on Windows.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def resource(*parts: str) -> Path:
    """Path inside the PyInstaller bundle when frozen, else inside the project root."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[3]))
    return base.joinpath(*parts)


def tool(name: str) -> str:
    exe = name + (".exe" if sys.platform == "win32" else "")
    bundled = resource("bin", exe)
    if bundled.exists():
        return str(bundled)
    found = shutil.which(name)
    if not found:
        raise RuntimeError(f"{name} not found: install it or put it into bin/")
    return found


def _run(args: list[str], stdin: bytes | None = None) -> bytes:
    # Without piped input ffmpeg gets /dev/null, so it cannot eat a shell loop's stdin.
    feed = {"input": stdin} if stdin is not None else {"stdin": subprocess.DEVNULL}
    p = subprocess.run(args, capture_output=True, creationflags=_NO_WINDOW, **feed)
    if p.returncode != 0:
        raise RuntimeError(f"{Path(args[0]).name} failed: {p.stderr.decode(errors='replace')[-800:]}")
    return p.stdout


@dataclass
class AudioInfo:
    sr: int
    channels: int
    codec: str = "mp3"
    bits: int = 0  # bit depth of a lossless source, 0 for lossy
    bitrate: int = 0  # bits per second as stored, 0 when unknown
    duration: float = 0.0
    tags: dict[str, str] = field(default_factory=dict)  # lowercase keys: artist, album, date, ...

    @property
    def lossless(self) -> bool:
        return self.bits > 0

    @property
    def format(self) -> str:
        """Default output format for this source."""
        return {"mp3": "mp3", "alac": "alac"}.get(self.codec, "flac")


def probe(path: str | Path) -> AudioInfo:
    out = _run([tool("ffprobe"), "-v", "error", "-select_streams", "a:0",
                "-show_entries", "stream=codec_name,sample_rate,channels,bits_per_raw_sample,sample_fmt,bit_rate"
                ":format=bit_rate,duration:format_tags:stream_tags",
                "-of", "json", str(Path(path).absolute())])
    data = json.loads(out)
    s, f = data["streams"][0], data.get("format", {})

    # FLAC and Ogg keep tags on the stream, mp3 and m4a on the container; keys vary in case.
    tags = {k.lower(): v for k, v in {**s.get("tags", {}), **f.get("tags", {})}.items()}
    codec = s["codec_name"]
    lossless = codec in ("flac", "alac") or codec.startswith("pcm_")
    bits = int(s.get("bits_per_raw_sample") or 0) or (32 if "flt" in s.get("sample_fmt", "") else 16)

    return AudioInfo(
        sr=int(s["sample_rate"]),
        channels=int(s["channels"]),
        codec=codec,
        bits=bits if lossless else 0,
        bitrate=int(s.get("bit_rate") or f.get("bit_rate") or 0),
        duration=float(f.get("duration") or 0),
        tags=tags,
    )


def decode(path: str | Path) -> tuple[np.ndarray, AudioInfo]:
    """Float32 PCM [n, channels] at the native rate.

    Clicks get placed on beats found in this same decoded stream, so the source's encoder delay never moves them.
    """
    info = probe(path)
    raw = _run([tool("ffmpeg"), "-v", "error", "-i", str(Path(path).absolute()), "-map", "0:a:0",
                "-f", "f32le", "-acodec", "pcm_f32le", "-ac", str(info.channels), "-"])
    pcm = np.frombuffer(raw, dtype="<f4").reshape(-1, info.channels).copy()
    return pcm, info


def _codec_args(fmt: str, info: AudioInfo) -> list[str]:
    # A lossy source has no bit depth of its own; 24 bit keeps the decoded float without dither.
    bits = info.bits if info.lossless and info.bits <= 24 else 24
    if fmt == "mp3":
        return ["-c:a", "libmp3lame", "-b:a", "320k", "-compression_level", "0", "-id3v2_version", "3"]
    if fmt == "flac":
        return ["-c:a", "flac", "-compression_level", "8",
                "-sample_fmt", "s16" if bits == 16 else "s32", "-bits_per_raw_sample", str(bits)]
    if fmt == "alac":
        return ["-c:a", "alac", "-sample_fmt", "s16p" if bits == 16 else "s32p", "-bits_per_raw_sample", str(bits)]
    if fmt == "wav":
        return ["-c:a", "pcm_s16le" if bits == 16 else "pcm_s24le"]
    raise ValueError(f"unknown format {fmt}")


def format_of(path: str | Path) -> str:
    ext = Path(path).suffix.lower()
    for fmt, e in FORMATS.items():
        if e == ext:
            return fmt
    raise ValueError(f"unsupported output extension {ext}, use one of {', '.join(FORMATS.values())}")


def encode(pcm: np.ndarray, info: AudioInfo, out: str | Path, tags_from: str | Path | None = None) -> None:
    """Write mp3 (320k, LAME tag for gapless), FLAC, ALAC or WAV by the extension of `out`. Tags and cover come from tags_from."""
    fmt = format_of(out)
    pcm = np.ascontiguousarray(pcm, dtype="<f4")
    args = [tool("ffmpeg"), "-v", "error", "-y",
            "-f", "f32le", "-ar", str(info.sr), "-ac", str(pcm.shape[1]), "-i", "-"]
    if tags_from:
        args += ["-i", str(tags_from), "-map", "0:a", "-map_metadata", "1"]

        # Cover art only: V matches real video, which the audio muxers reject. WAV has no cover stream.
        if fmt != "wav":
            args += ["-map", "1:v?", "-map", "-1:V?", "-c:v", "copy", "-disposition:v", "attached_pic"]

    # An absolute path cannot start with "-" and pass for an option.
    args += [*_codec_args(fmt, info), str(Path(out).resolve())]
    _run(args, stdin=pcm.tobytes())


def output_path(song: Path, fmt: str, levels: dict[str, float], folder: Path | None = None) -> Path:
    """In folder (default: next to the source): song_countin.ext, or song_backing_guitar-off_vocals-6dB.ext when stems got turned down."""
    parts = [f"{name}-off" if db == float("-inf") else f"{name}{db:g}dB" for name, db in levels.items()]
    tag = "_".join(["backing", *parts]) if parts else "countin"
    return (folder or song.parent) / f"{song.stem}_{tag}{FORMATS[fmt]}"


def describe(info: AudioInfo) -> str:
    """One line about the file: format, bitrate or bit depth, rate, channels, compression."""
    name = {"mp3": "MP3", "flac": "FLAC", "alac": "ALAC", "aac": "AAC", "vorbis": "Ogg Vorbis", "opus": "Opus"}
    parts = [name.get(info.codec, "WAV" if info.codec.startswith("pcm_") else info.codec.upper())]
    if info.lossless:
        parts.append(f"{info.bits} бит")
    elif info.bitrate:
        parts.append(f"{round(info.bitrate / 1000)} kbps")
    parts.append(f"{info.sr / 1000:g} кГц")
    parts.append({1: "моно", 2: "стерео"}.get(info.channels, f"{info.channels} кан."))
    if info.lossless and info.bitrate and not info.codec.startswith("pcm_"):
        parts.append(f"сжатие до {info.bitrate / (info.sr * info.bits * info.channels):.0%}")
    return " · ".join(parts)


_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_name(text: str) -> str:
    """A folder name valid on macOS and Windows."""
    return _UNSAFE.sub("_", text).strip().rstrip(".")[:120] or "_"


def organized_dir(root: Path, tags: dict[str, str], artist: bool, year: bool, album: bool) -> Path:
    """root/Artist/Year - Album from the source tags; levels left out are skipped."""
    d = root
    if artist:
        d /= safe_name(tags.get("album_artist") or tags.get("artist") or "Неизвестный исполнитель")
    y = (tags.get("date") or tags.get("year") or "")[:4]
    if album:
        a = tags.get("album") or "Без альбома"
        d /= safe_name(f"{y} - {a}" if year and y else a)
    elif year:
        d /= safe_name(y or "Без года")
    return d

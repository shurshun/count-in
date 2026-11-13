"""Count-in grid and sample-accurate mixing."""

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf
import soxr

from .io import resource

# Meter -> (beats per bar as the detector counts them, clicks per beat).
# 6/8 counts six eighths over two dotted-quarter beats.
METERS = {"4/4": (4, 1), "3/4": (3, 1), "6/8": (2, 3)}

SILENCE_DB = -60.0

# The closing hits of the build-up count-in, about +4 dB.
BUILDUP_GAIN = 1.6

# A sample starts at its first value above this share of the peak (-40 dB).
ONSET_LEVEL = 0.01


@dataclass
class Click:
    time: float  # seconds, relative to the original song start
    accent: bool  # first click of a bar
    gain: float = 1.0


def click_grid(first_downbeat: float, period: float, bars: int, meter: str) -> list[Click]:
    """Clicks over `bars` bars, ending one click before the first downbeat."""
    beats, sub = METERS[meter]
    per_bar = beats * sub
    step = period / sub
    n = bars * per_bar
    return [Click(first_downbeat - (n - k) * step, k % per_bar == 0) for k in range(n)]


def buildup_grid(first_downbeat: float, period: float, bars: int, meter: str) -> list[Click]:
    """Plain clicks over the first half, twice as dense over the second, the last three louder.

    Modeled on a live count-in (2 bars of 4/4: four quarters, eight eighths), so the band hears the intro coming.
    """
    beats, sub = METERS[meter]
    per_bar = beats * sub
    step = period / sub
    n = bars * per_bar
    half = n // 2

    # Positions in clicks before the downbeat: -n .. -(half+1) plain, then -half .. -0.5 in half steps.
    pos = [-float(k) for k in range(n, half, -1)] + [-half + j / 2 for j in range(2 * half)]
    return [Click(first_downbeat + q * step, q.is_integer() and int(q) % per_bar == 0,
                  BUILDUP_GAIN if i >= len(pos) - 3 else 1.0) for i, q in enumerate(pos)]


def pattern_grid(midi_path: str | Path, first_downbeat: float, period: float, meter: str) -> list[Click]:
    """MIDI note-on positions in beats, stretched to the song tempo. The pattern fills whole bars before the downbeat."""
    import mido

    mid = mido.MidiFile(str(midi_path))
    tick = 0
    hits = []
    for msg in mido.merge_tracks(mid.tracks):
        tick += msg.time
        if msg.type == "note_on" and msg.velocity > 0:
            hits.append((tick / mid.ticks_per_beat, msg.velocity / 127))
    if not hits:
        raise ValueError(f"no notes in {midi_path}")

    # MIDI beats are quarter notes. 6/8 bars hold three quarters of length 2/3 of a dotted-quarter beat.
    beats, sub = METERS[meter]
    quarters_per_bar = beats * sub / (2 if sub == 3 else 1)
    sec_per_quarter = period * beats / quarters_per_bar
    last = max(b for b, _ in hits)
    bars = max(1, math.floor(last / quarters_per_bar) + 1)
    start = first_downbeat - bars * quarters_per_bar * sec_per_quarter
    return [Click(start + b * sec_per_quarter, math.isclose(b % quarters_per_bar, 0, abs_tol=1e-6), v)
            for b, v in hits]


def _synth(freq: float, sr: int) -> np.ndarray:
    """Stick-like hit: onset at sample 0, noise plus a decaying tone."""
    t = np.arange(int(0.08 * sr)) / sr
    rng = np.random.default_rng(int(freq))
    body = np.sin(2 * np.pi * freq * t) * np.exp(-t / 0.012)
    noise = rng.standard_normal(len(t)) * np.exp(-t / 0.004)
    x = 0.6 * body + 0.4 * noise
    return (0.8 * x / np.abs(x).max()).astype(np.float32)


def load_samples(sr: int, folder: Path | None = None) -> tuple[np.ndarray, np.ndarray]:
    """(hi, lo) mono samples at `sr`, each starting at its attack. Missing files get synthesized in memory."""
    folder = folder or resource("samples")
    out = []
    for name, freq in (("stick_hi.wav", 2600.0), ("stick_lo.wav", 1700.0)):
        p = folder / name
        if p.exists():
            x, file_sr = sf.read(p, dtype="float32", always_2d=True)
            x = x.mean(axis=1)
        else:
            x, file_sr = _synth(freq, 44100), 44100
        if file_sr != sr:
            x = soxr.resample(x, file_sr, sr).astype(np.float32)

        # Silence before the attack would delay every click by its length.
        out.append(x[np.argmax(np.abs(x) > ONSET_LEVEL * np.abs(x).max()):])
    return out[0], out[1]


def leading_silence(pcm: np.ndarray, sr: int) -> float:
    level = 10 ** (SILENCE_DB / 20)
    loud = np.flatnonzero(np.abs(pcm).max(axis=1) > level)
    return (loud[0] if len(loud) else len(pcm)) / sr


@dataclass
class Rendered:
    pcm: np.ndarray
    offset: float  # original-song time t maps to output time t + offset
    click_samples: list[int]  # click onsets in the output
    clipped: int  # samples that hit full scale


def render(pcm: np.ndarray, sr: int, clicks: list[Click], first_downbeat: float,
           pre_roll: str = "keep", volume_db: float = 0.0,
           samples: tuple[np.ndarray, np.ndarray] | None = None) -> Rendered:
    """Mix clicks into the song. Audio before the downbeat stays and gets the clicks on top.

    trim cuts leading silence first, never past the downbeat.
    Silence gets prepended when the song starts too late to hold the count-in.
    """
    if first_downbeat >= len(pcm) / sr:
        raise ValueError(f"downbeat {first_downbeat:.3f} s is past the end of the song")
    hi, lo = samples or load_samples(sr)

    cut = min(int(leading_silence(pcm, sr) * sr), int(first_downbeat * sr)) if pre_roll == "trim" else 0
    earliest = min(c.time for c in clicks)

    # Each click rounds from its own float time, so error never accumulates.
    pad = max(0, -math.floor(earliest * sr - cut))
    shift = pad - cut
    starts = [int(round(c.time * sr)) + shift for c in clicks]

    song = pcm[cut:]
    out = np.zeros((pad + len(song), pcm.shape[1]), dtype=np.float32)
    out[pad:] = song

    # Loud masters decode with peaks above 1.0. Only overs the clicks add get counted, the song is never touched.
    # ponytail: counter only; add a limiter if loud intros clip in practice.
    g = 10 ** (volume_db / 20)
    clipped = 0
    for c, s in zip(clicks, starts):
        x = (hi if c.accent else lo) * (g * c.gain)
        end = min(len(out), s + len(x))
        before = np.abs(out[s:end]) > 1.0
        out[s:end] += x[: end - s, None]
        clipped += int(np.sum((np.abs(out[s:end]) > 1.0) & ~before))
    return Rendered(out, shift / sr, starts, clipped)

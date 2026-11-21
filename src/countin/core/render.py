"""Count-in grid and sample-accurate mixing."""

import math
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path

import numpy as np
import soundfile as sf
import soxr

from .io import resource

# Meter -> (beats per bar as the detector counts them, clicks per beat).
# 6/8 counts six eighths over two dotted-quarter beats.
METERS = {"4/4": (4, 1), "3/4": (3, 1), "6/8": (2, 3)}

SILENCE_DB = -60.0

# The closing hits of a pause build-up, about +4 dB.
BUILDUP_GAIN = 1.6

# The build-up count-in swells from -4 dB to +3 dB, where the louder stick sample reaches full scale.
BUILDUP_FROM, BUILDUP_TO = 0.6, 1.45

# Clicks fit under this peak; one gets turned down by at most LIMIT_DB to fit.
CEILING = 0.98
LIMIT_DB = 12.0

# A sample starts at its first value above this share of the peak (-40 dB).
ONSET_LEVEL = 0.01


@dataclass
class Click:
    time: float  # seconds, relative to the original song start
    accent: bool  # first click of a bar
    gain: float = 1.0
    voice: str = "stick"  # a VOICES key: stick for count-ins, hihat for the beats that fill pauses


def click_grid(first_downbeat: float, period: float, bars: int, meter: str) -> list[Click]:
    """Clicks over `bars` bars, ending one click before the first downbeat."""
    beats, sub = METERS[meter]
    per_bar = beats * sub
    step = period / sub
    n = bars * per_bar
    return [Click(first_downbeat - (n - k) * step, k % per_bar == 0) for k in range(n)]


def buildup_grid(first_downbeat: float, period: float, bars: int, meter: str) -> list[Click]:
    """Plain clicks up to the middle of the last bar, twice as dense over its second half, louder click by click
    up to full scale on the last one, so the band hears the intro coming."""
    beats, sub = METERS[meter]
    per_bar = beats * sub
    step = period / sub
    n = bars * per_bar
    half = per_bar // 2

    # Positions in clicks before the downbeat: -n .. -(half+1) plain, then -half .. -0.5 in half steps.
    pos = [-float(k) for k in range(n, half, -1)] + [-half + j / 2 for j in range(2 * half)]
    ramp = np.geomspace(BUILDUP_FROM, BUILDUP_TO, len(pos))
    return [Click(first_downbeat + q * step, q.is_integer() and int(q) % per_bar == 0, float(g))
            for q, g in zip(pos, ramp)]


def before_pickup(clicks: list[Click], pickup: float | None, bar: float) -> list[Click]:
    """End the count-in before the entry while keeping the original grid and bar accents."""
    if pickup is None or not clicks:
        return clicks
    cutoff = pickup - 0.012  # leave room for small differences between the fitted grid and the attack
    before = [c for c in clicks if c.time < cutoff]
    if before:
        return before
    shift = (math.floor((clicks[-1].time - cutoff) / bar) + 1) * bar
    return [replace(c, time=c.time - shift) for c in clicks]


def _quantize(beat: float) -> float:
    """A played note on the nearest sixteenth or eighth-note triplet, whichever is closer."""
    return min((round(beat * d) / d for d in (4, 3)), key=lambda q: abs(q - beat))


def pattern_grid(midi_path: str | Path, first_downbeat: float, period: float, meter: str) -> list[Click]:
    """The MIDI pattern laid on the song's grid. Only the notes count: the file's own tempo is ignored, each
    note is read in beats (ticks / ticks per quarter) and quantized to sixteenths or eighth-note triplets, so a
    pattern played in by hand at any tempo lands on the song's beats. Notes at the same spot become one click
    with the loudest velocity, empty bars at the start are dropped, and the pattern fills whole bars ending at
    the downbeat."""
    import mido

    mid = mido.MidiFile(str(midi_path))
    tick = 0
    hits: dict[float, float] = {}
    for msg in mido.merge_tracks(mid.tracks):
        tick += msg.time
        if msg.type == "note_on" and msg.velocity > 0:
            q = _quantize(tick / mid.ticks_per_beat)
            hits[q] = max(hits.get(q, 0.0), msg.velocity / 127)
    if not hits:
        raise ValueError(f"no notes in {midi_path}")

    # MIDI beats are quarter notes. 6/8 bars hold three quarters of length 2/3 of a dotted-quarter beat.
    beats, sub = METERS[meter]
    quarters_per_bar = beats * sub / (2 if sub == 3 else 1)
    sec_per_quarter = period * beats / quarters_per_bar
    lead = math.floor(min(hits) / quarters_per_bar + 1e-9) * quarters_per_bar  # empty bars before the first note
    notes = sorted((b - lead, v) for b, v in hits.items())
    bars = max(1, math.floor(notes[-1][0] / quarters_per_bar + 1e-9) + 1)
    start = first_downbeat - bars * quarters_per_bar * sec_per_quarter
    return [Click(start + b * sec_per_quarter, math.isclose(b % quarters_per_bar, 0, abs_tol=1e-6), v)
            for b, v in notes]


def load_samples(sr: int, folder: Path | None = None) -> tuple[np.ndarray, np.ndarray]:
    """(hi, lo) mono samples at `sr`, each starting at its attack."""
    folder = folder or resource("samples")
    out = []
    for name in ("stick_hi.wav", "stick_lo.wav"):
        x, file_sr = sf.read(folder / name, dtype="float32", always_2d=True)
        x = x.mean(axis=1)
        if file_sr != sr:
            x = soxr.resample(x, file_sr, sr).astype(np.float32)

        # Silence before the attack would delay every click by its length.
        out.append(x[np.argmax(np.abs(x) > ONSET_LEVEL * np.abs(x).max()):])
    return out[0], out[1]


# Six detuned square waves plus noise through a high band-pass: the analog drum-machine hi-hat recipe.
# Synthesized, so no sample license is involved.
HIHAT_FREQS = (205.3, 304.4, 369.6, 522.7, 540.0, 800.0)


@lru_cache(maxsize=4)
def hihat_samples(sr: int) -> tuple[np.ndarray, np.ndarray]:
    """(accent, plain) closed hi-hat hits; the downbeat one is a little longer and louder."""
    rng = np.random.default_rng(0)

    def hit(decay: float, peak: float) -> np.ndarray:
        n = int(sr * decay * 6)
        t = np.arange(n) / sr
        x = sum(np.sign(np.sin(2 * np.pi * f * t)) for f in HIHAT_FREQS) / len(HIHAT_FREQS)
        x = x + 0.8 * rng.standard_normal(n)
        spec = np.fft.rfft(x)
        freqs = np.fft.rfftfreq(n, 1 / sr)
        lo_hz, hi_hz = min(7000.0, 0.3 * sr), min(16000.0, 0.48 * sr)  # low sample rates have no 7-16 kHz band
        spec[(freqs < lo_hz) | (freqs > hi_hz)] = 0
        x = np.fft.irfft(spec, n) * np.exp(-t / decay)
        x[: int(0.001 * sr)] *= np.linspace(0, 1, int(0.001 * sr))  # 1 ms attack, no click from the onset step
        return (x / np.abs(x).max() * peak).astype(np.float32)

    return hit(0.06, 0.55), hit(0.035, 0.42)


def _tone(sr: int, parts: tuple[tuple[float, float], ...], decay: float, peak: float, square: bool = False) -> np.ndarray:
    """A struck tone: (frequency, weight) partials, sine or square, with an exponential decay."""
    t = np.arange(int(sr * decay * 6)) / sr
    wave = (lambda f: np.sign(np.sin(2 * np.pi * f * t))) if square else (lambda f: np.sin(2 * np.pi * f * t))
    x = sum(w * wave(f) for f, w in parts) * np.exp(-t / decay)
    x[: int(0.001 * sr)] *= np.linspace(0, 1, int(0.001 * sr))
    return (x / np.abs(x).max() * peak).astype(np.float32)


@lru_cache(maxsize=16)
def voice_samples(voice: str, sr: int) -> tuple[np.ndarray, np.ndarray]:
    """(accent, plain) hits of a click voice. All but the stick are synthesized, so no sample license applies."""
    if voice == "hihat":
        return hihat_samples(sr)
    if voice == "beep":  # electronic metronome: a higher tone on the downbeat
        return _tone(sr, ((1760, 1),), 0.03, 0.9), _tone(sr, ((1320, 1),), 0.025, 0.8)
    if voice == "cowbell":  # two detuned square waves, the drum-machine recipe
        bell = ((587, 1), (845, 1))
        return _tone(sr, bell, 0.09, 0.8, square=True), _tone(sr, bell, 0.06, 0.6, square=True)
    if voice == "ping":  # a short high tone
        return _tone(sr, ((2500, 1),), 0.03, 0.9), _tone(sr, ((2000, 1),), 0.025, 0.75)
    hi, lo = load_samples(sr)
    return hi, lo


# Sounds the song click can use, with their names in the app.
VOICES = {"beep": "Метроном", "stick": "Палочки", "cowbell": "Ковбелл", "ping": "Пинг", "hihat": "Хай-хэт"}


def leading_silence(pcm: np.ndarray, sr: int) -> float:
    level = 10 ** (SILENCE_DB / 20)
    loud = np.flatnonzero(np.abs(pcm).max(axis=1) > level)
    return (loud[0] if len(loud) else len(pcm)) / sr


@dataclass
class Rendered:
    pcm: np.ndarray
    offset: float  # original-song time t maps to output time t / speed + offset
    click_samples: list[int]  # click onsets in the output
    clipped: int  # samples that hit full scale
    click: np.ndarray | None = None  # the clicks alone, mono, as they went into pcm


def stretch(x: np.ndarray, sr: int, speed: float, semis: float = 0.0) -> np.ndarray:
    """(n, channels) audio played at speed (0.8 = 20% slower) with its pitch kept, shifted by semis semitones
    without changing its length, through Signalsmith Stretch (MIT). Transients stay on time within about a
    millisecond. The result has round(n / speed) samples."""
    if (speed == 1.0 and not semis) or not len(x):
        return x
    import python_stretch as ps

    st = ps.Signalsmith.Stretch()
    st.preset(x.shape[1], sr)
    st.setTimeFactor(speed)
    if semis:
        st.setTransposeSemitones(semis)
    y = np.asarray(st.process(np.ascontiguousarray(x.T, dtype=np.float32))).T
    n = int(round(len(x) / speed))
    return np.ascontiguousarray(y[:n] if len(y) >= n else np.pad(y, ((0, n - len(y)), (0, 0))), dtype=np.float32)


_pool = None


def stretch_many(parts: list[np.ndarray], sr: int, speed: float, semis: float = 0.0) -> list[np.ndarray]:
    """stretch() in a pool of processes, several signals in parallel. Signalsmith holds the GIL for about 2 s per
    part of a whole song, which froze the window when it ran in the app's process. The pool starts on first use
    and stays."""
    global _pool
    if (speed == 1.0 and not semis) or not parts:
        return list(parts)
    import multiprocessing as mp
    import os
    from concurrent.futures import ProcessPoolExecutor

    try:
        if _pool is None:
            import atexit

            _pool = ProcessPoolExecutor(min(6, os.cpu_count() or 2), mp_context=mp.get_context("spawn"))
            atexit.register(_pool.shutdown, wait=False, cancel_futures=True)  # no traceback at quit
        n = len(parts)
        return list(_pool.map(stretch, parts, [sr] * n, [speed] * n, [semis] * n))
    except Exception:
        _pool = None
        return [stretch(x, sr, speed, semis) for x in parts]


def render(pcm: np.ndarray, sr: int, clicks: list[Click], first_downbeat: float,
           pre_roll: str = "keep", volume_db: float = 0.0,
           samples: tuple[np.ndarray, np.ndarray] | None = None, speed: float = 1.0, semis: float = 0.0) -> Rendered:
    """Mix clicks into the song. Audio before the downbeat stays and gets the clicks on top.

    trim cuts leading silence first, never past the downbeat.
    Silence gets prepended when the song starts too late to hold the count-in.
    With speed below 1 the song gets slower with its pitch kept (stretch) and the clicks land on the slowed
    grid as they are, so they stay crisp.
    """
    if first_downbeat >= len(pcm) / sr:
        raise ValueError(f"downbeat {first_downbeat:.3f} s is past the end of the song")
    hi, lo = samples or load_samples(sr)

    cut = min(int(leading_silence(pcm, sr) * sr), int(first_downbeat * sr)) if pre_roll == "trim" else 0
    earliest = min((c.time for c in clicks), default=cut / sr)

    # Each click rounds from its own float time, so error never accumulates.
    pad = max(0, -math.floor((earliest * sr - cut) / speed))
    starts = [int(round((c.time * sr - cut) / speed)) + pad for c in clicks]

    song = stretch_many([pcm[cut:]], sr, speed, semis)[0]
    out = np.zeros((pad + len(song), pcm.shape[1]), dtype=np.float32)
    out[pad:] = song

    # The song is never touched. A click that would push the mix past CEILING gets turned down as a whole, so
    # its shape stays clean, by up to LIMIT_DB; overs left after that (a hot master's own peaks) get counted.
    g = 10 ** (volume_db / 20)
    clipped = 0
    alone = np.zeros(len(out), dtype=np.float32)
    for c, s in zip(clicks, starts):
        pair = (hi, lo) if c.voice == "stick" else voice_samples(c.voice, sr)
        x = (pair[0] if c.accent else pair[1]) * (g * c.gain)
        end = min(len(out), s + len(x))
        x = x[: end - s, None]
        y = out[s:end]
        over = np.abs(y + x) > CEILING
        if over.any():
            room = np.where(np.sign(y) == np.sign(x), CEILING - np.abs(y), CEILING + np.abs(y))
            k = np.clip(room[over] / np.abs(np.broadcast_to(x, y.shape)[over]), 0.0, 1.0).min()
            x = x * max(k, 10 ** (-LIMIT_DB / 20))
        before = np.abs(y) > 1.0
        y += x
        alone[s:end] += x[:, 0]
        clipped += int(np.sum((np.abs(y) > 1.0) & ~before))
    return Rendered(out, (pad - cut / speed) / sr, starts, clipped, alone)


def click_track(clicks: list[Click], sr: int, start: float, length: int, volume_db: float = 0.0,
                speed: float = 1.0) -> np.ndarray:
    """The clicks alone as mono audio of `length` samples, song time `start` at sample 0, at `speed`."""
    hi, lo = load_samples(sr)
    out = np.zeros(length, dtype=np.float32)
    g = 10 ** (volume_db / 20)
    for c in clicks:
        s = int(round((c.time - start) / speed * sr))
        pair = (hi, lo) if c.voice == "stick" else voice_samples(c.voice, sr)
        x = (pair[0] if c.accent else pair[1]) * (g * c.gain)
        if 0 <= s < length:
            n = min(len(x), length - s)
            out[s : s + n] += x[:n]
    return out


def _k_weight(sr: int, n: int) -> np.ndarray:
    """|H|² of the BS.1770 K-weighting (high shelf, then high-pass) at the rfft bins of an n-sample signal."""
    w = 2 * np.pi * np.fft.rfftfreq(n, 1 / sr) / sr
    z = np.exp(-1j * w)

    def biquad(b, a):
        return np.abs((b[0] + b[1] * z + b[2] * z * z) / (a[0] + a[1] * z + a[2] * z * z)) ** 2

    A, w0 = 10 ** (4.0 / 40), 2 * np.pi * 1500 / sr
    al, c, r = np.sin(w0) / (2 / np.sqrt(2)), np.cos(w0), np.sqrt(10 ** (4.0 / 40))
    shelf = biquad((A * ((A + 1) + (A - 1) * c + 2 * r * al), -2 * A * ((A - 1) + (A + 1) * c), A * ((A + 1) + (A - 1) * c - 2 * r * al)),
                   ((A + 1) - (A - 1) * c + 2 * r * al, 2 * ((A - 1) - (A + 1) * c), (A + 1) - (A - 1) * c - 2 * r * al))
    w0 = 2 * np.pi * 38 / sr
    al, c = np.sin(w0) / (2 * 0.5), np.cos(w0)
    hp = biquad(((1 + c) / 2, -(1 + c), (1 + c) / 2), (1 + al, -2 * c, 1 - al))
    return shelf * hp


def loudness(pcm: np.ndarray, sr: int) -> float:
    """Integrated loudness in LUFS (BS.1770-4: K-weighting, 400 ms blocks, absolute and relative gates). The
    filter runs in the frequency domain with zero phase, which leaves the block energies as they are."""
    n = len(pcm)
    if n < int(0.4 * sr):
        return -70.0
    gain = _k_weight(sr, n)
    block, hop = int(0.4 * sr), int(0.1 * sr)
    starts = np.arange(0, n - block + 1, hop)
    z = np.zeros(len(starts))
    for ch in range(pcm.shape[1]):
        y = np.fft.irfft(np.fft.rfft(pcm[:, ch].astype(np.float64)) * np.sqrt(gain), n) ** 2
        c = np.concatenate([[0.0], np.cumsum(y)])
        z += (c[starts + block] - c[starts]) / block
    lk = -0.691 + 10 * np.log10(z + 1e-12)
    z = z[lk > -70]
    if not len(z):
        return -70.0
    rel = -0.691 + 10 * np.log10(z.mean()) - 10
    z = z[-0.691 + 10 * np.log10(z) > rel]
    return float(-0.691 + 10 * np.log10(z.mean()))


def normalize(pcm: np.ndarray, sr: int, target: float = -14.0, peak_db: float = -1.0) -> np.ndarray:
    """pcm brought to `target` LUFS, as streaming services play songs, but never past peak_db: a dynamic song
    stays a little quieter instead of getting squashed."""
    gain = 10 ** ((target - loudness(pcm, sr)) / 20)
    peak = float(np.abs(pcm).max()) or 1.0
    return (pcm * min(gain, 10 ** (peak_db / 20) / peak)).astype(np.float32)

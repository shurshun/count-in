"""Beat and downbeat detection with beat_this, plus a sub-frame grid fit for the song start."""

from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np

from .io import resource

FPS = 50  # beat_this frame rate, 20 ms per frame
ANALYZE_SECONDS = 90.0
GRID_BEATS = 16


@dataclass
class Analysis:
    beats: np.ndarray  # seconds, sub-frame refined
    downbeats: np.ndarray
    bpm: float  # line fit over the first GRID_BEATS beats from the first downbeat
    first_downbeat: float  # grid phase fitted over the same beats
    confidence: float  # 0..1
    activation: float  # mean beat probability at detected beats
    regularity: float  # timing jitter around the fitted grid, scaled to 0..1
    beats_per_bar: int  # most common beat count between downbeats
    bar_agreement: float = 1.0  # share of the next downbeats that sit on the chosen bar phase
    alt_downbeat: float | None = None  # first beat on the bar phase most later downbeats agree on
    snap_ms: float = 0.0  # shift from the fitted grid to the audio attack
    notes: list[str] = field(default_factory=list)

    @property
    def period(self) -> float:
        return 60.0 / self.bpm


@lru_cache(maxsize=1)
def _model():
    from beat_this.inference import Audio2Frames

    ckpt = resource("models", "final0.ckpt")
    if not ckpt.exists():
        raise RuntimeError(f"beat_this checkpoint missing: {ckpt}")
    return Audio2Frames(checkpoint_path=str(ckpt), device="cpu")


def _refine(times: np.ndarray, logits: np.ndarray) -> np.ndarray:
    """Parabolic interpolation of the logit peak around each detected frame."""
    out = []
    for t in times:
        i = int(round(t * FPS))
        if 0 < i < len(logits) - 1:
            a, b, c = logits[i - 1], logits[i], logits[i + 1]
            den = a - 2 * b + c
            off = 0.5 * (a - c) / den if den < 0 else 0.0
            out.append((i + np.clip(off, -0.5, 0.5)) / FPS)
        else:
            out.append(i / FPS)
    return np.array(out)


def detect(pcm: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Refined beats, refined downbeats, beat probabilities per frame."""
    from beat_this.model.postprocessor import Postprocessor

    mono = pcm[: int(ANALYZE_SECONDS * sr)].mean(axis=1)
    beat_logits, down_logits = _model()(mono, sr)
    beats, downs = Postprocessor(type="minimal", fps=FPS)(beat_logits, down_logits)
    bl = beat_logits.cpu().numpy()
    return _refine(np.asarray(beats), bl), _refine(np.asarray(downs), down_logits.cpu().numpy()), 1 / (1 + np.exp(-bl))


def fit_grid(beats: np.ndarray, start: int, n: int = GRID_BEATS) -> tuple[float, float, float, float]:
    """(period, phase, jitter, inlier share) of the grid through n beat slots from beats[start].

    Each beat gets its slot index from the median interval, so missed beats (a half-time intro)
    leave gaps instead of shifting the count. A least-squares line through the slots averages out
    the 20 ms frame quantization.
    """
    seg = beats[start : start + n]
    if len(seg) < 3:
        raise ValueError("too few beats after the first downbeat")
    period = float(np.median(np.diff(seg)))
    k = np.round((seg - seg[0]) / period)
    resid = seg - seg[0] - k * period
    k, seg, resid = k[k < n], seg[k < n], resid[k < n]
    good = np.abs(resid) < 0.25 * period
    if good.sum() < 3:
        raise ValueError("beats do not fit a steady grid")
    slope, phase = np.polyfit(k[good], seg[good], 1)
    jitter = float(np.std(seg[good] - (phase + k[good] * slope)))
    return float(slope), float(phase), jitter, float(good.mean())


def snap_onset(pcm: np.ndarray, sr: int, t: float, radius: float = 0.025, min_rise_db: float = 12.0) -> float | None:
    """Sample-exact attack within t ± radius, or None when the audio has no clear attack there.

    Of several attacks in the window, the one nearest t wins, so a grace note or pick noise
    just before the beat cannot pull the downbeat away.
    """
    mono = np.abs(pcm.mean(axis=1))
    block = sr // 1000
    lo = int((t - radius) * sr) - 4 * block
    hi = int((t + radius) * sr)
    n = (hi - lo) // block + 1

    # Zeros stand in for audio before the file start and after its end.
    x = np.zeros(n * block, dtype=mono.dtype)
    a, b = max(lo, 0), min(lo + n * block, len(mono))
    if a < b:
        x[a - lo : b - lo] = mono[a:b]
    env = 20 * np.log10(x.reshape(n, block).max(axis=1) + 1e-9)
    rise = env[4:] - np.array([env[j - 4 : j].max() for j in range(4, len(env))])

    # Each attack shows up as a run of rising blocks; its first block is the onset.
    up = rise >= min_rise_db
    starts = np.flatnonzero(up & ~np.r_[False, up[:-1]])
    if not len(starts):
        return None
    j = int(starts[np.argmin(np.abs(lo + (starts + 4) * block - t * sr))])

    # First sample in the attack block above the level before it plus half the rise.
    s = (j + 4) * block
    thr = 10 ** ((env[j : j + 4].max() + rise[j] / 2) / 20)
    hit = np.flatnonzero(x[s : s + block] > thr)
    return (lo + s + (hit[0] if len(hit) else 0)) / sr


def beat_period(a: "Analysis", beats_per_bar: int) -> tuple[float, str | None]:
    """Click period for a meter with `beats_per_bar` beats.

    The detector may count eighths in 6/8 or half time in 4/4. The bar length stays right
    in those cases, so the period gets rescaled from it. Other mismatches only get a note.
    """
    if not a.beats_per_bar or a.beats_per_bar == beats_per_bar:
        return a.period, None
    ratio = a.beats_per_bar / beats_per_bar
    if any(np.isclose(ratio, r) for r in (1 / 3, 1 / 2, 2, 3)):
        return a.period * ratio, f"detector counts {a.beats_per_bar} beats per bar, period scaled by {ratio:g}"
    return a.period, f"detector counts {a.beats_per_bar} beats per bar, the meter expects {beats_per_bar}"


def analyze(pcm: np.ndarray, sr: int) -> Analysis:
    beats, downs, prob = detect(pcm, sr)
    if len(beats) < 4 or len(downs) == 0:
        raise ValueError("no stable beat found")

    notes = []
    start = int(np.argmin(np.abs(beats - downs[0])))
    period, t0, jitter, share = fit_grid(beats, start)

    # 20 ms of timing jitter around the fitted grid counts as no regularity at all.
    regularity = float(max(0.0, 1 - jitter / 0.02) * share)
    seg = beats[start : start + GRID_BEATS]
    frames = np.clip(np.round(seg * FPS).astype(int), 0, len(prob) - 1)
    activation = float(prob[frames].mean())

    counts = [int(np.sum((beats >= a - 0.05) & (beats < b - 0.05))) for a, b in zip(downs[:9], downs[1:9])]
    bpb = int(np.bincount(counts).argmax()) if counts else 0
    if counts and np.mean(np.array(counts) == bpb) < 0.7:
        notes.append("bar length varies between downbeats")

    # Bar phase: later downbeats should land a whole number of bars after t0.
    agreement, alt = 1.0, None
    later = downs[1:5]
    if bpb and len(later):
        slots = np.round((later - t0) / period).astype(int) % bpb
        agreement = float(np.mean(slots == 0))
        if agreement < 0.5:
            alt = t0 + int(np.bincount(slots).argmax()) * period
            notes.append(f"later downbeats disagree with the first one; candidate {alt:.3f} s")

    snapped = snap_onset(pcm, sr, t0)
    snap_ms = 0.0
    if snapped is not None:
        snap_ms = (snapped - t0) * 1000
        t0 = snapped
    else:
        notes.append("no clear attack at the downbeat, using the beat grid")

    return Analysis(
        beats=beats, downbeats=downs, bpm=60.0 / period, first_downbeat=t0,
        confidence=activation * regularity * agreement, activation=activation, regularity=regularity,
        beats_per_bar=bpb, bar_agreement=agreement, alt_downbeat=alt, snap_ms=snap_ms, notes=notes,
    )

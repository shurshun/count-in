"""EQ moves that bring one guitar's tone to another's: the player's recording to the separated guitar stem.

Both signals get a long-term average spectrum over the frames where they play, in 1/3-octave bands from 80 Hz
to 8 kHz. The mean difference becomes a level hint; the rest gets fitted greedily with capped peaking filters,
and shelves at the ends of the range.
"""

from dataclasses import dataclass

import numpy as np

FRAME = 4096
GATE_DB = 30.0  # frames this far under the loud ones count as silence or separation residue
FLOOR_DB = 50.0  # bands this far under a signal's loudest band carry no tone, only noise
CENTERS = 1000.0 * 2.0 ** (np.arange(-11, 10) / 3)  # 79 Hz .. 8 kHz
QS = (0.7, 1.4, 2.8)
SHELF_Q = 0.707
EDGE = 3  # bands at each end of the range that may get a shelf


@dataclass(frozen=True)
class Move:
    freq: float
    gain_db: float
    q: float
    kind: str = "peak"  # peak, low (low shelf), high (high shelf)


@dataclass
class Result:
    moves: list[Move]
    level_db: float  # gain that puts the source at the target's level
    source_db: np.ndarray  # band levels at CENTERS
    target_db: np.ndarray
    used: np.ndarray  # bands both signals carry, the ones the fit looked at
    left_db: float  # largest difference the moves leave in the used bands


def mono(x: np.ndarray) -> np.ndarray:
    return x.mean(axis=1) if x.ndim == 2 else x


def bands(x: np.ndarray, sr: int) -> np.ndarray:
    """Mono signal to its average power per band in dB, over frames within GATE_DB of the loud ones."""
    hop = FRAME // 2
    n = 1 + max(0, len(x) - FRAME) // hop
    frames = np.lib.stride_tricks.sliding_window_view(np.pad(x, (0, FRAME)), FRAME)[::hop][:n]
    rms = np.sqrt(np.mean(frames**2, axis=1)) + 1e-12
    loud = 20 * np.log10(rms) > 20 * np.log10(np.percentile(rms, 95)) - GATE_DB
    power = np.mean(np.abs(np.fft.rfft(frames[loud] * np.hanning(FRAME), axis=1)) ** 2, axis=0)
    f = np.fft.rfftfreq(FRAME, 1 / sr)
    edges = np.stack([CENTERS * 2 ** (-1 / 6), CENTERS * 2 ** (1 / 6)], axis=1)
    return np.array([10 * np.log10(power[(f >= lo) & (f < hi)].mean() + 1e-20) for lo, hi in edges])


def response_db(m: Move, freqs: np.ndarray, sr: int) -> np.ndarray:
    """Magnitude in dB of the move's RBJ biquad at freqs."""
    a = 10 ** (m.gain_db / 40)
    w0 = 2 * np.pi * m.freq / sr
    cw = np.cos(w0)
    alpha = np.sin(w0) / (2 * m.q)
    if m.kind == "peak":
        b = [1 + alpha * a, -2 * cw, 1 - alpha * a]
        d = [1 + alpha / a, -2 * cw, 1 - alpha / a]
    else:
        s = 1 if m.kind == "low" else -1
        k = 2 * np.sqrt(a) * alpha
        b = [a * ((a + 1) - s * (a - 1) * cw + k), s * 2 * a * ((a - 1) - s * (a + 1) * cw),
             a * ((a + 1) - s * (a - 1) * cw - k)]
        d = [(a + 1) + s * (a - 1) * cw + k, -s * 2 * ((a - 1) + s * (a + 1) * cw), (a + 1) + s * (a - 1) * cw - k]
    z = np.exp(-1j * 2 * np.pi * np.asarray(freqs) / sr)[:, None] ** np.arange(3)
    return 20 * np.log10(np.abs(z @ np.array(b)) / np.abs(z @ np.array(d)))


def fit(source_db: np.ndarray, target_db: np.ndarray, sr: int, max_moves: int = 6, cap_db: float = 6.0,
        stop_db: float = 1.0) -> Result:
    """Moves for band levels: cheap, so the app refits whenever the band count or the cap changes."""
    s, t = source_db, target_db
    ok = (s > s.max() - FLOOR_DB) & (t > t.max() - FLOOR_DB)
    diff = np.convolve(np.pad(t - s, 1, mode="edge"), [0.25, 0.5, 0.25], mode="valid")
    level = float(diff[ok].mean())
    r = np.where(ok, diff - level, 0.0)
    resp = lambda m: np.where(ok, response_db(m, CENTERS, sr), 0.0)
    moves: list[Move] = []
    free = ok.copy()
    for _ in range(max_moves):
        if not free.any():
            break
        j = int(np.argmax(np.where(free, np.abs(r), -1)))
        free[max(0, j - 2):j + 3] = False  # one move per region, or capped moves stack on the same band
        if abs(r[j]) < stop_db:
            break
        g = float(np.clip(r[j], -cap_db, cap_db))
        tries = [Move(float(CENTERS[j]), g, q) for q in QS]
        if j < EDGE:
            tries.append(Move(float(CENTERS[j]), g, SHELF_Q, "low"))
        if j >= len(CENTERS) - EDGE:
            tries.append(Move(float(CENTERS[j]), g, SHELF_Q, "high"))
        best = min(tries, key=lambda m: np.sum((r - resp(m)) ** 2))
        r = r - resp(best)
        moves.append(best)
    total = sum((response_db(m, CENTERS, sr) for m in moves), np.zeros(len(CENTERS))) + level
    left = float(np.abs((t - s - total)[ok]).max()) if ok.any() else 0.0
    return Result(sorted(moves, key=lambda m: m.freq), level, s, t, ok, left)


def match(source: np.ndarray, target: np.ndarray, sr: int, **kw) -> Result:
    """EQ moves and a level change that bring source's spectrum (mono or stereo) to target's."""
    return fit(bands(mono(source), sr), bands(mono(target), sr), sr, **kw)


def apply(x: np.ndarray, sr: int, moves: list[Move], level_db: float = 0.0) -> np.ndarray:
    """x (mono or [n, channels]) through the moves' magnitude response, zero phase."""
    # ponytail: FFT over the whole signal, fine for a recorded take; a biquad chain if it ever runs live
    spec = np.fft.rfft(x, axis=0)
    f = np.fft.rfftfreq(len(x), 1 / sr)
    db = level_db + sum((response_db(m, f, sr) for m in moves), np.zeros(len(f)))
    gain = 10 ** (db / 20)
    return np.fft.irfft(spec * (gain[:, None] if x.ndim == 2 else gain), n=len(x), axis=0).astype(x.dtype)


def offset(x: np.ndarray, ref: np.ndarray, sr: int, max_s: float = 60.0) -> int:
    """Samples to delay mono x by so it lines up with mono ref (negative: drop its start), from 1 ms envelopes."""
    b = sr // 1000
    env = lambda y: (lambda e: e - e.mean())(np.abs(y[: len(y) // b * b]).reshape(-1, b).mean(axis=1))
    ex, er = env(x), env(ref)
    n = 1 << int(np.ceil(np.log2(len(ex) + len(er))))
    c = np.fft.irfft(np.fft.rfft(er, n) * np.conj(np.fft.rfft(ex, n)), n)
    lim = int(max_s * 1000)
    lags = np.r_[np.arange(0, min(lim, len(er)) + 1), np.arange(-min(lim, len(ex) - 1), 0)]
    return int(lags[np.argmax(c[lags])]) * b

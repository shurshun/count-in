"""EQ moves that bring one guitar's tone to another's: the player's recording to the separated guitar stem.

Both signals get a long-term average spectrum over the frames where they play, in 1/3-octave bands from 80 Hz
to 8 kHz. The mean difference becomes a level hint; the rest gets fitted greedily with capped peaking filters.
"""

from dataclasses import dataclass

import numpy as np

FRAME = 4096
GATE_DB = 30.0  # frames this far under the loud ones count as silence or separation residue
FLOOR_DB = 50.0  # bands this far under a signal's loudest band carry no tone, only noise
CENTERS = 1000.0 * 2.0 ** (np.arange(-11, 10) / 3)  # 79 Hz .. 8 kHz
QS = (0.7, 1.4, 2.8)


@dataclass(frozen=True)
class Move:
    freq: float
    gain_db: float
    q: float


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


def peak_db(m: Move, freqs: np.ndarray, sr: int) -> np.ndarray:
    """Magnitude in dB of an RBJ peaking biquad at freqs."""
    a = 10 ** (m.gain_db / 40)
    w0 = 2 * np.pi * m.freq / sr
    alpha = np.sin(w0) / (2 * m.q)
    b = np.array([1 + alpha * a, -2 * np.cos(w0), 1 - alpha * a])
    d = np.array([1 + alpha / a, -2 * np.cos(w0), 1 - alpha / a])
    z = np.exp(-1j * 2 * np.pi * np.asarray(freqs) / sr)[:, None] ** np.arange(3)
    return 20 * np.log10(np.abs(z @ b) / np.abs(z @ d))


def match(source: np.ndarray, target: np.ndarray, sr: int, max_moves: int = 6, cap_db: float = 6.0,
          stop_db: float = 1.0) -> tuple[list[Move], float]:
    """EQ moves and a level change in dB that bring mono source's spectrum to mono target's."""
    s, t = bands(source, sr), bands(target, sr)
    ok = (s > s.max() - FLOOR_DB) & (t > t.max() - FLOOR_DB)
    diff = np.convolve(np.pad(t - s, 1, mode="edge"), [0.25, 0.5, 0.25], mode="valid")
    level = float(diff[ok].mean())
    r = np.where(ok, diff - level, 0.0)
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
        best = min(tries, key=lambda m: np.sum((r - np.where(ok, peak_db(m, CENTERS, sr), 0)) ** 2))
        r = r - np.where(ok, peak_db(best, CENTERS, sr), 0)
        moves.append(best)
    return moves, level


def apply(x: np.ndarray, sr: int, moves: list[Move], level_db: float = 0.0) -> np.ndarray:
    """x through the moves' magnitude response, zero phase: enough to audition and to check a match."""
    # ponytail: FFT over the whole signal; a biquad chain for live preview when the app needs it
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / sr)
    db = level_db + sum((peak_db(m, f, sr) for m in moves), np.zeros(len(f)))
    return np.fft.irfft(spec * 10 ** (db / 20), n=len(x))

"""Fill zones: bars that go quiet once guitar or vocals are removed get clicks on every beat.

The bars come from the detected beats and downbeats of the whole song, so the clicks follow tempo drift
and meter changes. A zone that starts after music gets one bar of count-in in front of it, with the beat
count and tempo of the zone's first bar.
"""

from dataclasses import dataclass

import numpy as np

from .render import Click

# A bar this far below the song's average level counts as silent. On the demo track the bars left with only
# reverb tails sit at -20 dB and below, bars with any instrument at -14 dB and above.
QUIET_DB = -18.0


@dataclass
class Bar:
    start: float
    end: float
    beats: list[float]  # beat times in the bar, the first one on the downbeat

    @property
    def period(self) -> float:
        return (self.end - self.start) / max(len(self.beats), 1)


def bars_from(beats: np.ndarray, downbeats: np.ndarray, duration: float) -> list[Bar]:
    """Bars between consecutive downbeats; the last one runs to the song end with the previous bar's length."""
    beats = np.asarray(beats, dtype=float)
    out = []
    for i, a in enumerate(downbeats):
        b = downbeats[i + 1] if i + 1 < len(downbeats) else None
        if b is None:
            if not out:
                break
            b = min(a + (out[-1].end - out[-1].start), duration)
        inside = [float(t) for t in beats if a - 0.05 <= t < b - 0.05]
        if not inside or abs(inside[0] - a) > 0.05:
            inside = [float(a)] + inside
        out.append(Bar(float(a), float(b), inside))
    return out


def quiet_bars(pcm: np.ndarray, sr: int, bars: list[Bar], reference_db: float, threshold_db: float = QUIET_DB) -> set[int]:
    """Indices of bars whose level sits threshold_db or more below reference_db (dBFS of the full mix).

    A quiet run that reaches the last bar is the song's ending, not a pause, so it is left out.
    """
    out = set()
    for i, bar in enumerate(bars):
        x = pcm[int(bar.start * sr) : int(bar.end * sr)]
        if len(x) and level_db(x) - reference_db < threshold_db:
            out.add(i)
    for a, b in ranges(out):
        if b == len(bars) - 1:
            out -= set(range(a, b + 1))
    return out


def level_db(x: np.ndarray) -> float:
    return float(20 * np.log10(np.sqrt(np.mean(np.square(x, dtype=np.float64))) + 1e-12))


def ranges(indices: set[int]) -> list[tuple[int, int]]:
    """Consecutive runs of bar indices as (first, last)."""
    out: list[tuple[int, int]] = []
    for i in sorted(indices):
        if out and i == out[-1][1] + 1:
            out[-1] = (out[-1][0], i)
        else:
            out.append((i, i))
    return out


def zone_clicks(bars: list[Bar], zone: set[int], downbeat: float) -> list[Click]:
    """Clicks on every beat of the zone bars, plus a bar of count-in before each zone.

    The count-in has as many clicks as the zone's first bar has beats, at that bar's beat period, and ends
    one beat before it. A zone that starts at the song's first downbeat needs none: the main count-in leads
    into it. Beats before that downbeat never click.
    """
    clicks = []
    for first, last in ranges(zone):
        bar = bars[first]
        if bar.start > downbeat + 1e-3:
            n, p = len(bar.beats), bar.period
            clicks += [Click(bar.start - (n - k) * p, k == 0) for k in range(n)]
        for b in bars[first : last + 1]:
            clicks += [Click(t, k == 0) for k, t in enumerate(b.beats) if t >= downbeat - 1e-3]
    return clicks

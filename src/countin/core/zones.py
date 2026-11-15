"""Fill zones: bars that go quiet once guitar or vocals are removed get a hi-hat on every beat.

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

    # A one-beat "bar" is a stray downbeat: the beat belongs to the bar before it
    merged: list[Bar] = []
    for bar in out:
        if merged and len(bar.beats) < 2:
            prev = merged[-1]
            merged[-1] = Bar(prev.start, bar.end, prev.beats + bar.beats)
        else:
            merged.append(bar)
    if len(merged) > 1 and len(merged[0].beats) < 2:
        merged[:2] = [Bar(merged[0].start, merged[1].end, merged[0].beats + merged[1].beats)]
    return merged


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


@dataclass
class Zone:
    """A pause over bars first..last. bpm and beats override the detected tempo and beats per bar inside it."""

    first: int
    last: int
    bpm: float | None = None
    beats: int | None = None


def zones_from(indices: set[int]) -> list[Zone]:
    return [Zone(a, b) for a, b in ranges(indices)]


def zone_bars(bars: list[Bar], z: Zone) -> list[Bar]:
    """The bars a zone plays: the detected ones, or a steady grid from the zone start when it has an override."""
    if z.bpm is None and z.beats is None:
        return bars[z.first : z.last + 1]
    start, end = bars[z.first].start, bars[z.last].end
    own = bars[z.first]
    p = 60.0 / z.bpm if z.bpm else own.period
    n = z.beats or len(own.beats)
    beats = list(start + p * np.arange(int(np.ceil((end - start) / p - 1e-6))))
    return [Bar(beats[k], beats[k] + n * p, beats[k : k + n]) for k in range(0, len(beats), n)]


def describe(bars: list[Bar], z: Zone) -> tuple[float, float, float, int]:
    """(start, end, bpm, beats per bar) of a zone as it will play."""
    zb = zone_bars(bars, z)
    periods = [b.period for b in zb if len(b.beats) > 1] or [zb[0].period]
    counts = [len(b.beats) for b in zb]
    return bars[z.first].start, bars[z.last].end, 60.0 / float(np.median(periods)), max(set(counts), key=counts.count)


def zone_clicks(bars: list[Bar], zones: list[Zone], downbeat: float) -> list[Click]:
    """A hi-hat on every beat of the zone bars, plus a bar of stick count-in before each zone.

    The count-in has as many clicks as the zone's first bar has beats, at that bar's beat period, and ends
    one beat before it. A zone that starts at the song's first downbeat needs none: the main count-in leads
    into it. Beats before that downbeat never click.
    """
    clicks = []
    for z in zones:
        zb = zone_bars(bars, z)
        bar = zb[0]
        if bar.start > downbeat + 1e-3:
            n, p = len(bar.beats), bar.period
            clicks += [Click(bar.start - (n - k) * p, k == 0) for k in range(n)]
        for b in zb:
            clicks += [Click(t, k == 0, voice="hihat") for k, t in enumerate(b.beats) if t >= downbeat - 1e-3]
    return clicks

"""Pauses: bars that go quiet once parts are turned down get a hi-hat on every beat.

The bars come from the detected beats and downbeats of the whole song, so the hi-hat follows tempo drift
and meter changes.
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

    The level runs up to the bar's last beat: a band that comes back with a pickup on that beat still leaves
    the bar quiet, so the hi-hat plays up to the entry instead of stopping a bar early.
    A quiet run that reaches the last bar is the song's ending, not a pause, so it is left out.
    """
    out = set()
    for i, bar in enumerate(bars):
        end = bar.beats[-1] if len(bar.beats) > 1 else bar.end
        x = pcm[int(bar.start * sr) : int(end * sr)]
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
    """A pause over bars first..last. bpm and beats override the detected tempo and beats per bar inside it.
    manual marks a pause the user made or edited; the automatic search keeps those."""

    first: int
    last: int
    bpm: float | None = None
    beats: int | None = None
    manual: bool = False

    @property
    def kept(self) -> bool:
        return self.manual or self.bpm is not None or self.beats is not None


def zones_from(indices: set[int]) -> list[Zone]:
    return [Zone(a, b) for a, b in ranges(indices)]


def _irregular(bars: list[Bar]) -> bool:
    """Bars of different beat counts or lengths 8% off the median: the detector lost the beat, as it does in a
    guitar-only intro."""
    lengths = [b.end - b.start for b in bars]
    med = float(np.median(lengths))
    return len({len(b.beats) for b in bars}) > 1 or any(abs(x - med) > 0.08 * med for x in lengths)


def _even(start: float, end: float, bar_len: float, n: int) -> list[Bar]:
    """Whole bars of n even beats from start to end, as close to bar_len as the span allows."""
    k = max(1, round((end - start) / bar_len))
    step = (end - start) / k
    return [Bar(start + i * step, start + (i + 1) * step, list(start + i * step + step / n * np.arange(n)))
            for i in range(k)]


def zone_bars(bars: list[Bar], z: Zone) -> list[Bar]:
    """The bars a zone plays: the detected ones; an even grid between the zone edges when the detected ones are
    irregular, in the meter and tempo of the bar the band comes back in; or a steady grid from the zone start
    when the zone has an override."""
    if z.bpm is None and z.beats is None:
        own = bars[z.first : z.last + 1]
        if not _irregular(own):
            return own
        ref = bars[z.last + 1] if z.last + 1 < len(bars) else own[-1]
        return _even(own[0].start, own[-1].end, ref.end - ref.start, len(ref.beats))
    start, end = bars[z.first].start, bars[z.last].end
    own = bars[z.first]
    p = 60.0 / z.bpm if z.bpm else own.period
    n = z.beats or len(own.beats)
    beats = list(start + p * np.arange(int(np.ceil((end - start) / p - 1e-6))))
    return [Bar(beats[k], min(beats[k] + n * p, end), beats[k : k + n]) for k in range(0, len(beats), n)]


def describe(bars: list[Bar], z: Zone) -> tuple[float, float, float, int]:
    """(start, end, bpm, beats per bar) of a zone as it will play."""
    own = bars[z.first]
    if z.bpm is not None or z.beats is not None:
        return own.start, bars[z.last].end, z.bpm or 60.0 / own.period, z.beats or len(own.beats)
    zb = zone_bars(bars, z)
    periods = [b.period for b in zb if len(b.beats) > 1] or [zb[0].period]
    counts = [len(b.beats) for b in zb]
    return bars[z.first].start, bars[z.last].end, 60.0 / float(np.median(periods)), max(set(counts), key=counts.count)


def zone_clicks(bars: list[Bar], zones: list[Zone], downbeat: float) -> list[Click]:
    """A hi-hat on every beat of the zone bars. Inside the song nothing else plays: the stick count-in belongs
    to the song's start only. The downbeat is the snapped "1", a few ms off the detected bar start, so beats
    compare with half a beat of slack."""
    clicks = []
    for z in sorted(zones, key=lambda q: q.first):
        zb = zone_bars(bars, z)
        slack = zb[0].period / 2
        for b in zb:
            clicks += [Click(t, k == 0, voice="hihat") for k, t in enumerate(b.beats) if t >= downbeat - slack]
    return clicks


def merge(manual: list[Zone], found: list[Zone]) -> list[Zone]:
    """Pauses the user made or edited stay; the found ones fill in where they don't overlap."""
    taken = {i for z in manual for i in range(z.first, z.last + 1)}
    extra = [z for z in found if not any(i in taken for i in range(z.first, z.last + 1))]
    return sorted(manual + extra, key=lambda q: q.first)

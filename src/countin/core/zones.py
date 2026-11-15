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

    The level runs over the first three quarters of the bar: a band that comes back with a pickup on the last
    beat, or a stray detected beat at the band's entry, still leaves the bar quiet, so the hi-hat plays up to
    the entry instead of stopping a bar early. Likewise a bar that
    only holds the band's last hit on its first beat joins the pause after it, so the hi-hat starts right after
    the hit and the time never drops.
    A quiet run that reaches the last bar is the song's ending, not a pause, so it is left out.
    """
    def quiet(a: float, b: float) -> bool:
        x = pcm[int(a * sr) : int(b * sr)]
        return bool(len(x)) and level_db(x) - reference_db < threshold_db

    out = {i for i, bar in enumerate(bars) if quiet(bar.start, _head_end(bar))}
    for a, _b in ranges(out):
        lead = bars[a - 1] if a > 0 else None
        if lead is not None and len(lead.beats) > 1 and quiet(lead.beats[1], lead.end):
            out.add(a - 1)
    for a, b in ranges(out):
        if b == len(bars) - 1:
            out -= set(range(a, b + 1))
    return out


def _head_end(bar: Bar) -> float:
    """Where a bar's level measurement stops: three quarters in, so an entry on its last beat stays out."""
    return bar.start + 0.75 * (bar.end - bar.start)


def auto_threshold(pcm: np.ndarray, sr: int, bars: list[Bar], reference_db: float) -> float | None:
    """A silence threshold that fits this backing. Bars below -40 dB are silent under any threshold and stay
    out; the rest fall into bars with only bleed or tails left and bars with music, and Otsu's split of the two
    in dB puts the threshold in the gap, within -40..-12 dB (above -12 a bass-and-drums section would turn into
    a pause). None when there is no clear quiet group, so the slider keeps its value."""
    lv = np.array([level_db(pcm[int(b.start * sr) : int(_head_end(b) * sr)]) - reference_db
                   for b in bars if b.end > b.start])
    lv = np.sort(lv[lv > -40.0])
    n = len(lv)
    if n < 4:
        return None
    csum = np.cumsum(lv)
    i = np.arange(1, n)
    m0, m1 = csum[:-1] / i, (csum[-1] - csum[:-1]) / (n - i)
    k = int(np.argmax(i / n * (1 - i / n) * (m1 - m0) ** 2)) + 1
    if m1[k - 1] - m0[k - 1] < 10.0:
        return None
    return float(np.clip((lv[k - 1] + lv[k]) / 2, -40.0, -12.0))


def level_db(x: np.ndarray) -> float:
    return float(20 * np.log10(np.sqrt(np.mean(np.square(x, dtype=np.float64))) + 1e-12))


def bar_levels(pcm: np.ndarray, sr: int, bars: list[Bar]) -> list[float]:
    """Level of every bar in dB against the whole song's level."""
    ref = level_db(pcm)
    return [level_db(pcm[int(b.start * sr) : int(b.end * sr)]) - ref if b.end > b.start else -120.0 for b in bars]


# The hi-hat follows the band around the pause, within these limits: a quiet ballad verse gets a soft hi-hat,
# a full chorus no more than a touch above the count-in level.
HIHAT_DB_RANGE = (-15.0, 3.0)
AROUND = 2  # bars on each side of a pause that set its level


def zone_gain(bar_db: list[float], z: "Zone") -> float:
    """Linear gain of a pause's hi-hat: the power average of up to AROUND bars on each side, clamped to
    HIHAT_DB_RANGE. A pause with no music around keeps the plain level."""
    near = bar_db[max(0, z.first - AROUND) : z.first] + bar_db[z.last + 1 : z.last + 1 + AROUND]
    if not near:
        return 1.0
    db = 10 * np.log10(np.mean([10 ** (d / 10) for d in near]))
    return float(10 ** (np.clip(db, *HIHAT_DB_RANGE) / 20))


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


def _meter_around(bars: list[Bar], z: Zone) -> tuple[int, float]:
    """(beats per bar, bar length) the band played in just before the pause: the most common beat count of up
    to four bars before it and their median length. A pause at the song start looks at the bars after it."""
    near = bars[max(0, z.first - 4) : z.first] or bars[z.last + 1 : z.last + 5] or bars[z.first : z.last + 1]
    counts = [len(b.beats) for b in near]
    n = max(set(counts), key=lambda c: (counts.count(c), -counts[::-1].index(c)))  # ties go to the nearest bar
    return n, float(np.median([b.end - b.start for b in near if len(b.beats) == n]))


def _even(start: float, end: float, bar_len: float, n: int) -> list[Bar]:
    """Whole bars of n even beats from start to end, as close to bar_len as the span allows."""
    k = max(1, round((end - start) / bar_len))
    step = (end - start) / k
    return [Bar(start + i * step, start + (i + 1) * step, list(start + i * step + step / n * np.arange(n)))
            for i in range(k)]


def zone_bars(bars: list[Bar], z: Zone) -> list[Bar]:
    """The bars a zone plays: the detected ones; an even grid between the zone edges when the detected ones are
    irregular, in the meter and tempo the band stopped in; or a steady grid from the zone start when the zone
    has an override."""
    if z.bpm is None and z.beats is None:
        own = bars[z.first : z.last + 1]
        if not _irregular(own):
            return own
        n, bar_len = _meter_around(bars, z)
        return _even(own[0].start, own[-1].end, bar_len, n)
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


def zone_clicks(bars: list[Bar], zones: list[Zone], downbeat: float, bar_db: list[float] | None = None) -> list[Click]:
    """A hi-hat on every beat of the zone bars, as loud as the music around the pause when bar_db (bar_levels)
    is given. Inside the song nothing else plays: the stick count-in belongs to the song's start only. The
    downbeat is the snapped "1", a few ms off the detected bar start, so beats compare with half a beat of
    slack."""
    clicks = []
    for z in sorted(zones, key=lambda q: q.first):
        zb = zone_bars(bars, z)
        slack = zb[0].period / 2
        gain = zone_gain(bar_db, z) if bar_db else 1.0
        for b in zb:
            clicks += [Click(t, k == 0, gain, voice="hihat") for k, t in enumerate(b.beats) if t >= downbeat - slack]
    return clicks


def merge(manual: list[Zone], found: list[Zone]) -> list[Zone]:
    """Pauses the user made or edited stay; the found ones fill in where they don't overlap."""
    taken = {i for z in manual for i in range(z.first, z.last + 1)}
    extra = [z for z in found if not any(i in taken for i in range(z.first, z.last + 1))]
    return sorted(manual + extra, key=lambda q: q.first)

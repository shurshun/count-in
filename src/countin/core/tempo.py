"""Editable rhythm sections in source seconds. A section's beat phase is independent of its edges."""

from bisect import bisect_right
from dataclasses import dataclass, replace
import math

import numpy as np

from .render import meter_shape
from .zones import Bar


@dataclass(frozen=True)
class Section:
    start: float
    bpm: float  # pulses/minute: dotted quarters in 6/8
    meter: str
    anchor: float  # the unshifted time of a first beat of a bar
    shift: float = 0.0
    certain: bool = True
    meter_known: bool = False
    fixed: bool = False  # manual BPM; pulses follow the exact edited grid
    meter_manual: bool = False
    pickup_start: float | None = None  # source time of the entrance in an imported pickup

    @property
    def period(self):
        return 60 / self.bpm


def index(sections, t):
    return max(0, bisect_right([s.start for s in sections], t) - 1)


def split(sections, t, duration):
    if t <= 1e-6 or t >= duration - 1e-6 or any(abs(s.start - t) < 1e-6 for s in sections):
        return list(sections)
    i = index(sections, t)
    return [*sections[:i + 1], replace(sections[i], start=t), *sections[i + 1:]]


def tapped_bpm(taps, speed):
    """Monotonic audible-time taps, at the practice speed, converted to original-song pulse BPM."""
    if len(taps) < 3 or speed <= 0:
        return None
    intervals = np.diff(taps)
    period = float(np.median(intervals))
    if period <= 0 or np.mean(np.abs(intervals - period) <= .22 * period) < .67:
        return None
    bpm = 60 / (period * speed)
    return bpm if 30 <= bpm <= 320 else None


def edit_span(sections, a, b, duration, bpm=None, meter=None, grid=()):
    """Split at source-time edges; the right-hand remainder retains its previous phase and pulse."""
    out = split(split(sections, a, duration), b, duration)
    for i, sec in enumerate(out):
        if a - 1e-6 <= sec.start < b - 1e-6:
            # Keep the closest existing bar phase rather than projecting a new BPM from far outside the span.
            bar = sec.period * meter_shape(sec.meter)[0]
            anchor = sec.anchor + sec.shift
            if sec.start > 0:
                anchor += round((sec.start - anchor) / bar) * bar
                nearby = [x.start for x in grid if x.accent and abs(x.start - sec.start) < bar]
                if nearby:
                    anchor = min(nearby, key=lambda x: abs(x - sec.start))
            out[i] = replace(sec, bpm=bpm if bpm is not None else sec.bpm, meter=meter or sec.meter,
                             anchor=anchor if bpm is not None else sec.anchor, shift=0.0 if bpm is not None else sec.shift,
                             fixed=sec.fixed or bpm is not None, certain=sec.certain or bpm is not None,
                             meter_known=sec.meter_known or meter is not None, meter_manual=sec.meter_manual or meter is not None)
    return out


def from_json(rows, duration):
    """Return a complete validated map or None; callers can still restore the rest of a song."""
    if not isinstance(rows, list) or not rows:
        return None
    try:
        out = [Section(**row) for row in rows]
        if out[0].start != 0 or any(not .5 <= s.bpm <= 5120
                                    or not all(math.isfinite(x) for x in (s.start, s.anchor, s.shift))
                                    or not 0 <= s.start < duration or abs(s.anchor) > duration + 30
                                    or abs(s.shift) > duration + 30
                                    or (s.pickup_start is not None and not 0 <= s.pickup_start < duration)
                                    for s in out):
            return None
        for sec in out:
            if not isinstance(sec.meter, str):
                return None
            meter_shape(sec.meter)
        return out if all(a.start < b.start for a, b in zip(out, out[1:])) else None
    except (KeyError, TypeError, ValueError):
        return None


def propose(beats, downs, duration, first, default_meter):
    """Estimate local pulse and grouping in short overlapping windows. Require sustained changes.

    The predominant interval provides an octave reference through sparse and double-counted passages.
    A two-pulse bar alone cannot identify compound meter. Six consistent subdivisions can support 6/8.
    """
    beats, downs = np.asarray(beats), np.asarray(downs)
    delta = np.diff(beats)
    good = delta[(delta >= 60 / 320) & (delta <= 2)]
    if len(good) < 3:
        return [Section(0.0, 120.0, default_meter, first, certain=False)]
    ordered = np.sort(good)
    reference = float(ordered[np.searchsorted(np.cumsum(ordered), ordered.sum() / 2)])
    # Grouping is estimated per downbeat interval, independently of missing/extra individual detections.
    positions = np.searchsorted(beats, downs - .05)
    counts = np.diff(positions)
    centers = (downs[1:] + downs[:-1]) / 2
    frames = []
    width = max(2.0, min(4.0, 7 * reference))
    for t in np.arange(0, duration, width):
        mid = t + width / 2
        l, r = np.searchsorted(beats, (max(0, mid - width), mid + width))
        ds = np.diff(beats[l:r])
        valid = ds[(ds >= .09) & (ds <= 2.2)]
        raw_period = float(np.median(valid)) if len(valid) else reference
        p = raw_period
        if p < reference * .63:
            p *= 2
        elif p > reference * 1.75:
            p /= 2
        ratios = valid / raw_period
        stable = len(valid) >= 4 and np.mean(np.abs(ratios - 1) < .11) >= .65
        near = counts[(centers >= mid - 2 * width) & (centers <= mid + 2 * width)]
        near = near[(near >= 1) & (near <= 12)]
        n = int(np.bincount(near).argmax()) if len(near) >= 3 else 0
        known = n in (3, 4, 6) and np.mean(near == n) >= .75
        meter = {3: "3/4", 4: "4/4", 6: "6/8"}[n] if known else default_meter
        if known and n == 6:
            # Six detected subdivisions, two compound pulses. A very slow candidate is ambiguous.
            if raw_period * 3 <= 2:
                p = raw_period * 3
            else:
                known, meter = False, default_meter
        frames.append((float(t), float(np.clip(60 / p, 30, 320)), meter, bool(stable), bool(known)))
    groups = []
    for f in frames:
        if groups:
            prev = groups[-1]
            typical = float(np.median([x[1] for x in prev]))
            if f[2:] == prev[-1][2:] and (not f[3] or abs(f[1] / typical - 1) < .065):
                prev.append(f)
                continue
        groups.append([f])
    # Isolated windows often contain a fill or missing beat. Require two windows for a new interpretation.
    concise = []
    for g in groups:
        if concise and len(g) == 1:
            concise[-1].extend(g)
        elif concise and g[0][2:] == concise[-1][0][2:] and abs(g[0][1] / concise[-1][0][1] - 1) < .065:
            concise[-1].extend(g)
        else:
            concise.append(g)
    out = []
    for g in concise:
        t, _, meter, stable, known = g[0]
        bpm = float(np.median([f[1] for f in g if f[2:] == g[0][2:]]))
        if not out:
            edge, anchor = 0.0, first
        else:
            near = downs[np.abs(downs - t) <= width]
            edge = float(near[np.argmin(abs(near - t))]) if len(near) else t
            if known and stable:
                n = {"3/4": 3, "4/4": 4, "6/8": 6}[meter]
                pulse = np.diff(downs) / meter_shape(meter)[0]
                fits = (counts == n) & (np.abs(pulse * bpm / 60 - 1) < .12)
                starts = downs[:-1][fits & ~np.r_[False, fits[:-1]]]
                near = starts[np.abs(starts - t) <= 2 * width]
                if len(near):
                    edge = float(near[np.argmin(abs(near - t))])
            if not out[-1].start + .1 < edge < duration:
                continue
            anchor = edge
        # Six subdivisions support a compound pulse but do not by themselves distinguish 6/8 from 6/4.
        out.append(Section(edge, bpm, meter, anchor, certain=stable, meter_known=known and meter != "6/8"))
    return out


def _pulses(sec, lo, hi, detected):
    """(time, index in the bar grid), followed from the same anchor on both sides of a split."""
    p = sec.period
    start, stop = lo - sec.shift, hi - sec.shift
    if sec.fixed or not len(detected):
        first = math.ceil((start - sec.anchor) / p - 1e-8)
        last = math.ceil((stop - sec.anchor) / p - 1e-8)
        return [(sec.anchor + k * p + sec.shift, k) for k in range(first, last)]
    out = []
    for direction in (-1, 1):
        at, step, k = sec.anchor, p, 0
        if direction == 1 and start - 1e-8 <= at < stop - 1e-8:
            out.append((at + sec.shift, k))
        limit = start if direction < 0 else stop
        while direction * (limit - at) > 1e-8:
            target = at + direction * step
            j = int(np.searchsorted(detected, target))
            candidates = detected[max(0, j - 1):j + 1]
            if len(candidates):
                near = float(candidates[np.argmin(np.abs(candidates - target))])
                if abs(near - target) < step / 4:
                    target = near
                    step = float(np.clip(.8 * step + .2 * abs(target - at), .85 * p, 1.15 * p))
            at, k = target, k + direction
            if start - 1e-8 <= at < stop - 1e-8:
                out.append((at + sec.shift, k))
    return sorted(out)


def bars(sections, detected, first, duration):
    """The effective song beats. Preserve pulse phase of both neighbours even at partial section boundaries."""
    out = []
    detected = np.asarray(detected)
    for i, sec in enumerate(sections):
        end = sections[i + 1].start if i + 1 < len(sections) else duration
        own = out[-1] if out and out[-1].meter == sec.meter else None
        for t, k in _pulses(sec, max(first, sec.start), end, detected):
            strong = k % meter_shape(sec.meter)[0] == 0
            if own is None or strong:
                if out:
                    out[-1].end = t
                own = Bar(t, end, [], meter=sec.meter, accent=strong, pulse=sec.period)
                out.append(own)
            own.beats.append(t)
            own.end = end
    return out

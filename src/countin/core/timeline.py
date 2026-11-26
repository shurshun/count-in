"""Non-destructive cuts and playback rates in source seconds."""

from dataclasses import dataclass

import numpy as np

from . import render as rd


@dataclass(frozen=True)
class Edit:
    start: float
    end: float
    speed: float | None = None  # None cuts the interval; speeds are relative to the original


@dataclass(frozen=True)
class Loop:
    name: str
    start: float
    end: float
    speed: float


def with_edit(edits: tuple[Edit, ...], start: float, end: float, speed: float | None) -> tuple[Edit, ...]:
    """Replace only the selected type of edit. A rate can be set across an existing cut."""
    pieces = [*without_edit(edits, start, end, cutting=speed is None), Edit(start, end, speed)]
    return tuple(sorted(pieces, key=lambda e: (e.start, e.end)))


def without_edit(edits: tuple[Edit, ...], start: float, end: float, *, cutting: bool) -> tuple[Edit, ...]:
    pieces = []
    for e in edits:
        if (e.speed is None) != cutting or e.end <= start or e.start >= end:
            pieces.append(e)
        else:
            if e.start < start:
                pieces.append(Edit(e.start, start, e.speed))
            if e.end > end:
                pieces.append(Edit(end, e.end, e.speed))
    return tuple(sorted(pieces, key=lambda e: (e.start, e.end)))


def from_json(rows, duration: float) -> tuple[Edit, ...]:
    edits = ()
    for row in rows or ():
        try:
            e = Edit(**row)
            if 0 <= e.start < e.end <= duration and (e.speed is None or .5 <= e.speed <= 1.5):
                edits = with_edit(edits, e.start, e.end, e.speed)
        except (TypeError, ValueError):
            continue
    return edits if Timeline(duration, 1.0, edits).length >= .02 else ()


class Timeline:
    def __init__(self, duration: float, speed: float, edits: tuple[Edit, ...], fade_ms: float = 5.0):
        self.duration = duration
        self.speed = speed
        self.fade_ms = fade_ms
        self.pieces: list[tuple[float, float, float, float, float]] = []
        edges = sorted({0.0, duration, *(x for e in edits for x in (e.start, e.end))})
        at = 0.0
        for a, b in zip(edges, edges[1:]):
            active = [e for e in edits if e.start <= a < e.end]
            if any(e.speed is None for e in active):
                continue
            rate = active[0].speed if active else speed
            end = at + (b - a) / rate
            if self.pieces and self.pieces[-1][1:3] == (a, rate):
                first, _, _, origin, _ = self.pieces[-1]
                self.pieces[-1] = (first, b, rate, origin, end)
            else:
                self.pieces.append((a, b, rate, at, end))
            at = end
        self.length = at

    def slices(self, start: float, end: float):
        """Surviving intersections of a source interval, including virtual pre-roll and MIDI tail."""
        if start < min(end, 0):
            yield start, min(end, 0), self.speed
        for a, b, rate, _, _ in self.pieces:
            if max(a, start) < min(b, end):
                yield max(a, start), min(b, end), rate
        if max(start, self.duration) < end:
            yield max(start, self.duration), end, self.speed

    def contains(self, t: float) -> bool:
        return t < 0 or any(a <= t < b for a, b, *_ in self.pieces)

    def rate_at(self, t: float) -> float:
        return next((rate for _, b, rate, _, _ in self.pieces if t < b), self.speed)

    def output_at(self, t: float) -> float:
        if t < 0:
            return t / self.speed
        for a, b, rate, at, _ in self.pieces:
            if t <= b:
                return at + max(0.0, t - a) / rate
        return self.length + max(0.0, t - self.duration) / self.speed

    def source_at(self, t: float) -> float:
        if t < 0:
            return t * self.speed
        for a, _, rate, at, end in self.pieces:
            if t < end:
                return a + max(0.0, t - at) * rate
        return self.duration + (t - self.length) * self.speed

    def audio(self, tracks: list[np.ndarray], sr: int, start: float, end: float, semis: float) -> list[np.ndarray]:
        """Stretch the surviving slices together and keep every part sample-aligned with the time map."""
        out = [[] for _ in tracks]
        offset = round(start * sr)
        pieces = list(self.slices(max(0.0, start), end))
        for k, (a, b, rate) in enumerate(pieces):
            i, j = round(a * sr) - offset, round(b * sr) - offset
            n = round(self.output_at(b) * sr) - round(self.output_at(a) * sr)
            fade = min(n // 2, round(self.fade_ms * sr / 1000))
            fade_in = k > 0 and a > pieces[k - 1][1] + 1e-8
            fade_out = k + 1 < len(pieces) and b < pieces[k + 1][0] - 1e-8
            ramp = np.linspace(0, 1, fade, endpoint=False, dtype=np.float32)[:, None] if fade and (fade_in or fade_out) else None
            for parts, part in zip(out, rd.stretch_many([x[i:j] for x in tracks], sr, rate, semis)):
                part = part[:n] if len(part) >= n else np.pad(part, ((0, n - len(part)), (0, 0)))
                if ramp is not None:
                    part = part.copy()
                    if fade_in:
                        part[:fade] *= ramp
                    if fade_out:
                        part[-fade:] *= ramp[::-1]
                parts.append(part)
        return [np.concatenate(parts) if parts else x[:0] for x, parts in zip(tracks, out)]

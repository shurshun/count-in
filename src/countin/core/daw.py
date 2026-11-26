"""Multitrack export for a DAW: parts as WAV, the click grid and the tempo map as MIDI.

Every file starts on a bar line. Output time 0 is bar 1 in the DAW, so the files line up when dropped at the
project start and the DAW's bars match the song's.
"""

import math
import shutil
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory, mkdtemp

import numpy as np

from . import zones as zn
from .render import Click, meter_parts, meter_shape, stretch_many

TPB = 480  # MIDI ticks per quarter note
DRUMS = 9  # GM drum channel (10 counted from 1)
# voice -> GM drum notes (accent, plain)
NOTES = {"stick": (76, 77), "hihat": (42, 42), "beep": (34, 33), "cowbell": (56, 56), "ping": (75, 75)}


@dataclass
class Grid:
    start: float  # song time of bar 1
    bars: list[zn.Bar]  # song bars from bar 1 on, gap-free


def song_beats(beats, downbeat: float, period: float) -> np.ndarray:
    """Beats from the downbeat on at the beat rate the user set. Each next beat is the detected one nearest one
    beat after the last, within a quarter beat; with none there the beat keeps time. The beat length follows
    the accepted beats, so real tempo changes come through, while sections the detector counts twice as fast
    or a missed beat do not change the rate."""
    b = np.sort(np.asarray(beats, dtype=float))
    if not len(b):
        return np.array([downbeat])
    out, p = [downbeat], period
    while out[-1] + p < b[-1] + p / 2:
        target = out[-1] + p
        i = int(np.clip(np.searchsorted(b, target), 1, len(b) - 1))
        near = b[i] if abs(b[i] - target) < abs(b[i - 1] - target) else b[i - 1]
        if abs(near - target) < p / 4:
            p = 0.7 * p + 0.3 * (near - out[-1])
            target = float(near)
        out.append(target)
    return np.array(out)


def song_clicks(beats, downbeat: float, period: float, per_bar: int, voice: str, gain: float = 1.0) -> list[Click]:
    """A click on every song beat (song_beats) from the "1" to the end, accented on each bar's first beat."""
    return [Click(float(t), i % per_bar == 0, gain, voice) for i, t in enumerate(song_beats(beats, downbeat, period))]


def _bars(b: np.ndarray, n: int, period: float) -> list[zn.Bar]:
    """Beats grouped n to a bar; the last bar may hold fewer and ends one beat after its last beat."""
    out = []
    for i in range(0, len(b), n):
        own = [float(x) for x in b[i : i + n]]
        out.append(zn.Bar(own[0], float(b[i + n]) if i + n < len(b) else own[-1] + period, own))
    return out


def grid(beats, bars: list[zn.Bar], zones: list[zn.Zone] | None, downbeat: float, period: float, meter: str,
         earliest: float) -> Grid:
    """Bars from a bar line at or before `earliest` to the song end, in the chosen meter from the downbeat on.
    The bars before the downbeat are even at the set tempo. Song bars group the detected beats, so a stray or
    missed downbeat never changes the meter. Pauses with their own tempo or meter take their zone bars."""
    n = meter_shape(meter)[0]
    bar_len = n * period
    lead = max(0, math.ceil((downbeat - earliest) / bar_len - 1e-9))
    out = [zn.Bar(downbeat - (lead - k) * bar_len, downbeat - (lead - k - 1) * bar_len,
                  [downbeat - (lead - k) * bar_len + j * period for j in range(n)]) for k in range(lead)]
    if bars and bars[0].meter:
        # The application has already resolved these beats, including edits and shifted sections.
        if bars[0].start > downbeat + 1e-8:
            out.append(zn.Bar(downbeat, bars[0].start, [downbeat], meter=meter, accent=False, pulse=period))
        out += bars
        return Grid(out[0].start, out)
    b = song_beats(beats, downbeat, period)
    lo = -math.inf
    for z in sorted(zones or [], key=lambda q: q.first):
        if z.bpm is None and z.beats is None:
            continue
        zb = zn.zone_bars(bars, z)
        out += _bars(b[(b >= lo) & (b < zb[0].start - period / 4)], n, period) + zb
        lo = zb[-1].end - period / 4
    tail = b[b >= lo]
    if len(tail) % n:  # the song ends mid-bar: the last bar runs on in time, so the meter holds to the end
        tail = np.append(tail, tail[-1] + period * np.arange(1, n - len(tail) % n + 1))
    out += _bars(tail, n, period)

    # Each bar ends where the next starts, so the bars never overlap or leave a gap.
    for k in range(len(out) - 1):
        out[k] = zn.Bar(out[k].start, out[k + 1].start, out[k].beats)
    return Grid(out[0].start, out)


def quarters(bar: zn.Bar, meter: str) -> tuple[float, int, int]:
    """(quarter notes, numerator, denominator) of a bar. A 6/8 beat is a dotted quarter."""
    n = len(bar.beats)
    _, den = meter_parts(meter)
    _, sub = meter_shape(meter)
    return n * sub * 4 / den, n * sub, den


def _intervals(g, meter):
    for bar in g.bars:
        if bar.meter is None:
            yield bar.start, bar.end, *quarters(bar, meter)
            continue
        num, den = meter_parts(bar.meter)
        _, sub = meter_shape(bar.meter)
        unit = sub * 4 / den
        for a, b in zip(bar.beats, [*bar.beats[1:], bar.end]):
            if b <= a:
                continue
            q = unit
            # A manually shifted neighbour or clipped song end can cut the last pulse short.
            if b == bar.end and abs((b - a) / bar.pulse - 1) > .15:
                q *= (b - a) / bar.pulse
            yield a, b, q, num, den


def _edited_intervals(g, meter, clock):
    for start, end, q, num, den in _intervals(g, meter):
        for a, b, _ in clock.slices(max(g.start, start), end):
            yield clock.output_at(a), clock.output_at(b), q * (b - a) / (end - start), num, den


def _tempo_track(g: Grid, meter: str, speed: float, clock=None):
    """Tempo and meter events at every bar line, and a function from song time to ticks. Each tempo rounds
    against the time the MIDI has reached so far, so rounding never accumulates."""
    import mido

    track = mido.MidiTrack()
    marks = []  # (song time, tick) at every bar line
    tick, last_tick, midi_us, sig = 0, 0, 0.0, None
    intervals, origin = (_edited_intervals(g, meter, clock), clock.output_at(g.start)) if clock else (_intervals(g, meter), g.start)
    for start, end, q, num, den in intervals:
        length = max(1, round(q * TPB))
        q = length / TPB
        target_us = (end - origin) / speed * 1e6
        tempo = max(1, round((target_us - midi_us) / q))
        events = []
        if (num, den) != sig:
            events.append(mido.MetaMessage("time_signature", numerator=num, denominator=den))
            sig = (num, den)
        events.append(mido.MetaMessage("set_tempo", tempo=tempo))
        for k, e in enumerate(events):
            e.time = tick - last_tick if k == 0 else 0
            track.append(e)
        last_tick = tick
        marks.append((start, tick, (end - start) / length))
        midi_us += tempo * q
        tick += length
    def to_tick(t: float) -> int:
        for start, at, sec_per_tick in reversed(marks):
            if t >= start - 1e-9:
                return at + round((t - start) / sec_per_tick)
        return 0

    return track, to_tick


def write_tempo(path: Path, g: Grid, meter: str, speed: float = 1.0, markers: list | None = None, *, clock=None) -> None:
    """The tempo map; section markers (song time, name) go in as MIDI markers a DAW shows on its timeline."""
    import mido

    track, to_tick = _tempo_track(g, meter, speed, clock)
    mid = mido.MidiFile(type=1, ticks_per_beat=TPB, charset="utf-8")
    mid.tracks.append(track)
    if markers:
        names = mido.MidiTrack([mido.MetaMessage("track_name", name="Sections")])
        last = 0
        for at, name in sorted((max(0, to_tick(t)), n) for t, n in markers):
            names.append(mido.MetaMessage("marker", text=name, time=at - last))
            last = at
        mid.tracks.append(names)
    mid.save(str(path))


def write_clicks(path: Path, g: Grid, clicks: list[Click], meter: str, speed: float = 1.0, *, clock=None) -> None:
    """The clicks as GM drum notes on channel 10, with the tempo map, so they land on the song's beats."""
    import mido

    tempo, to_tick = _tempo_track(g, meter, speed, clock)
    events = []
    for c in clicks:
        note = NOTES[c.voice][0 if c.accent else 1]
        vel = max(1, min(127, round((110 if c.accent else 90) * c.gain)))
        at = to_tick(c.time)
        events += [(at, 1, "note_on", note, vel), (at + TPB // 8, 0, "note_off", note, 0)]
    notes = mido.MidiTrack([mido.MetaMessage("track_name", name="Click")])
    last = 0
    for at, _order, kind, note, vel in sorted(events):
        notes.append(mido.Message(kind, channel=DRUMS, note=note, velocity=vel, time=at - last))
        last = at
    mid = mido.MidiFile(type=1, ticks_per_beat=TPB)
    mid.tracks += [tempo, notes]
    mid.save(str(path))


def place(xs: list[np.ndarray], sr: int, g: Grid, speed: float = 1.0, semis: float = 0.0, *, clock=None) -> list[np.ndarray]:
    """Audio of the whole song (song time 0 at sample 0) moved to output time, with bar 1 at sample 0.
    Silence fills the bars before the song starts; audio before bar 1 (trimmed silence) gets cut."""
    if clock:
        out = clock.audio(xs, sr, 0.0, clock.duration, semis)
        offset = round(clock.output_at(g.start) * sr)
        return [x[offset:] if offset >= 0 else np.pad(x, ((-offset, 0), (0, 0))) for x in out]
    if g.start > 0:
        return stretch_many([x[round(g.start * sr):] for x in xs], sr, speed, semis)
    out = stretch_many(xs, sr, speed, semis)
    return [np.concatenate([np.zeros((round(-g.start / speed * sr), x.shape[1]), dtype=np.float32), x]) for x in out]


def write_wav(path: Path, x: np.ndarray, sr: int) -> None:
    """32-bit float: stems of a loud master peak above full scale, and an integer WAV would clip them."""
    import soundfile as sf

    sf.write(str(path), x, sr, subtype="FLOAT")


@contextmanager
def staged_export(folder: Path):
    """Publish all files together and preserve unrelated files in an existing export."""
    folder.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=folder.parent, prefix=".count-in-export-") as tmp:
        stage = Path(tmp) / "out"
        stage.mkdir()
        yield stage
        if not folder.exists():
            stage.rename(folder)
            return
        names = {p.name.casefold() for p in stage.iterdir()}

        def skip_replaced(src, files):
            return [f for f in files if f.casefold() in names] if Path(src) == folder else []

        shutil.copytree(folder, stage, symlinks=True, dirs_exist_ok=True, ignore=skip_replaced)
        backup_root = Path(mkdtemp(dir=folder.parent, prefix=".count-in-previous-"))
        backup = backup_root / folder.name
        try:
            folder.rename(backup)
        except OSError:
            backup_root.rmdir()
            raise
        try:
            stage.rename(folder)
        except OSError:
            backup.rename(folder)
            backup_root.rmdir()
            raise
        shutil.rmtree(backup_root, ignore_errors=True)


def parts(pcm: np.ndarray, st: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """The separated parts plus "other", the rest of the mix, so the parts sum back to the song."""
    return {**st, "other": pcm - sum(st.values())}

"""Read the tempo, meter, and rehearsal marks of a Guitar Pro score."""

from dataclasses import dataclass, replace
import math
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from .render import meter_parts, meter_shape
from .tempo import Section

EXTENSIONS = {".gp", ".gpx", ".gp3", ".gp4", ".gp5"}
MAX_SCORE = 16 * 1024 * 1024
MAX_BARS = 10000
EPSILON = 1e-7


@dataclass(frozen=True)
class _Bar:
    meter: str
    quarters: float
    marker: str = ""
    repeat_start: bool = False
    repeat_count: int = 0
    endings: int = 0
    changes: tuple[tuple[float, float], ...] = ()


@dataclass(frozen=True)
class Score:
    sections: tuple[Section, ...]
    starts: tuple[float, ...]  # one entry per played measure, after expanding repeats
    markers: tuple[tuple[float, str], ...]
    duration: float
    pickup: bool = False
    has_jumps: bool = False
    has_ramps: bool = False

    def alignment(self, bar: int, at: float, second: tuple[int, float] | None = None) -> tuple[float, float]:
        start = self.starts[bar]
        scale = 1.
        if second is not None:
            later_bar, later_at = second
            if later_bar <= bar or later_at <= at:
                raise ValueError("The second anchor must follow the first.")
            scale = (later_at - at) / (self.starts[later_bar] - start)
        return at - start * scale, scale

    def aligned(self, bar: int, at: float, duration: float, second: tuple[int, float] | None = None):
        return self._aligned(*self.alignment(bar, at, second), duration)

    def previous_markers(self, sections: list[Section], duration: float) -> list[tuple[float, str]]:
        """Recognize a prior import of this score, including files saved by the one-anchor importer."""
        if not self.markers or not sections or not all(sec.fixed and sec.meter_manual for sec in sections):
            return []
        original, existing = self.sections[0], sections[0]
        scale = original.bpm / existing.bpm
        offset = existing.anchor - original.anchor * scale
        try:
            expected, _, marks = self._aligned(offset, scale, duration)
        except ValueError:
            return []
        if len(expected) != len(sections):
            return []
        for a, b in zip(expected, sections):
            if (a.meter != b.meter or b.shift or
                    any(abs(x - y) > EPSILON for x, y in ((a.start, b.start), (a.anchor, b.anchor), (a.bpm, b.bpm)))):
                return []
        return marks

    def _aligned(self, offset: float, scale: float, duration: float):
        full_bars = self.starts[1:] if self.pickup else self.starts
        first = next((start * scale + offset for start in full_bars if start * scale + offset >= -EPSILON), None)
        if first is None or first >= duration or self.duration * scale + offset <= 0:
            raise ValueError("The score and recording have no full bar in common.")
        sections = []
        for sec in self.sections:
            start = sec.start * scale + offset
            if start >= duration - EPSILON:
                break
            bpm = sec.bpm / scale
            if not .5 <= bpm <= 5120:
                raise ValueError("Aligned tempo is outside Count-In's range.")
            anchor = sec.anchor * scale + offset
            if start < 0:
                bar_length = sec.period * scale * meter_shape(sec.meter)[0]
                anchor -= math.floor(anchor / bar_length) * bar_length
            row = replace(sec, start=max(0., start), anchor=anchor, bpm=bpm)
            if sections and row.start <= EPSILON and sections[-1].start <= EPSILON:
                sections[-1] = replace(row, start=0.)
            else:
                sections.append(row)
        sections[0] = replace(sections[0], start=0.)
        pickup_start = max(0., offset) if self.pickup and self.starts[1] * scale + offset > 0 else None
        if pickup_start is not None:
            sections[0] = replace(sections[0], pickup_start=pickup_start)
        marks = [(t * scale + offset, name) for t, name in self.markers if 0 <= t * scale + offset < duration]
        return sections, max(first, 0.), marks


def _play_order(bars):
    # Each active repeat has its opening measure and current pass.
    stack = []
    i, implicit_start, last_pass = 0, 0, 0
    for _ in range(MAX_BARS):
        if i >= len(bars):
            return
        bar = bars[i]
        if bar.repeat_start and (not stack or stack[-1][0] != i):
            stack.append([i, 0])
        if not bar.endings:
            last_pass = 0
        current_pass = stack[-1][1] if stack else last_pass
        if not bar.endings or bar.endings & (1 << current_pass):
            yield i
        if bar.repeat_count > 1:
            if not stack:
                stack.append([implicit_start, last_pass])
            if stack[-1][1] + 1 < bar.repeat_count:
                stack[-1][1] += 1
                i = stack[-1][0]
                continue
            _, last_pass = stack.pop()
            implicit_start = i + 1
        i += 1
    raise ValueError("Too many measures after expanding repeats.")


def _score(bars, initial_bpm, *, pickup=False, has_jumps=False, has_ramps=False):
    if not bars or len(bars) > MAX_BARS:
        raise ValueError("Invalid number of measures.")
    sections, starts, markers = [], [], []
    time = 0.
    nominal = []
    bpm = initial_bpm
    for bar in bars:
        nominal.append(bpm)
        if bar.changes:
            bpm = bar.changes[-1][1]
    previous = -2
    bpm = initial_bpm
    for i in _play_order(bars):
        bar = bars[i]
        _, den = meter_parts(bar.meter)
        _, sub = meter_shape(bar.meter)
        quarters_per_pulse = sub * 4 / den
        if i != previous + 1 and i <= previous:
            bpm = nominal[i]  # a repeat returns to the written tempo at the repeat start
        previous = i
        starts.append(time)
        if bar.marker:
            markers.append((time, bar.marker))
        changes = dict(bar.changes)
        changes.setdefault(0., bpm)
        position = 0.
        for pos, new_bpm in sorted(changes.items()):
            time += (pos - position) * 60 / bpm
            bpm, position = new_bpm, pos
            pulse_bpm = bpm / quarters_per_pulse
            if not .5 <= pulse_bpm <= 5120:
                raise ValueError(f"Tempo outside Count-In's range at measure {i + 1}: {pulse_bpm:.2f} BPM.")
            if pos >= bar.quarters:
                continue
            anchor = time - pos * 60 / bpm
            sec = Section(time, pulse_bpm, bar.meter, anchor,
                          meter_known=True, fixed=True, meter_manual=True)
            if sections and sections[-1].meter == sec.meter and sections[-1].bpm == sec.bpm:
                phase = (anchor - sections[-1].anchor) / (sec.period * meter_shape(bar.meter)[0])
                if abs(phase - round(phase)) < EPSILON:
                    continue
            sections.append(sec)
        time += (bar.quarters - position) * 60 / bpm
    return Score(tuple(sections), tuple(starts), tuple(markers), time, pickup and len(starts) > 1, has_jumps, has_ramps)


def _legacy(path):
    import guitarpro

    if path.stat().st_size > MAX_SCORE:
        raise ValueError("Guitar Pro score is too large.")
    score = guitarpro.parse(str(path))
    changes = [dict() for _ in score.measureHeaders]
    has_ramps = False
    for track in score.tracks:
        for index, measure in enumerate(track.measures):
            for voice in measure.voices:
                for beat in voice.beats:
                    mix = beat.effect.mixTableChange
                    if mix is not None and mix.tempo is not None and mix.tempo.value > 0:
                        pos = (beat.start - measure.start) / guitarpro.Duration.quarterTime
                        changes[index].setdefault(pos, float(mix.tempo.value))
                        has_ramps |= mix.tempo.duration > 0
    bars = []
    for header, shifts in zip(score.measureHeaders, changes):
        meter = f"{header.timeSignature.numerator}/{header.timeSignature.denominator.value}"
        quarters = header.length / guitarpro.Duration.quarterTime
        bars.append(_Bar(meter, quarters, header.marker.title.strip() if header.marker else "",
                         header.isRepeatOpen, header.repeatClose + 1, header.repeatAlternative,
                         tuple(sorted((t, bpm) for t, bpm in shifts.items() if 0 <= t <= quarters))))
    return _score(bars, float(score.tempo), has_jumps=any(h.fromDirection for h in score.measureHeaders),
                  has_ramps=has_ramps)


def _gpx(data):
    """Unwrap the BCFS sectors or the BCFZ bitstream used by Guitar Pro 6."""
    if data.startswith(b"BCFZ"):
        size = int.from_bytes(data[4:8], "little")
        if not 4 <= size <= MAX_SCORE:
            raise ValueError("Invalid compressed GPX size.")
        bit = 64

        def bits(n, reverse=False):
            nonlocal bit
            if bit + n > len(data) * 8:
                raise EOFError("End of the GPX bitstream.")
            value = 0
            for i in range(n):
                digit = (data[bit // 8] >> (7 - bit % 8)) & 1
                value = value | (digit << i) if reverse else (value << 1) | digit
                bit += 1
            return value

        out = bytearray()
        try:
            while len(out) < size:
                if bits(1):
                    width = bits(4)
                    back, count = bits(width, True), bits(width, True)
                    if back > len(out):
                        raise ValueError("Invalid GPX back-reference.")
                    start = len(out) - back
                    out.extend(out[start:start + min(back, count)])
                else:
                    out.extend(bits(8) for _ in range(bits(2, True)))
        except EOFError:
            # Some writers omit trailing sector padding. The score's own size is checked below.
            pass
        data = bytes(out)
    if not data.startswith(b"BCFS"):
        raise ValueError("Invalid GPX container.")
    fs = memoryview(data)[4:]
    sector = 4096

    def integer(at):
        return int.from_bytes(fs[at:at + 4], "little")

    for pos in range(sector, len(fs) - 0x98, sector):
        if integer(pos) == 2 and bytes(fs[pos + 4:pos + 131]).split(b"\x00", 1)[0] == b"score.gpif":
            size = integer(pos + 0x8c)
            blocks = (size + sector - 1) // sector
            if not 0 < size <= len(fs) or pos + 0x94 + 4 * blocks > pos + sector:
                break
            chunks = []
            for j in range(blocks):
                start = integer(pos + 0x94 + 4 * j) * sector
                if not sector <= start < len(fs):
                    raise ValueError("Invalid GPX score sector.")
                chunks.append(fs[start:start + sector])
            payload = b"".join(chunks)
            if len(payload) < size:
                raise ValueError("Truncated GPX score.")
            return payload[:size]
    raise ValueError("No score.gpif in GPX container.")


class _ScoreTree(ET.TreeBuilder):
    def doctype(self, name, pubid, system):
        raise ValueError("Invalid GPIF document type.")


def _pickup_length(root, bar):
    def rows(plural, singular):
        return {n.get("id"): n for n in root.findall(f"{plural}/{singular}")}

    bars, voices, beats, rhythms = (rows(*p) for p in (("Bars", "Bar"), ("Voices", "Voice"),
                                                       ("Beats", "Beat"), ("Rhythms", "Rhythm")))
    units = {"Long": 16., "DoubleWhole": 8., "Whole": 4., "Half": 2., "Quarter": 1.,
             "Eighth": .5, "16th": .25, "32nd": .125, "64th": .0625,
             "128th": .03125, "256th": .015625}

    def beat_length(beat):
        rhythm = rhythms[beat.find("Rhythm").get("ref")]
        value = units[rhythm.findtext("NoteValue")]
        dot, tuplet = rhythm.find("AugmentationDot"), rhythm.find("PrimaryTuplet")
        if dot is not None:
            value *= 2 - .5 ** int(dot.get("count", "1"))
        if tuplet is not None:
            value *= int(tuplet.get("den")) / int(tuplet.get("num"))
        return value

    amounts = []
    for index in bar.findtext("Bars", "").split():
        if index not in bars:
            continue
        for voice in bars[index].findtext("Voices", "").split():
            if voice in voices:
                amounts.append(sum(beat_length(beats[b]) for b in voices[voice].findtext("Beats", "").split()))
    nominal, den = meter_parts(bar.findtext("Time", "4/4"))
    return max((n for n in amounts if 0 < n < nominal * 4 / den - EPSILON), default=None)


def _gpif(xml):
    root = ET.fromstring(xml, parser=ET.XMLParser(target=_ScoreTree()))
    if root.tag != "GPIF":
        raise ValueError("Invalid GPIF document.")
    nodes = root.findall("MasterBars/MasterBar")
    pickup = _pickup_length(root, nodes[0]) if nodes and root.find("MasterTrack/Anacrusis") is not None else None
    changes = [dict() for _ in nodes]
    ratios = {1: .5, 2: 1., 3: 1.5, 4: 2., 5: 3.}
    has_ramps = False
    for node in root.findall("MasterTrack/Automations/Automation"):
        if node.findtext("Type") != "Tempo":
            continue
        i = int(node.findtext("Bar", "0"))
        if not 0 <= i < len(nodes):
            continue
        at = float(node.findtext("Position", "0"))
        value = node.findtext("Value", "").split()
        bpm = float(value[0]) * ratios.get(int(value[1]) if len(value) > 1 else 1, 1.)
        if not math.isfinite(bpm) or bpm <= 0 or not 0 <= at <= 1:
            raise ValueError("Invalid score tempo automation.")
        changes[i][at] = bpm
        has_ramps |= node.findtext("Linear", "").lower() == "true"
    bars = []
    for i, node in enumerate(nodes):
        meter = node.findtext("Time", "4/4").strip()
        n, den = meter_parts(meter)
        quarters = pickup if i == 0 and pickup is not None else n * 4 / den
        section = node.findtext("Section/Text", "").strip() or node.findtext("Section/Letter", "").strip()
        repeat = node.find("Repeat")
        openings = repeat is not None and repeat.get("start", "").lower() == "true"
        closes = int(repeat.get("count", "1")) if repeat is not None and repeat.get("end", "").lower() == "true" else 0
        endings = set(int(x) for x in node.findtext("AlternateEndings", "").split())
        if closes > 32 or any(not 1 <= n <= 32 for n in endings):
            raise ValueError("Invalid repeat or alternate ending.")
        bars.append(_Bar(meter, quarters, section, openings, closes, sum(1 << (n - 1) for n in endings),
                         tuple(sorted((at * quarters, bpm) for at, bpm in changes[i].items()))))
    return _score(bars, 120., pickup=pickup is not None,
                  has_jumps=any(b.find("Directions/Jump") is not None for b in nodes), has_ramps=has_ramps)


def read(path: Path) -> Score:
    ext = path.suffix.lower()
    if ext not in EXTENSIONS:
        raise ValueError("Unknown Guitar Pro file extension.")
    if ext in (".gp3", ".gp4", ".gp5"):
        return _legacy(path)
    if ext == ".gpx":
        with path.open("rb") as stream:
            data = stream.read(MAX_SCORE + 1)
        if len(data) > MAX_SCORE:
            raise ValueError("Guitar Pro score is too large.")
        return _gpif(_gpx(data))
    with ZipFile(path) as archive:
        entry = next((f for f in archive.infolist() if f.filename.lower() == "content/score.gpif"), None)
        if entry is None or not 0 < entry.file_size <= MAX_SCORE:
            raise ValueError("No usable Content/score.gpif in Guitar Pro file.")
        return _gpif(archive.read(entry))

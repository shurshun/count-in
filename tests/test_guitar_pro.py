import struct
from zipfile import ZipFile

import guitarpro
import mido
import pytest

from countin.core import daw, guitar_pro, tempo


# A quarter at 120 through 4/4, two dotted quarters at 80 through 6/8,
# then seven eighth-note pulses after an interior tempo change in 7/8.
SCORE = """<GPIF>
<MasterTrack><Automations>
  <Automation><Type>Tempo</Type><Bar>0</Bar><Value>120 2</Value></Automation>
  <Automation><Type>Tempo</Type><Bar>1</Bar><Value>80 3</Value></Automation>
  <Automation><Type>Tempo</Type><Bar>2</Bar><Position>0.2857142857142857</Position><Value>60 2</Value></Automation>
</Automations></MasterTrack>
<MasterBars>
  <MasterBar><Time>4/4</Time><Section><Text>Куплет</Text></Section></MasterBar>
  <MasterBar><Time>6/8</Time><Section><Text>Припев</Text></Section></MasterBar>
  <MasterBar><Time>7/8</Time><Section><Letter>C</Letter></Section></MasterBar>
  <MasterBar><Time>4/4</Time></MasterBar>
</MasterBars>
</GPIF>""".encode()


def write_score(path):
    if path.suffix == ".gp":
        with ZipFile(path, "w") as archive:
            archive.writestr("Content/score.gpif", SCORE)
        return
    # GP6 stores the directory entry and the score in separate sectors.
    sector = 4096
    fs = bytearray(3 * sector)
    struct.pack_into("<I", fs, sector, 2)
    fs[sector + 4:sector + 14] = b"score.gpif"
    struct.pack_into("<I", fs, sector + 0x8c, len(SCORE))
    struct.pack_into("<I", fs, sector + 0x94, 2)
    fs[2 * sector:2 * sector + len(SCORE)] = SCORE
    path.write_bytes(b"BCFS" + fs)


@pytest.mark.parametrize("ext", [".gp", ".gpx"])
def test_imported_score_aligns_bar_meter_and_markers_in_midi(tmp_path, ext):
    path = tmp_path / ("score" + ext)
    write_score(path)
    score = guitar_pro.read(path)
    assert score.starts == pytest.approx([0, 2, 3.5, 6.5])

    sections, first, marks = score.aligned(1, 3.25, 12.)
    assert first == pytest.approx(1.25)
    assert [section.bpm for section in sections] == pytest.approx([120, 80, 240, 120, 60])
    assert [section.meter for section in sections] == ["4/4", "6/8", "7/8", "7/8", "4/4"]
    assert marks == [(1.25, "Куплет"), (3.25, "Припев"), (4.75, "C")]

    bars = tempo.bars(sections, (), first, 12.)
    assert [bar.start for bar in bars[:4]] == pytest.approx([1.25, 3.25, 4.75, 7.75])
    assert bars[2].beats == pytest.approx([4.75, 5., 5.25, 5.75, 6.25, 6.75, 7.25])
    grid = daw.grid([], bars, None, first, sections[0].period, "4/4", first)
    out = tmp_path / "tempo.mid"
    daw.write_tempo(out, grid, "4/4", markers=marks)
    elapsed, positions, signatures = 0, {}, []
    for event in mido.MidiFile(out, charset="utf-8"):
        elapsed += event.time
        if event.type == "marker":
            positions[event.text] = elapsed
        elif event.type == "time_signature":
            signatures.append((event.numerator, event.denominator))
    assert positions == pytest.approx({"Куплет": 0., "Припев": 2., "C": 3.5})
    assert signatures == [(4, 4), (6, 8), (7, 8), (4, 4)]


def test_legacy_reader_imports_marker_and_tempo_change_from_score_tracks(tmp_path):
    song = guitarpro.Song(tempo=100)
    header = song.measureHeaders[0]
    header.marker = guitarpro.Marker(title="Verse")
    measure = song.tracks[0].measures[0]
    voice = measure.voices[0]
    for k in range(4):
        beat = guitarpro.Beat(voice, start=header.start + k * 960, status=guitarpro.BeatStatus.rest)
        if k == 2:
            beat.effect.mixTableChange = guitarpro.MixTableChange(tempo=guitarpro.MixTableItem(value=120))
        voice.beats.append(beat)
    score_path = tmp_path / "fixture.gp5"
    guitarpro.write(song, str(score_path))
    score = guitar_pro.read(score_path)
    assert score.markers == ((0., "Verse"),)
    assert [(section.start, section.bpm) for section in score.sections] == [(0., 100.), (1.2, 120.)]
    assert score.duration == pytest.approx(2.2)


def test_second_anchor_scales_grid_and_markers_from_the_same_origin(tmp_path):
    path = tmp_path / "score.gp"
    write_score(path)
    score = guitar_pro.read(path)
    previous, _, previous_marks = score.aligned(1, 3.25, 12.)
    sections, first, marks = score.aligned(1, 3.25, 12., (3, 9.1))
    bars = tempo.bars(sections, (), first, 12.)
    assert [bar.start for bar in bars[:4]] == pytest.approx([.65, 3.25, 5.20, 9.1])
    assert [marker[1] for marker in marks] == ["Куплет", "Припев", "C"]
    assert [marker[0] for marker in marks] == pytest.approx([.65, 3.25, 5.20])
    assert sections[1].bpm == pytest.approx(80 / 1.3)
    assert score.previous_markers(previous, 12.) == previous_marks
    assert [at for at, _ in score.previous_markers(sections, 12.)] == pytest.approx([at for at, _ in marks])
    for second in ((1, 8.), (3, 2.)):
        with pytest.raises(ValueError):
            score.aligned(1, 3.25, 12., second)

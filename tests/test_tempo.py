"""The editable timeline uses source seconds across playback, persistence and MIDI conversion."""

import json
from dataclasses import replace
from pathlib import Path

import mido
import numpy as np
import pytest

from countin.core import daw, render, tempo, zones


def test_whole_song_groups_pulses_and_only_identifies_supported_meter():
    beats = np.r_[np.arange(1, 17, .5), np.arange(17, 33, 2/3), np.arange(33, 49, 2/9)]
    downs = np.r_[np.arange(1, 17, 2.), np.arange(17, 33, 2.), np.arange(33, 49, 4/3)]
    sections = tempo.propose(beats, downs, 49, 1, "4/4")
    assert [s.meter for s in sections] == ["4/4", "3/4", "6/8"]
    assert [s.start for s in sections] == pytest.approx([0, 17, 33])
    assert [s.bpm for s in sections] == pytest.approx([120, 90, 90])
    two_pulses = tempo.propose(np.arange(0, 32, .5), np.arange(0, 32), 32, 0, "4/4")
    assert not any(s.meter_known for s in two_pulses)


def test_editing_a_tapped_span_preserves_adjacent_pulses_and_shift_is_local():
    evidence = np.arange(0, 24, .5)
    original = [tempo.Section(0, 120, "4/4", 0)]
    old = tempo.bars(original, evidence, 0, 24)
    split = tempo.split(original, 7.3, 24)
    assert tempo.bars(split, evidence, 0, 24) == old
    # Playback time is continuous across the loop seam. The source plays at 85%.
    taps = np.arange(7) * (60 / 90 / .85) + 7.75
    assert tempo.tapped_bpm(taps[:2], .85) is None
    bpm = tempo.tapped_bpm(taps, .85)
    assert bpm == pytest.approx(90)
    sections = tempo.edit_span(original, 8, 16, 24, bpm=bpm, grid=old)
    new = tempo.bars(sections, evidence, 0, 24)
    assert [x for b in new for x in b.beats if x < 8 or x >= 16] == pytest.approx(
        [x for x in evidence if x < 8 or x >= 16])
    assert [x for b in new for x in b.beats if 8 <= x < 16] == pytest.approx(np.arange(8, 16, 2/3))
    sections[1] = replace(sections[1], shift=.125)
    shifted = tempo.bars(sections, evidence, 0, 24)
    assert [x for b in shifted for x in b.beats if 8 <= x < 16] == pytest.approx(np.arange(8, 16, 2/3) + .125)
    free = zones.mapped(zones.Zone(0, 0, manual=True, free=True), shifted, 9.127, 10.583)
    assert zones.zone_clicks(shifted, [free], 0)[0].time == pytest.approx(9.127)
    again = zones.mapped(free, old, *zones.bounds(shifted, free))
    assert zones.bounds(old, again) == pytest.approx((9.127, 10.583))


def test_mixed_meter_click_wav_and_midi_use_the_same_source_beats(tmp_path):
    from countin import gui
    from countin.verify import find_onset

    sections = [tempo.Section(0, 120, "4/4", 1, fixed=True),
                tempo.Section(9, 90, "3/4", 9, fixed=True),
                tempo.Section(15, 90, "6/8", 15, fixed=True)]
    bars = tempo.bars(sections, [], 1, 19.3)
    t = gui.Track(Path("fixture.wav"), bpm=120, t0=1, duration=19.3, bars=bars, sections=sections)
    s = gui.Settings("even", 1, "4/4", None, 0, click_all=True, click_voice="stick")
    clicks = gui.clicks_for(t, s)
    grid = daw.grid([], bars, None, 1, .5, "4/4", -1)
    speed, sr = .85, 16000
    wav = render.click_track(clicks, sr, grid.start, round(26 * sr), speed=speed)
    daw.write_tempo(tmp_path / "tempo.mid", grid, "4/4", speed, [(15., "Compound")])
    daw.write_clicks(tmp_path / "click.mid", grid, clicks, "4/4", speed)
    sigs, notes = [], []
    for file, events in (("tempo.mid", sigs), ("click.mid", notes)):
        at = 0.
        for msg in mido.MidiFile(tmp_path / file):
            at += msg.time
            if file == "tempo.mid" and msg.type == "time_signature":
                events.append((msg.numerator, msg.denominator, at))
            if file == "click.mid" and msg.type == "note_on":
                events.append(at)
            if msg.type == "marker":
                assert at == pytest.approx(16 / speed, abs=.001)
    assert [(n, d) for n, d, _ in sigs] == [(4, 4), (3, 4), (6, 8)]
    assert [at for _, _, at in sigs] == pytest.approx([0., 10 / speed, 16 / speed], abs=.001)
    expected = np.r_[np.arange(-1, 9, .5), np.arange(9, 15, 2/3), np.arange(15, 19.3, 2/3)]
    assert notes == pytest.approx((expected + 1) / speed, abs=.001)
    accent, _ = render.load_samples(sr)
    for source in (3., 11., 15.):
        at = (source + 1) / speed
        assert find_onset(wav, accent, round(at * sr), int(.01 * sr)) / sr == pytest.approx(at, abs=1/sr)


def test_legacy_pause_times_and_undo_survive_new_song_map(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    from countin import gui
    from countin.core import analyze, songs

    app = QApplication.instance() or QApplication([])
    fmt, org, name = QSettings.defaultFormat(), app.organizationName(), app.applicationName()
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path))
    app.setOrganizationName("Count-In tests")
    app.setApplicationName("Tempo memory")
    monkeypatch.setattr(songs, "folder", lambda: tmp_path / "songs")
    legacy = tmp_path / "songs" / "song.json"
    legacy.parent.mkdir()
    legacy.write_text(json.dumps({"bpm": 120, "t0": 1, "speed": 85, "meter": "4/4", "bpm_manual": True,
                                  "zones": [{"first": 2, "last": 3, "beats": 3, "manual": True}],
                                  "dismissed": [1], "fill": False, "loop": [7., 9.], "markers": [[7., "verse"]]}))
    w = gui.Window()
    try:
        beats, downs = np.arange(1, 21, .5), np.arange(1, 21, 2.)
        analysis = analyze.Analysis(beats, downs, 120, 1, 1., 1., 1., 4)
        def reopen():
            t = w.current = gui.Track(Path("old.wav"), analysis, duration=21, key="song",
                                      bars=zones.bars_from(beats, downs, 21),
                                      sections=[tempo.Section(0, 120, "4/4", 1)])
            w._restore_song(t)
            w._regrid()
            w.undo_base = w._edit_state()
            return t
        t = reopen()
        assert zones.bounds(t.bars, t.zones[0]) == pytest.approx((5., 9.))
        assert t.dismissed_times == [(3., 5.)]
        w._split_section(7.)
        w._shift_section(1, .137)
        w._undo()
        assert len(t.sections) == 2 and t.sections[1].shift == 0 and t.t0 == 1.
        w._undo(redo=True)
        w._save_song()
        t = reopen()
        assert t.sections[1].shift == pytest.approx(.137)
        assert zones.bounds(t.bars, t.zones[0]) == pytest.approx((5., 9.))
        assert (w.speed, w.loop, t.markers, t.dismissed_times) == (85, (7., 9.), [(7., "verse")], [(3., 5.)])
    finally:
        w.close()
        app.setOrganizationName(org)
        app.setApplicationName(name)
        QSettings.setDefaultFormat(fmt)

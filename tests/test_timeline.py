from types import SimpleNamespace

import mido
import numpy as np
import pytest
import soundfile as sf

from countin import gui
from countin.core import analyze, daw, songs, tempo, timeline, zones


def test_play_after_waveform_seek_reuses_ready_audio(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QPoint, QSettings, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    fmt, org, name = QSettings.defaultFormat(), app.organizationName(), app.applicationName()
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path))
    app.setOrganizationName("Count-In tests")
    app.setApplicationName("Seek playback")
    w = gui.Window()
    try:
        w.resize(1150, 750)
        w.show()
        w.stack.setCurrentIndex(1)
        w.fill.setChecked(False)
        w.preroll_cb.setChecked(False)
        beats = np.arange(0., 8., .5)
        analysis = analyze.Analysis(beats, beats[::4], 120., 0., 1., 1., 1., 4)
        w.current = gui.Track(tmp_path / "song.wav", analysis, duration=8.,
                              sections=[tempo.Section(0., 120., "4/4", 0.)])
        w.sr = 8000
        w.pcm = np.zeros((8 * w.sr, 2), np.float32)
        w.wave.set_env(np.ones(8000), 8.)
        w.wave.show_range(0., 8.)
        w._regrid()
        w._show_track_values()

        played = []

        def open_audio(base, stems, sr, start=0):
            # Use the real render and transport, with no physical audio device in tests.
            w.player.src, w.player.sr, w.player.pos = (base, stems), sr, start
            w.player.done = False
            played.append(base)

        monkeypatch.setattr(w.player, "play", open_audio)

        def play_from(at):
            x = round(w.wave.x_of(at))
            QTest.mouseClick(w.wave, Qt.LeftButton, pos=QPoint(x, 110))
            QTest.keyClick(w.wave, Qt.Key_Space)

        play_from(1.)
        for _ in range(300):
            if w.job is None:
                break
            QTest.qWait(10)
        assert w.play_start is not None and len(played) == 1
        for at in (2.5, 5.):
            QTest.keyClick(w.wave, Qt.Key_Space)
            assert w.play_start is None
            play_from(at)
            assert w.job is None and played[-1] is played[0]
            assert w.ready_preview.source_at(w.player.pos / w.sr) == pytest.approx(at, abs=.02)
        assert len(played) == 3
    finally:
        w.close()
        app.setOrganizationName(org)
        app.setApplicationName(name)
        QSettings.setDefaultFormat(fmt)


def test_pause_click_and_metronome_do_not_double_and_can_be_switched_separately(tmp_path):
    bars = [zones.Bar(float(a), a + 2.0, [a, a + .5, a + 1, a + 1.5], "4/4", pulse=.5) for a in (0, 2, 4)]
    track = gui.Track(tmp_path / "song.wav", bars=bars, sections=[tempo.Section(0, 120, "4/4", 0)],
                      zones=[zones.Zone(1, 1)])
    settings = gui.Settings("buildup", 1, "4/4", None, 0, click_all=True, click_voice="cowbell")
    both = gui.clicks_for(track, settings)
    assert [(c.time, c.voice) for c in both if 2 <= c.time < 4] == [(x, "hihat") for x in bars[1].beats]
    settings.fill = False
    no_fill = gui.clicks_for(track, settings)
    assert [(c.time, c.voice) for c in no_fill if 2 <= c.time < 4] == [(x, "cowbell") for x in bars[1].beats]
    assert [c for c in both if c.time < 0] == [c for c in no_fill if c.time < 0]


def test_pause_count_in_uses_next_tempo_and_meter_in_preview_and_export(tmp_path):
    sr = 16000
    source = tmp_path / "song.wav"
    pcm = np.full((6 * sr, 2), .03, dtype=np.float32)
    sf.write(source, pcm, sr, subtype="FLOAT")
    bars = [zones.Bar(a, a + 2, [a, a + .5, a + 1, a + 1.5], "4/4", pulse=.5) for a in (0., 2.)]
    bars += [zones.Bar(a, min(a + 1.5, 6.), [a, a + .75] if a + .75 < 6 else [a], "6/8", pulse=.75)
             for a in (4., 5.5)]
    beats = np.array([b for bar in bars for b in bar.beats])
    analysis = analyze.Analysis(beats, np.array([bar.start for bar in bars]), 120., 0., 1., 1., 1., 4)
    zone = zones.Zone(1, 1, count_in=True)
    track = gui.Track(source, analysis, duration=6., bars=bars, zones=[zone], zones_key=(),
                      sections=[tempo.Section(0., 120., "4/4", 0.), tempo.Section(4., 80., "6/8", 4.)])
    settings = gui.Settings("even", 1, "4/4", None, 0, click_all=True, click_voice="cowbell")
    expected = [(2., "hihat"), *((t, "stick") for t in np.arange(2.5, 4., .25))]

    clicks = gui.song_clicks_for(track, settings)
    assert [(c.time, c.voice) for c in clicks if 2. <= c.time < 4.] == expected
    assert [(c.time, c.accent) for c in clicks if 2.5 <= c.time < 4.] == [(t, t == 2.5) for t in np.arange(2.5, 4., .25)]
    assert [(c.time, c.voice) for c in clicks if 4. <= c.time < 5.5] == [(4., "cowbell"), (4.75, "cowbell")]

    job = SimpleNamespace(report=lambda *_: None)
    _, base, stems, _, _, _, _, state = gui.preview_job(job, track, settings, pcm, sr, None, False)
    waveform, _ = stems["click"]
    offset = round(state.seconds_at(0) * sr)
    assert len(base) - offset == len(pcm)
    assert np.allclose((base - waveform)[offset:], pcm, atol=1e-6)
    assert all(np.any(waveform[i:i + sr // 20]) for i in (round(state.seconds_at(c) * sr) for c, _ in expected))

    _, saved, _, _ = gui.save_job(job, track, settings, None)
    backing, _ = sf.read(saved)
    assert len(backing) == len(base)
    assert all(np.any(np.abs(backing[i:i + sr // 20] - .03) > .005)
               for i in (round(state.seconds_at(c) * sr) for c, _ in expected))

    _, folder, _, _ = gui.export_job(job, track, settings, None,
                                     {"parts": False, "clickwav": True, "click": True, "tempo": True})
    elapsed, notes = 0., []
    for msg in mido.MidiFile(folder / "click.mid"):
        elapsed += msg.time
        if msg.type == "note_on" and msg.velocity and 4. - .001 <= elapsed < 6. - .001:
            notes.append((elapsed, msg.note))
    assert [at for at, _ in notes] == pytest.approx([4., 4.5, 4.75, 5., 5.25, 5.5, 5.75], abs=.002)
    assert [note for _, note in notes] == [42, 76, 77, 77, 77, 77, 77]

    zone.start = 3.55
    short = gui.song_clicks_for(track, settings)
    assert [(c.time, c.voice) for c in short if 3.55 <= c.time < 4.] == [(3.75, "stick")]
    zone.start = 2.
    clock = timeline.Timeline(6., 1., (timeline.Edit(4., 6., .75),))
    retimed = [c.time for c in gui.edited_clicks(track, settings, clock) if 2. <= c.time < 4. and c.voice == "stick"]
    assert retimed == pytest.approx([2. + n / 3 for n in range(6)])


def test_cuts_and_section_speed_align_playback_clicks_and_exports(tmp_path):
    sr = 16000
    source = tmp_path / "song.wav"
    pcm = (.15 * np.sin(2 * np.pi * 220 * np.arange(8 * sr) / sr)).astype(np.float32)
    pcm = np.column_stack([pcm, pcm])
    sf.write(source, pcm, sr, subtype="FLOAT")
    original = source.read_bytes()
    beats = np.arange(1., 8., .5)
    bars = [zones.Bar(a, min(a + 2, 8), beats[(a <= beats) & (beats < a + 2)].tolist(), "4/4",
                      pulse=.5) for a in (1., 3., 5., 7.)]
    analysis = analyze.Analysis(beats, np.arange(1., 8., 2.), 120, 1, 1, 1, 1, 4)
    track = gui.Track(source, analysis, t0=1., duration=8., bars=bars,
                      sections=[tempo.Section(0, 120, "4/4", 1.)],
                      edits=(timeline.Edit(0., 1.), timeline.Edit(3., 3.6), timeline.Edit(4., 5.8, .9),
                             timeline.Edit(7.5, 8.)))
    settings = gui.Settings("even", 1, "4/4", None, 0, fill=False, click_all=True, fmt="wav")
    job = SimpleNamespace(report=lambda *_: None)

    _, base, stems, _, _, _, _, state = gui.preview_job(job, track, settings, pcm, sr, None, False)
    assert len(base) / sr == pytest.approx(8.1, abs=1 / sr)
    assert state.seconds_at(3.25) == pytest.approx(4., abs=1 / sr)
    assert state.source_at(4. + 1 / sr) == pytest.approx(3.6 + 1 / sr)
    assert state.seconds_at(5.) - state.seconds_at(4.) == pytest.approx(1 / .9)
    assert state.seconds_at(7.) - state.seconds_at(6.) == pytest.approx(1.)
    click, offset = stems["click"]
    assert offset == 0 and np.any(click[:sr // 20])
    sample = round(state.seconds_at(5.) * sr)
    assert np.any(click[sample:sample + sr // 20])
    audio = base[:, 0] - click[:, 0]
    eased = audio[sample:sample + sr // 2]
    freqs = np.fft.rfftfreq(len(eased), 1 / sr)
    assert freqs[np.argmax(np.abs(np.fft.rfft(eased)))] == pytest.approx(220, abs=2)

    _, saved, _, _ = gui.save_job(job, track, settings, None)
    assert sf.info(saved).frames == len(base)
    assert source.read_bytes() == original

    _, folder, _, _ = gui.export_job(job, track, settings, None,
                                     {"parts": False, "clickwav": True, "click": True, "tempo": True})
    midi = mido.MidiFile(folder / "click.mid")
    click_wav, _ = sf.read(folder / "click.wav")
    elapsed, positions = 0., []
    for message in midi:
        elapsed += message.time
        if message.type == "note_on" and message.velocity:
            positions.append(elapsed)
    assert len(positions) == 4 + 14 - 3  # four count-in hits, three song hits removed by cuts
    assert positions[0] == pytest.approx(1.0, abs=.001)  # DAW origin stays on the preceding whole bar
    assert positions[-2] - positions[-3] == pytest.approx(.5, abs=.002)
    assert any(delta == pytest.approx(.5 / .9, abs=.002) for delta in np.diff(positions))
    for at in positions:
        sample = round(at * sr)
        assert np.any(click_wav[max(0, sample - 32):sample + sr // 20])


def test_daw_export_keeps_bar_origin_and_preserves_previous_export(tmp_path, monkeypatch):
    sr = 16000
    source = tmp_path / "Click.wav"
    sf.write(source, np.full((8 * sr, 2), .1, dtype=np.float32), sr)
    beats = np.arange(3., 8., .5)
    bars = [zones.Bar(a, min(a + 2, 8), beats[(a <= beats) & (beats < a + 2)].tolist(),
                      "4/4", pulse=.5) for a in (3., 5., 7.)]
    analysis = analyze.Analysis(beats, np.array([3., 5., 7.]), 120, 3, 1, 1, 1, 4)
    track = gui.Track(source, analysis, t0=3., duration=8., bars=bars,
                      sections=[tempo.Section(0., 120, "4/4", 3.)], markers=[(3., "Припев")])
    settings = gui.Settings("even", 1, "4/4", None, 0, fill=False, fmt="wav")
    job = SimpleNamespace(report=lambda *_: None)
    options = {"parts": False, "clickwav": True, "click": True, "tempo": True}

    def check_export(duration):
        _, folder, _, _ = gui.export_job(job, track, settings, None, options)
        midi = mido.MidiFile(folder / "tempo.mid", charset="utf-8")
        marker, = (msg for msg in midi.tracks[1] if msg.type == "marker")
        assert (marker.text, marker.time) == ("Припев", 3840)
        audio = sf.info(folder / "Click_backing.wav")
        assert audio.channels == 2 and audio.frames == duration * sr
        assert sf.info(folder / "click.wav").channels == 1
        return folder

    folder = check_export(9)
    note = folder / "notes.txt"
    note.write_text("mix notes")
    track.edits = (timeline.Edit(6., 7.),)
    assert check_export(8) == folder
    assert note.read_text() == "mix notes"
    prior = {p.name: p.read_bytes() for p in folder.iterdir()}

    def fail(*args, **kwargs):
        raise OSError("tempo write failed")

    monkeypatch.setattr(daw, "write_tempo", fail)
    track.edits = ()
    with pytest.raises(OSError, match="tempo write failed"):
        gui.export_job(job, track, settings, None, options)
    assert {p.name: p.read_bytes() for p in folder.iterdir()} == prior


def test_selection_export_uses_its_meter_and_effective_speed(tmp_path):
    sr = 16000
    source = tmp_path / "source.wav"
    before, selected = np.full((2 * sr, 2), .04, np.float32), np.full((2 * sr, 2), .15, np.float32)
    sf.write(source, np.concatenate([before, selected, before]), sr, subtype="FLOAT")
    track = gui.Track(source, duration=6., sections=[tempo.Section(0., 120, "4/4", 0.),
                      tempo.Section(2., 80, "6/8", 2.)], edits=(timeline.Edit(2., 4., .70),))
    settings = gui.Settings("even", 1, "4/4", None, 0, fill=False, fmt="wav", training=(2., 4., 1))
    assert gui.edit_clock(track, settings).rate_at(2.) == pytest.approx(.75)
    assert track.edits[0].speed == .70
    job = SimpleNamespace(report=lambda *_: None)
    out = tmp_path / "selection.wav"
    _, saved, _, _ = gui.save_job(job, track, settings, None, (2., 4.), out)
    audio, _ = sf.read(saved)
    assert len(audio) == round((2 + 2 / .75) * sr)
    assert np.max(np.abs(audio[int(sr * .52):int(sr * .62)])) == 0
    assert np.max(np.abs(audio[sr:sr + sr // 10])) > .01
    assert np.max(np.abs(audio[3 * sr:4 * sr])) > .1
    with pytest.raises(ValueError):
        gui.save_job(job, track, settings, None, (2., 4.), source)
    assert sf.info(source).frames == 6 * sr


def test_cut_fades_each_stem_at_the_join_without_changing_length():
    sr = 16000
    pcm = np.array([[.8, .4]] * sr + [[0., 0.]] * sr + [[-.8, -.4]] * sr, dtype=np.float32)
    cuts = (timeline.Edit(1., 2.),)
    hard, = timeline.Timeline(3., 1., cuts, 0).audio([pcm], sr, 0., 3., 0.)
    smoothed, stem = timeline.Timeline(3., 1., cuts, 5).audio([pcm, pcm / 2], sr, 0., 3., 0.)
    assert hard.shape == smoothed.shape == (2 * sr, 2)
    np.testing.assert_array_equal(smoothed[sr - 1:sr + 1], 0)
    np.testing.assert_allclose(stem * 2, smoothed)
    np.testing.assert_allclose(smoothed[[0, -1]], hard[[0, -1]])
    assert pcm[sr - 1, 0] == np.float32(.8)


def test_new_dev_song_edits_survive_an_older_dev_write(tmp_path, monkeypatch):
    folder = tmp_path / "songs-editor-dev"
    monkeypatch.setattr(songs, "folder", lambda: folder)
    legacy = tmp_path / "songs-tempo-dev"
    legacy.mkdir()
    old_file = legacy / "test.json"
    old_file.write_text('{"bpm": 100}')
    assert songs.load("test") == {"bpm": 100}
    new = {"bpm": 120, "saved_loops": [{"name": "Solo", "start": 2, "end": 3, "speed": .8}]}
    songs.save("test", new)
    old_file.write_text('{"bpm": 150}')
    assert songs.load("test") == new

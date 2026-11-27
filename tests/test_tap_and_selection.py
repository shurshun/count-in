from types import SimpleNamespace

import numpy as np
import pytest
from PySide6.QtCore import QEvent, QPoint, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from countin import gui
from countin.core import analyze, tempo


@pytest.fixture
def qt_app(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    fmt, org, name = QSettings.defaultFormat(), app.organizationName(), app.applicationName()
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path))
    app.setOrganizationName("Count-In tests")
    app.setApplicationName("Tap and selection")
    yield app
    # Destroy the widgets and their timers while the application is still alive.
    app.sendPostedEvents(None, QEvent.DeferredDelete)
    app.setOrganizationName(org)
    app.setApplicationName(name)
    QSettings.setDefaultFormat(fmt)


def test_tap_tempo_is_silent_until_the_automatic_click(qt_app, monkeypatch):
    def audio_sink(player, base, stems, sr):
        player.src, player.sr = (base, stems), sr

    monkeypatch.setattr(gui.Player, "play", audio_sink)
    sr = 8000
    d = gui.TapDialog(None, np.zeros((10400, 2), np.float32), sr, 0., 1.3, 120., 1.)
    try:
        d.hit = np.array([.5], dtype=np.float32)
        # Simulate audio running while the taps arrive, including a loop wrap while the click is playing.
        def render(start, end):
            out = np.zeros((round(end * sr) - round(start * sr), 2), np.float32)
            d.player._fill(out, len(out), None, SimpleNamespace(output_underflow=False))
            return np.flatnonzero(out[:, 0]) + round(start * sr)

        previous = 0.
        for stamp in (.1, .6, 1.1):
            assert not render(previous, stamp).size
            d._now = lambda stamp=stamp: stamp
            QTest.keyClick(d.apply_b, Qt.Key_Space)
            previous = stamp
        assert d.valid and d.bpm == pytest.approx(120.)
        assert render(1.1, 3.4).tolist() == [round(t * sr) for t in (2.1, 2.6, 3.1)]
        assert d.player.laps == 2
    finally:
        d.reject()
        d.deleteLater()


def test_plain_wave_drag_scrolls_without_moving_the_start(qt_app, tmp_path):
    w = gui.Window()
    try:
        w.resize(1200, 800)
        w.show()
        w.stack.setCurrentIndex(1)
        beats = np.arange(0., 45., .5)
        analysis = analyze.Analysis(beats, beats[::4], 120., 0., 1., 1., 1., 4)
        w.current = gui.Track(tmp_path / "song.wav", analysis, duration=45.,
                              sections=[tempo.Section(0., 120., "4/4", 0.)])
        w.sr = 1000
        w.pcm = np.zeros((45000, 2), np.float32)
        w.wave.set_env(np.ones(45000), 45.)
        w._regrid()
        w._enable()
        wave = w.wave
        wave.show_range(34., 44.)
        QTest.qWait(30)
        start = QPoint(round(wave.x_of(36.)), 110)
        edge = QPoint(wave.width() - 1, 110)
        QTest.mousePress(wave, Qt.LeftButton, pos=start)
        QTest.mouseMove(wave, edge)
        QTest.qWait(220)
        QTest.mouseRelease(wave, Qt.LeftButton, pos=edge)
        assert wave.v1 > 45 and wave.sel == pytest.approx((36., 45.))

        QTest.mouseClick(wave, Qt.LeftButton, pos=QPoint(round(wave.x_of(42.)), 110))
        assert wave.play_t == pytest.approx(42.)
        w.select_end_a.trigger()
        assert wave.sel == pytest.approx((42., 45.))
    finally:
        w.close()
        w.deleteLater()

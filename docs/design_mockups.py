"""Renders the design variants of the editor (progress, navigation, tempo card) as PNG mockups.

    COUNTIN_ASSETS=models uv run python docs/design_mockups.py SONG OUT_DIR [name ...]

Background separation and audio output stay off; every state is staged by hand. Settings live in a
temporary folder.
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_SCALE_FACTOR", "2")

from PySide6.QtCore import QSettings, QTimer  # noqa: E402
from PySide6.QtGui import QColor, QPalette  # noqa: E402

from countin import gui, i18n  # noqa: E402
from countin.i18n import _  # noqa: E402

gui.Window._start_separation = lambda self: None
gui.warm_models = lambda: None


def stage_progress(w, kind):
    """kind: analysis, separation, docked or job."""
    if kind == "analysis":
        w.wave.env = None
        w.wave.beats, w.wave.downs, w.wave.clicks, w.wave.zones, w.wave.t0 = [], [], [], [], None
        w.wave._cache = (None, None)
        w.detector.setText(_("Анализ…"))
        w.warning.setText("")
        w.show_busy(_("Анализирую трек"), _(gui.BUSY_WHY["analysis"]), None, _("Пожалуйста, подождите…"))
    elif kind == "job":
        w.job = object()
        w.prog_why.setText(_(gui.JOB_WHY["preview_job"]))
        w._stage = None
        w._progress(_("разделение"), 44, 100)
        w.prog_eta.setText("44% · " + _("осталось ~{m}:{s:02d}", m=0, s=24))
        w.prog_phrase.setText("♪  " + _(gui.PHRASES[0]))
        w._enable()
    else:
        if gui.variant("PROGRESS") == "C":  # the progress runs along the waveform, so show the whole song
            w.wave.show_range(-5.0, w.wave.duration + 2.0)
        w.show_busy(_("Разделяю на дорожки"), _(gui.BUSY_WHY["separation"]), 0.44,
                    _("осталось ~{m}:{s:02d}", m=0, s=24), docked=kind == "docked")


def stage_nav(w):
    wave = w.wave
    if len(wave.zones) < 3:
        n = len(wave.bars)
        wave.zones = []
        wave.add_zone(n // 4, 4)
        wave.add_zone(n // 2, 2)
        wave.add_zone(3 * n // 4, 3)
    i = 1
    z = wave.zones[i]
    a, b = wave.bars[z.first].start, wave.bars[z.last].end
    wave.show_range(a - 8, b + 10)
    wave.current_zone = i
    wave.hover_edge = (i, "end")
    wave.play_t = wave.bars[max(z.first - 1, 0)].start
    w._nav_changed()


# name -> (variants, scheme, stage)
SHOTS = {"0-current-light": ({}, "light", ())}
for v in "ABC":
    SHOTS[f"1-progress-{v}-analysis-light"] = ({"PROGRESS": v}, "light", ("analysis",))
    SHOTS[f"1-progress-{v}-separation-light"] = ({"PROGRESS": v}, "light", ("separation",))
    SHOTS[f"2-transport-{v}-light"] = ({"NAV": v}, "light", ("nav",))
    SHOTS[f"3-tempo-{v}-light"] = ({"TEMPO": v, "NAV": "A" if v == "C" else ""}, "light", ())
SHOTS["1-progress-B-docked-light"] = ({"PROGRESS": "B"}, "light", ("docked",))
SHOTS["1-progress-A-job-light"] = ({"PROGRESS": "A"}, "light", ("job",))
REC = {"PROGRESS": "B", "NAV": "A", "TEMPO": "A"}
SHOTS["1-progress-B-analysis-dark"] = ({"PROGRESS": "B"}, "dark", ("analysis",))
SHOTS["1-progress-B-separation-dark"] = ({"PROGRESS": "B"}, "dark", ("separation",))
SHOTS["1-progress-B-docked-dark"] = ({"PROGRESS": "B"}, "dark", ("docked",))
SHOTS["2-transport-A-dark"] = ({"NAV": "A"}, "dark", ("nav",))
SHOTS["3-tempo-A-dark"] = ({"TEMPO": "A"}, "dark", ())
SHOTS["4-recommended-all-light"] = (REC, "light", ("nav", "docked"))
SHOTS["4-recommended-all-dark"] = (REC, "dark", ("nav", "docked"))


def main() -> int:
    song, out = Path(sys.argv[1]), Path(sys.argv[2])
    todo = sys.argv[3:] or list(SHOTS)
    out.mkdir(parents=True, exist_ok=True)
    i18n.set_language("ru")
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, tempfile.mkdtemp())
    app = gui.App([])
    app.setOrganizationName("Count-In")
    app.setApplicationName("Count-In")
    app.setQuitOnLastWindowClosed(False)
    font = app.font()
    font.setPointSizeF(font.pointSizeF() + 1.5)
    app.setFont(font)
    state = {"w": None, "n": 0}
    light, native = QPalette(app.palette()), app.style().objectName()

    def tick():
        w = state["w"]
        if w is None:
            if not todo:
                return app.quit()
            variants, scheme, _stage = SHOTS[todo[0]]
            for k in ("PROGRESS", "NAV", "TEMPO"):
                os.environ["COUNTIN_" + k] = variants.get(k, "")
            # The offscreen platform has no dark appearance, so dark shots get a dark palette by hand.
            dark = scheme == "dark"
            gui.is_dark = lambda _app: dark
            pal = QPalette(light)
            if dark:
                for role, c in ((QPalette.Window, "#1e1e20"), (QPalette.Base, "#2c2c2e"), (QPalette.Button, "#3a3a3c"),
                                (QPalette.AlternateBase, "#2c2c2e"), (QPalette.WindowText, "#f5f5f7"),
                                (QPalette.Text, "#f5f5f7"), (QPalette.ButtonText, "#f5f5f7"),
                                (QPalette.Highlight, "#0a84ff"), (QPalette.HighlightedText, "#ffffff"),
                                (QPalette.PlaceholderText, "#8e8e93"), (QPalette.Mid, "#48484a"),
                                (QPalette.Dark, "#48484a"), (QPalette.Light, "#48484a")):
                    pal.setColor(role, QColor(c))
            app.setStyle("Fusion" if dark else native)
            app.setPalette(pal)
            app.setStyleSheet(gui.style_sheet(app))
            w = state["w"] = gui.Window()
            w.resize(1280, 900)
            w.show()
            w.open_files([song])
            state["n"] = 0
        elif w.pcm is not None and state["n"] == 0:
            stage = SHOTS[todo[0]][2]
            w.alt_b.setVisible(True)
            if "nav" in stage:
                stage_nav(w)
            for kind in ("analysis", "separation", "docked", "job"):
                if kind in stage:
                    stage_progress(w, kind)
            state["n"] = 1
        elif state["n"] == 1:
            w.setFocus()
            app.processEvents()
            w.grab().save(str(out / f"{todo[0]}.png"))
            print(todo.pop(0), flush=True)
            w.job = None
            w.close()
            w.deleteLater()
            state["w"] = None

    timer = QTimer(app, interval=250, timeout=tick)
    timer.start()
    QTimer.singleShot(600_000, app.quit)
    app.exec()
    return 0


if __name__ == "__main__":
    sys.exit(main())

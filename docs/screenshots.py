"""Regenerates docs/screenshots from the running app.

    uv run python docs/screenshots.py SONG

SONG is any track with a clear beat. A copy with neutral tags ("Demo Artist", "Demo Album") goes on screen,
so no real names end up in the repository. Settings live in a temporary folder; the user's are untouched.
"""

import subprocess
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QPoint, QRectF, QSettings, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter

from countin import gui, i18n
from countin.core import io, songs
from countin.i18n import _

OUT = Path(__file__).resolve().parent / "screenshots"
GUIDE = Path(__file__).resolve().parent / "guide" / "img"  # shots for the user guide page


def card_of(widget):
    """The card frame a control sits in."""
    while widget is not None and widget.objectName() != "card":
        widget = widget.parentWidget()
    return widget


def stage_pauses(w):
    """Three pauses on the demo track (it has none at the default levels), the middle one selected."""
    wave = w.wave
    n = len(wave.bars)
    wave.zones = []
    for bar, length in ((n // 4, 4), (n // 2, 2), (3 * n // 4, 3)):
        wave.add_zone(bar, length)
    z = wave.zones[1]
    a, b = wave.bars[z.first].start, wave.bars[z.last].end
    wave.show_range(a - 8, b + 10)
    wave.current_zone = 1
    wave.play_t = wave.bars[max(z.first - 1, 0)].start
    w._nav_changed()
    w.wave.setFocus()  # no text cursor blinking in the tempo field


def neutral_copy(src: Path, dst: Path) -> Path:
    """Copy of src with its tags replaced; audio and cover are copied as they are."""
    subprocess.run([io.tool("ffmpeg"), "-v", "error", "-y", "-i", str(src), "-map", "0:a", "-c", "copy",
                    "-map_metadata", "-1", "-metadata", "artist=Demo Artist", "-metadata", "album=Demo Album",
                    "-metadata", "date=2024", "-metadata", "title=Demo Song", str(dst)], check=True)
    return dst


def social(shot: QImage, font: QFont, lang: str) -> None:
    """1280x640 repository preview: name, tagline, a large crop of the window."""
    img = QImage(1280, 640, QImage.Format_ARGB32)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    g = QLinearGradient(0, 0, 1280, 640)
    g.setColorAt(0, QColor(251, 251, 253))
    g.setColorAt(1, QColor(232, 236, 244))
    p.fillRect(img.rect(), g)
    f = QFont(font)
    f.setPixelSize(64)
    f.setWeight(QFont.Bold)
    p.setFont(f)
    p.setPen(QColor(29, 29, 31))
    p.drawText(QRectF(64, 48, 1100, 80), Qt.AlignLeft, "Count-In")
    f.setPixelSize(28)
    f.setWeight(QFont.Normal)
    p.setFont(f)
    p.setPen(QColor(110, 110, 115))
    p.drawText(QRectF(66, 130, 1150, 40), Qt.AlignLeft,
               "Drum count-in before the first downbeat · backing tracks without guitar or vocals" if lang == "en" else
               "Отсчёт перед первой долей · минусовки без гитары и вокала")
    sc = shot.scaledToWidth(1060, Qt.SmoothTransformation)
    sc.setDevicePixelRatio(1.0)
    p.drawImage(160, 200, sc)
    p.end()
    img.save(str(OUT / ("social-preview.png" if lang == "en" else "social-preview-ru.png")))


def run(song: Path, lang: str, tmp: Path) -> None:
    """Light-theme shots of one language; Russian files have no suffix, English ones end in -en."""
    i18n.set_language(lang)
    sfx = "" if lang == "ru" else "-en"
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp / "settings"))
    app = gui.App.instance() or gui.App([])
    app.setOrganizationName("Count-In")
    app.setApplicationName("Count-In")
    app.styleHints().setColorScheme(Qt.ColorScheme.Light)
    app.setStyleSheet(gui.style_sheet(app))
    gui.apply_icon(app)

    st = QSettings()
    st.setValue(gui.OUT_DIR, "~/Music/Count-In")  # shown in the settings shot; never a real home path
    st.setValue(gui.OUT_GROUP, True)

    # Each run starts from a song nobody has opened, with the settings of a fresh install
    songs.folder = lambda: tmp / f"songs-light{sfx}"
    gui.cloud_key = lambda: False

    w = gui.Window()
    w.models_warm = True
    w._start_separation = lambda: None
    w._auto_zones = lambda: None
    w.resize(1230, 920)
    w.show()
    w.open_files([song])
    step = {"n": 0}

    def tick():
        if step["n"] == 0 and w.pcm is not None:
            w._cancel_separation()
            track = w.current
            change = next((bar.start for bar in track.bars if bar.start >= track.t0 + 3.7), None)
            if change is not None and change < track.t0 + 6:
                w._set_map(gui.tempo.edit_span(track.sections, change, track.duration, track.duration,
                                               bpm=round(track.bpm + 6), meter="3/4", grid=track.bars))
            w.vocals.setValue(gui.MUTE)
            w.guitar.setValue(gui.MUTE)
            w._set_speed(80)  # the speed control shows a slowed song, as in the promo
            w.zone_timer.stop()  # no pause search: it would cover the shots with its progress
            w.status.setText(_("Сохранено: {name}", name="Demo Song_backing_guitar-off_vocals-off_80pct.mp3"))
            w.reveal_b.show()
            step["n"] = 1
        elif step["n"] == 1:
            step["n"] = 2  # processEvents below runs this timer again; the main shot must not be retaken
            shot = w.grab().toImage()
            shot.save(str(OUT / f"main-light{sfx}.png"))
            social(shot, app.font(), lang)
            w.reveal_b.hide()
            w.status.setText("")
            for name, guitar, vocals, speed in (("promo", 0, 0, 100), ("guitar", gui.MUTE, 0, 80),
                                                ("vocals", 0, gui.MUTE, 80)):
                w.guitar.setValue(guitar)
                w.vocals.setValue(vocals)
                w._set_speed(speed)
                w.zone_timer.stop()
                app.processEvents()
                w.grab().save(str(OUT / f"main-light-{name}{sfx}.png"))
            widgets = (w.lane, w.style_seg, w.click_voice, w.fill, w.guitar, w.speed_l, w.vocals)
            centers = [widget.mapTo(w, widget.rect().center()) for widget in widgets]
            centers[0] = w.lane.mapTo(w, w.lane._badge(0)[0].center().toPoint())
            marker = w.wave.mapTo(w, QPoint(round(w.wave.x_of(w.current.t0)), 24))
            actual_click = min(w.wave.clicks, key=lambda c: c.time)
            click = w.wave.mapTo(w, QPoint(round(w.wave.x_of(actual_click.time)), 42))
            print(lang, shot.width(), shot.height(),
                  [(round(pt.x() / w.width(), 3), round(pt.y() / w.height(), 3))
                   for pt in (*centers, marker, click)])
            w.vocals.setValue(gui.MUTE)
            w.guitar.setValue(gui.MUTE)
            w.zone_timer.stop()
            d = gui.PrefsDialog(w)
            d.show()
            app.processEvents()
            d.grab().save(str(OUT / f"prefs-light{sfx}.png"))
            d.close()
            w.reveal_b.hide()
            w.status.setText("")

            # Background separation folded into the capsule, with the tips card beside it
            w.show_busy(_("Разделяю на дорожки"), _(gui.BUSY_WHY["separation"]), 0.44,
                        _("осталось ~{m}:{s:02d}", m=1, s=24), docked=True)
            w.tip_timer.stop()
            w.wave.tips.set(_(gui.TIPS[5]), True)
            w.wave._place_overlay()
            app.processEvents()
            w.grab().save(str(OUT / f"progress-light{sfx}.png"))
            w.hide_busy()

            # The user guide: the window with a selected pause, then the cards one by one
            GUIDE.mkdir(parents=True, exist_ok=True)
            stage_pauses(w)
            app.processEvents()
            w.grab().save(str(GUIDE / f"window{sfx}.png"))
            w.wave.grab().save(str(GUIDE / f"wave{sfx}.png"))
            w.show_busy(_("Разделяю на дорожки"), _(gui.BUSY_WHY["separation"]), 0.44,
                        _("осталось ~{m}:{s:02d}", m=1, s=24), docked=True)
            w.tip_timer.stop()
            w.wave.tips.set(_(gui.TIPS[8]), True)
            w.wave._place_overlay()
            app.processEvents()
            w.wave.grab().save(str(GUIDE / f"busy{sfx}.png"))
            w.hide_busy()
            for name, widget in (("tempo", w.bpm_spin), ("count", w.style_seg), ("backing", w.guitar)):
                card_of(widget).grab().save(str(GUIDE / f"{name}{sfx}.png"))
            w.close()
            app.quit()

    timer = QTimer(app, interval=300, timeout=tick)
    timer.start()
    QTimer.singleShot(120_000, app.quit)
    app.exec()
    timer.stop()


def main() -> int:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        song = neutral_copy(Path(sys.argv[1]), tmp / f"Demo Song{Path(sys.argv[1]).suffix}")
        for lang in ("ru", "en"):
            run(song, lang, tmp)
    print(f"written to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

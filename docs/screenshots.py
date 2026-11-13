"""Regenerates docs/screenshots from the running app.

    uv run python docs/screenshots.py SONG

SONG is any track with a clear beat. A copy with neutral tags ("Demo Artist", "Demo Album") goes on screen,
so no real names end up in the repository. Settings live in a temporary folder; the user's are untouched.
"""

import subprocess
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QRectF, QSettings, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter

from countin import gui
from countin.core import io

OUT = Path(__file__).resolve().parent / "screenshots"


def neutral_copy(src: Path, dst: Path) -> Path:
    """Copy of src with its tags replaced; audio and cover are copied as they are."""
    subprocess.run([io.tool("ffmpeg"), "-v", "error", "-y", "-i", str(src), "-map", "0:a", "-c", "copy",
                    "-map_metadata", "-1", "-metadata", "artist=Demo Artist", "-metadata", "album=Demo Album",
                    "-metadata", "date=2024", "-metadata", "title=Demo Song", str(dst)], check=True)
    return dst


def social(shot: QImage, font: QFont) -> None:
    """1280x640 repository preview: name, tagline, a large crop of the window."""
    img = QImage(1280, 640, QImage.Format_ARGB32)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    g = QLinearGradient(0, 0, 1280, 640)
    g.setColorAt(0, QColor(28, 28, 32))
    g.setColorAt(1, QColor(18, 30, 52))
    p.fillRect(img.rect(), g)
    f = QFont(font)
    f.setPixelSize(64)
    f.setWeight(QFont.Bold)
    p.setFont(f)
    p.setPen(QColor(255, 255, 255))
    p.drawText(QRectF(64, 48, 1100, 80), Qt.AlignLeft, "Count-In")
    f.setPixelSize(28)
    f.setWeight(QFont.Normal)
    p.setFont(f)
    p.setPen(QColor(200, 205, 215))
    p.drawText(QRectF(66, 130, 1150, 40), Qt.AlignLeft,
               "Drum count-in before the first downbeat · backing tracks without guitar or vocals")
    sc = shot.scaledToWidth(1060, Qt.SmoothTransformation)
    sc.setDevicePixelRatio(1.0)
    p.drawImage(160, 200, sc)
    p.end()
    img.save(str(OUT / "social-preview.png"))


def run(song: Path, scheme: str, tmp: Path) -> None:
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp / "settings"))
    app = gui.App.instance() or gui.App([])
    app.setOrganizationName("Count-In")
    app.setApplicationName("Count-In")
    app.styleHints().setColorScheme(Qt.ColorScheme.Light if scheme == "light" else Qt.ColorScheme.Dark)
    f = app.font()
    f.setPointSizeF(f.pointSizeF() + 1.5)
    app.setFont(f)
    app.setStyleSheet(gui.style_sheet(app))
    gui.apply_icon(app)

    st = QSettings()
    st.setValue(gui.OUT_DIR, str(Path.home() / "Music" / "Count-In"))
    st.setValue(gui.OUT_GROUP, True)

    w = gui.Window()
    w.resize(1180, 800)
    w.show()
    w.open_files([song])
    step = {"n": 0}

    def tick():
        if step["n"] == 0 and w.pcm is not None:
            w.vocals.setValue(-6)
            w.guitar.setValue(gui.MUTE)
            w.status.setText("Сохранено: Demo Song_backing_guitar-off_vocals-6dB.mp3")
            w.reveal_b.show()
            step["n"] = 1
        elif step["n"] == 1:
            shot = w.grab().toImage()
            shot.save(str(OUT / f"main-{scheme}.png"))
            if scheme == "dark":
                social(shot, app.font())
                d = gui.PrefsDialog(w)
                d.show()
                app.processEvents()
                d.grab().save(str(OUT / "prefs-dark.png"))
                d.close()
                w.reveal_b.hide()
                w.status.setText("")
                w.prog_panel.show()
                w.prog_label.setText("Demo Song.mp3: разделение")
                w.progress.setRange(0, 1000)
                w.progress.setValue(440)
                w.prog_eta.setText("44% · осталось ~1:24")
                app.processEvents()
                w.grab().save(str(OUT / "progress-dark.png"))
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
        for scheme in ("dark", "light"):
            run(song, scheme, tmp)
    print(f"written to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

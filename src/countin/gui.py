"""Count-In desktop app."""

import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PySide6.QtCore import QEvent, QObject, QSettings, QUrl, QPointF, QRectF, QRunnable, QSize, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QIcon, QKeySequence, QShortcut, QColor, QFont, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QProgressBar, QPushButton,
    QSizePolicy, QSlider, QStackedWidget, QStatusBar, QStyle, QStyledItemDelegate, QToolBar, QVBoxLayout, QWidget,
)

from . import update
from .core import analyze as an
from .core import io
from .core import render as rd
from .core.io import resource

AUDIO_EXT = {".mp3", ".flac", ".m4a", ".wav", ".aif", ".aiff", ".ogg"}
PREVIEW_AFTER = 10.0  # seconds of song after the downbeat
MUTE = -41  # level slider bottom means "remove"
FORMAT_NAMES = {None: "Как у исходника", "mp3": "MP3 320 kbps", "flac": "FLAC", "alac": "ALAC (.m4a)", "wav": "WAV"}
ORANGE = QColor(255, 159, 10)
GREEN = QColor(48, 209, 88)

STYLE = """
#sidebar { background: @SIDEBAR@; border-right: 1px solid @BORDER@; }
QMainWindow, QStackedWidget > QWidget, #progWrap { background: @WINDOW@; }
#sidebarTitle { color: @FIELD@; font-size: 12px; font-weight: 600; padding: 10px 14px 4px 14px; }
QListWidget#tracks { background: transparent; border: none; outline: 0; }
QListWidget#tracks::item { border-radius: 7px; margin: 1px 8px; }
QListWidget#tracks::item:selected { background: palette(highlight); }
#title { font-size: 24px; font-weight: 600; }
#subtitle { color: @FIELD@; }
#warning { color: #ff9f0a; }
#drop { border: 2px dashed @BORDER@; border-radius: 18px; color: @FIELD@; font-size: 18px; }
QFrame#card { background: @CARD@; border: 1px solid @BORDER@; border-radius: 12px; }
#cardTitle { font-weight: 600; font-size: 16px; }
#field { color: @FIELD@; }
QPushButton[seg] { border: 1px solid rgba(127,127,127,0.35); background: rgba(127,127,127,0.06);
                   padding: 3px 11px; min-height: 18px; border-radius: 0; }
QPushButton[seg="first"] { border-top-left-radius: 6px; border-bottom-left-radius: 6px; }
QPushButton[seg="last"] { border-top-right-radius: 6px; border-bottom-right-radius: 6px; }
QPushButton[seg]:checked { background: palette(highlight); color: palette(highlighted-text); border-color: palette(highlight); }
QPushButton#primary { background: palette(highlight); color: white; border: none;
                      border-radius: 9px; padding: 8px 26px; font-size: 15px; font-weight: 600; min-height: 22px; }
QPushButton#primary:disabled { background: rgba(127,127,127,0.25); }
QPushButton#tool { border: 1px solid @BORDER@; border-radius: 9px; padding: 8px 18px; font-size: 15px;
                   background: @CARD@; min-height: 22px; }
QPushButton#tool:disabled { color: gray; }
QPushButton#small { border: 1px solid rgba(127,127,127,0.3); border-radius: 6px; padding: 2px 8px;
                    background: rgba(127,127,127,0.06); min-width: 26px; }
QSlider::groove:horizontal { height: 4px; background: rgba(127,127,127,0.3); border-radius: 2px; }
QSlider::sub-page:horizontal { background: palette(highlight); border-radius: 2px; }
QSlider::handle:horizontal { width: 16px; height: 16px; margin: -6px 0; border-radius: 8px;
                             background: white; border: 1px solid rgba(0,0,0,0.25); }
QStatusBar { color: @FIELD@; }
"""


# App icon variants. The built ICO/ICNS stay light; this only changes the window and Dock icon while running.
ICON_CHOICES = {"light": "Светлая", "dark": "Тёмная", "system": "Как тема приложения"}
ICON_KEY = "appearance/icon"
ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256, 512, 1024)


def icon_choice() -> str:
    """Saved icon choice; a missing or unknown value means light."""
    v = QSettings().value(ICON_KEY, "light")
    return v if v in ICON_CHOICES else "light"


def icon_variant(choice: str, scheme: Qt.ColorScheme) -> str:
    if choice == "system":
        return "dark" if scheme == Qt.ColorScheme.Dark else "light"
    return choice


def app_icon(variant: str) -> QIcon:
    icon = QIcon()
    for size in ICON_SIZES:
        path = resource("assets", "icons", variant, "png", f"Count-In-{size}.png")
        if path.exists():
            icon.addFile(str(path), QSize(size, size))
    return icon


def apply_icon(app: QApplication) -> str:
    """Sets the app and window icon from the saved choice and the current color scheme. Returns the variant."""
    variant = icon_variant(icon_choice(), app.styleHints().colorScheme())
    icon = app_icon(variant)
    app.setWindowIcon(icon)
    for w in app.topLevelWidgets():
        w.setWindowIcon(icon)
    return variant


class Cancelled(Exception):
    pass


@dataclass
class Track:
    path: Path
    analysis: an.Analysis | None = None
    bpm: float = 120.0
    t0: float = 0.0
    duration: float = 0.0
    error: str | None = None


@dataclass
class Settings:
    style: str  # even, buildup, midi
    bars: int
    meter: str
    pattern: Path | None
    volume_db: float
    levels: dict[str, float] = field(default_factory=dict)  # stem -> dB, only turned-down stems
    pre_roll: str = "keep"
    fmt: str | None = None  # None follows the source


def clicks_for(t: Track, s: Settings) -> list[rd.Click]:
    period = 60.0 / t.bpm
    if s.style == "midi" and s.pattern:
        return rd.pattern_grid(s.pattern, t.t0, period, s.meter)
    grid = rd.buildup_grid if s.style == "buildup" else rd.click_grid
    return grid(t.t0, period, s.bars, s.meter)


def fmt_time(t: float) -> str:
    sign = "−" if t < 0 else ""
    t = abs(t)
    return f"{sign}{int(t // 60)}:{t % 60:06.3f}"


NOTES = {
    "bar length varies between downbeats": "длина такта плавает",
    "later downbeats disagree with the first one; candidate": "следующие сильные доли не сходятся с первой, кандидат",
    "no clear attack at the downbeat, using the beat grid": "на доле нет чёткой атаки, маркер стоит по сетке",
}


def translate(note: str) -> str:
    for en, ru in NOTES.items():
        note = note.replace(en, ru)
    return note.replace(" s", " с")


def reveal(path: Path) -> None:
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])
    elif sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", str(path)])


# --- background work ---

class Job(QRunnable):
    """Runs fn(job, *args) in a pool thread. fn calls job.report() between steps, which also checks for cancel."""

    class Signals(QObject):
        progress = Signal(str, int, int)
        done = Signal(object)
        failed = Signal(str)

    def __init__(self, fn, *args):
        super().__init__()
        self.fn, self.args = fn, args
        self.signals = Job.Signals()
        self.cancelled = threading.Event()

    def report(self, text: str, done: int = 0, total: int = 0) -> None:
        if self.cancelled.is_set():
            raise Cancelled
        self.signals.progress.emit(text, done, total)

    def run(self):
        try:
            self.signals.done.emit(self.fn(self, *self.args))
        except Cancelled:
            self.signals.failed.emit("Отменено")
        except Exception as e:  # shown to the user, the app keeps running
            self.signals.failed.emit(f"{type(e).__name__}: {e}")


def analyze_job(job: Job, track: Track):
    job.report(f"Анализ: {track.path.name}")
    try:
        pcm, info = io.decode(track.path)
        track.duration = len(pcm) / info.sr
        track.analysis = an.analyze(pcm, info.sr)
    except Exception as e:
        track.error = str(e)
    return track


def _stems(job: Job, pcm, sr, label: str) -> dict[str, np.ndarray]:
    from .core import stems

    job.report(f"{label}: загрузка модели")
    st = stems.separate(pcm, sr, lambda d, n: job.report(label, d, n))
    return {k: st[k] for k in ("guitar", "vocals")}


def _covers(cached, i0: int, i1: int) -> bool:
    """cached is (first sample, {stem: array}) and holds samples i0..i1."""
    return bool(cached) and cached[0] <= i0 and cached[0] + len(cached[1]["guitar"]) >= i1


def preview_job(job: Job, track: Track, s: Settings, pcm: np.ndarray, sr: int, cached, separate: bool):
    """Count-in plus PREVIEW_AFTER seconds, with stems kept apart so the player can mix them live.

    Returns (song with clicks, {stem: aligned array}, sr, original-song time of the first sample, new stem cache).
    """
    clicks = clicks_for(track, s)
    first = min(c.time for c in clicks)
    i0 = int(max(0.0, first - 0.5) * sr)
    i1 = int(min(track.t0 + PREVIEW_AFTER, len(pcm) / sr) * sr)
    part, new_cache = None, None
    if _covers(cached, i0, i1):
        c0, st = cached
        part = {k: v[i0 - c0 : i1 - c0] for k, v in st.items()}
    elif separate:
        part = _stems(job, pcm[i0:i1], sr, "Разделяю отрывок")
        new_cache = (i0, part)

    job.report("Сборка отрывка")
    r = rd.render(pcm, sr, clicks, track.t0, s.pre_roll, s.volume_db, rd.load_samples(sr))
    shift = int(round(r.offset * sr))
    o0 = max(0, int((first - 0.3) * sr) + shift)
    base = r.pcm[o0 : i1 + shift]

    # Stems go to the same output positions as the song samples they came from.
    aligned = {}
    for k, v in (part or {}).items():
        z = np.zeros_like(base)
        a = i0 + shift - o0
        lo, hi = max(a, 0), min(a + len(v), len(base))
        z[lo:hi] = v[lo - a : hi - a]
        aligned[k] = z
    return base, aligned, sr, o0 / sr - r.offset, new_cache


def save_job(job: Job, tracks: list[Track], s: Settings, cached: dict[Path, tuple]):
    """Writes each track next to its source. Returns (saved paths, {path: (0, stems)} of the last separated track)."""
    from .core import stems

    saved, last = [], {}
    for k, t in enumerate(tracks, 1):
        name = f"{k}/{len(tracks)} {t.path.name}" if len(tracks) > 1 else t.path.name
        job.report(f"{name}: чтение")
        pcm, info = io.decode(t.path)
        if s.levels:
            c = cached.get(t.path)
            st = c[1] if _covers(c, 0, len(pcm)) and c[0] == 0 else _stems(job, pcm, info.sr, f"{name}: разделение")
            last = {t.path: (0, st)}
            pcm = stems.attenuate(pcm, st, s.levels)
        job.report(f"{name}: запись")
        r = rd.render(pcm, info.sr, clicks_for(t, s), t.t0, s.pre_roll, s.volume_db, rd.load_samples(info.sr))
        out = io.output_path(t.path, s.fmt or info.format, s.levels)
        io.encode(r.pcm, info, out, tags_from=t.path)
        saved.append(out)
    return saved, last


def keep(slider_value: int) -> float:
    """Linear gain a level slider leaves on its stem."""
    return 0.0 if slider_value == MUTE else 10 ** (slider_value / 20)


class Player:
    """Mixes the song and its stems block by block, so stem levels change while it plays."""

    def __init__(self):
        self.stream = None
        self.pos = 0
        self.done = False
        self.gains: dict[str, float] = {}

    def play(self, base: np.ndarray, stems: dict[str, np.ndarray], sr: int):
        import sounddevice as sd

        self.stop()
        self.base, self.stems, self.sr, self.pos, self.done = base, stems, sr, 0, False

        # A large buffer keeps audio smooth while the GUI thread holds the interpreter.
        self.stream = sd.OutputStream(samplerate=sr, channels=base.shape[1], dtype="float32",
                                      blocksize=2048, latency="high", callback=self._fill)
        self.stream.start()

    def _fill(self, out, frames, _time, _status):
        a = self.pos
        b = min(a + frames, len(self.base))
        x = self.base[a:b].copy()
        for k, st in self.stems.items():
            g = self.gains.get(k, 1.0)
            if g != 1.0:
                x -= (1.0 - g) * st[a:b]
        np.clip(x, -1.0, 1.0, out=x)
        out[: b - a] = x
        out[b - a :] = 0
        self.pos = b
        if b >= len(self.base):
            self.done = True

    def position(self) -> float:
        return self.pos / self.sr

    def stop(self):
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None


# --- widgets ---

class Segmented(QWidget):
    """macOS-like segmented control."""

    changed = Signal(object)

    def __init__(self, options: list[tuple[str, object]], value=None):
        super().__init__()
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        self.values = [v for _, v in options]
        self.group = QButtonGroup(self)
        for i, (label, _) in enumerate(options):
            b = QPushButton(label, checkable=True)
            b.setProperty("seg", "first" if i == 0 else "last" if i == len(options) - 1 else "mid")
            b.setFocusPolicy(Qt.NoFocus)
            self.group.addButton(b, i)
            h.addWidget(b)
        h.addStretch()
        self.group.idClicked.connect(lambda i: self.changed.emit(self.values[i]))
        self.set_value(self.values[0] if value is None else value)

    def value(self):
        return self.values[self.group.checkedId()]

    def set_value(self, v):
        self.group.button(self.values.index(v)).setChecked(True)


class TrackDelegate(QStyledItemDelegate):
    """Two-line sidebar row: file name and detection summary."""

    def sizeHint(self, option, index):
        return QSize(200, 50)

    def paint(self, p, option, index):
        self.initStyleOption(option, index)
        selected = option.state & QStyle.State_Selected
        option.text = ""
        option.widget.style().drawControl(QStyle.CE_ItemViewItem, option, p, option.widget)
        r = option.rect.adjusted(18, 5, -14, -5)
        pal = option.palette
        p.save()
        f = QFont(option.font)
        f.setWeight(QFont.DemiBold)
        p.setFont(f)
        p.setPen(pal.highlightedText().color() if selected else pal.text().color())
        name = p.fontMetrics().elidedText(index.data(Qt.DisplayRole), Qt.ElideMiddle, r.width())
        p.drawText(r, Qt.AlignTop | Qt.AlignLeft, name)
        f.setWeight(QFont.Normal)
        f.setPointSizeF(f.pointSizeF() * 0.88)
        p.setFont(f)
        p.setPen(pal.highlightedText().color() if selected else QColor(140, 140, 145))
        p.drawText(r, Qt.AlignBottom | Qt.AlignLeft, index.data(Qt.UserRole) or "")
        p.restore()


class Wave(QWidget):
    """Waveform with beat lines, count-in clicks, a time ruler and a draggable first-downbeat marker."""

    t0Changed = Signal(float)
    RULER = 20

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(220)
        self.setMouseTracking(True)
        self.env = None  # peak per millisecond
        self.duration = 0.0
        self.v0, self.v1 = 0.0, 10.0
        self.beats, self.downs, self.clicks = [], [], []
        self.t0 = None
        self.cursor_t = None
        self.drag = None
        self._cache = (None, None)

    def set_audio(self, pcm: np.ndarray, sr: int):
        m = np.abs(pcm).max(axis=1)
        b = sr // 1000
        n = len(m) // b
        env = m[: n * b].reshape(n, b).max(axis=1)
        self.env = env / max(float(env.max()), 1e-9)
        self.duration = len(pcm) / sr
        self._cache = (None, None)

    def show_range(self, a: float, b: float):
        self.v0, self.v1 = a, b
        self.update()

    def x_of(self, t: float) -> float:
        return (t - self.v0) / (self.v1 - self.v0) * self.width()

    def t_of(self, x: float) -> float:
        return self.v0 + x / max(self.width(), 1) * (self.v1 - self.v0)

    def _wave_pixmap(self) -> QPixmap:
        """Waveform layer, redrawn only when the view or size changes; playback repaints stay cheap."""
        dpr = self.devicePixelRatioF()
        w, h = self.width(), self.height() - self.RULER
        key = (w, h, dpr, self.v0, self.v1, id(self.env), self.palette().text().color().rgb())
        if self._cache[0] == key:
            return self._cache[1]
        pix = QPixmap(int(w * dpr), int(h * dpr))
        pix.setDevicePixelRatio(dpr)
        pix.fill(Qt.transparent)
        if self.env is not None and w > 1:
            ts = self.v0 + np.arange(w + 1) / w * (self.v1 - self.v0)
            env = np.r_[self.env, 0.0]
            idx = np.clip((ts * 1000).astype(int), 0, len(env) - 1)
            peaks = np.maximum.reduceat(env, idx[:-1])
            peaks[(ts[:-1] < 0) | (ts[:-1] > self.duration)] = 0
            mid = h / 2
            top = [QPointF(x, mid - a * mid * 0.92) for x, a in enumerate(peaks)]
            bottom = [QPointF(x, mid + a * mid * 0.92) for x, a in reversed(list(enumerate(peaks)))]
            c = QColor(self.palette().text().color())
            c.setAlphaF(0.55)
            p = QPainter(pix)
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(Qt.NoPen)
            p.setBrush(c)
            p.drawPolygon(QPolygonF(top + bottom))
            p.end()
        self._cache = (key, pix)
        return pix

    def _ruler_step(self) -> float:
        per_px = (self.v1 - self.v0) / max(self.width(), 1)
        return next((s for s in (0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60) if s / per_px >= 70), 120)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        wh = h - self.RULER
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, w, h), 12, 12)
        p.setClipPath(clip)
        p.fillRect(0, 0, w, h, QColor(127, 127, 127, 22))

        accent = self.palette().highlight().color()
        if self.clicks and self.t0 is not None:
            zone = QColor(accent)
            zone.setAlpha(28)
            x0 = self.x_of(min(c.time for c in self.clicks))
            p.fillRect(QRectF(x0, 0, self.x_of(self.t0) - x0, wh), zone)
        if self.v0 < 0:
            p.fillRect(QRectF(0, 0, self.x_of(0), wh), QColor(127, 127, 127, 30))

        p.drawPixmap(0, 0, self._wave_pixmap())

        p.setPen(QPen(QColor(127, 127, 127, 70), 1))
        for t in self.beats:
            p.drawLine(QPointF(self.x_of(t), 0), QPointF(self.x_of(t), wh))
        p.setPen(QPen(QColor(127, 127, 127, 160), 1, Qt.DashLine))
        for t in self.downs:
            p.drawLine(QPointF(self.x_of(t), 0), QPointF(self.x_of(t), wh))

        p.setPen(Qt.NoPen)
        p.setBrush(accent)
        for c in self.clicks:
            hgt = wh * (0.42 if c.accent else 0.28) * min(c.gain, 1.6)
            p.drawRoundedRect(QRectF(self.x_of(c.time) - 2, 6, 4, hgt), 2, 2)

        if self.t0 is not None:
            x = self.x_of(self.t0)
            p.setPen(QPen(ORANGE, 2))
            p.drawLine(QPointF(x, 0), QPointF(x, wh))
            p.setPen(Qt.NoPen)
            p.setBrush(ORANGE)
            p.drawEllipse(QPointF(x, 12), 9, 9)
            p.setPen(QColor(255, 255, 255))
            f = p.font()
            f.setBold(True)
            p.setFont(f)
            p.drawText(QRectF(x - 9, 3, 18, 18), Qt.AlignCenter, "1")
        if self.cursor_t is not None:
            p.setPen(QPen(GREEN, 2))
            p.drawLine(QPointF(self.x_of(self.cursor_t), 0), QPointF(self.x_of(self.cursor_t), wh))

        # Ruler.
        p.setPen(QPen(QColor(127, 127, 127, 60), 1))
        p.drawLine(QPointF(0, wh), QPointF(w, wh))
        f = p.font()
        f.setBold(False)
        f.setPointSizeF(f.pointSizeF() * 0.85)
        p.setFont(f)
        step = self._ruler_step()
        t = np.ceil(self.v0 / step) * step
        while t <= self.v1:
            x = self.x_of(t)
            p.setPen(QPen(QColor(127, 127, 127, 140), 1))
            p.drawLine(QPointF(x, wh), QPointF(x, wh + 4))
            p.drawText(QRectF(x + 3, wh + 2, 80, self.RULER - 2), Qt.AlignLeft | Qt.AlignVCenter,
                       f"{t:.2f}".rstrip("0").rstrip(".") + " с")
            t += step

    def _near_marker(self, x: float) -> bool:
        return self.t0 is not None and abs(x - self.x_of(self.t0)) < 10

    def mousePressEvent(self, e):
        x = e.position().x()
        self.drag = "marker" if self._near_marker(x) else ("pan", x, self.v0, self.v1)

    def mouseMoveEvent(self, e):
        x = e.position().x()
        if self.drag == "marker":
            t = self.t_of(x)

            # Snap to a detected beat within 6 px; Alt places it freely.
            if self.beats and not e.modifiers() & Qt.AltModifier:
                b = min(self.beats, key=lambda b: abs(self.x_of(b) - x))
                if abs(self.x_of(b) - x) < 6:
                    t = b
            self.t0 = float(np.clip(t, 0.0, max(self.duration - 0.01, 0.0)))
            self.t0Changed.emit(self.t0)
            self.update()
        elif self.drag:
            _, x0, a, b = self.drag
            dt = (x - x0) / max(self.width(), 1) * (b - a)
            self.show_range(a - dt, b - dt)
        else:
            self.setCursor(Qt.SizeHorCursor if self._near_marker(x) else Qt.OpenHandCursor)

    def mouseReleaseEvent(self, _):
        self.drag = None

    def wheelEvent(self, e):
        t = self.t_of(e.position().x())
        k = 0.8 if e.angleDelta().y() > 0 else 1.25
        span = float(np.clip((self.v1 - self.v0) * k, 0.3, max(self.duration + 10, 10)))
        r = (t - self.v0) / (self.v1 - self.v0)
        self.show_range(t - r * span, t + (1 - r) * span)


def card(title: str) -> tuple[QFrame, QGridLayout]:
    f = QFrame(objectName="card")
    v = QVBoxLayout(f)
    v.setContentsMargins(14, 12, 14, 14)
    v.setSpacing(10)
    v.addWidget(QLabel(title, objectName="cardTitle"))
    g = QGridLayout()
    g.setHorizontalSpacing(10)
    g.setVerticalSpacing(9)
    g.setColumnStretch(1, 1)
    v.addLayout(g)
    v.addStretch()
    return f, g


def row(grid: QGridLayout, label: str, *widgets) -> None:
    r = grid.rowCount()
    grid.addWidget(QLabel(label, objectName="field"), r, 0)
    h = QHBoxLayout()
    h.setSpacing(6)
    for w in widgets:
        h.addWidget(w)
    h.addStretch()
    grid.addLayout(h, r, 1)


# --- window ---

class UpdateSignals(QObject):
    found = Signal(object)


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Count-In {update.current_version()}")
        self.setAcceptDrops(True)
        self.setUnifiedTitleAndToolBarOnMac(True)
        self.resize(1180, 780)

        self.tracks: list[Track] = []
        self.current: Track | None = None
        self.pcm, self.sr = None, 0
        self.stem_cache: dict[Path, dict] = {}  # stems of the current track
        self.taps: list[float] = []
        self.job: Job | None = None
        self.analysis_pool = QThreadPool()
        self.analysis_pool.setMaxThreadCount(1)
        self.job_pool = QThreadPool()
        self.job_pool.setMaxThreadCount(1)
        self.analysis_jobs = []
        self.player = Player()
        self.play_start = None
        self.play_timer = QTimer(self, interval=30, timeout=self._tick)
        self.last_saved: Path | None = None

        self.updates = UpdateSignals()
        self.updates.found.connect(self._update_found)
        self._build_toolbar()
        self._build_menu()
        self._build_body()
        QShortcut(QKeySequence(Qt.Key_Space), self, activated=self._play)
        self._build_status()
        self._labels()
        self._enable()

    # --- layout ---

    def _build_menu(self):
        menu = self.menuBar().addMenu("Настройки").addMenu("Значок приложения")
        group = QActionGroup(self)
        current = icon_choice()
        for key, title in ICON_CHOICES.items():
            act = QAction(title, self, checkable=True, checked=key == current)
            act.triggered.connect(lambda _=False, k=key: self._set_icon(k))
            group.addAction(act)
            menu.addAction(act)

    def _set_icon(self, choice: str):
        QSettings().setValue(ICON_KEY, choice)
        apply_icon(QApplication.instance())

    def _build_toolbar(self):
        tb = QToolBar(movable=False, floatable=False)
        tb.setContextMenuPolicy(Qt.PreventContextMenu)
        self.addToolBar(tb)
        add = QPushButton("＋  Добавить треки", objectName="tool", clicked=self._add_dialog)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.play_b = QPushButton("▶  Прослушать", objectName="primary", clicked=self._play, toolTip="Пробел")
        self.save_b = QPushButton("Сохранить", objectName="tool", clicked=self._save)
        self.save_all_b = QPushButton("Сохранить все", objectName="tool", clicked=self._save_all)
        for w in (add, spacer, self.play_b, self.save_b, self.save_all_b):
            tb.addWidget(w)
            w.setFocusPolicy(Qt.NoFocus)
            if w is not spacer:
                tb.addWidget(QWidget(minimumWidth=6))

    def _build_body(self):
        side = QWidget(objectName="sidebar")
        side.setFixedWidth(260)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(0, 0, 0, 8)
        sv.addWidget(QLabel("ТРЕКИ", objectName="sidebarTitle"))
        self.list = QListWidget(objectName="tracks")
        self.list.setItemDelegate(TrackDelegate(self.list))
        self.list.currentRowChanged.connect(self._select)
        sv.addWidget(self.list)

        self.stack = QStackedWidget()
        drop = QLabel("Перетащите сюда треки или папки\n\nmp3, FLAC, ALAC, WAV", objectName="drop",
                      alignment=Qt.AlignCenter)
        drop_page = QWidget()
        dv = QVBoxLayout(drop_page)
        dv.setContentsMargins(40, 40, 40, 40)
        dv.addWidget(drop)
        self.stack.addWidget(drop_page)
        self.stack.addWidget(self._editor())

        body = QWidget()
        h = QHBoxLayout(body)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        h.addWidget(side)
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.addWidget(self._update_banner())
        right.addWidget(self.stack, 1)
        right.addWidget(self._progress_panel())
        h.addLayout(right, 1)
        self.setCentralWidget(body)

    def _update_banner(self) -> QWidget:
        panel = QFrame(objectName="card")
        h = QHBoxLayout(panel)
        h.setContentsMargins(16, 8, 10, 8)
        self.update_l = QLabel(objectName="cardTitle")
        get = QPushButton("Скачать", objectName="primary", clicked=self._download_update)
        later = QPushButton("Позже", objectName="tool", clicked=lambda: self.update_wrap.hide())
        h.addWidget(self.update_l, 1)
        h.addWidget(get)
        h.addWidget(later)
        wrap = QWidget(objectName="progWrap")
        v = QVBoxLayout(wrap)
        v.setContentsMargins(22, 12, 22, 0)
        v.addWidget(panel)
        wrap.hide()
        self.update_wrap, self.release = wrap, None
        return wrap

    def check_updates(self):
        """Asks GitHub in a daemon thread; the answer comes back to the UI thread as a queued signal."""
        threading.Thread(target=lambda: self.updates.found.emit(update.check()), daemon=True).start()

    def _update_found(self, release):
        if release:
            self.release = release
            self.update_l.setText(f"Доступна версия {release.version} (у вас {update.current_version()})")
            self.update_wrap.show()

    def _download_update(self):
        QDesktopServices.openUrl(QUrl(self.release.url))
        self.update_wrap.hide()

    def _progress_panel(self) -> QWidget:
        panel = QFrame(objectName="card")
        panel.setContentsMargins(0, 0, 0, 0)
        g = QGridLayout(panel)
        g.setContentsMargins(16, 10, 16, 12)
        self.prog_label = QLabel(objectName="cardTitle")
        self.prog_eta = QLabel(objectName="field")
        self.progress = QProgressBar(textVisible=False, maximumHeight=8)
        self.cancel_b = QPushButton("Отмена", objectName="tool", clicked=self._cancel)
        g.addWidget(self.prog_label, 0, 0)
        g.addWidget(self.prog_eta, 0, 1, Qt.AlignRight)
        g.addWidget(self.progress, 1, 0, 1, 2)
        g.addWidget(self.cancel_b, 0, 2, 2, 1)
        g.setColumnStretch(0, 1)
        wrap = QWidget(objectName="progWrap")
        v = QVBoxLayout(wrap)
        v.setContentsMargins(22, 0, 22, 12)
        v.addWidget(panel)
        self.prog_panel = wrap
        self._stage = None
        return wrap

    def _editor(self) -> QWidget:
        self.title = QLabel(objectName="title")
        self.subtitle = QLabel(objectName="subtitle")
        self.warning = QLabel(objectName="warning", wordWrap=True)
        self.wave = Wave()
        self.wave.t0Changed.connect(self._marker_moved)
        hint = QLabel("Тяните оранжевый маркер «1» на первую сильную долю. Колесо — масштаб, перетаскивание — сдвиг, "
                      "⌥ — без прилипания к долям.", objectName="subtitle", wordWrap=True)

        # Tempo card.
        tempo, g = card("Темп и первая доля")
        self.bpm_spin = QDoubleSpinBox(decimals=2, minimum=30, maximum=320, singleStep=0.1, suffix=" BPM")
        self.bpm_spin.valueChanged.connect(self._bpm_edited)
        x2 = QPushButton("×2", objectName="small", clicked=lambda: self._scale_bpm(2.0))
        half = QPushButton("÷2", objectName="small", clicked=lambda: self._scale_bpm(0.5))
        tap = QPushButton("Tap", objectName="small", clicked=self._tap)
        row(g, "Темп", self.bpm_spin, x2, half, tap)
        self.t0_spin = QDoubleSpinBox(decimals=3, maximum=36000, singleStep=0.001, suffix=" с")
        self.t0_spin.valueChanged.connect(self._t0_edited)
        prev_b = QPushButton("◀", objectName="small", clicked=lambda: self._shift_beat(-1))
        next_b = QPushButton("▶", objectName="small", clicked=lambda: self._shift_beat(1))
        row(g, "Доля «1»", self.t0_spin, prev_b, next_b)
        self.alt_b = QPushButton("Взять кандидата детектора", objectName="small", clicked=self._use_alt)
        g.addWidget(self.alt_b, g.rowCount(), 1)

        # Count-in card.
        count, g = card("Отсчёт")
        self.style_seg = Segmented([("Ровный", "even"), ("Нарастание", "buildup"), ("MIDI…", "midi")], "buildup")
        self.style_seg.changed.connect(self._style_changed)
        row(g, "Рисунок", self.style_seg)
        self.bars_seg = Segmented([("1 такт", 1), ("2 такта", 2)], 2)
        self.bars_seg.changed.connect(self._refresh)
        row(g, "Длина", self.bars_seg)
        self.meter_seg = Segmented([(m, m) for m in rd.METERS], "4/4")
        self.meter_seg.changed.connect(self._meter_changed)
        row(g, "Размер", self.meter_seg)
        self.volume = QSlider(Qt.Horizontal, minimum=-24, maximum=6, value=0, minimumWidth=140)
        self.volume_l = QLabel(minimumWidth=60)
        row(g, "Громкость", self.volume, self.volume_l)
        self.pattern: Path | None = None

        # Backing track card.
        mix, g = card("Бэктрек")
        self.guitar = QSlider(Qt.Horizontal, minimum=MUTE, maximum=0, value=0, minimumWidth=140)
        self.guitar_l = QLabel(minimumWidth=90)
        row(g, "Гитара", self.guitar, self.guitar_l)
        self.vocals = QSlider(Qt.Horizontal, minimum=MUTE, maximum=0, value=0, minimumWidth=140)
        self.vocals_l = QLabel(minimumWidth=90)
        row(g, "Вокал", self.vocals, self.vocals_l)
        self.trim = QCheckBox("Обрезать тишину в начале")
        g.addWidget(self.trim, g.rowCount(), 1)
        self.fmt = QComboBox()
        for key, name in FORMAT_NAMES.items():
            self.fmt.addItem(name, key)
        row(g, "Формат", self.fmt)

        for w in (self.volume, self.guitar, self.vocals):
            w.valueChanged.connect(self._labels)
        for w in (self.guitar, self.vocals):
            w.valueChanged.connect(self._gains)
        self.trim.toggled.connect(self._refresh)

        cards = QHBoxLayout()
        cards.setSpacing(12)
        for c in (tempo, count, mix):
            cards.addWidget(c, 1)

        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(22, 16, 22, 12)
        v.setSpacing(8)
        v.addWidget(self.title)
        v.addWidget(self.subtitle)
        v.addWidget(self.warning)
        v.addWidget(self.wave, 1)
        v.addWidget(hint)
        v.addSpacing(6)
        v.addLayout(cards)
        return page

    def _build_status(self):
        sb = QStatusBar()
        self.setStatusBar(sb)
        self.status = QLabel()
        self.reveal_b = QPushButton("Показать в Finder" if sys.platform == "darwin" else "Показать в папке",
                                    objectName="small", clicked=lambda: reveal(self.last_saved))
        sb.addWidget(self.status, 1)
        sb.addPermanentWidget(self.reveal_b)
        self.reveal_b.hide()

    # --- files ---

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.add_files([Path(u.toLocalFile()) for u in e.mimeData().urls()])

    def _add_dialog(self):
        exts = " ".join(f"*{x}" for x in sorted(AUDIO_EXT))
        files, _ = QFileDialog.getOpenFileNames(self, "Треки", "", f"Аудио ({exts})")
        self.add_files([Path(f) for f in files])

    def add_files(self, paths: list[Path]):
        found = []
        for p in paths:
            found += sorted(q for q in p.rglob("*") if q.suffix.lower() in AUDIO_EXT) if p.is_dir() else [p]
        known = {t.path for t in self.tracks}
        for p in found:
            if p in known or p.suffix.lower() not in AUDIO_EXT:
                continue
            t = Track(p)
            self.tracks.append(t)
            item = QListWidgetItem(p.name)
            item.setData(Qt.UserRole, "Анализ…")
            self.list.addItem(item)
            job = Job(analyze_job, t)
            job.signals.done.connect(self._analyzed)
            self.analysis_jobs.append(job)
            self.analysis_pool.start(job)
        if self.list.currentRow() < 0 and self.tracks:
            self.list.setCurrentRow(0)
        self._enable()

    def _analyzed(self, t: Track):
        if t.analysis:
            t.bpm = 60.0 / an.beat_period(t.analysis, rd.METERS[self.meter_seg.value()][0])[0]
            t.t0 = t.analysis.first_downbeat
        self._update_item(t)
        if t is self.current:
            self._select(self.tracks.index(t))
        self._enable()

    def _update_item(self, t: Track):
        item = self.list.item(self.tracks.index(t))
        if t.error:
            item.setData(Qt.UserRole, "⚠︎ " + t.error)
        elif t.analysis:
            item.setData(Qt.UserRole, f"{t.bpm:.1f} BPM · «1» на {fmt_time(t.t0)}")

    def _select(self, row: int):
        self._stop()
        self.current = self.tracks[row] if 0 <= row < len(self.tracks) else None
        t = self.current
        self.pcm = None
        self.stem_cache = {k: v for k, v in self.stem_cache.items() if t and k == t.path}
        self.stack.setCurrentIndex(1 if t else 0)
        self.wave.env, self.wave.t0, self.wave.beats, self.wave.downs, self.wave.clicks = None, None, [], [], []
        self.wave._cache = (None, None)
        self.warning.hide()
        if not t:
            self._enable()
            return
        self.title.setText(t.path.stem)
        if not t.analysis:
            self.subtitle.setText("Анализ…" if not t.error else "")
            if t.error:
                self.warning.setText(t.error)
                self.warning.show()
            self.wave.update()
            self._enable()
            return

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.pcm, info = io.decode(t.path)
            self.sr = info.sr
        finally:
            QApplication.restoreOverrideCursor()
        a = t.analysis
        self.wave.set_audio(self.pcm, self.sr)
        self.wave.beats, self.wave.downs = list(a.beats), list(a.downbeats)
        self.subtitle.setText(f"Детектор: {a.bpm:.2f} BPM · «1» на {fmt_time(a.first_downbeat)} · "
                              f"уверенность {a.confidence:.0%} · {fmt_time(t.duration)}")
        notes = [translate(n) for n in a.notes]
        if a.confidence < 0.4:
            notes.insert(0, "детектор не уверен, проверьте маркер «1» на слух")
        self.warning.setText("; ".join(notes))
        self.warning.setVisible(bool(notes))
        self.alt_b.setVisible(a.alt_downbeat is not None)
        self._show_track_values()
        bar = 60.0 / t.bpm * rd.METERS[self.meter_seg.value()][0]
        self.wave.show_range(t.t0 - bar * self.bars_seg.value() - 1.0, t.t0 + 6.0)
        self._enable()

    def _show_track_values(self):
        t = self.current
        for spin, v in ((self.bpm_spin, t.bpm), (self.t0_spin, t.t0)):
            spin.blockSignals(True)
            spin.setValue(v)
            spin.blockSignals(False)
        self._update_item(t)
        self._refresh()

    # --- edits ---

    def _bpm_edited(self, v: float):
        if self.current:
            self.current.bpm = v
            self._update_item(self.current)
            self._refresh()

    def _scale_bpm(self, k: float):
        if self.current:
            self.current.bpm = float(np.clip(self.current.bpm * k, 30, 320))
            self._show_track_values()

    def _tap(self):
        now = time.monotonic()
        if self.taps and now - self.taps[-1] > 2.0:
            self.taps = []
        self.taps = (self.taps + [now])[-8:]
        if len(self.taps) >= 3 and self.current:
            self.current.bpm = 60.0 / float(np.median(np.diff(self.taps)))
            self._show_track_values()

    def _meter_changed(self, meter: str):
        t = self.current
        if t and t.analysis:
            t.bpm = 60.0 / an.beat_period(t.analysis, rd.METERS[meter][0])[0]
            self._show_track_values()

    def _t0_edited(self, v: float):
        if self.current:
            self.current.t0 = v
            self._update_item(self.current)
            self._refresh()

    def _marker_moved(self, t0: float):
        self.current.t0 = t0
        self.t0_spin.blockSignals(True)
        self.t0_spin.setValue(t0)
        self.t0_spin.blockSignals(False)
        self._update_item(self.current)
        self._refresh()

    def _shift_beat(self, k: int):
        if self.current:
            self.current.t0 = max(0.0, self.current.t0 + k * 60.0 / self.current.bpm)
            self._show_track_values()

    def _use_alt(self):
        if self.current and self.current.analysis.alt_downbeat is not None:
            self.current.t0 = self.current.analysis.alt_downbeat
            self._show_track_values()

    def _style_changed(self, style: str):
        if style == "midi":
            f, _ = QFileDialog.getOpenFileName(self, "MIDI-шаблон", "", "MIDI (*.mid *.midi)")
            if f:
                self.pattern = Path(f)
                self.style_seg.group.button(2).setText(self.pattern.stem[:14] + "…")
            elif not self.pattern:
                self.style_seg.set_value("buildup")
        self.bars_seg.setEnabled(self.style_seg.value() != "midi")
        self._refresh()

    def _gains(self):
        self.player.gains = {"guitar": keep(self.guitar.value()), "vocals": keep(self.vocals.value())}

    def _labels(self):
        self.volume_l.setText(f"{self.volume.value():+d} дБ")
        for s, l in ((self.guitar, self.guitar_l), (self.vocals, self.vocals_l)):
            v = s.value()
            l.setText("как есть" if v == 0 else "убрать" if v == MUTE else f"{v} дБ")

    def settings(self) -> Settings:
        levels = {}
        for name, s in (("guitar", self.guitar), ("vocals", self.vocals)):
            if s.value() < 0:
                levels[name] = float("-inf") if s.value() == MUTE else float(s.value())
        return Settings(
            style=self.style_seg.value(), bars=self.bars_seg.value(), meter=self.meter_seg.value(),
            pattern=self.pattern, volume_db=float(self.volume.value()),
            levels=levels, pre_roll="trim" if self.trim.isChecked() else "keep", fmt=self.fmt.currentData(),
        )

    def _refresh(self, *_):
        t = self.current
        if not t or not t.analysis:
            return
        try:
            self.wave.clicks = clicks_for(t, self.settings())
        except ValueError as e:
            self.wave.clicks = []
            self.status.setText(str(e))
        self.wave.t0 = t.t0
        self.wave.update()

    # --- jobs ---

    def _enable(self):
        busy = self.job is not None
        ready = bool(self.current and self.current.analysis)
        self.play_b.setEnabled(ready and (not busy or self.play_start is not None))
        self.save_b.setEnabled(ready and not busy)
        self.save_all_b.setEnabled(any(t.analysis for t in self.tracks) and not busy)
        self.prog_panel.setVisible(busy)

    def _run(self, fn, *args, on_done):
        self.reveal_b.hide()
        self.job = Job(fn, *args)
        self.job.signals.progress.connect(self._progress)
        self.job.signals.done.connect(on_done)
        self.job.signals.failed.connect(self._failed)
        self.progress.setRange(0, 0)
        self._stage = None
        self.job_pool.start(self.job)
        self._enable()

    def _progress(self, text: str, done: int, total: int):
        if text != self._stage:
            self._stage, self._stage_start, self._stage_done = text, time.monotonic(), done
        self.prog_label.setText(text)
        if not total:
            self.progress.setRange(0, 0)
            self.prog_eta.setText("")
            return
        self.progress.setRange(0, 1000)
        frac = done / total
        self.progress.setValue(int(frac * 1000))

        # Rate from the chunks finished since this stage began; the first one carries model loading.
        rate = (done - self._stage_done) / max(time.monotonic() - self._stage_start, 1e-6)
        left = (total - done) / rate if done > self._stage_done else None
        self.prog_eta.setText(f"{frac:.0%}" + (f" · осталось ~{int(left // 60)}:{int(left % 60):02d}"
                                               if left is not None else " · считаю время…"))

    def _finish(self):
        self.job = None
        self._enable()

    def _failed(self, msg: str):
        self._finish()
        self.status.setText(msg)
        if msg != "Отменено":
            QMessageBox.warning(self, "Count-In", msg)

    def _cancel(self):
        if self.job:
            self.job.cancelled.set()
            self.status.setText("Отменяю…")

    def _play(self):
        if self.play_start is not None:
            self._stop()
            return
        if self.job or not (self.current and self.current.analysis):
            return
        t, s = self.current, self.settings()
        self._run(preview_job, t, s, self.pcm, self.sr, self.stem_cache.get(t.path), bool(s.levels),
                  on_done=self._preview_ready)

    def _preview_ready(self, result):
        self._finish()
        base, stems, sr, start, new_cache = result
        if new_cache:
            self.stem_cache = {self.current.path: new_cache}
        self.status.setText("Гитару и вокал можно менять на ходу" if stems
                            else "Чтобы менять гитару и вокал на ходу, опустите ползунок до прослушивания")
        self._gains()
        self.player.play(base, stems, sr)
        self.play_start = start
        self.play_b.setText("■  Стоп")
        self.play_timer.start()
        self._enable()

    def _tick(self):
        if self.player.done:
            self._stop()
            return
        self.wave.cursor_t = self.play_start + self.player.position()
        self.wave.update()

    def _stop(self):
        if self.play_start is None:
            return
        self.player.stop()
        self.play_start = None
        self.play_timer.stop()
        self.play_b.setText("▶  Прослушать")
        self.wave.cursor_t = None
        self.wave.update()
        self._enable()

    def _save(self):
        self._stop()
        self._run(save_job, [self.current], self.settings(), dict(self.stem_cache), on_done=self._saved)

    def _save_all(self):
        self._stop()
        tracks = [t for t in self.tracks if t.analysis]
        self._run(save_job, tracks, self.settings(), dict(self.stem_cache), on_done=self._saved)

    def _saved(self, result):
        saved, st = result
        self._finish()
        if self.current and self.current.path in st:
            self.stem_cache = st
        self.last_saved = saved[-1]
        self.status.setText(f"Сохранено: {saved[0].name}" if len(saved) == 1 else f"Сохранено файлов: {len(saved)}")
        self.reveal_b.show()

    def closeEvent(self, e):
        self._stop()
        self._cancel()
        super().closeEvent(e)


class App(QApplication):
    """Takes files dropped on the Dock icon or opened with Finder."""

    window: Window | None = None

    def event(self, e):
        if e.type() == QEvent.FileOpen and self.window:
            self.window.add_files([Path(e.file())])
            return True
        return super().event(e)


def style_sheet(app: QApplication) -> str:
    """STYLE with colors for the system light or dark appearance."""
    dark = app.palette().window().color().lightness() < 128
    colors = ({"@WINDOW@": "#1e1e20", "@SIDEBAR@": "#262628", "@CARD@": "#2c2c2e", "@BORDER@": "#3a3a3c",
               "@FIELD@": "#a1a1a6"} if dark else
              {"@WINDOW@": "#f5f5f7", "@SIDEBAR@": "#ececee", "@CARD@": "#ffffff", "@BORDER@": "#d8d8dc",
               "@FIELD@": "#6e6e73"})
    out = STYLE
    for k, v in colors.items():
        out = out.replace(k, v)
    return out


def main() -> int:
    app = App(sys.argv)
    app.setOrganizationName("Count-In")
    app.setApplicationName("Count-In")
    apply_icon(app)

    # "Как тема приложения" follows the system appearance; the other choices ignore it.
    app.styleHints().colorSchemeChanged.connect(lambda _: apply_icon(app))
    font = app.font()
    font.setPointSizeF(font.pointSizeF() + 1.5)
    app.setFont(font)
    app.setStyleSheet(style_sheet(app))
    w = app.window = Window()
    w.add_files([Path(a) for a in sys.argv[1:]])
    w.show()
    w.check_updates()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

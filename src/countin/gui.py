"""Count-In desktop app."""

import logging
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PySide6.QtCore import QEvent, QObject, QSettings, QStandardPaths, QUrl, QPointF, QRectF, QRunnable, QSize, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QAction, QDesktopServices, QIcon, QKeySequence, QColor, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QAbstractSpinBox, QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFrame,
    QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QProgressBar, QPushButton, QRadioButton,
    QSlider, QStackedWidget, QStatusBar, QVBoxLayout, QWidget,
)

from . import i18n, update
from .i18n import _
from .core import analyze as an
from .core import io
from .core import render as rd
from .core.io import resource

log = logging.getLogger("countin")

AUDIO_EXT = {".mp3", ".flac", ".m4a", ".wav", ".aif", ".aiff", ".ogg"}
PREVIEW_AFTER = 10.0  # seconds of song after the downbeat
MUTE = -41  # level slider bottom means "remove"
FORMAT_NAMES = {None: "Как у исходника", "mp3": "MP3 320 kbps", "flac": "FLAC", "alac": "ALAC (.m4a)", "wav": "WAV"}
ORANGE = QColor(255, 159, 10)
GREEN = QColor(48, 209, 88)

STYLE = """
QMainWindow, QStackedWidget > QWidget, #progWrap, #header { background: @WINDOW@; }
QStatusBar { background: @SIDEBAR@; border-top: 1px solid @BORDER@; }
QStatusBar QLabel { padding: 0 8px; }
QPushButton#update { background: #0a84ff; color: white; border: none; border-radius: 10px; font-weight: 700;
                     min-width: 20px; max-width: 20px; min-height: 20px; max-height: 20px; padding: 0; }
QPushButton#update:hover { background: #409cff; }
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
QStatusBar, QStatusBar QLabel { color: @FIELD@; }
"""


# App icon variants. The built ICO/ICNS stay light; this only changes the window and Dock icon while running.
ICON_CHOICES = {"light": "Светлая", "dark": "Тёмная"}
LANG_KEY = "appearance/language"
ICON_KEY = "appearance/icon"
ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256, 512, 1024)


def icon_choice() -> str:
    """Saved icon choice; a missing or unknown value means light."""
    v = QSettings().value(ICON_KEY, "light")
    return v if v in ICON_CHOICES else "light"


def app_icon(variant: str) -> QIcon:
    icon = QIcon()
    for size in ICON_SIZES:
        path = resource("assets", "icons", variant, "png", f"Count-In-{size}.png")
        if path.exists():
            icon.addFile(str(path), QSize(size, size))
    return icon


def apply_icon(app: QApplication) -> str:
    """Sets the app and window icon from the saved choice. Returns the variant."""
    variant = icon_choice()
    icon = app_icon(variant)
    app.setWindowIcon(icon)
    for w in app.topLevelWidgets():
        w.setWindowIcon(icon)
    return variant


class Cancelled(Exception):
    pass


@dataclass(eq=False)
class Track:
    path: Path
    analysis: an.Analysis | None = None
    bpm: float = 120.0
    bpm_manual: bool = False  # set by the BPM field, ×2/÷2 or Tap; a meter change then keeps it
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
    out_root: Path | None = None  # None: next to the source
    group: tuple[bool, bool, bool] | None = None  # (artist, year, album) folders under out_root


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
    """Detector notes come in English; the Russian UI translates them."""
    if i18n.language() == "en":
        return note
    for en, ru in NOTES.items():
        note = note.replace(en, ru)
    return note.replace(" s", " с")


def reveal(path: Path) -> None:
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])
    elif sys.platform == "win32":
        # Full path: a bare name would also be looked up in the current folder.
        explorer = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "explorer.exe"
        subprocess.Popen([str(explorer), f"/select,{path}"])


def clip_note(clipped: int) -> str:
    return _("Клики перегружают звук в {n} сэмплах, убавьте громкость отсчёта", n=clipped) if clipped else ""


def log_path() -> Path:
    return Path(QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)) / "count-in.log"


def setup_log() -> None:
    """Errors go to a log file next to the settings; a windowed Windows build has no console to show them."""
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=path, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sys.excepthook = lambda *exc: log.critical("unhandled error", exc_info=exc)


# --- background work ---

class Job(QRunnable):
    """Runs fn(job, *args) in a pool thread. fn calls job.report() between steps, which also checks for cancel."""

    class Signals(QObject):
        progress = Signal(str, int, int)
        done = Signal(object)
        failed = Signal(str)
        cancelled = Signal()

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
            self.signals.cancelled.emit()
        except Exception as e:  # shown to the user, the app keeps running
            log.exception("job %s failed", getattr(self.fn, "__name__", self.fn))
            self.signals.failed.emit(f"{type(e).__name__}: {e}")


def analyze_job(job: Job, track: Track):
    """Returns (track, pcm, sr); the UI keeps the PCM, so the file is decoded once and never on the UI thread."""
    job.report(_("Анализ: {name}", name=track.path.name))
    pcm, sr = None, 0
    try:
        pcm, info = io.decode(track.path)
        sr = info.sr
        track.duration = len(pcm) / sr
        track.analysis = an.analyze(pcm, sr)
    except Exception as e:
        log.exception("analysis of %s failed", track.path)
        track.error = str(e)
    return track, pcm, sr


def _stems(job: Job, pcm, sr, label: str) -> dict[str, np.ndarray]:
    from .core import stems

    job.report(_("{name}: загрузка модели", name=label))
    return stems.separate(pcm, sr, lambda d, n: job.report(label, d, n), only=("guitar", "vocals"))


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
        part = _stems(job, pcm[i0:i1], sr, _("Разделяю отрывок"))
        new_cache = (i0, part)

    job.report(_("Сборка отрывка"))
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
    return track, base, aligned, sr, o0 / sr - r.offset, new_cache, r.clipped


def target_path(song: Path, s: Settings, info: io.AudioInfo) -> Path:
    """Where the saved file goes: the output folder from the preferences, optionally Artist/Year - Album."""
    folder = s.out_root
    if folder and s.group:
        folder = io.organized_dir(folder, info.tags, *s.group)
    return io.output_path(song, s.fmt or info.format, s.levels, folder)


def save_job(job: Job, t: Track, s: Settings, cached):
    """Writes the track. Returns (track, saved path, (0, stems) when separated or None, clipped samples)."""
    from .core import stems

    job.report(_("{name}: чтение", name=t.path.name))
    pcm, info = io.decode(t.path)
    full = None
    if s.levels:
        st = cached[1] if _covers(cached, 0, len(pcm)) else _stems(job, pcm, info.sr, _("{name}: разделение", name=t.path.name))
        full = (0, st)
        pcm = stems.attenuate(pcm, st, s.levels)
    job.report(_("{name}: запись", name=t.path.name))
    r = rd.render(pcm, info.sr, clicks_for(t, s), t.t0, s.pre_roll, s.volume_db, rd.load_samples(info.sr))
    out = target_path(t.path, s, info)
    out.parent.mkdir(parents=True, exist_ok=True)
    io.encode(r.pcm, info, out, tags_from=t.path)
    return t, out, full, r.clipped


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
        """Seconds heard so far: samples handed to the device minus its output latency."""
        lag = self.stream.latency if self.stream else 0.0
        return max(0.0, self.pos / self.sr - lag)

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
        self.values = [v for _label, v in options]
        self.group = QButtonGroup(self)
        for i, (label, _value) in enumerate(options):
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
            peaks = np.maximum.reduceat(env, idx)[:-1]
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

    def paintEvent(self, _event):
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
                       f"{t:.2f}".rstrip("0").rstrip(".") + _(" с"))
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
            _kind, x0, a, b = self.drag
            dt = (x - x0) / max(self.width(), 1) * (b - a)
            self.show_range(a - dt, b - dt)
        else:
            self.setCursor(Qt.SizeHorCursor if self._near_marker(x) else Qt.OpenHandCursor)

    def mouseReleaseEvent(self, _event):
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


# --- preferences ---

OUT_DIR, OUT_GROUP = "output/dir", "output/group"
OUT_REVEAL = "output/reveal"
OUT_LEVELS = {"output/artist": "Исполнитель", "output/year": "Год", "output/album": "Альбом"}


def pref_bool(key: str, default: bool) -> bool:
    return QSettings().value(key, default) in (True, "true", "1", 1)


def output_prefs() -> tuple[Path | None, tuple[bool, bool, bool] | None]:
    """(root folder or None for next to the source, (artist, year, album) grouping or None)."""
    root = QSettings().value(OUT_DIR, "") or None
    if not root:
        return None, None
    group = tuple(pref_bool(k, True) for k in OUT_LEVELS) if pref_bool(OUT_GROUP, False) else None
    return Path(root), group


class PrefsDialog(QDialog):
    """Output folder and layout, app icon. Every change is saved at once."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle(_("Настройки"))
        self.setMinimumWidth(520)
        st = QSettings()

        out, g = card(_("Сохранение"))
        self.next_to = QRadioButton(_("Рядом с исходным файлом"))
        self.to_dir = QRadioButton(_("В папку"))
        self.dir_l = QLabel(objectName="field")
        pick = QPushButton(_("Выбрать…"), objectName="small", clicked=self._pick)
        g.addWidget(self.next_to, 0, 0, 1, 3)
        g.addWidget(self.to_dir, 1, 0)
        g.addWidget(self.dir_l, 1, 1)
        g.addWidget(pick, 1, 2)
        self.group = QCheckBox(_("Раскладывать по папкам"))
        g.addWidget(self.group, 2, 0, 1, 3)
        levels = QHBoxLayout()
        levels.setContentsMargins(24, 0, 0, 0)
        self.levels = {}
        for key, title in OUT_LEVELS.items():
            box = QCheckBox(_(title), checked=pref_bool(key, True))
            box.toggled.connect(lambda on, k=key: self._save(k, on))
            self.levels[key] = box
            levels.addWidget(box)
        levels.addStretch()
        g.addLayout(levels, 3, 0, 1, 3)
        self.example = QLabel(objectName="field", wordWrap=True)
        g.addWidget(self.example, 4, 0, 1, 3)
        self.open_after = QCheckBox(_("Открывать папку после сохранения"), checked=pref_bool(OUT_REVEAL, True))
        self.open_after.toggled.connect(lambda on: self._save(OUT_REVEAL, on))
        g.addWidget(self.open_after, 5, 0, 1, 3)

        look, g2 = card(_("Внешний вид"))
        self.icon = QComboBox()
        for key, title in ICON_CHOICES.items():
            self.icon.addItem(_(title), key)
        self.icon.setCurrentIndex(list(ICON_CHOICES).index(icon_choice()))
        self.icon.currentIndexChanged.connect(self._icon)
        row(g2, _("Иконка приложения"), self.icon)
        self.lang = QComboBox()
        for key, title in i18n.LANGUAGES.items():
            self.lang.addItem(_(title) if key == "system" else title, key)
        saved = st.value(LANG_KEY, "system")
        self.lang.setCurrentIndex(max(0, list(i18n.LANGUAGES).index(saved) if saved in i18n.LANGUAGES else 0))
        self.lang.currentIndexChanged.connect(self._lang)
        row(g2, _("Язык"), self.lang)
        self.lang_note = QLabel(_("Язык сменится после перезапуска приложения"), objectName="field")
        self.lang_note.hide()
        g2.addWidget(self.lang_note, g2.rowCount(), 1)

        self.dir = st.value(OUT_DIR, "") or ""
        self.group.setChecked(pref_bool(OUT_GROUP, False))
        (self.to_dir if self.dir else self.next_to).setChecked(True)
        self.to_dir.toggled.connect(self._mode)
        self.group.toggled.connect(lambda on: self._save(OUT_GROUP, on))

        close = QPushButton(_("Готово"), objectName="primary", clicked=self.accept)
        v = QVBoxLayout(self)
        v.addWidget(out)
        v.addWidget(look)
        v.addWidget(close, 0, Qt.AlignRight)
        self._update()

    def _save(self, key, value):
        QSettings().setValue(key, value)
        self._update()

    def _pick(self):
        d = QFileDialog.getExistingDirectory(self, _("Папка для сохранения"), self.dir or str(Path.home()))
        if d:
            self.dir = d
            self.to_dir.setChecked(True)
            self._mode(True)

    def _mode(self, to_dir: bool):
        if to_dir and not self.dir:
            self._pick()
            return
        QSettings().setValue(OUT_DIR, self.dir if to_dir else "")
        self._update()

    def _lang(self):
        QSettings().setValue(LANG_KEY, self.lang.currentData())
        self.lang_note.show()

    def _icon(self):
        QSettings().setValue(ICON_KEY, self.icon.currentData())
        apply_icon(QApplication.instance())

    def _update(self):
        to_dir = self.to_dir.isChecked() and bool(self.dir)
        self.dir_l.setText(self.dir_l.fontMetrics().elidedText(self.dir or _("не выбрана"), Qt.ElideMiddle, 300))
        self.dir_l.setToolTip(self.dir)
        self.group.setEnabled(to_dir)
        for box in self.levels.values():
            box.setEnabled(to_dir and self.group.isChecked())
        root, group = output_prefs()
        tags = {"artist": _("Исполнитель"), "album": _("Альбом"), "date": "2005"}
        folder = io.organized_dir(root, tags, *group) if root and group else root
        sample = io.output_path(Path(_("Песня") + ".mp3"), "mp3", {}, folder)
        shown = sample.relative_to(root) if folder else None
        self.example.setText(_("Пример: …/{path}", path=shown.as_posix()) if shown
                             else _("Файл ляжет рядом с исходником: {name}", name=sample.name))


# --- window ---

class Window(QMainWindow):
    updateFound = Signal(object)
    updateProgress = Signal(int)
    updateReady = Signal(object)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Count-In")
        self.setAcceptDrops(True)
        self.resize(1180, 780)

        self.current: Track | None = None
        self.info: io.AudioInfo | None = None
        self.pcm, self.sr = None, 0
        self.stem_cache = None  # (first sample, stems) of the current track
        self.taps: list[float] = []
        self.job: Job | None = None
        self.analysis_job: Job | None = None
        self.analysis_pool = QThreadPool()
        self.analysis_pool.setMaxThreadCount(1)
        self.job_pool = QThreadPool()
        self.job_pool.setMaxThreadCount(1)
        self.player = Player()
        self.play_start = None
        self.play_timer = QTimer(self, interval=30, timeout=self._tick)
        self.last_saved: Path | None = None
        self.release = None
        self.staged: Path | None = None  # unpacked update waiting for a restart or quit

        self.updateFound.connect(self._update_found)
        self.updateProgress.connect(self._update_progress)
        self.updateReady.connect(self._update_ready)
        self._build_menu()
        self._build_body()
        self._build_status()
        self._labels()
        self._enable()

        # Space plays from anywhere except text fields, so focus on a slider or checkbox does not swallow it.
        QApplication.instance().installEventFilter(self)

    def eventFilter(self, obj, e):
        if (e.type() == QEvent.KeyPress and e.key() == Qt.Key_Space and not e.isAutoRepeat()
                and not e.modifiers() and QApplication.activeModalWidget() is None):
            # Key events go to the focused widget first, so obj is what would type the space.
            if not isinstance(obj, (QLineEdit, QAbstractSpinBox)):
                self._play()
                return True
        return super().eventFilter(obj, e)

    # --- layout ---

    def _build_menu(self):
        mb = self.menuBar()
        file = mb.addMenu(_("Файл"))
        self.open_a = file.addAction(_("Открыть…"), QKeySequence.Open, self._open_dialog)
        self.save_a = file.addAction(_("Сохранить"), QKeySequence.Save, self._save)
        self.reveal_a = file.addAction(_("Показать сохранённый файл"), self._reveal)
        file.addSeparator()
        prefs = file.addAction(_("Настройки…"), QKeySequence.Preferences, self._prefs)
        prefs.setMenuRole(QAction.PreferencesRole)

        play = mb.addMenu(_("Воспроизведение"))
        self.play_a = play.addAction(_("Прослушать / Стоп (Пробел)"), self._play)

        help_ = mb.addMenu(_("Справка"))
        about = help_.addAction(_("О программе Count-In"), self._about)
        about.setMenuRole(QAction.AboutRole)

        # ApplicationSpecificRole puts it into the app menu on macOS, under Settings, as macOS apps do.
        self.update_a = help_.addAction(_("Проверить обновления…"), self._update_action)
        self.update_a.setMenuRole(QAction.ApplicationSpecificRole)
        help_.addAction(_("Страница проекта"), lambda: QDesktopServices.openUrl(QUrl(f"https://github.com/{update.REPO}")))

    def _header(self) -> QWidget:
        head = QWidget(objectName="header")
        h = QHBoxLayout(head)
        h.setContentsMargins(22, 14, 22, 6)
        text = QVBoxLayout()
        text.setSpacing(2)
        self.title = QLabel("Count-In", objectName="title")
        self.subtitle = QLabel(objectName="subtitle")
        text.addWidget(self.title)
        text.addWidget(self.subtitle)
        h.addLayout(text, 1)
        self.open_b = QPushButton(_("Открыть…"), objectName="tool", clicked=self._open_dialog)
        self.play_b = QPushButton(_("▶  Прослушать"), objectName="primary", clicked=self._play, toolTip=_("Пробел"))
        self.save_b = QPushButton(_("Сохранить"), objectName="tool", clicked=self._save, toolTip="⌘S")
        for b in (self.open_b, self.play_b, self.save_b):
            b.setFocusPolicy(Qt.NoFocus)
            h.addWidget(b)
        return head

    def _build_body(self):
        self.stack = QStackedWidget()
        drop_page = QWidget()
        dv = QVBoxLayout(drop_page)
        dv.setContentsMargins(40, 20, 40, 40)
        drop = QLabel(_("Перетащите сюда трек\n\nmp3, FLAC, ALAC, WAV"), objectName="drop", alignment=Qt.AlignCenter)
        dv.addWidget(drop)
        self.stack.addWidget(drop_page)
        self.stack.addWidget(self._editor())

        body = QWidget()
        v = QVBoxLayout(body)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self._header())
        v.addWidget(self.stack, 1)
        v.addWidget(self._progress_panel())
        self.setCentralWidget(body)

    def _progress_panel(self) -> QWidget:
        panel = QFrame(objectName="card")
        g = QGridLayout(panel)
        g.setContentsMargins(16, 10, 16, 12)
        self.prog_label = QLabel(objectName="cardTitle")
        self.prog_eta = QLabel(objectName="field")
        self.progress = QProgressBar(textVisible=False, maximumHeight=8)
        self.cancel_b = QPushButton(_("Отмена"), objectName="tool", clicked=self._cancel)
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
        self.detector = QLabel(objectName="subtitle")
        self.warning = QLabel(objectName="warning", wordWrap=True)
        self.wave = Wave()
        self.wave.t0Changed.connect(self._marker_moved)
        hint = QLabel(_("Тяните оранжевый маркер «1» на первую сильную долю. Колесо — масштаб, перетаскивание — сдвиг, "
                        "⌥ — без прилипания к долям."), objectName="subtitle", wordWrap=True)

        tempo, g = card(_("Темп и первая доля"))
        self.bpm_spin = QDoubleSpinBox(decimals=2, minimum=30, maximum=320, singleStep=0.1, suffix=" BPM")
        self.bpm_spin.valueChanged.connect(self._bpm_edited)
        x2 = QPushButton("×2", objectName="small", clicked=lambda: self._scale_bpm(2.0))
        half = QPushButton("÷2", objectName="small", clicked=lambda: self._scale_bpm(0.5))
        tap = QPushButton("Tap", objectName="small", clicked=self._tap)
        row(g, _("Темп"), self.bpm_spin, x2, half, tap)
        self.t0_spin = QDoubleSpinBox(decimals=3, maximum=36000, singleStep=0.001, suffix=_(" с"))
        self.t0_spin.valueChanged.connect(self._t0_edited)
        prev_b = QPushButton("◀", objectName="small", clicked=lambda: self._shift_beat(-1))
        next_b = QPushButton("▶", objectName="small", clicked=lambda: self._shift_beat(1))
        row(g, _("Доля «1»"), self.t0_spin, prev_b, next_b)
        self.alt_b = QPushButton(_("Взять кандидата детектора"), objectName="small", clicked=self._use_alt)
        g.addWidget(self.alt_b, g.rowCount(), 1)
        for b in (x2, half, tap, prev_b, next_b, self.alt_b):
            b.setFocusPolicy(Qt.NoFocus)

        count, g = card(_("Отсчёт"))
        self.style_seg = Segmented([(_("Ровный"), "even"), (_("Нарастание"), "buildup"), ("MIDI…", "midi")], "buildup")
        self.style_seg.changed.connect(self._style_changed)
        row(g, _("Рисунок"), self.style_seg)
        self.bars_seg = Segmented([(_("1 такт"), 1), (_("2 такта"), 2)], 2)
        self.bars_seg.changed.connect(self._refresh)
        row(g, _("Длина"), self.bars_seg)
        self.meter_seg = Segmented([(m, m) for m in rd.METERS], "4/4")
        self.meter_seg.changed.connect(self._meter_changed)
        row(g, _("Размер"), self.meter_seg)
        self.volume = QSlider(Qt.Horizontal, minimum=-24, maximum=6, value=0, minimumWidth=140)
        self.volume_l = QLabel(minimumWidth=60)
        row(g, _("Громкость"), self.volume, self.volume_l)
        self.pattern: Path | None = None

        mix, g = card(_("Бэктрек"))
        self.guitar = QSlider(Qt.Horizontal, minimum=MUTE, maximum=0, value=0, minimumWidth=140)
        self.guitar_l = QLabel(minimumWidth=90)
        row(g, _("Гитара"), self.guitar, self.guitar_l)
        self.vocals = QSlider(Qt.Horizontal, minimum=MUTE, maximum=0, value=0, minimumWidth=140)
        self.vocals_l = QLabel(minimumWidth=90)
        row(g, _("Вокал"), self.vocals, self.vocals_l)
        self.trim = QCheckBox(_("Обрезать тишину в начале"))
        g.addWidget(self.trim, g.rowCount(), 1)
        self.fmt = QComboBox()
        for key, name in FORMAT_NAMES.items():
            self.fmt.addItem(_(name), key)
        row(g, _("Формат"), self.fmt)

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
        v.setContentsMargins(22, 4, 22, 12)
        v.setSpacing(8)
        v.addWidget(self.detector)
        v.addWidget(self.warning)
        v.addWidget(self.wave, 1)
        v.addWidget(hint)
        v.addSpacing(6)
        v.addLayout(cards)
        return page

    def _build_status(self):
        sb = QStatusBar()
        sb.setSizeGripEnabled(False)
        self.setStatusBar(sb)
        self.file_l = QLabel()
        self.status = QLabel()
        self.reveal_b = QPushButton(_("Показать в Finder") if sys.platform == "darwin" else _("Показать в папке"),
                                    objectName="small", clicked=self._reveal)
        self.reveal_b.setFocusPolicy(Qt.NoFocus)
        self.version_l = QLabel(f"v{update.current_version()}")

        # Round blue badge left of the version, shown only when a newer release exists.
        self.update_b = QPushButton("↓", objectName="update", clicked=self._update_action)
        self.update_b.setFocusPolicy(Qt.NoFocus)
        self.update_b.setCursor(Qt.PointingHandCursor)
        sb.addWidget(self.file_l)
        sb.addWidget(self.status, 1)
        sb.addPermanentWidget(self.reveal_b)

        # Badge and version share one widget, so the status bar's item spacing stays out from between them.
        ver = QWidget()
        h = QHBoxLayout(ver)
        h.setContentsMargins(0, 0, 8, 0)
        h.setSpacing(0)
        for x in (self.update_b, self.version_l):
            x.setAttribute(Qt.WA_LayoutUsesWidgetRect)  # macOS style otherwise pads them unevenly
            h.addWidget(x)
        sb.addPermanentWidget(ver)
        self.reveal_b.hide()
        self._show_version()

    def _show_version(self):
        """The badge shows once an update is ready to install, or when it can only be downloaded by hand."""
        r = self.release
        self.update_b.setVisible(bool(r) and (self.staged is not None or not update.can_self_update(r)))
        if self.staged:
            self.update_b.setToolTip(_("Перезапустить и обновить до v{version}", version=r.version))
        elif r:
            self.update_b.setToolTip(_("Доступна v{version}, скачать", version=r.version))

    # --- dialogs ---

    def _prefs(self):
        PrefsDialog(self).exec()

    def _about(self):
        QMessageBox.about(self, _("О программе Count-In"), (
            f"<h3>Count-In v{update.current_version()}</h3>"
            f"<p>{_('Барабанный отсчёт перед первой сильной долей и бэктреки без гитары или вокала.')}</p>"
            f'<p><a href="https://github.com/{update.REPO}">github.com/{update.REPO}</a></p>'
            "<p>beat_this (MIT), BS-RoFormer (MIT), FFmpeg (LGPL), Qt for Python (LGPL), PyTorch (BSD).</p>"))

    # --- updates ---

    def check_updates(self, manual: bool = False):
        """Asks GitHub in a daemon thread; the answer comes back to the UI thread as a queued signal."""
        self._manual_check = manual
        self._update_menu(_("Проверка обновлений…"), enabled=False)
        threading.Thread(target=self._check_in_thread, daemon=True).start()

    def _check_in_thread(self):
        try:
            release = update.check()
        except Exception as e:  # offline, TLS, rate limit
            log.warning("update check failed: %s", e)
            release = e
        self._emit(self.updateFound, release)

    def _emit(self, signal, value):
        try:
            signal.emit(value)
        except RuntimeError:  # the window closed while the thread worked
            pass

    def _update_menu(self, text: str, enabled: bool = True):
        self.update_a.setText(text)
        self.update_a.setEnabled(enabled)

    def _update_found(self, release):
        manual = getattr(self, "_manual_check", False)
        self._update_menu(_("Проверить обновления…"))
        if isinstance(release, Exception):
            if manual:
                self.status.setText(_("Не удалось проверить обновления: {e}", e=release))
        elif not release:
            if manual:
                self.status.setText(_("Установлена последняя версия, v{version}", version=update.current_version()))
        elif update.can_self_update(release):
            self.release = release
            if manual:
                QMessageBox.information(self, "Count-In", _(
                    "Доступна новая версия v{version}. Она скачается и установится автоматически.",
                    version=release.version))
            self._update_menu(_("Загрузка обновления…"), enabled=False)
            threading.Thread(target=self._download_in_thread, args=(release,), daemon=True).start()
        else:
            self.release = release
        self._show_version()

    def _download_in_thread(self, release):
        last = -1

        def progress(done, total):
            nonlocal last
            pct = done * 100 // total if total else 0
            if pct != last:
                last = pct
                self._emit(self.updateProgress, pct)

        try:
            result = update.download(release, update.installed_bundle(), progress)
        except Exception as e:
            log.warning("update download failed: %s", e)
            result = e
        self._emit(self.updateReady, result)

    def _update_progress(self, pct: int):
        self._update_menu(_("Загрузка обновления… {p}%", p=pct), enabled=False)

    def _update_ready(self, result):
        if isinstance(result, Exception):
            self._update_menu(_("Проверить обновления…"))
            self.status.setText(_("Не удалось загрузить обновление: {e}", e=result))

            # The badge falls back to the download page.
            self.release.sha256 = ""
        else:
            self.staged = result
            self._update_menu(_("Перезапустить и обновить до v{version}", version=self.release.version))
        self._show_version()

    def _update_action(self, *_args):
        """Menu item and badge: restart into a ready update, open the download page, or check again."""
        if self.staged:
            if self.job:
                self.status.setText(_("Дождитесь окончания сохранения"))
                return
            update.install_on_exit(update.installed_bundle(), self.staged, relaunch=True)
            self.staged = None
            self.close()
        elif self.release and not update.can_self_update(self.release):
            QDesktopServices.openUrl(QUrl(self.release.url))
        else:
            self.check_updates(manual=True)

    # --- files ---

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.open_files([Path(u.toLocalFile()) for u in e.mimeData().urls()])

    def _open_dialog(self):
        exts = " ".join(f"*{x}" for x in sorted(AUDIO_EXT))
        f, _sel = QFileDialog.getOpenFileName(self, _("Трек"), "", _("Аудио ({exts})", exts=exts))
        if f:
            self.open_files([Path(f)])

    def open_files(self, paths: list[Path]):
        """Opens the first audio file; the app works on one track at a time."""
        path = next((p for p in paths if p.is_file() and p.suffix.lower() in AUDIO_EXT), None)
        if not path or self.job:
            if paths and not path:
                self.status.setText(_("Это не аудиофайл: mp3, FLAC, ALAC или WAV"))
            return
        self._stop()
        if self.analysis_job:
            self.analysis_job.cancelled.set()
        t = self.current = Track(path)
        self.pcm, self.info, self.stem_cache, self.last_saved = None, None, None, None
        self.reveal_b.hide()
        self.status.setText("")
        self.stack.setCurrentIndex(1)
        self.wave.env, self.wave.t0, self.wave.beats, self.wave.downs, self.wave.clicks = None, None, [], [], []
        self.wave._cache = (None, None)
        self.wave.update()
        self.warning.hide()
        self.title.setText(path.stem)
        self.subtitle.setText("")
        self.detector.setText(_("Анализ…"))
        try:
            self.info = io.probe(path)
            self._show_file()
        except RuntimeError as e:
            self.file_l.setText(str(e)[:120])
        self.analysis_job = Job(analyze_job, t)
        self.analysis_job.signals.done.connect(self._analyzed)
        self.analysis_pool.start(self.analysis_job)
        self._enable()

    def _show_file(self):
        i = self.info
        tags = i.tags
        who = " — ".join(x for x in (tags.get("artist"), tags.get("album")) if x)
        year = (tags.get("date") or "")[:4]
        self.subtitle.setText(who + (f" ({year})" if year and who else year))
        self.file_l.setText(f"{io.describe(i)} · {fmt_time(i.duration)[:-4]}")

    def _analyzed(self, result):
        t, pcm, sr = result
        if t is not self.current:
            return
        if t.error:
            self.detector.setText("")
            self.warning.setText(t.error)
            self.warning.show()
            self._enable()
            return
        t.bpm = 60.0 / an.beat_period(t.analysis, rd.METERS[self.meter_seg.value()][0])[0]
        t.t0 = t.analysis.first_downbeat
        self.pcm, self.sr = pcm, sr
        a = t.analysis
        self.wave.set_audio(self.pcm, self.sr)
        self.wave.beats, self.wave.downs = list(a.beats), list(a.downbeats)
        self.detector.setText(_("Детектор: {bpm:.2f} BPM · «1» на {t} · уверенность {conf:.0%}",
                                bpm=a.bpm, t=fmt_time(a.first_downbeat), conf=a.confidence))
        notes = [translate(n) for n in a.notes]
        if a.confidence < 0.4:
            notes.insert(0, _("детектор не уверен, проверьте маркер «1» на слух"))
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
        self._refresh()

    # --- edits ---

    def _bpm_edited(self, v: float):
        if self.current:
            self.current.bpm, self.current.bpm_manual = v, True
            self._refresh()

    def _scale_bpm(self, k: float):
        if self.current:
            self.current.bpm = float(np.clip(self.current.bpm * k, 30, 320))
            self.current.bpm_manual = True
            self._show_track_values()

    def _tap(self):
        now = time.monotonic()
        if self.taps and now - self.taps[-1] > 2.0:
            self.taps = []
        self.taps = (self.taps + [now])[-8:]
        if len(self.taps) >= 3 and self.current:
            self.current.bpm = float(np.clip(60.0 / float(np.median(np.diff(self.taps))), 30, 320))
            self.current.bpm_manual = True
            self._show_track_values()

    def _meter_changed(self, meter: str):
        t = self.current
        if t and t.analysis and not t.bpm_manual:
            t.bpm = 60.0 / an.beat_period(t.analysis, rd.METERS[meter][0])[0]
            self._show_track_values()
        else:
            self._refresh()

    def _t0_edited(self, v: float):
        if self.current:
            self.current.t0 = v
            self._refresh()

    def _marker_moved(self, t0: float):
        self.current.t0 = t0
        self.t0_spin.blockSignals(True)
        self.t0_spin.setValue(t0)
        self.t0_spin.blockSignals(False)
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
            f, _sel = QFileDialog.getOpenFileName(self, _("MIDI-шаблон"), "", "MIDI (*.mid *.midi)")
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
        self.volume_l.setText(_("{v:+d} дБ", v=self.volume.value()))
        for s, l in ((self.guitar, self.guitar_l), (self.vocals, self.vocals_l)):
            v = s.value()
            l.setText(_("как есть") if v == 0 else _("убрать") if v == MUTE else _("{v} дБ", v=v))

    def settings(self) -> Settings:
        levels = {}
        for name, s in (("guitar", self.guitar), ("vocals", self.vocals)):
            if s.value() < 0:
                levels[name] = float("-inf") if s.value() == MUTE else float(s.value())
        out_root, group = output_prefs()
        return Settings(
            style=self.style_seg.value(), bars=self.bars_seg.value(), meter=self.meter_seg.value(),
            pattern=self.pattern, volume_db=float(self.volume.value()),
            levels=levels, pre_roll="trim" if self.trim.isChecked() else "keep", fmt=self.fmt.currentData(),
            out_root=out_root, group=group,
        )

    def _refresh(self, *_):
        t = self.current
        if not t or not t.analysis:
            return
        try:
            self.wave.clicks = clicks_for(t, self.settings())
        except Exception as e:  # unreadable or empty MIDI pattern
            self.wave.clicks = []
            self.status.setText(str(e))
        self.wave.t0 = t.t0
        self.wave.update()

    # --- jobs ---

    def _enable(self):
        busy = self.job is not None
        ready = bool(self.current and self.current.analysis and self.pcm is not None)
        self.play_b.setEnabled(ready and (not busy or self.play_start is not None))
        self.play_a.setEnabled(self.play_b.isEnabled())
        self.save_b.setEnabled(ready and not busy)
        self.save_a.setEnabled(ready and not busy)
        self.open_b.setEnabled(not busy)
        self.open_a.setEnabled(not busy)
        self.reveal_a.setEnabled(self.last_saved is not None)
        self.prog_panel.setVisible(busy)

    def _run(self, fn, *args, on_done):
        self.reveal_b.hide()
        self.job = Job(fn, *args)
        self.job.signals.progress.connect(self._progress)
        self.job.signals.done.connect(on_done)
        self.job.signals.failed.connect(self._failed)
        self.job.signals.cancelled.connect(self._cancelled)
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
        rest = (_("осталось ~{m}:{s:02d}", m=int(left // 60), s=int(left % 60)) if left is not None
                else _("считаю время…"))
        self.prog_eta.setText(f"{frac:.0%} · {rest}")

    def _finish(self):
        self.job = None
        self._enable()

    def _failed(self, msg: str):
        self._finish()
        self.status.setText(msg)
        QMessageBox.warning(self, "Count-In", msg)

    def _cancelled(self):
        self._finish()
        self.status.setText(_("Отменено"))

    def _cancel(self):
        if self.job:
            self.job.cancelled.set()
            self.status.setText(_("Отменяю…"))

    def _play(self):
        if self.play_start is not None:
            self._stop()
            return
        if self.job or not (self.current and self.current.analysis and self.pcm is not None):
            return
        s = self.settings()
        self._run(preview_job, self.current, s, self.pcm, self.sr, self.stem_cache, bool(s.levels),
                  on_done=self._preview_ready)

    def _preview_ready(self, result):
        self._finish()
        track, base, stems, sr, start, new_cache, clipped = result
        if track is not self.current:
            return
        if new_cache:
            self.stem_cache = new_cache
        self.status.setText(clip_note(clipped) or (_("Гитару и вокал можно менять на ходу") if stems
                            else _("Чтобы менять гитару и вокал на ходу, опустите ползунок до прослушивания")))
        self._gains()
        try:
            self.player.play(base, stems, sr)
        except Exception as e:  # no output device, unsupported channel count
            log.exception("audio output failed")
            self.status.setText(_("Не удалось воспроизвести: {e}", e=e))
            return
        self.play_start = start
        self.play_b.setText(_("■  Стоп"))
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
        self.play_b.setText(_("▶  Прослушать"))
        self.wave.cursor_t = None
        self.wave.update()
        self._enable()

    def _save(self):
        if not self.save_b.isEnabled():
            return
        self._stop()
        s = self.settings()
        out = target_path(self.current.path, s, self.info or io.probe(self.current.path))
        if out.exists() and QMessageBox.question(
                self, "Count-In", _("Файл {name} уже есть. Заменить?", name=out.name)) != QMessageBox.Yes:
            return
        self._run(save_job, self.current, s, self.stem_cache, on_done=self._saved)

    def _saved(self, result):
        track, out, full, clipped = result
        self._finish()
        if track is not self.current:
            return
        if full:
            self.stem_cache = full
        self.last_saved = out
        self.status.setText(" · ".join(x for x in (_("Сохранено: {name}", name=out.name), clip_note(clipped)) if x))
        self.reveal_b.show()
        self._enable()
        if pref_bool(OUT_REVEAL, True):
            reveal(out)

    def _reveal(self):
        if self.last_saved:
            reveal(self.last_saved)

    def closeEvent(self, e):
        # A ready update installs on quit too, like Sparkle does; it just does not relaunch.
        if self.staged:
            update.install_on_exit(update.installed_bundle(), self.staged, relaunch=False)
            self.staged = None
        self._stop()
        self._cancel()
        if self.analysis_job:
            self.analysis_job.cancelled.set()
        self.analysis_pool.clear()
        self.job_pool.clear()
        super().closeEvent(e)


class App(QApplication):
    """Takes files dropped on the Dock icon or opened with Finder."""

    window: Window | None = None

    def event(self, e):
        if e.type() == QEvent.FileOpen and self.window:
            self.window.open_files([Path(e.file())])
            return True
        return super().event(e)


def style_sheet(app: QApplication) -> str:
    """STYLE with colors for the system light or dark appearance."""
    scheme = app.styleHints().colorScheme()
    dark = (scheme == Qt.ColorScheme.Dark if scheme != Qt.ColorScheme.Unknown
            else app.palette().window().color().lightness() < 128)
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

    setup_log()
    i18n.set_language(QSettings().value(LANG_KEY, "system"))

    # The window restyles with the system appearance.
    app.styleHints().colorSchemeChanged.connect(lambda _scheme: app.setStyleSheet(style_sheet(app)))
    font = app.font()
    font.setPointSizeF(font.pointSizeF() + 1.5)
    app.setFont(font)
    app.setStyleSheet(style_sheet(app))
    w = app.window = Window()
    w.open_files([Path(a) for a in sys.argv[1:]])
    w.show()

    # CI smoke test: start, show the window, quit; a missing bundled icon fails it.
    if os.environ.get("COUNTIN_SMOKE"):
        QTimer.singleShot(2000, lambda: app.exit(1 if app.windowIcon().isNull() else 0))
    else:
        w.check_updates()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

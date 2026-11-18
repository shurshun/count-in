"""Count-In desktop app."""

import logging
import os
import subprocess
import sys
import threading
import random
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np
from PySide6.QtCore import QEvent, QLocale, QObject, QSettings, QStandardPaths, QUrl, QPointF, QRectF, QRunnable, QSize, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QAction, QDesktopServices, QFont, QIcon, QImage, QKeySequence, QColor, QPainter, QPainterPath, QPen, QPalette, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QAbstractSpinBox, QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFrame,
    QGridLayout, QHBoxLayout, QLabel, QLayout, QLineEdit, QPlainTextEdit, QMainWindow, QMessageBox, QProgressBar, QPushButton, QRadioButton, QScrollArea,
    QGraphicsDropShadowEffect, QSlider, QSpinBox, QStackedWidget, QStatusBar, QVBoxLayout, QWidget,
)

from . import i18n, update
from .i18n import _
from .help import HELP, TIPS
from .core import analyze as an
from .core import io
from .core import render as rd
from .core import zones as zn
from .core import assets
from .core.io import resource

log = logging.getLogger("countin")

AUDIO_EXT = {".mp3", ".flac", ".m4a", ".wav", ".aif", ".aiff", ".ogg"}
PREVIEW_AFTER = 10.0  # seconds of song after the downbeat while the excerpt gets separated on its own
PREVIEW_FROM = 30.0  # seconds played from a point in that case
MUTE = -41  # level slider bottom means "remove"
# Parts the backing-track card can turn down: (stem name in the model, label, icon, mute tooltip)
PARTS = (
    ("guitar", "Гитара", "guitar", "Убрать гитару"),
    ("vocals", "Вокал", "mic", "Убрать вокал"),
    ("bass", "Бас", "bass", "Убрать бас"),
    ("drums", "Барабаны", "drums", "Убрать барабаны"),
    ("piano", "Пианино", "piano", "Убрать пианино"),
)
# Why each job runs, shown under its progress title
JOB_WHY = {
    "zones_job": "Раскладываю песню на дорожки, чтобы найти паузы без убранных инструментов. Это делается один раз на трек.",
    "preview_job": "Готовлю прослушивание: отсчёт и песню с вашими настройками.",
    "save_job": "Сохраняю трек с отсчётом и выбранным уровнем дорожек.",
    "export_job": "Сохраняю мультитрек: партии, клик и темп начинаются с первого такта.",
}

# Lines that turn up while a job runs
PHRASES = (
    "Барабанщик уже считает: раз, два, три, четыре…",
    "Гитарист подкручивает колки",
    "Басист где-то рядом, его просто не слышно",
    "Звукорежиссёр просит ещё немного вокала в мониторе",
    "Метроном не устаёт, в отличие от барабанщика",
    "Первая доля — самая важная, мы её не пропустим",
    "Кто-то за кулисами ищет медиатор",
    "Хай-хэт уже на подставке",
    "В репетиционной пахнет кофе и проводами",
    "Вокалист распевается: ми-ми-ми",
    "Каждая нейросеть мечтает стать барабанщиком",
    "Темп держим, даже когда очень хочется ускориться",
)
FORMAT_NAMES = {None: "Как у исходника", "mp3": "MP3 320 kbps", "flac": "FLAC", "alac": "ALAC (.m4a)", "wav": "WAV"}
ORANGE = QColor(255, 159, 10)
GREEN = QColor(48, 209, 88)

# What the centred progress card says under its title
BUSY_WHY = {
    "analysis": "Ищу темп, доли и первую сильную долю. Обычно это несколько секунд.",
    "separation": "Раскладываю песню на гитару, вокал, бас, барабаны и пианино. Трек уже можно слушать и править.",
}


STYLE = """
QMainWindow, QStackedWidget > QWidget, #header { background: @WINDOW@; }
QStatusBar { background: @SIDEBAR@; border-top: 1px solid @BORDER@; }
QStatusBar QLabel { padding: 0 8px; }
QPushButton#update { background: #0a84ff; color: white; border: none; border-radius: 10px; font-weight: 700;
                     min-width: 20px; max-width: 20px; min-height: 20px; max-height: 20px; padding: 0; }
QPushButton#update:hover { background: #409cff; }
#title { font-size: 24px; font-weight: 600; }
#headerLabel { font-size: 15px; color: @FIELD@; }
QPushButton#iconButton { border: 1px solid @BORDER@; border-radius: 7px; background: rgba(127,127,127,0.12); padding: 0; }
QPushButton#iconButton:hover { background: rgba(127,127,127,0.24); }
#progOverlay { background: transparent; }
#sepWait { color: #ff9f0a; font-weight: 600; }
#progTitle { font-size: 26px; font-weight: 700; }
#progWhy, #progEta { font-size: 15px; color: @FIELD@; }
#progPhrase { font-size: 15px; font-style: italic; color: @FIELD@; }
#subtitle { color: @FIELD@; }
#warning { color: #ff9f0a; }
#drop { border: 2px dashed @BORDER@; border-radius: 18px; color: @FIELD@; font-size: 18px; }
QFrame#card { background: @CARD@; border: 1px solid @BORDER@; border-radius: 12px; }
#cardTitle { font-weight: 600; font-size: 16px; }
QFrame#option { background: @CARD@; border: 1px solid @BORDER@; border-radius: 12px; margin: 1px; }
QFrame#option:focus { border-color: @ACCENT@; }
QFrame#option[selected="true"] { border: 2px solid @ACCENT@; margin: 0; }
#optionTitle { font-weight: 600; font-size: 16px; }
#optionNote { font-size: 13px; color: @FIELD@; }
QFrame#target { background: rgba(127,127,127,0.11); border-radius: 9px; }
#targetName { font-weight: 600; }
#field { color: @FIELD@; }
QPushButton[seg] { border: 1px solid rgba(127,127,127,0.35); background: rgba(127,127,127,0.06);
                   padding: 3px 11px; min-height: 18px; border-radius: 0; }
QPushButton[seg="first"] { border-top-left-radius: 6px; border-bottom-left-radius: 6px; }
QPushButton[seg="last"] { border-top-right-radius: 6px; border-bottom-right-radius: 6px; }
QPushButton[seg]:checked { background: @ACCENT@; color: @ACCENT_TEXT@; border-color: @ACCENT@; }
QPushButton#primary { background: @ACCENT@; color: @ACCENT_TEXT@; border: none;
                      border-radius: 9px; padding: 8px 26px; font-size: 15px; font-weight: 600; min-height: 22px; }
QPushButton#primary:disabled { background: rgba(127,127,127,0.25); }
QPushButton#tool { border: 1px solid @BORDER@; border-radius: 9px; padding: 8px 18px; font-size: 15px;
                   background: @CARD@; min-height: 22px; }
QPushButton#tool:disabled { color: gray; }
QPushButton#small { border: 1px solid rgba(127,127,127,0.3); border-radius: 6px; padding: 2px 8px;
                    background: rgba(127,127,127,0.06); min-width: 26px; }
QSlider::groove:horizontal { height: 4px; background: rgba(127,127,127,0.3); border-radius: 2px; }
QCheckBox#rowCheck { margin-left: 2px; }  /* the box lines up with the row icons above */
QSlider::sub-page:horizontal { background: @ACCENT@; border-radius: 2px; }
QSlider::handle:horizontal { width: 16px; height: 16px; margin: -6px 0; border-radius: 8px;
                             background: white; border: 1px solid rgba(0,0,0,0.25); }
QStatusBar, QStatusBar QLabel { color: @FIELD@; }
QFrame#busyCard { background: @SEP_CARD@; border: 1px solid @BORDER@; border-radius: 20px; }
#busyTitle { font-size: 24px; font-weight: 700; }
#busyWhy { font-size: 14px; color: @FIELD@; }
#busyEta { font-size: 14px; font-weight: 600; }
QFrame#tipCard { background: @TIP_CARD@; border: 1px solid @BORDER@; border-radius: 14px; }
#tipText { font-size: 14px; }
#helpTitle { font-size: 26px; font-weight: 700; }
#helpLine { font-size: 14px; }
QScrollArea#helpArea, QScrollArea#helpArea > QWidget > QWidget { background: @WINDOW@; border: none; }
QFrame#busyPill { background: @SEP_CARD@; border: 1px solid @BORDER@; border-radius: 19px; }
#pillText { font-size: 14px; font-weight: 600; }
#pillNote { font-size: 14px; font-weight: 600; color: #ff9f0a; }
QFrame#transport { background: @CARD@; border: 1px solid @BORDER@; border-radius: 12px; }
QPushButton#tbtn { border: none; border-radius: 8px; background: transparent; min-width: 34px; min-height: 30px; padding: 0 6px; }
QPushButton#tbtn:hover { background: rgba(127,127,127,0.18); }
QPushButton#tplay { border: none; border-radius: 17px; background: @ACCENT@; padding: 0;
                    min-width: 34px; max-width: 34px; min-height: 34px; max-height: 34px; }
QPushButton#tplay:disabled { background: rgba(127,127,127,0.35); }
QFrame#lcd { background: rgba(127,127,127,0.11); border-radius: 9px; }
#lcdTime { font-size: 19px; font-weight: 600; }
#lcdSub { font-size: 12px; color: @FIELD@; }
QFrame#vsep { background: @BORDER@; }
#groupTitle { font-size: 13px; font-weight: 600; color: @FIELD@; }
#chipText { font-size: 14px; font-weight: 600; }
QFrame#hline { background: @BORDER@; max-height: 1px; min-height: 1px; }
QPushButton#round { border: 1px solid @BORDER@; border-radius: 16px; background: rgba(127,127,127,0.10); padding: 0;
                    min-width: 32px; max-width: 32px; min-height: 32px; max-height: 32px; }
QPushButton#round:hover, QPushButton#tapPad:hover { background: rgba(127,127,127,0.22); }
QPushButton#tapPad { border: 1px solid @BORDER@; border-radius: 9px; background: rgba(127,127,127,0.10);
                     font-size: 15px; font-weight: 600; min-height: 30px; padding: 0 14px; }
QPushButton#candidate { border: none; border-radius: 8px; background: rgba(255,159,10,0.16); color: @WARN@;
                        padding: 7px 10px; text-align: left; font-weight: 600; }
#unit { font-size: 13px; font-weight: 600; color: @FIELD@; }
#badge { background: #ff9f0a; color: white; border-radius: 10px; font-weight: 700; min-width: 20px; max-width: 20px;
         min-height: 20px; max-height: 20px; }
"""


# App icon variants. The built ICO/ICNS stay light; this only changes the window and Dock icon while running.
ICON_CHOICES = {"light": "Светлая", "dark": "Тёмная"}
LANG_KEY = "appearance/language"
SITE = "https://count-in.app/"


def site_url(path: str = "") -> str:
    """A page of the site in the app's language: the Russian pages live under /ru/."""
    return SITE + ("" if i18n.language() == "en" else "ru/") + path
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
    bars: list[zn.Bar] = field(default_factory=list)  # whole-song bars from the detected downbeats
    zones: list[zn.Zone] | None = None  # pauses that get the hi-hat; None until searched
    zones_key: tuple | None = None  # (levels, threshold) the search ran with
    search_levels: dict | None = None  # each part's lowest level on this track; pauses are searched with these
    auto_done: bool = False  # the silence threshold was fitted to this track's separated backing
    bar_db: list | None = None  # level of each bar against the song (zones.bar_levels); sets the hi-hat level
    covered: set = field(default_factory=set)  # bars under the pauses as last shown
    dismissed: set = field(default_factory=set)  # bars whose pause the user removed; a search never brings them back


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
    fill: bool = True  # hi-hat in the pauses, found automatically
    click_all: bool = False  # a click on every beat of the song; the pauses then need no fill
    click_voice: str = "beep"  # rd.VOICES key of the song click
    click_db: float = 0.0  # song click level on top of the count-in volume
    fill_build: bool = True  # the last bar of each pause runs into the entry in eighths
    speed: float = 1.0  # playback and export speed, pitch kept (0.8 = 20% slower)
    threshold: float = zn.QUIET_DB  # dB below the song average that counts as a pause


def clicks_for(t: Track, s: Settings) -> list[rd.Click]:
    period = 60.0 / t.bpm
    if s.style == "midi" and s.pattern:
        clicks = rd.pattern_grid(s.pattern, t.t0, period, s.meter)
    else:
        grid = rd.buildup_grid if s.style == "buildup" else rd.click_grid
        clicks = grid(t.t0, period, s.bars, s.meter)
    if s.click_all and t.bars:
        from .core import daw

        clicks += daw.song_clicks(t.analysis.beats, t.t0, period, rd.METERS[s.meter][0], s.click_voice,
                                  10 ** (s.click_db / 20))
    elif s.fill and t.zones:
        clicks += zn.zone_clicks(t.bars, t.zones, t.t0, t.bar_db, s.fill_build)
    return clicks


def search_levels(t: Track, s: Settings) -> dict:
    """The levels the pauses are searched with: each part at the lowest level it was set to on this track.
    Raising a part again, fully or partly, to check the hi-hat against it keeps the pauses where they were;
    only the threshold or the user's own edits change them."""
    out = dict(t.search_levels or {})
    for k, v in s.levels.items():
        out[k] = min(v, out.get(k, 0.0))
    return out


def search_key(t: Track, s: Settings) -> tuple:
    return tuple(sorted(search_levels(t, s).items())), s.threshold


def zones_key(s: Settings) -> tuple:
    return tuple(sorted(s.levels.items())), s.threshold


def find_zones(t: Track, pcm: np.ndarray, sr: int, st: dict[str, np.ndarray] | None, s: Settings,
               backing: np.ndarray | None = None) -> list[zn.Zone]:
    """Pauses: bars that go quiet once the turned-down stems are taken out (backing, when already made)."""
    from .core import stems

    if backing is None:
        backing = stems.attenuate(pcm, st, s.levels) if s.levels and st else pcm
    return zn.zones_from(zn.quiet_bars(backing, sr, t.bars, zn.level_db(pcm), s.threshold))


def mix_job(job: "Job", seq: int, pcm: np.ndarray, sr: int, st, levels: dict, search):
    """The backing (the song minus the turned-down stems), its waveform peaks and, when search holds
    (bars, threshold, search levels, fit the threshold), the pauses. They are searched in the mix without the
    search levels' parts, which may differ from what plays; with fit set the threshold first gets fitted to that
    mix (zones.auto_threshold). Runs off the UI thread: on a six-minute song this is about 0.4 s.
    Returns (seq, backing, peaks, found pauses or None, (threshold, fitted) or None)."""
    from .core import stems

    backing = stems.attenuate(pcm, st, levels) if st and levels else pcm
    env = peaks_per_ms(backing, sr)
    found, used = None, None
    if search is not None:
        bars, threshold, sl, fit = search
        mix = backing if sl == levels else stems.attenuate(pcm, st, sl) if st and sl else pcm
        ref = zn.level_db(pcm)
        fitted = zn.auto_threshold(mix, sr, bars, ref) if fit else None
        if fitted is not None:
            threshold = float(round(fitted))
        found = zn.zones_from(zn.quiet_bars(mix, sr, bars, ref, threshold))
        used = (threshold, fit)
    return seq, backing, env, found, used


def fmt_short(t: float) -> str:
    return f"{int(t // 60)}:{t % 60:04.1f}"


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
            try:
                self.signals.cancelled.emit()
            except RuntimeError:  # the window is gone, nobody listens
                pass
        except Exception as e:  # shown to the user, the app keeps running
            log.exception("job %s failed", getattr(self.fn, "__name__", self.fn))
            try:
                self.signals.failed.emit(f"{type(e).__name__}: {e}")
            except RuntimeError:  # the window is gone, nobody listens
                pass


def analyze_job(job: Job, track: Track):
    """Returns (track, pcm, sr, waveform peaks); the UI keeps the PCM, so the file is decoded once and never on
    the UI thread, and the waveform peaks get made here too."""
    job.report(_("Анализ: {name}", name=track.path.name))
    pcm, sr, env = None, 0, None
    try:
        pcm, info = io.decode(track.path)
        sr = info.sr
        track.duration = len(pcm) / sr
        track.analysis = an.analyze(pcm, sr)
        track.bars = zn.bars_from(track.analysis.beats, track.analysis.downbeats, track.duration)
        track.bar_db = zn.bar_levels(pcm, sr, track.bars)
        env = peaks_per_ms(pcm, sr)
    except Exception as e:
        log.exception("analysis of %s failed", track.path)
        track.error = str(e)
    return track, pcm, sr, env


def cloud_key() -> bool:
    from .core import cloud

    return bool(cloud.key())


def cloud_on() -> bool:
    """Cloud separation is switched on and a key is stored."""
    from .core import cloud

    return pref_bool(CLOUD_KEY, False) and bool(cloud.key())


def cloud_overlap() -> int:
    from .core import cloud

    try:
        v = int(QSettings().value(CLOUD_OVERLAP, 4))
    except (TypeError, ValueError):
        v = 4
    return v if v in cloud.OVERLAPS else 4


def stem_key(song: Path) -> str:
    """Stem cache key; cloud stems of each overlap are kept apart from local ones."""
    from .core import stemcache

    return stemcache.key(song, cloud_overlap() if cloud_on() else None)


def _stems(job: Job, pcm, sr, label: str, urgent: bool = False, song: Path | None = None) -> dict[str, np.ndarray]:
    """Stems from the separation process (core/worker.py), so the interface and the audio keep running. An
    urgent request, a preview excerpt, goes ahead of the whole-song separation. A whole song (song given) goes
    to the cloud when that is on; when the cloud fails, this computer separates it."""
    from .core import worker

    if song is not None and cloud_on():
        from .core import cloud, stemcache

        try:
            began = time.monotonic()
            st = cloud.separate(pcm, sr, stem_key(song), job.report, job.cancelled.is_set, cloud_overlap())
            worker.separator().last = (time.monotonic() - began, _("в облаке"))
            return st
        except cloud.Cancelled:
            raise Cancelled
        except cloud.CloudError as e:
            log.warning("cloud separation failed, separating here: %s", e)
            label = _("Облако недоступно, разделяю на этом компьютере")
    job.report(label)
    try:
        return worker.separator().separate(pcm, sr, lambda d, n: job.report(label, d, n),
                                           only=tuple(p[0] for p in PARTS), urgent=urgent,
                                           cancelled=job.cancelled.is_set)
    except worker.Cancelled:
        raise Cancelled


def _covers(cached, i0: int, i1: int) -> bool:
    """cached is (first sample, {stem: array}) and holds samples i0..i1."""
    return bool(cached) and cached[0] <= i0 and cached[0] + len(cached[1]["guitar"]) >= i1


def preview_job(job: Job, track: Track, s: Settings, pcm: np.ndarray, sr: int, cached, separate: bool,
                at: float | None = None):
    """From the count-in, or from song time `at` (play from here), to the song end, with stems kept apart so the
    player can mix them live, rendered at s.speed. Only when the parts have yet to be separated for it, it stops
    PREVIEW_AFTER seconds after the downbeat or PREVIEW_FROM seconds after `at`.

    Returns (song with clicks, {stem: aligned array}, sr, original-song time of the first sample, new stem cache,
    clipped samples). Output time runs at s.speed: song time = start + output time * speed.
    """
    clicks = clicks_for(track, s)
    dur = len(pcm) / sr
    first = min(c.time for c in clicks) if at is None else at
    i0 = int(max(0.0, first - 0.5) * sr)
    if not separate or _covers(cached, i0, len(pcm)):
        end = dur
    else:
        end = track.t0 + PREVIEW_AFTER if at is None else at + PREVIEW_FROM
    i1 = int(min(end, dur) * sr)
    part, new_cache = None, None
    if _covers(cached, i0, i1):
        c0, st = cached
        part = {k: v[i0 - c0 : i1 - c0] for k, v in st.items()}
    elif separate and not cloud_on():  # with the cloud the whole song is on its way; the excerpt plays as is
        part = _stems(job, pcm[i0:i1], sr, _("Разделяю отрывок"), urgent=True)
        new_cache = (i0, part)

    job.report(_("Сборка отрывка"))
    lo_t = i0 / sr
    win = [rd.Click((c.time - lo_t) / s.speed, c.accent, c.gain, c.voice)
           for c in clicks if first - 0.35 <= c.time < end]
    song, part = pcm[i0:i1], dict(part or {})
    if s.speed != 1.0:
        # Stretching shifts phases, so a stretched song minus a stretched stem no longer cancels. The slowed
        # song gets rebuilt from its slowed pieces, every stem and the rest, so the player's live subtraction
        # stays exact.
        rest = song - sum(part.values()) if part else song
        *slow, rest = rd.stretch_many([*part.values(), rest], sr, s.speed)
        part = dict(zip(part, slow))
        song = rest + sum(part.values()) if part else rest
    r = rd.render(song, sr, win, 0.0, "keep", s.volume_db, rd.load_samples(sr))
    base = r.pcm

    # Stems go to the same output positions as the song samples they came from.
    aligned = {}
    a = int(round(r.offset * sr))
    for k, v in part.items():
        z = np.zeros_like(base)
        lo, hi = max(a, 0), min(a + len(v), len(base))
        z[lo:hi] = v[lo - a : hi - a]
        aligned[k] = z
    return track, base, aligned, sr, lo_t - r.offset * s.speed, new_cache, r.clipped


def target_path(song: Path, s: Settings, info: io.AudioInfo) -> Path:
    """Where the saved file goes: the output folder from the preferences, optionally Artist/Year - Album."""
    folder = s.out_root
    if folder and s.group:
        folder = io.organized_dir(folder, info.tags, *s.group)
    return io.output_path(song, s.fmt or info.format, s.levels, folder, s.speed)


def save_job(job: Job, t: Track, s: Settings, cached):
    """Writes the track. Returns (track, saved path, (0, stems) when separated or None, clipped samples)."""
    from .core import stems

    job.report(_("{name}: чтение", name=t.path.name))
    pcm, info = io.decode(t.path)
    full = None
    if s.levels:
        st = cached[1] if _covers(cached, 0, len(pcm)) else _stems(job, pcm, info.sr, _("{name}: разделение", name=t.path.name), song=t.path)
        full = (0, st)
        if s.fill and t.zones is None:
            t.zones, t.zones_key = find_zones(t, pcm, info.sr, st, s), zones_key(s)
        pcm = stems.attenuate(pcm, st, s.levels)
    job.report(_("{name}: запись", name=t.path.name))
    if s.speed != 1.0:
        job.report(_("{name}: замедление", name=t.path.name))
    r = rd.render(pcm, info.sr, clicks_for(t, s), t.t0, s.pre_roll, s.volume_db, rd.load_samples(info.sr), s.speed)
    out = target_path(t.path, s, info)
    out.parent.mkdir(parents=True, exist_ok=True)
    io.encode(r.pcm, info, out, tags_from=t.path)
    return t, out, full, r.clipped


def multitrack_dir(song: Path, s: Settings, info: io.AudioInfo) -> Path:
    """The folder of a multitrack export, where the backing track would go."""
    out = target_path(song, s, info)
    tag = f"_{round(s.speed * 100)}pct" if s.speed != 1.0 else ""
    return out.parent / f"{song.stem}_multitrack{tag}"


def export_job(job: Job, t: Track, s: Settings, cached, what: dict):
    """Multitrack for a DAW in one folder: the parts (or the whole song) as WAV, the click grid and the tempo
    map as MIDI, all starting on bar 1. Parts come at full level; the whole song, when parts are off, has the
    turned-down parts taken out. Returns (track, folder, (0, stems) when separated or None, 0)."""
    from .core import daw, stems

    job.report(_("{name}: чтение", name=t.path.name))
    pcm, info = io.decode(t.path)
    full, st = None, None
    if what["parts"] or s.levels:
        st = cached[1] if _covers(cached, 0, len(pcm)) else _stems(job, pcm, info.sr, _("{name}: разделение", name=t.path.name), song=t.path)
        full = (0, st)
    if s.fill and t.zones is None:
        t.zones, t.zones_key = find_zones(t, pcm, info.sr, st, s), zones_key(s)
    clicks = clicks_for(t, s)
    cut = rd.leading_silence(pcm, info.sr) if s.pre_roll == "trim" else 0.0
    g = daw.grid(t.analysis.beats, t.bars, t.zones if s.fill else None, t.t0, 60.0 / t.bpm, s.meter,
                 min([min(t.t0, cut)] + [c.time for c in clicks]))
    folder = multitrack_dir(t.path, s, info)
    folder.mkdir(parents=True, exist_ok=True)
    backing = stems.attenuate(pcm, st, s.levels) if st and s.levels else pcm
    tracks = daw.parts(pcm, st) if what["parts"] else {t.path.stem: backing}
    for name, x in tracks.items():
        job.report(_("{name}: запись", name=f"{name}.wav"))
        daw.write_wav(folder / f"{name}.wav", daw.place(x, info.sr, g, s.speed), info.sr)
    if what["click"]:
        daw.write_clicks(folder / "click.mid", g, clicks, s.meter, s.speed)
    if what["tempo"]:
        daw.write_tempo(folder / "tempo.mid", g, s.meter, s.speed)
    return t, folder, full, 0


def warm_models():
    """Starts the separation process and loads its models there, so the first separation does not wait for
    them; on a Mac's first run it also builds the Core ML model. They stay loaded for the life of the app."""
    try:
        from .core import worker

        worker.separator().warm()
    except Exception:
        log.exception("model warm-up failed")


def zones_job(job: Job, t: Track, s: Settings, pcm: np.ndarray, sr: int, cached):
    """Separates the whole song when needed and finds the pauses. Returns (track, zones, key, (0, stems))."""
    st = cached[1] if _covers(cached, 0, len(pcm)) else _stems(job, pcm, sr, _("{name}: поиск пауз", name=t.path.name), song=t.path)
    job.report(_("Поиск пауз"))
    return t, find_zones(t, pcm, sr, st, s), zones_key(s), (0, st)


def separate_job(job: Job, t: Track, pcm: np.ndarray, sr: int):
    """Separates the whole song in the background right after it opens, or takes the stems from the disk cache
    when the song was separated before. Returns (track, (0, stems), from the cache)."""
    from .core import stemcache

    names = tuple(p[0] for p in PARTS)
    job.report(_("Разделение на дорожки"))
    try:
        k = stem_key(t.path)
        st = stemcache.load(k, names, len(pcm))
    except Exception:
        log.exception("stem cache unreadable")
        k, st = None, None
    if st is not None:
        return t, (0, st), True
    st = _stems(job, pcm, sr, _("Разделение на дорожки"), song=t.path)
    if k is not None:
        def keep():
            try:
                stemcache.save(k, st, sr)
            except Exception:
                log.exception("stem cache not written")

        threading.Thread(target=keep, name="stem-cache", daemon=True).start()  # writing takes a few seconds
    return t, (0, st), False


def _bars_of(zones) -> set[int]:
    return {i for z in zones or [] for i in range(z.first, z.last + 1)}


def peaks_per_ms(pcm: np.ndarray, sr: int) -> np.ndarray:
    """Peak of every millisecond. Bins start at exact millisecond boundaries: whole-sample bins of sr // 1000
    run short at 44.1 kHz and draw a hit 0.4 s late by the third minute."""
    m = np.abs(pcm).max(axis=1)
    starts = (np.arange(int(len(m) * 1000 // sr)) * sr // 1000).astype(np.int64)
    return np.maximum.reduceat(m, starts) if len(starts) else m[:0]


def keep(slider_value: int) -> float:
    """Linear gain a level slider leaves on its stem."""
    return 0.0 if slider_value == MUTE else 10 ** (slider_value / 20)


class Player:
    """Mixes the song and its stems block by block, so stem levels change while it plays."""

    streams = 0  # open streams of all players

    def __init__(self):
        self.stream = None
        self.pos = 0
        self.done = False
        self.gains: dict[str, float] = {}
        self.volume = 1.0  # speaker level only; the saved file never sees it

    def play(self, base: np.ndarray, stems: dict[str, np.ndarray], sr: int, start: int = 0):
        import sounddevice as sd

        self.stop()
        self.base, self.stems, self.sr, self.pos, self.done = base, stems, sr, max(0, start), False
        self.dry = self.bad = 0

        # PortAudio reads the devices and the system default once; reload them, so a switched or plugged-in
        # output gets used. Reloading closes every stream, so only when none is open.
        if not Player.streams:
            sd._terminate()
            sd._initialize()
        log.info("audio output: %s", sd.query_devices(kind="output")["name"])

        # A large buffer keeps audio smooth while the GUI thread holds the interpreter.
        self.stream = sd.OutputStream(samplerate=sr, channels=base.shape[1], dtype="float32",
                                      blocksize=2048, latency="high", callback=self._fill)
        Player.streams += 1
        self.stream.start()

    def _fill(self, out, frames, _time, status):
        if status.output_underflow:
            self.dry += 1
        a = self.pos
        b = min(a + frames, len(self.base))
        x = self.base[a:b].copy()
        for k, st in self.stems.items():
            g = self.gains.get(k, 1.0)
            if g != 1.0:
                x -= (1.0 - g) * st[a:b]
        x *= self.volume
        if not np.isfinite(x).all():  # one NaN makes Core Audio click and fall silent for the rest of the stream
            self.bad += 1
            np.nan_to_num(x, copy=False, nan=0.0, posinf=1.0, neginf=-1.0)
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
        if s := self.stream:
            self.stream = None
            Player.streams -= 1
            s.stop()
            s.close()
            if self.dry or self.bad:
                log.warning("preview audio ran dry in %d blocks, %d blocks had NaN or inf", self.dry, self.bad)


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


def glyph(kind: str, color: QColor, size: int = 18) -> QIcon:
    """Transport glyph on an 18-unit grid: start, prev, next, play, stop, left, right, plus, minus, close, fit."""
    dpr = 2
    pix = QPixmap(size * dpr, size * dpr)
    pix.setDevicePixelRatio(dpr)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(size / 18, size / 18)
    p.setPen(Qt.NoPen)
    p.setBrush(color)

    def tri(tip: float, base: float):
        p.drawPolygon(QPolygonF([QPointF(tip, 9), QPointF(base, 3.5), QPointF(base, 14.5)]))

    def lines(*pts):
        p.setPen(QPen(color, 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        for a, b, c, d in pts:
            p.drawLine(QPointF(a, b), QPointF(c, d))

    if kind == "start":
        p.drawRoundedRect(QRectF(3, 3.5, 2.4, 11), 1, 1)
        tri(6.5, 15)
    elif kind == "prev":
        tri(1.5, 9)
        tri(9, 16.5)
    elif kind == "next":
        tri(16.5, 9)
        tri(9, 1.5)
    elif kind == "play":
        tri(15.5, 5)
    elif kind == "left":
        tri(4.5, 13)
    elif kind == "right":
        tri(13.5, 5)
    elif kind == "stop":
        p.drawRoundedRect(QRectF(4.5, 4.5, 9, 9), 1.5, 1.5)
    elif kind == "plus":
        lines((9, 4, 9, 14), (4, 9, 14, 9))
    elif kind == "minus":
        lines((4, 9, 14, 9))
    elif kind == "close":
        lines((5, 5, 13, 13), (13, 5, 5, 13))
    elif kind == "fit":
        lines((2.5, 9, 15.5, 9), (2.5, 9, 5.5, 6), (2.5, 9, 5.5, 12), (15.5, 9, 12.5, 6), (15.5, 9, 12.5, 12))
    p.end()
    return QIcon(pix)


def glyph_button(kind: str, tip: str, slot, name: str = "tbtn", color: QColor | None = None, size: int = 18) -> QPushButton:
    b = QPushButton(objectName=name, clicked=slot, toolTip=tip)
    b.setIcon(glyph(kind, color or QApplication.palette().text().color(), size))
    if color is None:
        b.setProperty("glyph", kind)  # redrawn in the new text colour when the appearance changes
    b.setIconSize(QSize(size, size))
    b.setFocusPolicy(Qt.NoFocus)
    b.setCursor(Qt.PointingHandCursor)
    return b


class Ring(QWidget):
    """Progress ring with the percent inside; without a value an arc turns to show the app is working."""

    def __init__(self, size: int):
        super().__init__()
        self.setFixedSize(size, size)
        self.frac: float | None = None
        self.angle = 0
        self.timer = QTimer(self, interval=40, timeout=self._turn)

    def set(self, frac: float | None):
        self.frac = frac
        self.timer.start() if frac is None else self.timer.stop()
        self.update()

    def _turn(self):
        self.angle = (self.angle - 12) % 360
        self.update()

    def hideEvent(self, e):
        self.timer.stop()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        s = self.width()
        pen = max(s / 9, 2.5)
        box = QRectF(pen / 2, pen / 2, s - pen, s - pen)
        p.setPen(QPen(QColor(127, 127, 127, 60), pen))
        p.drawEllipse(box)
        p.setPen(QPen(accent_color(QApplication.instance()), pen, Qt.SolidLine, Qt.RoundCap))
        if self.frac is None:
            p.drawArc(box, (90 + self.angle) * 16, -90 * 16)
            return
        p.drawArc(box, 90 * 16, -int(self.frac * 360 * 16))
        if s >= 48:
            f = QFont(self.font())
            f.setBold(True)
            f.setPixelSize(int(s * 0.27))
            p.setFont(f)
            p.setPen(self.palette().text().color())
            p.drawText(self.rect(), Qt.AlignCenter, f"{self.frac:.0%}")


class BusyCard(QFrame):
    """Large progress card in the centre of the waveform: what runs, why, how long, a music line. Clicks pass
    through it, so the waveform underneath stays editable."""

    def __init__(self):
        super().__init__(objectName="busyCard")
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setFixedWidth(600)
        h = QHBoxLayout(self)
        h.setContentsMargins(26, 20, 26, 20)
        h.setSpacing(22)
        self.ring = Ring(76)
        h.addWidget(self.ring, 0, Qt.AlignVCenter)
        v = QVBoxLayout()
        v.setSpacing(5)
        align = Qt.AlignLeft
        self.title = QLabel(objectName="busyTitle", alignment=align)
        self.why = QLabel(objectName="busyWhy", alignment=align, wordWrap=True)
        self.eta = QLabel(objectName="busyEta", alignment=align)
        self.phrase = QLabel(objectName="progPhrase", alignment=align, wordWrap=True)
        self.wait = QLabel(objectName="sepWait", alignment=align, wordWrap=True)
        self.wait.hide()
        for w in (self.title, self.why, self.eta, self.wait, self.phrase):
            v.addWidget(w)
        h.addLayout(v, 1)
        shadow = QGraphicsDropShadowEffect(self, blurRadius=48, color=QColor(0, 0, 0, 70))
        shadow.setOffset(0, 12)
        self.setGraphicsEffect(shadow)
        self.hide()

    def set(self, title: str, why: str, frac: float | None, eta: str):
        self.title.setText(title)
        self.why.setText(why)
        self.ring.set(frac)
        self.eta.setText(eta)
        self.eta.setVisible(bool(self.eta.text()))


class BusyPill(QFrame):
    """The progress card folded into a capsule at the bottom of the waveform; a click unfolds it."""

    clicked = Signal()

    def __init__(self):
        super().__init__(objectName="busyPill")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(38)
        h = QHBoxLayout(self)
        h.setContentsMargins(10, 0, 16, 0)
        h.setSpacing(9)
        self.ring = Ring(22)
        self.text = QLabel(objectName="pillText")
        self.note = QLabel(objectName="pillNote")  # what waits for the separation, in orange
        self.note.hide()
        h.addWidget(self.ring)
        h.addWidget(self.text)
        h.addWidget(self.note)
        shadow = QGraphicsDropShadowEffect(self, blurRadius=24, color=QColor(0, 0, 0, 60))
        shadow.setOffset(0, 5)
        self.setGraphicsEffect(shadow)
        self.hide()

    def mousePressEvent(self, _e):
        self.clicked.emit()


def tip_badge(kind: str, size: int = 28) -> QPixmap:
    """An orange round badge: a light bulb for a tip about the app, a note for a music line."""
    dpr = 2.0
    pix = QPixmap(int(size * dpr), int(size * dpr))
    pix.setDevicePixelRatio(dpr)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(ORANGE)
    p.drawEllipse(QRectF(0, 0, size, size))
    white = QColor(255, 255, 255)
    c = size / 2
    if kind == "tip":
        p.setBrush(white)
        p.drawEllipse(QPointF(c, c - size * 0.08), size * 0.21, size * 0.21)  # the bulb
        p.drawRoundedRect(QRectF(c - size * 0.11, c + size * 0.06, size * 0.22, size * 0.13), 1.5, 1.5)  # its neck
        p.drawRoundedRect(QRectF(c - size * 0.08, c + size * 0.22, size * 0.16, size * 0.06), 1.5, 1.5)  # the cap
    else:
        p.setPen(white)
        f = QFont()
        f.setPixelSize(int(size * 0.62))
        f.setBold(True)
        p.setFont(f)
        p.drawText(QRectF(0, -size * 0.02, size, size), Qt.AlignCenter, "♪")
    p.end()
    return pix


class TipCard(QFrame):
    """A tip about the app, or now and then a music line, shown over the waveform next to the progress
    capsule. An orange badge tells them apart. Clicks pass through it."""

    def __init__(self):
        super().__init__(objectName="tipCard")
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        h = QHBoxLayout(self)
        h.setContentsMargins(12, 10, 14, 10)
        h.setSpacing(10)
        self.badge = QLabel(fixedSize=QSize(28, 28))
        self.text = QLabel(objectName="tipText", wordWrap=True)
        h.addWidget(self.badge, 0, Qt.AlignTop)
        h.addWidget(self.text, 1)
        self.badges = {k: tip_badge(k) for k in ("tip", "music")}
        self.hide()

    def set(self, text: str, tip: bool):
        self.badge.setPixmap(self.badges["tip" if tip else "music"])
        self.text.setText(text.removeprefix("♪").strip())


class Overview(QWidget):
    """The whole song under the waveform: pauses, the «1», the play position and the visible window. Dragging
    the window scrolls the waveform; a click elsewhere centres the view there."""

    def __init__(self, wave: "Wave", height: int):
        super().__init__()
        self.wave = wave
        wave.overview = self
        self.setFixedHeight(height)
        self.setCursor(Qt.PointingHandCursor)
        self.grab_dt = None
        self._cache = (None, None)

    def _span(self) -> tuple[float, float]:
        w = self.wave
        lo = min([c.time for c in w.clicks] + [0.0])
        return lo, max(w.duration, lo + 1.0)

    def x_of(self, t: float) -> float:
        lo, hi = self._span()
        return (t - lo) / (hi - lo) * self.width()

    def t_of(self, x):
        lo, hi = self._span()
        return lo + x / max(self.width(), 1) * (hi - lo)

    def _pixmap(self) -> QPixmap:
        w, h, env = self.width(), self.height(), self.wave.env
        color = QColor(self.palette().text().color())
        key = (w, h, id(env), self._span(), color.rgb())
        if self._cache[0] == key:
            return self._cache[1]
        dpr = self.devicePixelRatioF()
        pix = QPixmap(int(w * dpr), int(h * dpr))
        pix.setDevicePixelRatio(dpr)
        pix.fill(Qt.transparent)
        if env is not None and w > 1:
            p = QPainter(pix)
            color.setAlpha(120)
            p.setPen(QPen(color, 1))
            ts = self.t_of(np.arange(w + 1))
            idx = np.maximum.accumulate(np.clip((ts * 1000).astype(int), 0, len(env)))
            peaks = np.maximum.reduceat(np.r_[env, 0.0], idx)[:-1]
            peaks[ts[:-1] < 0] = 0
            mid = h / 2
            for x, v in enumerate(peaks):
                half = max(v * (mid - 3), 0.5)
                p.drawLine(QPointF(x + 0.5, mid - half), QPointF(x + 0.5, mid + half))
            p.end()
        self._cache = (key, pix)
        return pix

    def paintEvent(self, _e):
        wv = self.wave
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, w, h), min(8, h / 2), min(8, h / 2))
        p.setClipPath(clip)
        p.fillRect(0, 0, w, h, QColor(127, 127, 127, 22))
        for i, z in enumerate(wv.zones):
            xa, xb = self.x_of(wv.bars[z.first].start), self.x_of(wv.bars[z.last].end)
            c = QColor(GREEN)
            c.setAlpha(230 if i == wv.current_zone else 130)
            p.fillRect(QRectF(xa, 0, max(xb - xa, 2), h), c)
        p.drawPixmap(0, 0, self._pixmap())
        if wv.t0 is not None:
            p.fillRect(QRectF(self.x_of(wv.t0) - 1, 0, 2, h), ORANGE)
        t = wv.cursor_t if wv.cursor_t is not None else wv.play_t
        if t is not None:
            p.fillRect(QRectF(self.x_of(t) - 0.75, 0, 1.5, h), wv.playhead_color())
        xa, xb = max(self.x_of(wv.v0), 0), min(self.x_of(wv.v1), w)
        veil = QColor(self.palette().window().color())
        veil.setAlpha(150)
        p.fillRect(QRectF(0, 0, xa, h), veil)
        p.fillRect(QRectF(xb, 0, w - xb, h), veil)
        p.setClipping(False)
        accent = accent_color(QApplication.instance())
        p.setPen(QPen(accent, 2))
        p.setBrush(Qt.NoBrush)
        box = QRectF(xa + 1, 1, max(xb - xa - 2, 6), h - 2)
        p.drawRoundedRect(box, min(6, h / 2), min(6, h / 2))
        if h >= 24:  # grips on the window edges
            p.setPen(Qt.NoPen)
            p.setBrush(accent)
            for x in (box.left(), box.right()):
                p.drawRoundedRect(QRectF(x - 2.5, h / 2 - 8, 5, 16), 2.5, 2.5)

    def mousePressEvent(self, e):
        t, wv = self.t_of(e.position().x()), self.wave
        if not wv.v0 <= t <= wv.v1:
            half = (wv.v1 - wv.v0) / 2
            wv.show_range(t - half, t + half)
        self.grab_dt = t - wv.v0
        self.press_x = e.position().x()

    def mouseMoveEvent(self, e):
        if self.grab_dt is not None:
            wv = self.wave
            a = self.t_of(e.position().x()) - self.grab_dt
            wv.show_range(a, a + wv.v1 - wv.v0)

    def mouseReleaseEvent(self, e):
        self.grab_dt = None
        if abs(e.position().x() - getattr(self, "press_x", -1e9)) < 4:  # a click, not a drag: the playhead goes there
            self.wave.set_playhead(self.t_of(e.position().x()), snap=False)
            self.wave.playheadClicked.emit()


class Wave(QWidget):
    """Waveform with beat lines, count-in clicks, pauses, a time ruler, a draggable first-downbeat marker and
    the play position.

    Each pause carries a header with its number, meter and tempo; clicking it edits the pause, the cross in the
    current pause's header removes it. Right-click on a pause removes it, right-click elsewhere adds a one-bar
    pause; a pause edge has a handle and drags to another bar line. A plain click sets the play position.
    """

    t0Changed = Signal(float)
    zonesEdited = Signal(object)
    zoneClicked = Signal(int)
    spanSelected = Signal(int, int)  # first and last bar of a span dragged with Shift or along the ruler
    playFrom = Signal(float)
    playheadClicked = Signal()  # the user put the playhead somewhere by a click  # double-click: play from here
    navChanged = Signal()  # the play position or the current pause changed
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
        self.bars, self.zones, self.fill = [], [], True
        self.sel = None  # bars (first, last) while a span is being dragged
        self.peak, self.ref_db, self.threshold_db = 1.0, None, None  # for the pause threshold band
        self.threshold_full = False  # band over the whole track while the slider moves
        self.labels: list[tuple[QRectF, int]] = []
        self.overlay: QWidget | None = None  # progress card shown over the dimmed waveform during a job
        self.cursor_t = None
        self.drag = None
        self._cache = (None, None)

        self.overview: QWidget | None = None  # mini-map of the whole song, repainted with the waveform
        self.busy: QWidget | None = None  # centred progress card
        self.pill: QWidget | None = None  # the same progress folded into a capsule at the bottom
        self.tips: QWidget | None = None  # tips card at the bottom left while the app is busy
        self.tips_on = False
        self.tip_lines: list[QLabel] = []  # the centred cards' own tip lines, used when the card has no room
        self.play_t: float | None = None  # where playback starts; None means from the count-in
        self.current_zone: int | None = None
        self.hover_edge = None  # (pause index, side) under the pointer
        self.closes: list[tuple[QRectF, int]] = []  # remove buttons in pause headers
        self.press_x = 0.0

    def playhead_color(self) -> QColor:
        return self.palette().text().color()

    def set_audio(self, pcm: np.ndarray, sr: int):
        self.set_env(peaks_per_ms(pcm, sr), len(pcm) / sr)

    def set_env(self, env: np.ndarray, duration: float):
        """The waveform from peaks made off the UI thread (peaks_per_ms)."""
        self.peak = max(float(env.max()), 1e-9) if len(env) else 1e-9
        self.env = env / self.peak
        self.duration = duration
        self._cache = (None, None)
        self.update()

    def show_range(self, a: float, b: float):
        """Shows song time a..b, kept within the song: 5 s before it, or as far as the count-in reaches, and 5 s
        after the end."""
        lo = min(-5.0, min((c.time for c in self.clicks), default=0.0) - 1.0)
        hi = max(self.duration, 0.0) + 5.0
        span = min(b - a, hi - lo)
        a = min(max(a, lo), hi - span)
        self.v0, self.v1 = a, a + span
        self.update()

    def _place_overlay(self):
        """The job progress fills the waveform; the busy card sits in its centre, the capsule at the bottom and
        the tips card at the bottom left, or under the pause headers when the capsule leaves it no room."""
        if self.overlay is not None:
            self.overlay.setGeometry(self.rect())
            for label in self.overlay.findChildren(QLabel, "progWhy"):
                label.setVisible(self.height() >= 340)  # a low waveform has no room for the explanation
        wh = self.height() - self.RULER
        if self.busy is not None:
            self.busy.adjustSize()
            self.busy.move((self.width() - self.busy.width()) // 2, max((wh - self.busy.height()) // 2, 4))
            self.busy.raise_()
        if self.pill is not None:
            self.pill.adjustSize()
            self.pill.move((self.width() - self.pill.width()) // 2, wh - self.pill.height() - 10)
            self.pill.raise_()
        if self.tips is not None:
            # The tips card takes the room left of the centred card or of the capsule. Next to the capsule a
            # narrow waveform moves it under the pause headers; next to a centred card it gives way to that
            # card's own tip line.
            centred = self.busy.isVisible() or self.overlay.isVisible()
            room = ((self.width() - self.busy.width()) // 2 if centred else self.pill.x()) - 30
            fits = room >= 240 or not centred
            self.tips.setFixedWidth(min(max(room, 240), 380))
            self.tips.adjustSize()
            self.tips.move(14, wh - self.tips.height() - 10 if room >= 240 else 30)
            self.tips.setVisible(self.tips_on and fits)
            self.tips.raise_()
            for label in self.tip_lines:
                label.setVisible(not fits)

    def resizeEvent(self, e):
        self._place_overlay()
        super().resizeEvent(e)

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
        W, H = max(int(w * dpr), 1), max(int(h * dpr), 1)
        img = np.zeros((H, W), np.uint32)
        if self.env is not None and w > 1:
            # One peak per device pixel column, painted straight into the image: building a polygon point by
            # point in Python took 70 ms per zoom step on a six-minute song, this takes a few.
            ts = self.v0 + np.arange(W + 1) / W * (self.v1 - self.v0)
            env = np.r_[self.env, 0.0]
            idx = np.clip((ts * 1000).astype(int), 0, len(env) - 1)
            idx = np.maximum.accumulate(idx)
            peaks = np.maximum.reduceat(env, idx)[:-1]
            peaks[(ts[:-1] < 0) | (ts[:-1] > self.duration)] = 0
            mid = H / 2
            half = np.maximum(peaks * mid * 0.92, 0.5)
            half[peaks == 0] = 0
            rows = np.abs(np.arange(H) + 0.5 - mid)[:, None]
            c = QColor(self.palette().text().color())
            rgba = np.array([c.red(), c.green(), c.blue(), 140], np.uint8).view(np.uint32)[0]
            img = np.where(rows <= half[None, :], rgba, np.uint32(0)).astype(np.uint32)
        q = QImage(np.ascontiguousarray(img).data, W, H, 4 * W, QImage.Format_RGBA8888).copy()
        pix = QPixmap.fromImage(q)
        pix.setDevicePixelRatio(dpr)
        self._cache = (key, pix)
        return pix

    def _visible(self, times) -> list[float]:
        a = np.asarray(times)
        return a[np.searchsorted(a, self.v0) : np.searchsorted(a, self.v1, side="right")].tolist()

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

        accent = accent_color(QApplication.instance())
        if self.clicks and self.t0 is not None:
            zone = QColor(accent)
            zone.setAlpha(28)
            x0 = self.x_of(min(c.time for c in self.clicks))
            p.fillRect(QRectF(x0, 0, self.x_of(self.t0) - x0, wh), zone)
        if self.v0 < 0:
            p.fillRect(QRectF(0, 0, self.x_of(0), wh), QColor(127, 127, 127, 30))
        fill = QColor(GREEN)
        fill.setAlpha(46 if self.fill else 18)
        edge = QColor(GREEN)
        edge.setAlpha(200 if self.fill else 80)
        for i, z in enumerate(self.zones):
            xa, xb = self.x_of(self.bars[z.first].start), self.x_of(self.bars[z.last].end)
            fill.setAlpha((78 if i == self.current_zone else 34) if self.fill else 18)
            p.fillRect(QRectF(xa, 0, xb - xa, wh), fill)
            p.setPen(QPen(edge, 3 if i == self.current_zone else 2))
            p.drawLine(QPointF(xa, 0), QPointF(xa, wh))
            p.drawLine(QPointF(xb, 0), QPointF(xb, wh))

        p.drawPixmap(0, 0, self._wave_pixmap())

        # Only what is on screen, and no beat lines closer than 4 px: a whole song would be a grey wall
        px_per_s = w / max(self.v1 - self.v0, 1e-6)
        dense = len(self.beats) > 1 and (self.duration / len(self.beats)) * px_per_s < 4
        p.setPen(QPen(QColor(127, 127, 127, 70), 1))
        for t in ([] if dense else self._visible(self.beats)):
            p.drawLine(QPointF(self.x_of(t), 0), QPointF(self.x_of(t), wh))
        p.setPen(QPen(QColor(127, 127, 127, 160), 1, Qt.DashLine))
        for t in self._visible(self.downs):
            p.drawLine(QPointF(self.x_of(t), 0), QPointF(self.x_of(t), wh))

        p.setPen(Qt.NoPen)
        last_x = None
        for c in sorted(self.clicks, key=lambda c: c.time):
            if not self.v0 - 1 <= c.time <= self.v1 + 1:
                continue
            x = self.x_of(c.time)
            if last_x is not None and x - last_x < 5 and not c.accent:  # zoomed out: bar starts only
                continue
            last_x = x
            p.setBrush(GREEN if self.t0 is not None and c.time >= self.t0 - 0.05 else accent)  # inside the song
            hgt = wh * (0.42 if c.accent else 0.28) * min(c.gain, 1.6)
            p.drawRoundedRect(QRectF(self.x_of(c.time) - 2, 28, 4, hgt), 2, 2)

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
        if self.sel:
            a, b = self.sel
            xa, xb = self.x_of(self.bars[a].start), self.x_of(self.bars[b].end)
            sel = QColor(accent)
            sel.setAlpha(50)
            p.fillRect(QRectF(xa, 0, xb - xa, wh), sel)

        # Pause threshold as an orange band from the top and bottom: bars whose level stays inside the gap
        # between them become pauses (RMS shown as a sine peak). While the slider moves, the band covers the
        # whole track; otherwise only a strip at the right edge shows it.
        if self.fill and self.ref_db is not None and self.threshold_db is not None and self.env is not None:
            a = min(10 ** ((self.ref_db + self.threshold_db) / 20) * 1.414 / self.peak, 1.0)
            mid = wh / 2
            gap = a * mid * 0.92
            band = QColor(ORANGE)
            band.setAlpha(38 if self.threshold_full else 150)
            x0, bw = (0, w) if self.threshold_full else (w - 12, 8)
            p.setPen(Qt.NoPen)
            p.setBrush(band)
            p.drawRoundedRect(QRectF(x0, 4, bw, max(mid - gap - 4, 0)), 3, 3)
            p.drawRoundedRect(QRectF(x0, mid + gap, bw, max(mid - gap - 4, 0)), 3, 3)

        self._paint_pauses(p, w, wh)
        if self.overlay is not None and self.overlay.isVisible():
            veil = QColor(self.palette().window().color())
            veil.setAlpha(225)
            p.fillRect(QRectF(0, 0, w, h), veil)

        # Ruler.
        p.setPen(QPen(QColor(127, 127, 127, 60), 1))
        p.drawLine(QPointF(0, wh), QPointF(w, wh))
        f = QFont(self.font())
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
        self._paint_playhead(p, wh)
        if self.overview is not None:
            self.overview.update()

    def _paint_playhead(self, p: QPainter, wh: float):
        """A line at the play position (or where playback will start) with a head on the time ruler."""
        t = self.cursor_t if self.cursor_t is not None else self.play_t
        if t is None:
            return
        x, c = self.x_of(t), self.playhead_color()
        p.setPen(QPen(c, 1.8))
        p.drawLine(QPointF(x, 0), QPointF(x, wh))
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawPolygon(QPolygonF([QPointF(x, wh - 1), QPointF(x + 7, wh + 6), QPointF(x + 7, wh + 17),
                                 QPointF(x - 7, wh + 17), QPointF(x - 7, wh + 6)]))

    def _paint_pauses(self, p: QPainter, w: float, wh: float):
        """Pause headers (number, meter, tempo; a click opens the editor) and the handles on pause edges."""
        self.labels, self.closes = [], []
        f = QFont(self.font())
        f.setBold(True)
        f.setPointSizeF(f.pointSizeF() * 0.85)
        p.setFont(f)
        white = QColor(255, 255, 255)
        for i, z in enumerate(self.zones):
            a, b, bpm, n = zn.describe(self.bars, z)
            xa, xb = self.x_of(a), self.x_of(b)
            if xb < 0 or xa > w:
                continue
            cur = i == self.current_zone
            head = QRectF(xa, 0, xb - xa, 22)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(36, 138, 61, 245 if cur else 150))
            p.drawRect(head)
            text = _("Пауза {n}", n=i + 1) + f" · {n}/4 · {bpm:.0f} BPM" + (" ✎" if z.bpm or z.beats else "")
            room = QRectF(max(xa, 0) + 9, 0, xb - max(xa, 0) - (34 if cur else 14), 22)
            p.setPen(white)
            p.drawText(room, Qt.AlignVCenter | Qt.AlignLeft,
                       p.fontMetrics().elidedText(text, Qt.ElideRight, int(max(room.width(), 0))))
            self.labels.append((head.adjusted(0, 0, -26 if cur else 0, 0), i))
            if cur and xb - xa > 70:  # remove button at the right end of the header
                box = QRectF(xb - 23, 3, 16, 16)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(255, 255, 255, 70))
                p.drawEllipse(box)
                p.setPen(QPen(white, 1.6, Qt.SolidLine, Qt.RoundCap))
                p.drawLine(box.center() + QPointF(-3, -3), box.center() + QPointF(3, 3))
                p.drawLine(box.center() + QPointF(3, -3), box.center() + QPointF(-3, 3))
                self.closes.append((box, i))
            for side, x in (("start", xa), ("end", xb)):
                self._paint_handle(p, x, wh, side, self.hover_edge == (i, side))

    def _paint_handle(self, p: QPainter, x: float, wh: float, side: str, hot: bool):
        """Grip in the middle of a pause edge; under the pointer it grows and says what dragging does."""
        white = QColor(255, 255, 255)
        hw, hh = (7, 27) if hot else (5, 18)
        p.setPen(QPen(white, 1.5))
        p.setBrush(GREEN if hot else QColor(36, 138, 61))
        p.drawRoundedRect(QRectF(x - hw, wh / 2 - hh, 2 * hw, 2 * hh), hw, hw)
        p.setPen(QPen(white, 1.2, Qt.SolidLine, Qt.RoundCap))
        for dx in (-1.6, 1.6):
            p.drawLine(QPointF(x + dx, wh / 2 - hh * 0.4), QPointF(x + dx, wh / 2 + hh * 0.4))
        if hot:
            text = "⇤ ⇥  " + _("тяните по тактам")
            tw = p.fontMetrics().horizontalAdvance(text) + 16
            box = QRectF(x + 14 if side == "start" else x - 14 - tw, wh / 2 - 11, tw, 22)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(30, 30, 32, 225))
            p.drawRoundedRect(box, 6, 6)
            p.setPen(white)
            p.drawText(box, Qt.AlignCenter, text)

    def _near_marker(self, x: float) -> bool:
        return self.t0 is not None and abs(x - self.x_of(self.t0)) < 10

    def _bar_at(self, t: float) -> int | None:
        return next((i for i, b in enumerate(self.bars) if b.start <= t < b.end), None)

    def _near_edge(self, x: float):
        """(pause index, side) of a pause edge within 6 px, or None."""
        for i, z in enumerate(self.zones):
            if abs(x - self.x_of(self.bars[z.first].start)) < 9:
                return i, "start"
            if abs(x - self.x_of(self.bars[z.last].end)) < 9:
                return i, "end"
        return None

    def _zone_at(self, bar: int) -> int | None:
        return next((i for i, z in enumerate(self.zones) if z.first <= bar <= z.last), None)

    def _set_zones(self, zones: list):
        self.zones = sorted(zones, key=lambda z: z.first)
        self.zonesEdited.emit(self.zones)
        self.update()

    def add_zone(self, bar: int, length: int = 1):
        """A pause over bars bar..bar+length-1, unless one already covers it. Next to a pause that follows the
        song, that pause grows instead, so the hi-hat runs on without a count-in in between."""
        last = min(bar + length, len(self.bars)) - 1
        if not self.bars or any(self._zone_at(i) is not None for i in range(bar, last + 1)):
            return
        for z in self.zones:
            if z.bpm is None and z.beats is None and z.last == bar - 1:
                z.last, z.manual = last, True
                return self._set_zones(self.zones)
            if z.bpm is None and z.beats is None and z.first == last + 1:
                z.first, z.manual = bar, True
                return self._set_zones(self.zones)
        self._set_zones(self.zones + [zn.Zone(bar, last, manual=True)])

    def mousePressEvent(self, e):
        if isinstance(QApplication.focusWidget(), QAbstractSpinBox):
            QApplication.focusWidget().clearFocus()  # a click on the waveform ends typing in a number field
        x, pos = e.position().x(), e.position()
        if e.button() == Qt.RightButton:
            i = self._bar_at(self.t_of(x))
            if i is not None:
                z = self._zone_at(i)
                if z is None:
                    self.add_zone(i)
                else:
                    self._set_zones(self.zones[:z] + self.zones[z + 1 :])
            return
        close = next((i for box, i in self.closes if box.contains(pos)), None)
        if close is not None:
            self.current_zone = None
            self._set_zones(self.zones[:close] + self.zones[close + 1:])
            self.navChanged.emit()
            return
        label = next((i for box, i in self.labels if box.contains(pos)), None)
        if label is not None:
            self.current_zone = label
            self.zoneClicked.emit(label)
            return
        self.press_x = x
        if self.bars and (e.modifiers() & Qt.ShiftModifier or pos.y() > self.height() - self.RULER):
            i = self._bar_at(self.t_of(x))
            if i is not None:
                self.drag = ("select", i, x)
                self.sel = (i, i)
                self.update()
            return
        if self._near_marker(x):
            self.drag = "marker"
        elif self.bars and (hit := self._near_edge(x)):
            self.drag = ("edge", self.zones[hit[0]], hit[1])
        else:
            self.drag = ("pan", x, self.v0, self.v1)

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
        elif self.drag and self.drag[0] == "select":
            t = self.t_of(x)
            i = self._bar_at(t)
            if i is None:
                i = 0 if t < self.bars[0].start else len(self.bars) - 1
            self.sel = (min(self.drag[1], i), max(self.drag[1], i))
            self.update()
        elif self.drag and self.drag[0] == "edge":
            # The pause is held by reference, so a search that replaces the list mid-drag cannot break it;
            # the edit is announced once, on release, so a playing preview rebuilds only for the final edge.
            _kind, z, side = self.drag
            if z not in self.zones:
                self.drag = None
                return
            t = self.t_of(x)
            others = [o for o in self.zones if o is not z]
            lo = max([o.last + 1 for o in others if o.last < z.first], default=0)
            hi = min([o.first - 1 for o in others if o.first > z.last], default=len(self.bars) - 1)
            if side == "start":
                z.first = min(range(lo, z.last + 1), key=lambda k: abs(self.bars[k].start - t))
            else:
                z.last = min(range(z.first, hi + 1), key=lambda k: abs(self.bars[k].end - t))
            z.manual = True
            self.update()
        elif self.drag:
            _kind, x0, a, b = self.drag
            dt = (x - x0) / max(self.width(), 1) * (b - a)
            self.show_range(a - dt, b - dt)
        else:
            edge = self.bars and self._near_edge(x)
            if (edge or None) != self.hover_edge:
                self.hover_edge = edge or None
                self.update()
            on_label = any(box.contains(e.position()) for box, _i in self.labels)
            self.setCursor(Qt.PointingHandCursor if on_label else
                           Qt.SizeHorCursor if self._near_marker(x) or edge else Qt.OpenHandCursor)

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.playFrom.emit(self.t_of(e.position().x()))

    def mouseReleaseEvent(self, e):
        if self.drag and self.drag[0] == "select" and self.sel:
            a, b = self.sel
            moved = abs(e.position().x() - self.drag[2]) > 4  # a plain click on the ruler makes nothing
            self.sel = None
            self.update()
            if moved:
                self.spanSelected.emit(a, b)
        elif self.drag and self.drag[0] == "edge":
            self.zonesEdited.emit(self.zones)
        elif self.drag and self.drag[0] == "pan" and abs(e.position().x() - self.press_x) < 4:
            self.set_playhead(self.t_of(e.position().x()), snap=not e.modifiers() & Qt.AltModifier)
            self.playheadClicked.emit()
        self.drag = None

    def set_playhead(self, t: float, snap: bool = True):
        """Puts the play position at t, on the nearest beat within 8 px unless snap is off, and makes the
        pause under it the current one."""
        if snap and self.beats:
            b = min(self.beats, key=lambda b: abs(b - t))
            if abs(self.x_of(b) - self.x_of(t)) < 8:
                t = b
        self.play_t = float(np.clip(t, min(self.v0, 0.0), max(self.duration, 0.0)))
        bar = self._bar_at(self.play_t)
        self.current_zone = self._zone_at(bar) if bar is not None else None
        self.navChanged.emit()
        self.update()

    def wheelEvent(self, e):
        """Zoom in proportion to the scroll, so a trackpad zooms smoothly and a wheel notch by about 20%;
        sideways trackpad scrolling pans."""
        span = self.v1 - self.v0
        dx = e.pixelDelta().x() or e.angleDelta().x() / 4
        if abs(dx) > abs(e.pixelDelta().y() or e.angleDelta().y() / 4):
            d = -dx / max(self.width(), 1) * span
            self.show_range(self.v0 + d, self.v1 + d)
            return
        dy = e.pixelDelta().y() * 4 if not e.pixelDelta().isNull() else e.angleDelta().y()
        if not dy:
            return
        t = self.t_of(e.position().x())
        k = 0.9985 ** dy
        span = float(np.clip((self.v1 - self.v0) * k, 0.3, max(self.duration + 10, 10)))
        r = (t - self.v0) / (self.v1 - self.v0)
        self.show_range(t - r * span, t + (1 - r) * span)


def card(title: str, *buttons: QWidget) -> tuple[QFrame, QGridLayout]:
    """A titled card; buttons sit at the right end of the title row."""
    f = QFrame(objectName="card")
    v = QVBoxLayout(f)
    v.setContentsMargins(14, 12, 14, 14)
    v.setSpacing(10)
    head = QHBoxLayout()
    head.addWidget(QLabel(title, objectName="cardTitle"))
    head.addStretch()
    for b in buttons:
        head.addWidget(b, 0, Qt.AlignVCenter)
    v.addLayout(head)
    g = QGridLayout()
    g.setHorizontalSpacing(10)
    g.setVerticalSpacing(9)
    g.setColumnStretch(1, 1)
    v.addLayout(g)
    v.addStretch()
    return f, g


class MuteButton(QPushButton):
    """Speaker toggle beside a level slider. On moves the slider to MUTE, off brings the last level back."""

    def __init__(self, slider: QSlider, tip: str):
        super().__init__(objectName="small", checkable=True, toolTip=tip, focusPolicy=Qt.NoFocus)
        self.setFixedSize(30, 22)
        self.slider, self.level = slider, slider.value() if slider.value() != MUTE else 0
        self.toggled.connect(self._toggle)
        slider.valueChanged.connect(self._sync)

    def _toggle(self, on: bool):
        if on and self.slider.value() != MUTE:
            self.slider.setValue(MUTE)
        elif not on and self.slider.value() == MUTE:
            self.slider.setValue(self.level)

    def _sync(self, v: int):
        if v != MUTE:
            self.level = v
        self.setChecked(v == MUTE)

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = ORANGE if self.isChecked() else self.palette().color(QPalette.WindowText)
        x, y = self.width() / 2 - 7, self.height() / 2
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawPolygon(QPolygonF([QPointF(x, y - 3), QPointF(x + 3, y - 3), QPointF(x + 7, y - 6.5),
                                 QPointF(x + 7, y + 6.5), QPointF(x + 3, y + 3), QPointF(x, y + 3)]))
        p.setPen(QPen(c, 1.6, Qt.SolidLine, Qt.RoundCap))
        p.setBrush(Qt.NoBrush)
        if self.isChecked():
            p.drawLine(QPointF(x + 10, y - 3), QPointF(x + 15, y + 3))
            p.drawLine(QPointF(x + 15, y - 3), QPointF(x + 10, y + 3))
        else:
            for r in (4, 7):
                p.drawArc(QRectF(x + 7 - r, y - r, 2 * r, 2 * r), -50 * 16, 100 * 16)


def part_icon(kind: str, color: QColor, size: int = 16) -> QPixmap:
    """Small part or speaker glyph (guitar, mic, bass, drums, piano, speaker), drawn so it follows the theme colour."""
    dpr = 2
    pix = QPixmap(size * dpr, size * dpr)
    pix.setDevicePixelRatio(dpr)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    s = size / 16
    if kind == "save":  # floppy disk
        p.drawRoundedRect(QRectF(2 * s, 2 * s, 12 * s, 12 * s), 1.6 * s, 1.6 * s)
        p.setCompositionMode(QPainter.CompositionMode_Clear)
        p.drawRect(QRectF(4.5 * s, 2 * s, 6.5 * s, 4 * s))  # shutter opening
        p.drawRoundedRect(QRectF(4 * s, 8.5 * s, 8 * s, 4 * s), 0.8 * s, 0.8 * s)  # label
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        p.drawRect(QRectF(8.6 * s, 2.6 * s, 1.6 * s, 2.6 * s))  # shutter slot
    elif kind == "reset":  # undo: an arc running counter-clockwise, its arrowhead at the upper left pointing left
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(color, 1.6 * s, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(QRectF(3 * s, 3 * s, 10 * s, 10 * s), 135 * 16, -300 * 16)  # from upper left, clockwise round
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawPolygon(QPolygonF([QPointF(1.6 * s, 2.6 * s), QPointF(6.6 * s, 3.4 * s), QPointF(3.0 * s, 7.6 * s)]))
    elif kind == "threshold":  # bars of a level meter cut by a dashed line
        for x, hgt in ((2, 6), (5.5, 11), (9, 4), (12.5, 8)):
            p.drawRoundedRect(QRectF(x * s, (15 - hgt) * s, 2.2 * s, hgt * s), 0.6 * s, 0.6 * s)
        p.setPen(QPen(color, 1.2 * s, Qt.DashLine))
        p.drawLine(QPointF(0.5 * s, 7.5 * s), QPointF(15.5 * s, 7.5 * s))
    elif kind == "file":  # document with a folded corner and a note
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(color, 1.2 * s, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPolygon(QPolygonF([QPointF(3.5 * s, 1.5 * s), QPointF(9.5 * s, 1.5 * s), QPointF(12.5 * s, 4.5 * s),
                                 QPointF(12.5 * s, 14.5 * s), QPointF(3.5 * s, 14.5 * s)]))
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawEllipse(QPointF(6.8 * s, 11.2 * s), 1.4 * s, 1.1 * s)  # note head
        p.drawRect(QRectF(7.6 * s, 6 * s, 0.9 * s, 5.2 * s))  # stem
    elif kind == "tracks":  # three clips on their lanes
        for x, y, w in ((1.5, 2.5, 9), (4.5, 6.6, 10), (2.5, 10.7, 7)):
            p.drawRoundedRect(QRectF(x * s, y * s, w * s, 2.8 * s), 1.2 * s, 1.2 * s)
    elif kind in ("speaker", "muted"):
        p.drawPolygon(QPolygonF([QPointF(1.5 * s, 6 * s), QPointF(4.5 * s, 6 * s), QPointF(8.5 * s, 2.5 * s),
                                 QPointF(8.5 * s, 13.5 * s), QPointF(4.5 * s, 10 * s), QPointF(1.5 * s, 10 * s)]))
        p.setBrush(Qt.NoBrush)
        if kind == "muted":  # a red cross where the sound waves were
            p.setPen(QPen(QColor(235, 64, 52), 1.6 * s, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(10.5 * s, 5.5 * s), QPointF(15 * s, 10.5 * s))
            p.drawLine(QPointF(10.5 * s, 10.5 * s), QPointF(15 * s, 5.5 * s))
        else:
            p.setPen(QPen(color, 1.3 * s, Qt.SolidLine, Qt.RoundCap))
            for r in (3.5, 6):
                p.drawArc(QRectF(8.5 * s - r * s, 8 * s - r * s, 2 * r * s, 2 * r * s), -50 * 16, 100 * 16)
    elif kind == "bass":
        p.save()
        p.translate(8 * s, 8 * s)
        p.rotate(45)
        p.drawRoundedRect(QRectF(-0.7 * s, -8.4 * s, 1.4 * s, 10 * s), 0.5 * s, 0.5 * s)  # long neck
        p.drawRoundedRect(QRectF(-1.4 * s, -8.8 * s, 2.8 * s, 2.2 * s), 0.7 * s, 0.7 * s)  # head
        p.drawEllipse(QPointF(0, 3.2 * s), 2.6 * s, 2.4 * s)
        p.drawEllipse(QPointF(0, 5.4 * s), 3.2 * s, 2.6 * s)
        p.restore()
    elif kind == "drums":
        p.drawEllipse(QRectF(2 * s, 5 * s, 12 * s, 4 * s))  # head
        p.drawRect(QRectF(2 * s, 7 * s, 12 * s, 5 * s))  # shell
        p.drawEllipse(QRectF(2 * s, 10 * s, 12 * s, 4 * s))
        p.setPen(QPen(color, 1.3 * s, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(4 * s, 1.5 * s), QPointF(8 * s, 6 * s))  # sticks
        p.drawLine(QPointF(12 * s, 1.5 * s), QPointF(8 * s, 6 * s))
    elif kind == "piano":
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(color, 1.2 * s))
        p.drawRoundedRect(QRectF(1.5 * s, 3 * s, 13 * s, 10 * s), 1.2 * s, 1.2 * s)
        for x in (5.8, 10.2):
            p.drawLine(QPointF(x * s, 9 * s), QPointF(x * s, 13 * s))
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        for x in (4.4, 8.8, 13.2):
            p.drawRect(QRectF((x - 1.1) * s, 3 * s, 2.2 * s, 6 * s))  # black keys
    elif kind == "guitar":
        p.save()
        p.translate(8 * s, 8 * s)
        p.rotate(45)
        p.drawRoundedRect(QRectF(-0.9 * s, -8 * s, 1.8 * s, 9 * s), 0.6 * s, 0.6 * s)  # neck
        p.drawRoundedRect(QRectF(-1.6 * s, -8.6 * s, 3.2 * s, 2.4 * s), 0.8 * s, 0.8 * s)  # head
        p.drawEllipse(QPointF(0, 1.4 * s), 3.2 * s, 2.8 * s)  # upper bout
        p.drawEllipse(QPointF(0, 4.6 * s), 4.0 * s, 3.4 * s)  # lower bout
        p.setBrush(QColor(0, 0, 0, 0))
        p.setCompositionMode(QPainter.CompositionMode_Clear)
        p.drawEllipse(QPointF(0, 2.8 * s), 1.1 * s, 1.1 * s)  # sound hole
        p.restore()
    else:
        p.drawRoundedRect(QRectF(5.5 * s, 1 * s, 5 * s, 8.5 * s), 2.5 * s, 2.5 * s)  # capsule
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(color, 1.4 * s, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(QRectF(3.5 * s, 3.5 * s, 9 * s, 8.5 * s), 180 * 16, 180 * 16)  # holder
        p.drawLine(QPointF(8 * s, 12 * s), QPointF(8 * s, 14.6 * s))  # stem
        p.drawLine(QPointF(5.5 * s, 14.6 * s), QPointF(10.5 * s, 14.6 * s))  # base
    p.end()
    return pix


def icon_label(kind: str, text: str) -> QWidget:
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(6)
    icon = QLabel()
    icon.setPixmap(part_icon(kind, QColor(127, 127, 127)))
    h.addWidget(icon, 0, Qt.AlignVCenter)
    h.addWidget(QLabel(text, objectName="field"), 0, Qt.AlignVCenter)
    h.addStretch()  # icon and text stay together at the left edge
    return w


def row(grid: QGridLayout, label, *widgets) -> None:
    """A label (text or a widget) and the controls after it."""
    r = grid.rowCount()
    grid.addWidget(label if isinstance(label, QWidget) else QLabel(label, objectName="field"), r, 0)
    h = QHBoxLayout()
    h.setSpacing(6)
    for w in widgets:
        h.addWidget(w)
    h.addStretch()
    grid.addLayout(h, r, 1)


def stack(grid: QGridLayout, label: str, *widgets) -> None:
    """A label above its controls, for a narrow card."""
    grid.addWidget(QLabel(label, objectName="field"), grid.rowCount(), 0)
    h = QHBoxLayout()
    h.setSpacing(6)
    for w in widgets:
        h.addWidget(w)
    h.addStretch()
    grid.addLayout(h, grid.rowCount(), 0)


# --- preferences ---

OUT_DIR, OUT_GROUP = "output/dir", "output/group"
OUT_REVEAL = "output/reveal"
FILL_KEY = "fill/enabled"
CLOUD_KEY = "cloud/enabled"
CLOUD_OVERLAP = "cloud/overlap"
FILL_BUILD_KEY = "fill/buildup"
CLICK_VOICE_KEY = "click/voice"
CLICK_DB_KEY = "click/volume"
OUTPUT_VOLUME_KEY = "playback/volume"
DEFAULTS = "defaults/"  # prefix for the settings saved with "Save as default"
THRESHOLD_KEY = "fill/threshold"
OUT_LEVELS = {"output/artist": "Исполнитель", "output/year": "Год", "output/album": "Альбом"}


def output_volume() -> float:
    """Speaker level for previews, 0..1, remembered between runs."""
    return int(QSettings().value(OUTPUT_VOLUME_KEY, 80)) / 100


def pref_bool(key: str, default: bool) -> bool:
    return QSettings().value(key, default) in (True, "true", "1", 1)


def output_prefs() -> tuple[Path | None, tuple[bool, bool, bool] | None]:
    """(root folder or None for next to the source, (artist, year, album) grouping or None)."""
    root = QSettings().value(OUT_DIR, "") or None
    if not root:
        return None, None
    group = tuple(pref_bool(k, True) for k in OUT_LEVELS) if pref_bool(OUT_GROUP, False) else None
    return Path(root), group


class AssetsDialog(QDialog):
    """First run without the models: download them once, or take them from a folder (a flash drive, a backup).
    App updates no longer carry them, so they stay small."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle(_("Модели для Count-In"))
        self.setMinimumWidth(560)
        v = QVBoxLayout(self)
        v.setSpacing(12)
        v.addWidget(QLabel(_("Нужно скачать модели"), objectName="progTitle"))
        mb = assets.missing_bytes() / 1e6
        text = QLabel(_("Count-In находит доли и разделяет песню на дорожки нейросетями. Их веса ({mb:.0f} МБ) "
                        "скачиваются один раз и потом не меняются, поэтому обновления приложения остаются маленькими. "
                        "Если модели уже есть на флешке или в другой папке, укажите её. Папка assets рядом с "
                        "приложением подхватывается сама.", mb=mb), wordWrap=True, objectName="progWhy")
        v.addWidget(text)
        self.bar = QProgressBar(textVisible=False, fixedHeight=8)
        self.bar.hide()
        self.note = QLabel(objectName="field")
        v.addWidget(self.bar)
        v.addWidget(self.note)
        h = QHBoxLayout()
        self.later_b = QPushButton(_("Позже"), objectName="tool", clicked=self.reject)
        self.folder_b = QPushButton(_("Указать папку…"), objectName="tool", clicked=self._folder)
        self.get_b = QPushButton(_("Скачать"), objectName="primary", clicked=self._download)
        h.addWidget(self.later_b)
        h.addStretch()
        h.addWidget(self.folder_b)
        h.addWidget(self.get_b)
        v.addLayout(h)
        self.pool = QThreadPool()
        self.job = None

    def _start(self, fn, *args):
        self.bar.show()
        self.bar.setRange(0, 0)
        for b in (self.folder_b, self.get_b):
            b.setEnabled(False)
        self.later_b.setText(_("Отмена"))
        self.job = Job(fn, *args)
        self.job.signals.progress.connect(self._progress)
        self.job.signals.done.connect(lambda _r: self.accept())
        self.job.signals.failed.connect(self._failed)
        self.job.signals.cancelled.connect(self.reject)
        self.pool.start(self.job)

    def _progress(self, text, done, total):
        if total:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(done / total * 1000))
        self.note.setText(text)

    def _failed(self, msg):
        self.note.setText(_("Не получилось: {e}", e=msg))
        for b in (self.folder_b, self.get_b):
            b.setEnabled(True)
        self.later_b.setText(_("Позже"))

    def _download(self):
        def run(job):
            start = time.monotonic()

            def progress(d, n):
                text = _("Скачано {d:.0f} из {n:.0f} МБ", d=d / 1e6, n=n / 1e6)
                elapsed = time.monotonic() - start
                if elapsed > 2 and d:  # the first seconds give no steady speed
                    speed = d / elapsed
                    left = (n - d) / speed
                    text += " · " + _("{v} МБ/с", v=QLocale().toString(speed / 1e6, "f", 1))
                    text += " · " + _("осталось ~{m}:{s:02d}", m=int(left // 60), s=int(left % 60))
                job.report(text, d, n)

            assets.download(progress, job.cancelled.is_set)
        self._start(run)

    def _folder(self):
        d = QFileDialog.getExistingDirectory(self, _("Папка с моделями"))
        if not d:
            return
        folder = Path(d)
        if (folder / "assets").is_dir() and not assets.has_all(folder):
            folder = folder / "assets"
        if not any((folder / n).exists() for n in assets.FILES):
            self.note.setText(_("В этой папке нет моделей Count-In"))
            return

        def run(job):
            assets.copy_from(folder, lambda d, n: job.report(_("Скопировано {d:.0f} из {n:.0f} МБ", d=d / 1e6, n=n / 1e6), d, n))
        self._start(run)

    def reject(self):
        if self.job and not self.job.cancelled.is_set() and self.pool.activeThreadCount():
            self.job.cancelled.set()
            return
        super().reject()


def ensure_assets(parent) -> bool:
    """True when the models are in place, after asking for them if needed."""
    if not assets.missing():
        return True
    AssetsDialog(parent).exec()
    return not assets.missing()


class TapDialog(QDialog):
    """Tap tempo over the playing song: Space taps, and once the taps stop the song carries on with a click
    at the tapped tempo and phase. Closing stops it; Apply keeps the tempo."""

    def __init__(self, parent, pcm: np.ndarray, sr: int, start: float, bpm: float):
        super().__init__(parent)
        self.setWindowTitle(_("Темп на слух"))
        self.setMinimumWidth(460)
        self.pcm, self.sr, self.bpm = pcm, sr, bpm
        self.taps: list[float] = []  # song times of the taps
        self.clicking = False
        self.player = Player()
        self.player.volume = output_volume()
        self.offset = 0.0  # song time of the first sample the player got
        self.wall0 = time.monotonic()

        v = QVBoxLayout(self)
        v.setSpacing(14)
        self.value = QLabel(f"{bpm:.1f}", alignment=Qt.AlignCenter)
        f = self.value.font()
        f.setPointSizeF(f.pointSizeF() * 4.5)
        f.setBold(True)
        self.value.setFont(f)
        v.addWidget(self.value)
        v.addWidget(QLabel("BPM", objectName="field", alignment=Qt.AlignCenter))
        self.hint = QLabel(objectName="subtitle", alignment=Qt.AlignCenter, wordWrap=True)
        v.addWidget(self.hint)
        h = QHBoxLayout()
        h.addStretch()
        for text, slot, name in ((_("Отмена"), self.reject, "tool"), (_("Применить"), self.accept, "primary")):
            b = QPushButton(text, objectName=name, clicked=slot)
            b.setFocusPolicy(Qt.NoFocus)
            b.setAutoDefault(False)
            h.addWidget(b)
        v.addLayout(h)
        self._show_hint()

        self.timer = QTimer(self, interval=100, timeout=self._check)
        self.timer.start()
        self._play_from(start, clicks=False)

    def _now(self) -> float:
        """Song time heard right now. The wall clock, not the player's block counter: blocks are 43 ms coarse."""
        lag = self.player.stream.latency if self.player.stream else 0.0
        return self.offset + max(0.0, time.monotonic() - self.wall0 - lag)

    def _play_from(self, t: float, clicks: bool):
        """Plays the song from t, with the tapped click on top when clicks is set."""
        i0 = int(max(0.0, t) * self.sr)
        part = self.pcm[i0:].copy()
        if clicks and len(self.taps) >= 2:
            hi, lo = rd.load_samples(self.sr)
            p = 60.0 / self.bpm
            k = int(np.ceil((t - self.taps[-1]) / p))
            for n in range(k, k + int(len(part) / self.sr / p) + 1):
                s = int(round((self.taps[-1] + n * p) * self.sr)) - i0
                if 0 <= s < len(part):
                    x = lo[: len(part) - s]
                    part[s : s + len(x)] += x[:, None]
        self.offset, self.clicking = i0 / self.sr, clicks
        try:
            self.player.play(part, {}, self.sr)
            self.wall0 = time.monotonic()
        except Exception as e:  # no output device
            self.hint.setText(_("Не удалось воспроизвести: {e}", e=e))

    def _show_hint(self):
        self.hint.setText(_("Стучите пробелом в такт, клик с вашим темпом пойдёт сам") if not self.clicking
                          else _("Играет клик с настуканным темпом. Пробел, чтобы настучать заново"))

    def keyPressEvent(self, e):
        if e.key() != Qt.Key_Space or e.isAutoRepeat():
            return super().keyPressEvent(e)
        now = self._now()
        if self.clicking or (self.taps and now - self.taps[-1] > 2.0):
            self.taps = []
            if self.clicking:
                self._play_from(now, clicks=False)
                now = self._now()
        self.taps = (self.taps + [now])[-12:]
        if len(self.taps) >= 3:
            self.bpm = float(np.clip(60.0 / float(np.median(np.diff(self.taps))), 30, 320))
            self.value.setText(f"{self.bpm:.1f}")
        self._show_hint()

    def _check(self):
        """Two beats without a tap: the tapping is over, the click takes over at the tapped tempo."""
        if self.player.done:
            self.timer.stop()
        elif not self.clicking and len(self.taps) >= 3 and self._now() - self.taps[-1] > 2 * 60.0 / self.bpm:
            self._play_from(self._now(), clicks=True)
            self._show_hint()

    def done(self, r):
        self.timer.stop()
        self.player.stop()
        super().done(r)


class ZoneDialog(QDialog):
    """Tempo and beats per bar of one pause; reset goes back to the detected bars."""

    def __init__(self, parent, start: float, end: float, bpm: float, beats: int):
        super().__init__(parent)
        self.setWindowTitle(_("Пауза {a}–{b}", a=fmt_short(start), b=fmt_short(end)))
        self.removed = self.reset = False
        g = QGridLayout(self)
        self.bpm = QDoubleSpinBox(minimum=30, maximum=320, decimals=1, value=bpm, suffix=" BPM")
        self.beats = QSpinBox(minimum=1, maximum=12, value=beats)
        g.addWidget(QLabel(_("Темп"), objectName="field"), 0, 0)
        g.addWidget(self.bpm, 0, 1)
        g.addWidget(QLabel(_("Долей в такте"), objectName="field"), 1, 0)
        g.addWidget(self.beats, 1, 1)
        h = QHBoxLayout()
        for text, slot in ((_("Удалить паузу"), self._remove), (_("Как в песне"), self._reset)):
            b = QPushButton(text, objectName="tool", clicked=slot)
            if slot == self._reset:
                b.setToolTip(_("Убрать ручной темп и размер: пауза снова идёт по долям, найденным в песне"))
            h.addWidget(b)
        h.addStretch()
        ok = QPushButton(_("Готово"), objectName="primary", clicked=self.accept, default=True)
        h.addWidget(QPushButton(_("Отмена"), objectName="tool", clicked=self.reject))
        h.addWidget(ok)
        g.addLayout(h, 2, 0, 1, 2)

    def _remove(self):
        self.removed = True
        self.reject()

    def _reset(self):
        self.reset = True
        self.reject()


EXPORT_MODE, EXPORT_PARTS, EXPORT_CLICK, EXPORT_TEMPO, EXPORT_FORMAT = (
    "export/mode", "export/parts", "export/click", "export/tempo", "export/format")


def export_format() -> str | None:
    """The backing-track format chosen in the save dialog; None follows the source. A format saved with the
    defaults by an older version still counts until the dialog saves its own."""
    st = QSettings()
    v = st.value(EXPORT_FORMAT, st.value(DEFAULTS + "format", "")) or None
    return v if v in FORMAT_NAMES else None


def option_pix(kind: str | None, on: bool) -> QPixmap:
    """The icon tile of an option card, or its radio mark when kind is None. A chosen one sits on the accent."""
    app = QApplication.instance()
    size = 40 if kind else 20
    pix = QPixmap(size * 2, size * 2)
    pix.setDevicePixelRatio(2)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    ink = on_accent(app) if on else QColor(127, 127, 127)
    if kind:
        p.setBrush(accent_color(app) if on else QColor(127, 127, 127, 40))
        p.drawRoundedRect(QRectF(0, 0, size, size), 10, 10)
        p.drawPixmap(QPointF(9, 9), part_icon(kind, ink, 22))
    elif on:
        p.setBrush(accent_color(app))
        p.drawEllipse(QRectF(0, 0, size, size))
        p.setPen(QPen(ink, 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPolyline(QPolygonF([QPointF(5.5, 10.5), QPointF(8.8, 13.6), QPointF(14.5, 6.8)]))
    else:
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(127, 127, 127, 150), 1.5))
        p.drawEllipse(QRectF(1, 1, size - 2, size - 2))
    p.end()
    return pix


class OptionCard(QFrame):
    """A selectable card: icon, title, one-line note, and the options of the choice, shown while it is chosen."""

    picked = Signal()

    def __init__(self, kind: str, title: str, note: str, body: QLayout):
        super().__init__(objectName="option")
        self.kind = kind
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.icon, self.mark = QLabel(), QLabel()
        text = QVBoxLayout()
        text.setSpacing(2)
        text.addWidget(QLabel(title, objectName="optionTitle"))
        text.addWidget(QLabel(note, objectName="optionNote"))
        head = QHBoxLayout()
        head.setSpacing(12)
        head.addWidget(self.icon)
        head.addLayout(text, 1)
        head.addWidget(self.mark)
        self.body = QWidget()
        self.body.setCursor(Qt.ArrowCursor)
        body.setContentsMargins(52, 2, 0, 2)  # under the title, past the icon
        self.body.setLayout(body)
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(10)
        v.addLayout(head)
        v.addWidget(self.body)
        self.set_on(False)

    def set_on(self, on: bool):
        self.on = on
        self.setProperty("selected", on)
        self.style().unpolish(self)
        self.style().polish(self)
        self.icon.setPixmap(option_pix(self.kind, on))
        self.mark.setPixmap(option_pix(None, on))
        self.body.setVisible(on)

    def mousePressEvent(self, e):
        self.setFocus()
        self.picked.emit()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Space:
            self.picked.emit()
        else:
            super().keyPressEvent(e)


class ExportDialog(QDialog):
    """What Save writes: one backing track in a chosen format, or a multitrack for a DAW. The choice is
    remembered. target(fmt, multi) names the file or folder a choice would write."""

    def __init__(self, parent, target):
        super().__init__(parent)
        self.setWindowTitle(_("Сохранить"))
        self.target = target

        self.fmt = Segmented([(_(name), key) for key, name in FORMAT_NAMES.items()], export_format())
        self.fmt.changed.connect(self._update)
        fmt = QVBoxLayout()
        fmt.setSpacing(6)
        fmt.addWidget(QLabel(_("Формат"), objectName="field"))
        fmt.addWidget(self.fmt)

        self.boxes = {
            EXPORT_PARTS: QCheckBox(_("Партии отдельными файлами (WAV)")),
            EXPORT_CLICK: QCheckBox(_("Сетка кликов (MIDI)")),
            EXPORT_TEMPO: QCheckBox(_("Темповая карта (MIDI)")),
        }
        tips = {
            EXPORT_PARTS: _("Гитара, вокал, бас, барабаны, пианино и всё остальное, на полной громкости. Без галочки пишется один WAV с выбранными уровнями дорожек"),
            EXPORT_CLICK: _("Отсчёт и хай-хэт в паузах нотами на 10-м канале, General MIDI"),
            EXPORT_TEMPO: _("Темп и размер каждого такта, чтобы такты DAW совпали с песней"),
        }
        files = QVBoxLayout()
        files.setSpacing(8)
        for key, box in self.boxes.items():
            box.setChecked(pref_bool(key, True))
            box.setToolTip(tips[key])
            box.toggled.connect(self._update)
            files.addWidget(box)

        self.backing = OptionCard("file", _("Бэктрек одним файлом"), _("Песня с отсчётом в одном аудиофайле"), fmt)
        self.multi = OptionCard("tracks", _("Мультитрек для DAW"),
                                _("Папка с WAV 32 бит float и MIDI, все файлы с первого такта"), files)
        self.multi.setToolTip(_("Отдельная папка. Все файлы начинаются с первого такта, их можно положить в начало "
                                "проекта Cubase, Logic или Reaper."))
        self.cards = (self.backing, self.multi)
        for c in self.cards:
            c.picked.connect(lambda c=c: self._pick(c))

        # What gets written and where
        self.kind_l = QLabel(objectName="field")
        self.name_l = QLabel(objectName="targetName")
        self.dir_l = QLabel(objectName="optionNote")
        box = QFrame(objectName="target", minimumWidth=544)
        g = QGridLayout(box)
        g.setContentsMargins(14, 9, 14, 9)
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(2)
        g.addWidget(self.kind_l, 0, 0)
        g.addWidget(self.name_l, 0, 1)
        g.addWidget(self.dir_l, 1, 1)
        g.setColumnStretch(1, 1)

        cancel = QPushButton(_("Отмена"), objectName="tool", clicked=self.reject, autoDefault=False)
        self.ok = QPushButton(_("Сохранить"), objectName="primary", clicked=self._accept, default=True)
        h = QHBoxLayout()
        h.addStretch()
        h.addWidget(cancel)
        h.addWidget(self.ok)
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 18, 18, 16)
        v.setSpacing(10)
        v.addWidget(self.backing)
        v.addWidget(self.multi)
        v.addWidget(box)
        v.addSpacing(6)
        v.addLayout(h)
        v.setSizeConstraint(QLayout.SetFixedSize)  # the window follows the options of the chosen card
        self._pick(self.multi if QSettings().value(EXPORT_MODE, "backing") == "multi" else self.backing)

    def _pick(self, card: OptionCard):
        for c in self.cards:
            c.set_on(c is card)
        card.setFocus()
        self._update()

    def _update(self, *_args):
        multi = self.multi.on
        self.ok.setEnabled(not multi or any(b.isChecked() for b in self.boxes.values()))
        out = self.target(self.fmt.value(), multi)
        folder = str(out.parent)
        home = str(Path.home())
        if folder.startswith(home):
            folder = "~" + folder[len(home):]
        self.kind_l.setText(_("Папка") if multi else _("Файл"))
        width = 440  # fits the target box at its narrowest
        self.name_l.setText(self.name_l.fontMetrics().elidedText(out.name, Qt.ElideMiddle, width))
        self.dir_l.setText(self.dir_l.fontMetrics().elidedText(folder, Qt.ElideLeft, width))
        self.name_l.setToolTip(str(out))

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Up, Qt.Key_Down) and isinstance(self.focusWidget(), OptionCard):
            self._pick(self.multi if e.key() == Qt.Key_Down else self.backing)
        elif e.key() in (Qt.Key_Left, Qt.Key_Right) and self.focusWidget() is self.backing and self.backing.on:
            i = self.fmt.values.index(self.fmt.value()) + (1 if e.key() == Qt.Key_Right else -1)
            self.fmt.set_value(self.fmt.values[min(max(i, 0), len(self.fmt.values) - 1)])
            self._update()
        else:
            super().keyPressEvent(e)

    def _accept(self):
        st = QSettings()
        st.setValue(EXPORT_MODE, "multi" if self.multi.on else "backing")
        st.setValue(EXPORT_FORMAT, self.fmt.value() or "")
        for key, box in self.boxes.items():
            st.setValue(key, box.isChecked())
        self.accept()

    def choice(self) -> dict | None:
        """None for the backing track, else which multitrack files to write."""
        if not self.multi.on:
            return None
        return {"parts": self.boxes[EXPORT_PARTS].isChecked(), "click": self.boxes[EXPORT_CLICK].isChecked(),
                "tempo": self.boxes[EXPORT_TEMPO].isChecked()}


def system_info() -> dict[str, str]:
    """What a bug report tells about this computer: no names of files, folders or the user."""
    import platform

    from .core import worker

    info = {
        _("Версия"): update.current_version(),
        _("Система"): platform.platform(terse=True),
        _("Архитектура"): platform.machine(),
        _("Процессор"): _cpu_name(),
        _("Ядер"): str(os.cpu_count()),
        _("Память"): f"{_ram_gb():.0f} GB" if _ram_gb() else "?",
        _("Язык"): i18n.language(),
        # The setting alone: reading the key would touch the Keychain and could ask for access.
        _("Разделение"): _("в облаке") if pref_bool(CLOUD_KEY, False) else _("на этом компьютере"),
    }
    if worker.separator().last:
        sec, how = worker.separator().last
        info[_("Последнее разделение")] = f"{sec:.0f} s, {how}"
    return info


def _cpu_name() -> str:
    import platform
    import subprocess

    if sys.platform == "darwin":
        try:
            return subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True,
                                  timeout=2).stdout.strip()
        except OSError:
            pass
    return platform.processor() or "?"


def _ram_gb() -> float:
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024**3
    except (ValueError, OSError, AttributeError):  # Windows has no sysconf
        import ctypes

        class Mem(ctypes.Structure):
            _fields_ = [("len", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                        ("rest", ctypes.c_ulonglong * 6)]

        m = Mem(len=ctypes.sizeof(Mem))
        try:
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return m.total / 1024**3
        except Exception:
            return 0.0


def log_tail(limit: int = 190 * 1024) -> str:
    try:
        data = log_path().read_bytes()[-limit:]
    except OSError:
        return ""
    return data.decode("utf-8", "replace").replace(str(Path.home()), "~")  # paths carry the account name


class ReportDialog(QDialog):
    """Help → Report a problem: the user's words, and with their consent this computer's details and the app log,
    go to the author through the Count-In server."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle(_("Сообщить о проблеме"))
        self.setMinimumWidth(520)
        v = QVBoxLayout(self)
        v.addWidget(QLabel(_("Что случилось? Что вы делали перед этим?"), objectName="field"))
        self.text = QPlainTextEdit(placeholderText=_("Например: после нажатия «Сохранить» программа зависла на 40%"))
        self.text.setMinimumHeight(120)
        v.addWidget(self.text)
        self.contact = QLineEdit(placeholderText=_("Почта или Telegram, если хотите получить ответ (необязательно)"))
        v.addWidget(self.contact)

        self.info = system_info()
        self.send_info = QCheckBox(_("Приложить сведения о компьютере"), checked=True)
        v.addWidget(self.send_info)
        shown = QLabel("\n".join(f"{k}: {val}" for k, val in self.info.items()), objectName="field")
        shown.setTextInteractionFlags(Qt.TextSelectableByMouse)
        shown.setContentsMargins(24, 0, 0, 0)
        v.addWidget(shown)
        self.send_info.toggled.connect(shown.setEnabled)
        self.send_log = QCheckBox(_("Приложить журнал программы (ошибки и время операций, без звука)"),
                                  checked=True)
        v.addWidget(self.send_log)

        self.note = QLabel(objectName="field", wordWrap=True)
        v.addWidget(self.note)
        h = QHBoxLayout()
        h.addStretch()
        h.addWidget(QPushButton(_("Отмена"), objectName="tool", clicked=self.reject))
        self.ok = QPushButton(_("Отправить"), objectName="primary", clicked=self._send, default=True)
        h.addWidget(self.ok)
        v.addLayout(h)
        self.pool = QThreadPool()

    def _send(self):
        from .core import cloud

        text = self.text.toPlainText().strip()
        if not text:
            self.note.setText(_("Опишите проблему хотя бы парой слов."))
            return
        payload = {"text": text[:4000], "contact": self.contact.text().strip()}
        if self.send_info.isChecked():
            payload["system"] = self.info
        if self.send_log.isChecked():
            payload["log"] = log_tail()
        self.ok.setEnabled(False)
        self.note.setText(_("Отправляю…"))
        self.job = Job(lambda _job: cloud.report(payload))
        self.job.signals.done.connect(self._sent)
        self.job.signals.failed.connect(self._failed)
        self.pool.start(self.job)

    def _sent(self, _r):
        log.info("bug report sent")
        self.note.setText(_("Отправлено. Спасибо!"))
        self.ok.setText(_("Готово"))
        self.ok.setEnabled(True)
        self.ok.clicked.disconnect()
        self.ok.clicked.connect(self.accept)

    def _failed(self, error: str):
        self.ok.setEnabled(True)
        if " 429 " in f" {error} ":
            self.note.setText(_("С этого адреса уже отправлено несколько отчётов. Попробуйте через час."))
        else:
            self.note.setText(_("Не отправилось: {e}", e=error.removeprefix("CloudError: ")))


class CloudKeyDialog(QDialog):
    """Paste a Count-In key; a valid one turns on separation in the cloud and shows it in the preferences."""

    def __init__(self, parent):
        from .core import cloud

        super().__init__(parent)
        self.setWindowTitle(_("Ключ Count-In"))
        self.setMinimumWidth(460)
        v = QVBoxLayout(self)
        v.addWidget(QLabel(_("Ключ включает разделение на дорожки на сервере Count-In. Песня уходит на сервер, "
                             "исходный файл удаляется через сутки, партии хранятся 30 дней, чтобы та же песня "
                             "открывалась без нового разделения."), objectName="field", wordWrap=True))
        self.edit = QLineEdit(cloud.key() or "", placeholderText="cin_…", echoMode=QLineEdit.Password)
        v.addWidget(self.edit)
        self.note = QLabel(objectName="field", wordWrap=True)
        v.addWidget(self.note)
        h = QHBoxLayout()
        self.remove = QPushButton(_("Удалить ключ"), objectName="tool", clicked=self._remove, enabled=bool(cloud.key()))
        h.addWidget(self.remove)
        h.addStretch()
        h.addWidget(QPushButton(_("Отмена"), objectName="tool", clicked=self.reject))
        self.ok = QPushButton(_("Проверить и сохранить"), objectName="primary", clicked=self._check, default=True)
        h.addWidget(self.ok)
        v.addLayout(h)
        self.pool = QThreadPool()

    def _check(self):
        from .core import cloud

        token = self.edit.text().strip()
        if not token:
            return
        self.ok.setEnabled(False)
        self.note.setText(_("Проверяю…"))
        self.job = Job(lambda _job: cloud.me(token))
        self.job.signals.done.connect(self._checked)
        self.job.signals.failed.connect(self._refused)
        self.pool.start(self.job)

    def _checked(self, info):
        from .core import cloud

        cloud.set_key(self.edit.text().strip())
        QSettings().setValue(CLOUD_KEY, True)
        self.note.setText(_("Ключ {name}: {used} из {limit} песен в этом месяце. Разделение в облаке включено.",
                            name=info["name"], used=info["used"], limit=info["limit"]))
        self.ok.setText(_("Готово"))
        self.ok.setEnabled(True)
        self.ok.clicked.disconnect()
        self.ok.clicked.connect(self.accept)

    def _refused(self, error: str):
        self.ok.setEnabled(True)
        self.note.setText(_("Ключ не принят: {e}", e=error))

    def _remove(self):
        from .core import cloud

        cloud.set_key(None)
        QSettings().setValue(CLOUD_KEY, False)
        self.accept()


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

        # Stems of songs already separated; the size gets counted off the UI thread
        cache, g3 = card(_("Кэш дорожек"))
        self.cache_l = QLabel(_("считаю…"), objectName="field", minimumWidth=190)
        self.cache_b = QPushButton(_("Очистить"), objectName="small", clicked=self._clear_cache, enabled=False)
        row(g3, _("Занимает"), self.cache_l, self.cache_b)
        g3.addWidget(QLabel(_("Песню, которую уже разделяли, программа открывает без нового разделения."),
                            objectName="field", wordWrap=True), g3.rowCount(), 0, 1, 2)
        self.pool = QThreadPool()
        self._count_cache()

        close = QPushButton(_("Готово"), objectName="primary", clicked=self.accept)
        v = QVBoxLayout(self)
        v.addWidget(out)
        v.addWidget(look)
        v.addWidget(cache)
        if cloud_key():
            box, g4 = card(_("Облачное разделение"))
            self.cloud = QCheckBox(_("Разделять песни на сервере Count-In"), checked=pref_bool(CLOUD_KEY, False))
            self.cloud.toggled.connect(self._cloud_toggled)
            g4.addWidget(self.cloud, 0, 0, 1, 2)
            g4.addWidget(QLabel(_("Перекрытие"), objectName="field"), 1, 0)
            self.overlap = QComboBox()
            for n, text in ((2, _("2 · быстрее")), (4, "4"), (8, _("8 · чище"))):
                self.overlap.addItem(text, n)
            self.overlap.setCurrentIndex(self.overlap.findData(cloud_overlap()))
            self.overlap.setToolTip(_("Сколько раз модель слушает каждый момент песни. Больше даёт чуть чище партии "
                                      "и дольше разделение. Новое значение действует на песни, открытые после "
                                      "смены; партии каждого значения хранятся отдельно."))
            self.overlap.currentIndexChanged.connect(lambda _i: QSettings().setValue(CLOUD_OVERLAP, self.overlap.currentData()))
            g4.addWidget(self.overlap, 1, 1)
            self.cloud_l = QLabel(_("проверяю ключ…"), objectName="field", wordWrap=True)
            g4.addWidget(self.cloud_l, 2, 0, 1, 2)
            v.addWidget(box)
            from .core import cloud

            self.cloud_job = Job(lambda _job: cloud.me())
            self.cloud_job.signals.done.connect(self._cloud_info)
            self.cloud_job.signals.failed.connect(self._cloud_refused)
            self.pool.start(self.cloud_job)
        v.addWidget(close, 0, Qt.AlignRight)
        self._update()

    def _save(self, key, value):
        QSettings().setValue(key, value)
        self._update()

    def _cloud_toggled(self, on: bool):
        """Takes effect at once: the cloud frees the local model, the local mode loads it again."""
        from .core import worker

        QSettings().setValue(CLOUD_KEY, on)
        if on:
            worker.separator().stop()
        else:
            warm_models()

    def _cloud_info(self, i: dict):
        self.cloud_l.setText(_("Ключ {name}: {used} из {limit} песен в этом месяце", name=i["name"], used=i["used"],
                               limit=i["limit"]))

    def _cloud_refused(self, error: str):
        self.cloud_l.setText(_("Ключ не принят: {e}", e=error))

    def _count_cache(self):
        from .core import stemcache

        self.cache_job = Job(lambda _job: stemcache.size())
        self.cache_job.signals.done.connect(self._cache_counted)
        self.pool.start(self.cache_job)

    def _cache_counted(self, result):
        size, songs = result
        self.cache_l.setText(_("{mb:.0f} МБ · песен: {n}", mb=size / 1e6, n=songs) if songs else _("пусто"))
        self.cache_b.setEnabled(songs > 0)

    def _clear_cache(self):
        from .core import stemcache

        self.cache_b.setEnabled(False)
        self.cache_l.setText(_("очищаю…"))
        self.cache_job = Job(lambda _job: (stemcache.clear(), stemcache.size())[1])
        self.cache_job.signals.done.connect(self._cache_counted)
        self.pool.start(self.cache_job)

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
        self.job: Job | None = None
        self.analysis_job: Job | None = None
        self.analysis_pool = QThreadPool()
        self.analysis_pool.setMaxThreadCount(1)
        self.job_pool = QThreadPool()
        self.job_pool.setMaxThreadCount(1)
        self.sep_job: Job | None = None  # whole-song separation started when a track opens
        self.sep_pool = QThreadPool()
        self.sep_pool.setMaxThreadCount(1)
        self.mix_pool = QThreadPool()  # backing, waveform peaks and pause search (mix_job)
        self.mix_pool.setMaxThreadCount(1)
        self.mix_seq, self.mix_wanted = 0, (None, None, None)
        self.after_sep: list = []  # actions that need the whole-song stems, run when they arrive
        self.player = Player()
        self.play_start = None
        self.play_speed = 1.0  # speed of the preview now playing: song time = start + position * speed
        self.speed = 100  # playback and export speed in percent
        self.resume_at = None  # song time a rebuilt preview carries on from
        self.zones_pending = False  # a pause search waits for the running job
        self.models_warm = False  # the separation model starts loading with the first track
        self.backing_key, self.backing, self.wave_source = None, None, None  # see _update_backing
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
        self._load_defaults()
        self._labels()
        self._enable()
        self.player.volume = output_volume()

        # Space plays from anywhere except text fields, so focus on a slider or checkbox does not swallow it.
        QApplication.instance().installEventFilter(self)

    def eventFilter(self, obj, e):
        if (e.type() == QEvent.KeyPress and e.key() == Qt.Key_Space and not e.isAutoRepeat()
                and not e.modifiers() and QApplication.activeModalWidget() is None):
            # Key events go to the focused widget first, so obj is what would type the space.
            if not isinstance(obj, (QLineEdit, QAbstractSpinBox)):
                self._play()
                return True
        front = QApplication.activeWindow()
        if (e.type() == QEvent.KeyPress and e.key() in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Home)
                and not e.modifiers() and QApplication.activeModalWidget() is None
                and QApplication.activePopupWidget() is None
                and (front in (self, None) or not front.isVisible())  # not while Help is in front
                and not any(isinstance(x, (QLineEdit, QAbstractSpinBox, QSlider, QComboBox))
                            for x in (obj, QApplication.focusWidget()))):
            # Arrows walk the pauses, Home goes back to the count-in.
            self._to_start() if e.key() == Qt.Key_Home else self._jump_pause(-1 if e.key() == Qt.Key_Left else 1)
            return True
        return super().eventFilter(obj, e)

    # --- layout ---

    def _build_menu(self):
        mb = self.menuBar()
        file = mb.addMenu(_("Файл"))
        self.open_a = file.addAction(_("Открыть…"), QKeySequence.Open, self._open_dialog)
        self.save_a = file.addAction(_("Сохранить"), QKeySequence.Save, self._save)
        self.close_a = file.addAction(_("Закрыть трек"), QKeySequence.Close, self._close_track)
        self.reveal_a = file.addAction(_("Показать сохранённый файл"), self._reveal)
        file.addSeparator()
        file.addAction(_("Сохранить настройки как по умолчанию"), self._save_defaults)
        file.addAction(_("Вернуть настройки по умолчанию"), self._reset_settings)
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
        help_.addAction(_("Справка Count-In"), QKeySequence.HelpContents, self._help)
        help_.addAction(_("Страница проекта"), lambda: QDesktopServices.openUrl(QUrl(site_url())))
        help_.addSeparator()
        help_.addAction(_("Сообщить о проблеме…"), lambda: ReportDialog(self).exec())

        # Not in any menu: the cloud key is for people the author gave one to.
        key_a = QAction(self, shortcut=QKeySequence("Ctrl+Alt+K"), triggered=lambda: CloudKeyDialog(self).exec())
        key_a.setShortcutContext(Qt.ApplicationShortcut)
        self.addAction(key_a)

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

        # Speaker level of the preview; not part of the song or the saved file
        self.speaker = speaker = QLabel(toolTip=_("Громкость прослушивания"))
        self.out_volume = QSlider(Qt.Horizontal, minimum=0, maximum=100, fixedWidth=130,
                                  toolTip=_("Громкость прослушивания"))
        self.out_volume.setValue(int(output_volume() * 100))
        self.out_volume.setFocusPolicy(Qt.NoFocus)
        self.out_volume.valueChanged.connect(self._output_volume)
        self._speaker_icon()
        h.addWidget(speaker, 0, Qt.AlignVCenter)
        h.addWidget(QLabel(_("Громкость вывода"), objectName="headerLabel"), 0, Qt.AlignVCenter)
        h.addWidget(self.out_volume, 0, Qt.AlignVCenter)
        h.addSpacing(16)
        self.open_b = QPushButton(_("Открыть…"), objectName="tool", clicked=self._open_dialog)
        self.save_b = QPushButton(_("Сохранить"), objectName="tool", clicked=self._save, toolTip="⌘S")
        self.close_b = QPushButton(_("Закрыть"), objectName="tool", clicked=self._close_track, toolTip="⌘W")
        for b in (self.open_b, self.close_b, self.save_b):  # Play lives in the transport bar under the waveform
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
        self.setCentralWidget(body)

    def _progress_panel(self) -> QWidget:
        """Progress of a blocking job in a card at the centre of the veiled waveform: what runs, why, how long,
        a tip and Cancel. Nothing in the window moves during a job."""
        panel = QWidget(objectName="progOverlay")
        box = QFrame(objectName="busyCard", fixedWidth=600)
        outer = QVBoxLayout(panel)
        outer.setAlignment(Qt.AlignCenter)
        outer.addWidget(box)
        shadow = QGraphicsDropShadowEffect(box, blurRadius=48, color=QColor(0, 0, 0, 70))
        shadow.setOffset(0, 12)
        box.setGraphicsEffect(shadow)
        v = QVBoxLayout(box)
        v.setAlignment(Qt.AlignCenter)
        v.setSpacing(4)
        self.prog_label = QLabel(objectName="progTitle", alignment=Qt.AlignCenter)
        self.prog_why = QLabel(objectName="progWhy", alignment=Qt.AlignCenter, wordWrap=True)
        self.prog_why.setFixedWidth(540)  # a fixed width lets the wrapped text get its full height
        self.progress = QProgressBar(textVisible=False, fixedHeight=8, fixedWidth=440)
        self.prog_eta = QLabel(objectName="progEta", alignment=Qt.AlignCenter)
        self.prog_phrase = QLabel(objectName="progPhrase", alignment=Qt.AlignCenter, wordWrap=True)
        self.prog_phrase.setFixedWidth(540)
        self.cancel_b = QPushButton(_("Отмена"), objectName="tool", clicked=self._cancel)
        self.cancel_b.setFocusPolicy(Qt.NoFocus)
        for w in (self.prog_label, self.prog_why):
            v.addWidget(w, 0, Qt.AlignHCenter)
        v.addSpacing(4)
        v.addWidget(self.progress, 0, Qt.AlignHCenter)
        v.addWidget(self.prog_eta, 0, Qt.AlignHCenter)
        v.addSpacing(2)
        v.addWidget(self.prog_phrase, 0, Qt.AlignHCenter)
        v.addSpacing(6)
        v.addWidget(self.cancel_b, 0, Qt.AlignHCenter)
        self.tip_timer = QTimer(self, interval=7000, timeout=self._next_tip)
        self.prog_panel = panel
        self._stage = None
        return panel

    def _next_tip(self):
        """A practical tip for every progress display; while the parts separate with a part turned down, a note
        that the pauses come only after it."""
        tip = True  # music lines are off until a better set is chosen
        text = _(random.choice(TIPS))
        if self.sep_job and self._full_stems() is None and self._filling() and self.settings().levels:
            text = _("Паузы с хай-хэтом появятся, когда закончится разделение на дорожки: их ищут без убранных инструментов")
        self.prog_phrase.setText(text)
        self.wave.busy.phrase.setText(text)
        self.wave.tips.set(text, tip)
        self.wave._place_overlay()

    # --- progress of analysis and background separation ---

    def show_busy(self, title: str, why: str, frac: float | None, eta: str, docked: bool = False):
        """Analysis or background separation in the large card at the centre of the waveform, or, when docked,
        in the capsule at its bottom with the tips card beside it. Both let clicks through to the waveform."""
        w = self.wave
        if not self.tip_timer.isActive():
            self._next_tip()
            self.tip_timer.start()
        w.busy.set(title, why, frac, eta)
        w.pill.ring.set(frac)
        w.pill.text.setText(title + (f"  ·  {frac:.0%}  ·  {eta}" if frac is not None else ""))

        # A part turned down before the parts exist: the waveform and the pauses wait for them, and the user
        # should see that rather than think the levels did nothing
        waiting = bool(self.sep_job and self._full_stems() is None and self.settings().levels)
        w.pill.note.setText("·  " + _("волна и паузы обновятся после разделения"))
        w.pill.note.setVisible(waiting)
        if waiting:
            w.busy.wait.setText(_("Волна и паузы без убранных инструментов появятся, когда закончится разделение"))
        w.busy.wait.setVisible(waiting or bool(self.after_sep))
        w.busy.setVisible(not docked)
        w.pill.setVisible(docked)
        self._show_tips()

    def _show_tips(self):
        """The tips card shows while anything runs: analysis, background separation or a blocking job."""
        w = self.wave
        w.tips_on = w.busy.isVisible() or w.pill.isVisible() or self.job is not None
        w._place_overlay()
        w.update()

    def changeEvent(self, e):
        if e.type() == QEvent.PaletteChange:  # light or dark appearance: redraw the glyphs in the new text colour
            for b in self.findChildren(QPushButton):
                if b.property("glyph"):
                    b.setIcon(glyph(b.property("glyph"), self.palette().text().color(), b.iconSize().width()))
        super().changeEvent(e)

    def hide_busy(self):
        w = self.wave
        self.dock_timer.stop()
        self.busy_docked = False
        w.busy.hide()
        w.busy.wait.hide()
        w.pill.hide()
        if not self.job:
            self.tip_timer.stop()
        self._show_tips()

    def _dock_busy(self):
        """A few seconds into the separation the card folds into the capsule."""
        if not self.sep_job:
            return
        self.busy_docked = True
        self.wave.busy.hide()
        self.wave.pill.show()
        self._show_tips()

    def _undock_busy(self):
        """The capsule opens back into the card for a few seconds."""
        self.busy_docked = False
        self.wave.pill.hide()
        self.wave.busy.show()
        self._show_tips()
        self.dock_timer.start()

    # --- transport and navigation ---

    def _transport(self) -> QWidget:
        """Transport bar under the mini-map: to the count-in, previous pause, play, next pause, the position,
        pause tools, zoom and help."""
        bar = QFrame(objectName="transport")
        h = QHBoxLayout(bar)
        h.setContentsMargins(10, 6, 10, 6)
        h.setSpacing(4)
        self.play_b = glyph_button("play", _("Пробел"), self._play, "tplay", on_accent(QApplication.instance()))
        for b in (glyph_button("start", _("К отсчёту (Home)"), self._to_start),
                  glyph_button("prev", _("Предыдущая пауза (←)"), lambda: self._jump_pause(-1)),
                  self.play_b,
                  glyph_button("next", _("Следующая пауза (→)"), lambda: self._jump_pause(1))):
            h.addWidget(b)
        h.addSpacing(8)
        lcd = QFrame(objectName="lcd")
        lh = QHBoxLayout(lcd)
        lh.setContentsMargins(12, 3, 12, 3)
        lh.setSpacing(8)
        self.lcd_time = QLabel(objectName="lcdTime", minimumWidth=96)  # room for "−0:03.36" in the count-in
        self.lcd_bar = QLabel(objectName="lcdSub", minimumWidth=52)
        lh.addWidget(self.lcd_time)
        lh.addWidget(self.lcd_bar)
        h.addWidget(lcd)

        # Pause fill: title, the count in a chip like the clock, and buttons like the tempo card's
        def vsep() -> QFrame:
            line = QFrame(objectName="vsep", fixedWidth=1, fixedHeight=24)  # short of the bar's edges
            return line

        h.addSpacing(10)
        h.addWidget(vsep())
        h.addSpacing(10)
        h.addWidget(QLabel(_("Заполнение пауз"), objectName="groupTitle"))
        h.addSpacing(4)

        # − count + as in the tempo card: minus removes the current pause, plus makes one at the play position
        h.addWidget(glyph_button("minus", _("Убрать текущую паузу"), self._remove_pause, "round"))
        chip = QFrame(objectName="lcd")
        ch = QHBoxLayout(chip)
        ch.setContentsMargins(10, 3, 10, 3)
        self.pause_l = QLabel(objectName="chipText", alignment=Qt.AlignCenter, minimumWidth=56)
        ch.addWidget(self.pause_l)
        h.addWidget(chip)
        h.addWidget(glyph_button("plus", _("Сделать паузой такт под плейхедом. Несколько тактов: тяните с ⇧ или по шкале времени."),
                                 self._add_pause, "round"))
        help_b = QPushButton("?", objectName="tbtn", clicked=self._help, toolTip=_("Справка Count-In"))
        help_b.setFocusPolicy(Qt.NoFocus)

        # Speed: − percent +, slower with the pitch kept; steps of 5% from 80% to 100%; slower than that the stretch smears the sound
        h.addSpacing(10)
        h.addWidget(vsep())
        h.addSpacing(10)
        h.addWidget(QLabel(_("Скорость"), objectName="groupTitle"))
        h.addSpacing(4)
        h.addWidget(glyph_button("minus", _("Медленнее на 5%, тон не меняется"), lambda: self._set_speed(self.speed - 5), "round"))
        schip = QFrame(objectName="lcd")
        sh = QHBoxLayout(schip)
        sh.setContentsMargins(10, 3, 10, 3)
        self.speed_l = QLabel("100%", objectName="chipText", alignment=Qt.AlignCenter, minimumWidth=48)
        self.speed_l.setToolTip(_("Скорость прослушивания и сохранения. Тон не меняется, отсчёт и хай-хэт встают на замедленную сетку."))
        sh.addWidget(self.speed_l)
        h.addWidget(schip)
        h.addWidget(glyph_button("plus", _("Быстрее на 5%"), lambda: self._set_speed(self.speed + 5), "round"))
        self.speed_bpm_l = QLabel(objectName="lcdSub")  # the tempo it plays at
        self.speed_bpm_l.setToolTip(_("Темп с учётом скорости"))
        h.addSpacing(6)
        h.addWidget(self.speed_bpm_l)
        h.addStretch()
        h.addWidget(vsep())
        h.addSpacing(6)
        for b in (glyph_button("minus", _("Отдалить"), lambda: self._zoom(2.0)),
                  glyph_button("plus", _("Приблизить"), lambda: self._zoom(0.5)),
                  glyph_button("fit", _("Весь трек"), self._zoom_fit)):
            h.addWidget(b)
        h.addSpacing(6)
        h.addWidget(vsep())
        h.addSpacing(6)
        h.addWidget(help_b)
        return bar

    def _set_speed(self, pct: int):
        """Playback and export speed in percent, 80..100; a playing preview carries on at the new speed."""
        pct = int(np.clip(pct, 80, 100))
        if pct == self.speed:
            return
        self.speed = pct
        self.speed_l.setText(f"{pct}%")
        self._speed_bpm()
        self._restart_preview()

    def _speed_bpm(self):
        """The tempo the song plays at with the speed applied, next to the speed; empty at 100%."""
        t = self.current
        if t is None or not t.bpm or self.speed == 100:
            self.speed_bpm_l.setText("")
            return
        self.speed_bpm_l.setText(_("→ {bpm} BPM", bpm=QLocale().toString(t.bpm * self.speed / 100, "f", 1)))

    def _nav_changed(self):
        """Shows the play position and the current pause in the transport bar."""
        w = self.wave
        t = w.cursor_t if w.cursor_t is not None else w.play_t
        if t is None:
            t = min((c.time for c in w.clicks), default=0.0)
        bar = w._bar_at(t)
        self.lcd_time.setText(fmt_time(t)[:-1])
        self.lcd_bar.setText(_("такт {n}", n=bar + 1) if bar is not None else _("отсчёт"))
        icon = "stop" if self.play_start is not None else "play"
        if icon != self.play_b.property("icon"):  # this runs on every playback tick
            self.play_b.setProperty("icon", icon)
            self.play_b.setIcon(glyph(icon, on_accent(QApplication.instance())))
        self.pause_l.setText(_("{i} из {n}", i=w.current_zone + 1, n=len(w.zones)) if w.current_zone is not None
                             else str(len(w.zones)))  # the group title already says what it counts
        w.overview.update()

    def _jump_pause(self, d: int):
        """Previous (-1) or next (+1) pause from the current one, or from the play position."""
        w = self.wave
        if not w.zones:
            return
        if w.current_zone is not None:
            i = w.current_zone + d
        else:
            t = w.cursor_t if w.cursor_t is not None else w.play_t if w.play_t is not None else 0.0
            starts = [w.bars[z.first].start for z in w.zones]
            i = sum(a <= t for a in starts) - (1 if d < 0 else 0)
        self._go_pause(int(np.clip(i, 0, len(w.zones) - 1)))

    def _go_pause(self, i: int):
        """Makes pause i the current one, brings it into view and plays from the bar before it."""
        w = self.wave
        z = w.zones[i]
        a, b = w.bars[z.first].start, w.bars[z.last].end
        w.current_zone, w.play_t = i, w.bars[max(z.first - 1, 0)].start
        span = max(w.v1 - w.v0, (b - a) * 1.6)
        w.show_range((a + b - span) / 2, (a + b + span) / 2)
        self._nav_changed()
        self._play_at(w.play_t)

    def _to_start(self):
        """Back to the count-in: Play starts from it again."""
        w, t = self.wave, self.current
        self._stop()
        w.play_t, w.current_zone = None, None
        if t and t.analysis:
            lead = min((c.time for c in w.clicks), default=t.t0)
            w.show_range(lead - 1.0, lead - 1.0 + (w.v1 - w.v0))
        self._nav_changed()

    def _add_pause(self):
        w = self.wave
        bar = w._bar_at(w.play_t if w.play_t is not None else (w.v0 + w.v1) / 2)
        if bar is not None:
            w.add_zone(bar)
            w.current_zone = w._zone_at(bar)
            self._nav_changed()

    def _remove_pause(self):
        w = self.wave
        if w.current_zone is not None:
            i, w.current_zone = w.current_zone, None
            w._set_zones(w.zones[:i] + w.zones[i + 1:])
            self._nav_changed()

    def _zoom(self, k: float):
        w = self.wave
        mid, half = (w.v0 + w.v1) / 2, float(np.clip((w.v1 - w.v0) * k, 0.3, max(w.duration + 10, 10))) / 2
        w.show_range(mid - half, mid + half)

    def _zoom_fit(self):
        self.wave.show_range(min((c.time for c in self.wave.clicks), default=0.0) - 1.0, self.wave.duration + 1.0)

    def _help(self):
        """The user guide on the site, in the app's language."""
        QDesktopServices.openUrl(QUrl(site_url("guide/")))

    # --- tempo card ---

    def _tempo_card(self) -> QWidget:
        """Large tempo readout with steppers and a tap pad; under it the first downbeat with one-beat shifts
        and the detector's other candidate."""
        self.bpm_spin = QDoubleSpinBox(decimals=2, minimum=30, maximum=320, singleStep=0.1)
        self.bpm_spin.valueChanged.connect(self._bpm_edited)
        self.t0_spin = QDoubleSpinBox(decimals=3, maximum=36000, singleStep=0.001, suffix=_(" с"))
        self.t0_spin.valueChanged.connect(self._t0_edited)
        for spin, size, width in ((self.bpm_spin, 40, 170), (self.t0_spin, 24, 150)):
            spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
            spin.setAlignment(Qt.AlignCenter)

            # Plain numbers until clicked: no focus by tab or on start, so no blinking cursor while working
            # elsewhere; Enter or a click elsewhere drops the focus again.
            spin.setFocusPolicy(Qt.ClickFocus)
            spin.editingFinished.connect(spin.clearFocus)

            # A frameless field with its font set in code: a style sheet border on a spin box without buttons
            # leaves its text field a few pixels wide.
            spin.setFrame(False)

            # The native macOS field still draws its own box and focus ring around the text; a transparent inner
            # field drops both, and a soft fill shows where the typing goes.
            spin.setAttribute(Qt.WA_MacShowFocusRect, False)
            spin.lineEdit().setAttribute(Qt.WA_MacShowFocusRect, False)
            spin.setStyleSheet("QDoubleSpinBox { background: transparent; border: none; padding: 0; margin: 0; }"
                               "QDoubleSpinBox QLineEdit { background: transparent; border: none; border-radius: 8px; }"
                               "QDoubleSpinBox QLineEdit:hover, QDoubleSpinBox QLineEdit:focus "
                               "{ background: rgba(127,127,127,0.13); }")
            spin.lineEdit().setMinimumWidth(width - 4)  # a styled spin box would squeeze its text field
            font = QFont(self.font())
            font.setPixelSize(size)
            font.setWeight(QFont.Bold if size == 40 else QFont.DemiBold)
            spin.setFont(font)
            spin.setFixedSize(width, int(size * 1.35))
            spin.lineEdit().setStyleSheet("background: transparent;")

        def pad(text: str, slot, tip: str = "") -> QPushButton:
            b = QPushButton(text, objectName="tapPad", clicked=slot, toolTip=tip)
            b.setFocusPolicy(Qt.NoFocus)
            return b

        def line(*widgets) -> QHBoxLayout:
            h = QHBoxLayout()
            h.setSpacing(8)
            for w in widgets:
                h.addStretch() if w is None else h.addWidget(w, 0, Qt.AlignVCenter)
            return h

        tempo, g = card(_("Темп"), self._icon_button("reset", _("Вернуть темп, найденный детектором"), self._reset_tempo))
        tempo.setMinimumWidth(310)
        read = QVBoxLayout()
        read.setSpacing(0)
        read.addWidget(self.bpm_spin, 0, Qt.AlignHCenter)
        read.addWidget(QLabel("BPM", objectName="unit", alignment=Qt.AlignCenter))
        top = line(glyph_button("minus", "−0,1 BPM", lambda: self.bpm_spin.stepBy(-1), "round"), None)
        top.addLayout(read)
        top.addStretch()
        top.addWidget(glyph_button("plus", "+0,1 BPM", lambda: self.bpm_spin.stepBy(1), "round"), 0, Qt.AlignVCenter)
        g.addLayout(top, 0, 0, 1, 2)
        pads = line(pad("÷2", lambda: self._scale_bpm(0.5)), pad("×2", lambda: self._scale_bpm(2.0)))
        pads.addWidget(pad(_("Tap  ·  настучать"), self._tap, _("Настучать темп пробелом под играющую песню")), 1)
        g.addLayout(pads, 1, 0, 1, 2)
        g.addWidget(QFrame(objectName="hline"), 2, 0, 1, 2)
        g.addLayout(line(QLabel("1", objectName="badge", alignment=Qt.AlignCenter),
                         QLabel(_("Первая доля"), objectName="cardTitle"), None,
                         self._icon_button("reset", _("Вернуть «1», найденную детектором"), self._reset_t0)), 3, 0, 1, 2)
        g.addLayout(line(glyph_button("left", _("«1» на долю раньше"), lambda: self._shift_beat(-1), "round"), None,
                         self.t0_spin, None,
                         glyph_button("right", _("«1» на долю позже"), lambda: self._shift_beat(1), "round")), 4, 0, 1, 2)
        self.alt_b = QPushButton(_("Детектор нашёл другую «1»  ·  взять"), objectName="candidate", clicked=self._use_alt)
        sp = self.alt_b.sizePolicy()
        sp.setRetainSizeWhenHidden(True)  # the card keeps its height when the button comes and goes
        self.alt_b.setSizePolicy(sp)
        self.alt_b.setFocusPolicy(Qt.NoFocus)
        g.addWidget(self.alt_b, 5, 0, 1, 2)
        return tempo

    def _editor(self) -> QWidget:
        self.detector = QLabel(objectName="subtitle")
        # One reserved line, so notes appearing or going away never move the waveform
        self.warning = QLabel(objectName="warning")
        self.warning.setFixedHeight(self.warning.fontMetrics().height() + 4)
        self.wave = Wave()
        self.wave.t0Changed.connect(self._marker_moved)
        self.wave.overlay = self._progress_panel()
        self.wave.overlay.setParent(self.wave)
        self.wave.overlay.hide()
        self.busy_docked = False
        self.dock_timer = QTimer(self, singleShot=True, interval=4000, timeout=self._dock_busy)
        self.wave.busy, self.wave.pill, self.wave.tips = BusyCard(), BusyPill(), TipCard()
        for w in (self.wave.busy, self.wave.pill, self.wave.tips):
            w.setParent(self.wave)
        self.wave.pill.clicked.connect(self._undock_busy)
        self.wave.tip_lines = [self.wave.busy.phrase, self.prog_phrase]
        self.wave.navChanged.connect(self._nav_changed)

        tempo = self._tempo_card()

        count, g = card(_("Отсчёт"), self._icon_button("reset", _("Вернуть отсчёт по умолчанию"), self._reset_count))
        self.style_seg = Segmented([(_("Ровный"), "even"), (_("Нарастание"), "buildup"), ("MIDI…", "midi")], "buildup")
        self.style_seg.changed.connect(self._style_changed)
        row(g, _("Рисунок"), self.style_seg)
        self.bars_seg = Segmented([(_("1 такт"), 1), (_("2 такта"), 2)], 2)
        self.bars_seg.changed.connect(self._refresh)
        row(g, _("Длина"), self.bars_seg)
        self.meter_seg = Segmented([(m, m) for m in rd.METERS], "4/4")
        self.meter_seg.changed.connect(self._meter_changed)
        row(g, _("Размер"), self.meter_seg)
        self.volume = QSlider(Qt.Horizontal, minimum=-24, maximum=6, value=0, minimumWidth=90)
        self.volume_l = QLabel(minimumWidth=60)
        row(g, _("Громкость"), self.volume, self.volume_l)
        self.click_all = QCheckBox(_("Клик на всю песню"), objectName="rowCheck")
        self.click_all.setToolTip(_("Клик на каждую долю после отсчёта, до конца песни. Заполнение пауз при этом не нужно."))
        g.addWidget(self.click_all, g.rowCount(), 0, 1, 2, Qt.AlignLeft | Qt.AlignVCenter)
        self.click_voice = QComboBox()
        for key, title in rd.VOICES.items():
            self.click_voice.addItem(_(title), key)
        voice = QSettings().value(CLICK_VOICE_KEY, "beep")
        self.click_voice.setCurrentIndex(list(rd.VOICES).index(voice) if voice in rd.VOICES else 0)
        row(g, _("Звук клика"), self.click_voice)
        self.click_db = QSlider(Qt.Horizontal, minimum=-12, maximum=12, minimumWidth=90)
        self.click_db.setValue(int(QSettings().value(CLICK_DB_KEY, 0)))
        self.click_db_l = QLabel(minimumWidth=60)
        row(g, _("Громкость клика"), self.click_db, self.click_db_l)
        self.pattern: Path | None = None

        save_b = self._icon_button("save", _("Сохранить эти настройки как настройки по умолчанию"), self._save_defaults)
        reset_b = self._icon_button("reset", _("Вернуть настройки по умолчанию"), self._reset_settings)
        mix, g = card(_("Бэктрек"), reset_b, save_b)
        self.parts: dict[str, QSlider] = {}
        self.part_l: dict[str, QLabel] = {}
        for name, label, icon, tip in PARTS:
            s = self.parts[name] = QSlider(Qt.Horizontal, minimum=MUTE, maximum=0, value=0, minimumWidth=110)
            self.part_l[name] = QLabel(minimumWidth=80)
            row(g, icon_label(icon, _(label)), MuteButton(s, _(tip)), s, self.part_l[name])
        self.guitar, self.vocals = self.parts["guitar"], self.parts["vocals"]
        self.fill = QCheckBox(_("Заполнять паузы хай-хэтом"), checked=pref_bool(FILL_KEY, True))
        self.fill.setToolTip(_("Такты, где без убранных дорожек тихо, получают хай-хэт на каждую долю. "
                               "Правый щелчок по паузе убирает её, рядом с паузой добавляет такт, края паузы тянутся мышью."))
        self.fill.setObjectName("rowCheck")
        g.addWidget(self.fill, g.rowCount(), 0, 1, 2, Qt.AlignLeft | Qt.AlignVCenter)
        self.fill_build = QCheckBox(_("Нарастание к концу паузы"), checked=pref_bool(FILL_BUILD_KEY, True), objectName="rowCheck")
        self.fill_build.setToolTip(_("Последний такт паузы ведёт к вступлению: вторая половина такта звучит восьмыми, "
                                     "последние три удара громче, как в отсчёте «Нарастание»."))
        self.fill_build.toggled.connect(lambda on: QSettings().setValue(FILL_BUILD_KEY, on))
        self.fill_build.toggled.connect(self._refresh)
        self.fill_build.toggled.connect(lambda _on: self._restart_preview())
        g.addWidget(self.fill_build, g.rowCount(), 0, 1, 2, Qt.AlignLeft | Qt.AlignVCenter)

        # How quiet a bar must be to count as a pause; the waveform shows the level as a dashed line
        self.threshold = QSlider(Qt.Horizontal, minimum=-36, maximum=-6, minimumWidth=110)
        self.threshold.setValue(int(QSettings().value(THRESHOLD_KEY, zn.QUIET_DB)))
        self.threshold.setToolTip(_("Насколько тише среднего уровня песни должен быть такт, чтобы считаться паузой. "
                                    "На волне этот уровень показан пунктиром."))
        self.threshold_l = QLabel(minimumWidth=80)
        row(g, icon_label("threshold", _("Порог тишины")), self.threshold, self.threshold_l)
        self.trim = QCheckBox(_("Обрезать тишину в начале"), objectName="rowCheck")
        g.addWidget(self.trim, g.rowCount(), 0, 1, 2, Qt.AlignLeft | Qt.AlignVCenter)

        for w in (self.volume, *self.parts.values()):
            w.valueChanged.connect(self._labels)
        for w in self.parts.values():
            w.valueChanged.connect(self._gains)
        self.trim.toggled.connect(self._refresh)
        self.fill.toggled.connect(self._fill_toggled)
        self.click_all.toggled.connect(self._click_all_toggled)
        self.click_voice.currentIndexChanged.connect(self._click_sound)
        self.click_db.valueChanged.connect(self._labels)
        self.click_db.valueChanged.connect(self._refresh)
        self.click_db.sliderReleased.connect(self._click_sound)
        self.click_db.valueChanged.connect(lambda v: QSettings().setValue(CLICK_DB_KEY, v))
        self.wave.zonesEdited.connect(self._zones_edited)
        self.wave.zoneClicked.connect(self._edit_zone)
        self.wave.spanSelected.connect(self._span_selected)
        self.wave.playFrom.connect(self._play_from)
        self.wave.playheadClicked.connect(self._seek)

        # Pauses get searched again shortly after the levels or the threshold stop moving.
        self.zone_timer = QTimer(self, singleShot=True, interval=500, timeout=self._levels_settled)
        for w in (*self.parts.values(), self.threshold):
            w.valueChanged.connect(lambda _v: self.zone_timer.start())  # start(int) would take the value as msec
        self.threshold.valueChanged.connect(self._labels)
        self.threshold_timer = QTimer(self, singleShot=True, interval=1200)
        self.threshold_timer.timeout.connect(self._threshold_shown)
        self.threshold.valueChanged.connect(self._threshold_moved)
        self.threshold.sliderReleased.connect(self.threshold_timer.start)
        self.threshold.valueChanged.connect(self._refresh)
        self.threshold.valueChanged.connect(lambda v: QSettings().setValue(THRESHOLD_KEY, v))

        cards = QHBoxLayout()
        cards.setSpacing(12)
        cards.addWidget(tempo, 0)
        cards.addWidget(count, 0)
        cards.addWidget(mix, 1)  # the backing card takes the room the other two leave

        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(22, 4, 22, 12)
        v.setSpacing(8)
        v.addWidget(self.detector)
        v.addWidget(self.warning)
        v.addWidget(self.wave, 1)
        v.addWidget(Overview(self.wave, 46))
        v.addWidget(self._transport())
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
        if not ensure_assets(self):
            self.status.setText(_("Без моделей трек не разобрать"))
            return
        if not self.models_warm and not cloud_on():  # with the cloud the local model only starts when the cloud fails
            self.models_warm = True
            warm_models()
        self._reset_track()
        self._load_defaults()  # every new track starts from the saved defaults
        t = self.current = Track(path)
        self.stack.setCurrentIndex(1)
        self.title.setText(path.stem)
        self.subtitle.setText("")
        self.detector.setText(_("Анализ…"))
        self.show_busy(_("Анализирую трек"), _(BUSY_WHY["analysis"]), None, _("Пожалуйста, подождите…"))
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
        t, pcm, sr, env = result
        if t is not self.current:
            return
        self.hide_busy()
        if t.error:
            self.detector.setText("")
            self.warning.setText(t.error)

            self._enable()
            return
        t.bpm = 60.0 / an.beat_period(t.analysis, rd.METERS[self.meter_seg.value()][0])[0]
        t.t0 = t.analysis.first_downbeat
        self.pcm, self.sr = pcm, sr
        a = t.analysis
        self.wave.set_env(env, len(pcm) / sr)
        self.wave.beats, self.wave.downs, self.wave.bars = list(a.beats), list(a.downbeats), t.bars
        self.detector.setText(_("Детектор: {bpm:.2f} BPM · «1» на {t} · уверенность {conf:.0%}",
                                bpm=a.bpm, t=fmt_time(a.first_downbeat), conf=a.confidence))
        notes = [translate(n) for n in a.notes]
        if a.confidence < 0.4:
            notes.insert(0, _("детектор не уверен, проверьте маркер «1» на слух"))
        self.warning.setText("; ".join(notes))
        self.warning.setToolTip("; ".join(notes))

        self.alt_b.setVisible(a.alt_downbeat is not None)
        self._show_track_values()
        bar = 60.0 / t.bpm * rd.METERS[self.meter_seg.value()][0]
        self.wave.show_range(t.t0 - bar * self.bars_seg.value() - 1.0, t.t0 + 6.0)
        self._enable()
        if isinstance(QApplication.focusWidget(), QAbstractSpinBox):
            QApplication.focusWidget().clearFocus()  # a new track starts with plain numbers, no blinking cursor
        self._start_separation()
        self._auto_zones()

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
        """Opens the tap window over the playing song; Apply sets the tempo."""
        t = self.current
        if not (t and t.analysis and self.pcm is not None):
            return
        self._stop()
        d = TapDialog(self, self.pcm, self.sr, max(0.0, t.t0 - 1.0), t.bpm)
        if d.exec() == QDialog.Accepted and d.taps:
            t.bpm, t.bpm_manual = d.bpm, True
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

    def _reset_tempo(self):
        """The tempo the detector found, for the meter now chosen."""
        t = self.current
        if t and t.analysis:
            t.bpm = 60.0 / an.beat_period(t.analysis, rd.METERS[self.meter_seg.value()][0])[0]
            t.bpm_manual = False
            self._show_track_values()

    def _reset_t0(self):
        """The first downbeat the detector found."""
        t = self.current
        if t and t.analysis:
            t.t0 = t.analysis.first_downbeat
            self._show_track_values()

    def _reset_count(self):
        """The count-in from the saved defaults, or the factory one when none are saved."""
        st = QSettings()
        saved = st.value(DEFAULTS + "style") is not None
        for seg, key, cast, factory in ((self.style_seg, "style", str, "buildup"), (self.bars_seg, "bars", int, 2),
                                        (self.meter_seg, "meter", str, "4/4")):
            try:
                v = cast(st.value(DEFAULTS + key)) if saved else factory
            except (TypeError, ValueError):
                v = factory
            seg.set_value(v if v in seg.values else factory)
        try:
            self.volume.setValue(int(st.value(DEFAULTS + "volume", 0)) if saved else 0)
        except (TypeError, ValueError):
            self.volume.setValue(0)

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
        self.player.gains = {name: keep(s.value()) for name, s in self.parts.items()}

        # A preview started with all parts as they are has no stems to turn down: rebuild it with them. With
        # the pause fill on, the pause search separates the whole song anyway and rebuilds the preview after,
        # so a second, excerpt-only separation would only cost time.
        if (self.play_start is not None and not self.player.stems and any(g < 1 for g in self.player.gains.values())
                and not (self._filling() and self._full_stems() is None) and not cloud_on()):
            self._restart_preview(separate=True)

    def _labels(self):
        self.volume_l.setText(_("{v:+d} дБ", v=self.volume.value()))
        self.click_db_l.setText(_("{v:+d} дБ", v=self.click_db.value()))
        self.threshold_l.setText(_("{v:+d} дБ", v=self.threshold.value()))
        for s, l in ((self.parts[n], self.part_l[n]) for n in self.parts):
            v = s.value()
            l.setText(_("как есть") if v == 0 else _("убрать") if v == MUTE else _("{v} дБ", v=v))

    def settings(self) -> Settings:
        levels = {}
        for name, s in self.parts.items():
            if s.value() < 0:
                levels[name] = float("-inf") if s.value() == MUTE else float(s.value())
        out_root, group = output_prefs()
        return Settings(
            style=self.style_seg.value(), bars=self.bars_seg.value(), meter=self.meter_seg.value(),
            pattern=self.pattern, volume_db=float(self.volume.value()),
            levels=levels, pre_roll="trim" if self.trim.isChecked() else "keep", fmt=export_format(),
            out_root=out_root, group=group, fill=self._filling(), click_all=self.click_all.isChecked(),
            click_voice=self.click_voice.currentData(), click_db=float(self.click_db.value()), fill_build=self.fill_build.isChecked(),
            threshold=float(self.threshold.value()), speed=self.speed / 100,
        )

    def _refresh(self, *_args):
        self._speed_bpm()
        t = self.current
        if not t or not t.analysis:
            return
        try:
            self.wave.clicks = clicks_for(t, self.settings())
        except Exception as e:  # unreadable or empty MIDI pattern
            self.wave.clicks = []
            self.status.setText(str(e))
        self.wave.t0 = t.t0
        self.wave.zones = t.zones or []
        self.wave.fill = self._filling()
        self.wave.threshold_db = float(self.threshold.value())
        if self.wave.ref_db is None and self.pcm is not None:
            self.wave.ref_db = zn.level_db(self.pcm)
        if self.wave.current_zone is not None and self.wave.current_zone >= len(self.wave.zones):
            self.wave.current_zone = None
        self._nav_changed()
        self.wave.update()

    def _threshold_moved(self, _v):
        self.wave.threshold_full = True
        self.threshold_timer.start()

    def _threshold_shown(self):
        if self.threshold.isSliderDown():
            return  # sliderReleased starts the timer again
        self.wave.threshold_full = False
        self.wave.update()

    # --- jobs ---

    def _enable(self):
        busy = self.job is not None
        ready = bool(self.current and self.current.analysis and self.pcm is not None)
        self.play_b.setEnabled(ready and (not busy or self.play_start is not None))
        self.play_a.setEnabled(self.play_b.isEnabled())
        self.save_b.setEnabled(ready and not busy)
        self.fill.setEnabled(not self.click_all.isChecked())
        self.click_voice.setEnabled(self.click_all.isChecked())
        self.click_db.setEnabled(self.click_all.isChecked())
        self.threshold.setEnabled(self._filling())
        self.fill_build.setEnabled(self._filling())
        self.save_a.setEnabled(ready and not busy)
        self.open_b.setEnabled(not busy)
        self.close_b.setEnabled(self.current is not None)
        self.close_a.setEnabled(self.current is not None)
        self.open_a.setEnabled(not busy)
        self.reveal_a.setEnabled(self.last_saved is not None)
        self.prog_panel.setVisible(busy)
        self._nav_changed()
        self._show_tips()
        self.wave.update()

    def _run(self, fn, *args, on_done):
        self.reveal_b.hide()
        self.job = Job(fn, *args)
        self.job.signals.progress.connect(self._progress)
        self.job.signals.done.connect(on_done)
        self.job.signals.failed.connect(self._failed)
        self.job.signals.cancelled.connect(self._cancelled)
        self.progress.setRange(0, 0)
        self._stage = None
        self.prog_label.setText("")
        self.prog_why.setText(_(JOB_WHY.get(fn.__name__, "")))
        self.prog_eta.setText("")
        self._next_tip()
        self.tip_timer.start()
        self.job_pool.start(self.job)
        self._enable()

    def _progress(self, text: str, done: int, total: int):
        if text != self._stage:
            self._stage, self._stage_start, self._stage_done = text, time.monotonic(), done
        if self.current:
            text = text.replace(self.current.path.name + ": ", "")  # the title already names the file
        self.prog_label.setText(text[:1].upper() + text[1:])
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
        if not self.sep_job:
            self.tip_timer.stop()
        self._enable()
        if self.zones_pending:
            self.zones_pending = False
            QTimer.singleShot(0, self._auto_zones)

    def _failed(self, msg: str):
        self._finish()
        self.resume_at = None
        self.status.setText(msg)
        QMessageBox.warning(self, "Count-In", msg)

    def _cancelled(self):
        self._finish()
        self.resume_at = None
        self.status.setText(_("Отменено"))

    def _reset_track(self):
        """Forgets the current track: its audio, stems, waveform and pauses."""
        self.current, self.pcm, self.info, self.stem_cache, self.last_saved = None, None, None, None, None
        self.resume_at, self.zones_pending = None, False
        self.wave.play_t, self.wave.current_zone, self.wave.hover_edge = None, None, None
        self._cancel_separation()
        self.backing_key, self.backing, self.wave_source = None, None, None
        self.wave.env, self.wave.t0, self.wave.beats, self.wave.downs, self.wave.clicks = None, None, [], [], []
        self.wave.bars, self.wave.zones, self.wave.ref_db = [], [], None
        self.wave._cache = (None, None)
        self.wave.update()
        for label in (self.subtitle, self.detector, self.warning, self.file_l, self.status):
            label.setText("")
        self.reveal_b.hide()

    def _close_track(self):
        """Back to the drop page; the app keeps running and the models stay loaded. A running job is cancelled
        and cut loose at once, so a new file can open right away instead of waiting for its next step."""
        if self.current is None:
            return
        self._stop()
        for j in (self.job, self.analysis_job):
            if j:
                j.cancelled.set()
                for sig in (j.signals.progress, j.signals.done, j.signals.failed, j.signals.cancelled):
                    try:
                        sig.disconnect()
                    except (RuntimeError, TypeError):  # nothing connected
                        pass
        self.job = self.analysis_job = None
        if not self.sep_job:
            self.tip_timer.stop()
        self._reset_track()
        self.title.setText("Count-In")
        self.stack.setCurrentIndex(0)
        self._enable()

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
        if self.wave.play_t is not None:
            return self._play_at(self.wave.play_t)
        s = self.settings()
        self._run(preview_job, self.current, s, self.pcm, self.sr, self.stem_cache, self._separate_now(s),
                  on_done=self._preview_ready)

    def _preview_ready(self, result):
        self._finish()
        track, base, stems, sr, start, new_cache, clipped = result
        if track is not self.current:
            return
        if new_cache:
            self.stem_cache = new_cache
        self.status.setText(clip_note(clipped) or (_("Дорожки можно менять на ходу") if stems else
                            _("Пока дорожки разделяются, играет оригинал") if self.sep_job and self.settings().levels
                            else ""))
        self._gains()
        resume, self.resume_at = self.resume_at, None
        try:
            speed = self.settings().speed
            self.player.play(base, stems, sr, int((resume - start) / speed * sr) if resume is not None else 0)
            self.play_speed = speed
        except Exception as e:  # no output device, unsupported channel count
            log.exception("audio output failed")
            self.status.setText(_("Не удалось воспроизвести: {e}", e=e))
            return
        self.play_start = start
        self.play_timer.start()
        self._enable()

    def _tick(self):
        if self.player.done:
            self._stop()
            return
        w = self.wave
        w.cursor_t = self.play_start + self.player.position() * self.play_speed

        # The view pages along with the playhead, as in a DAW, unless the user is moving it by hand
        span = w.v1 - w.v0
        if w.drag is None and w.overview.grab_dt is None and not w.v0 <= w.cursor_t <= w.v1 - 0.03 * span:
            w.show_range(w.cursor_t - 0.05 * span, w.cursor_t + 0.95 * span)
        self._nav_changed()
        w.update()

    def _stop(self):
        if self.play_start is None:
            return
        self.player.stop()
        self.play_start = None
        self.play_timer.stop()
        self.wave.cursor_t = None
        self.wave.update()
        self._enable()

    def _save(self):
        if not self.save_b.isEnabled():
            return
        self._stop()
        s, info, song = self.settings(), self.info or io.probe(self.current.path), self.current.path
        d = ExportDialog(self, lambda fmt, multi: (multitrack_dir if multi else target_path)(song, replace(s, fmt=fmt), info))
        if d.exec() != QDialog.Accepted:
            return
        self.export = d.choice()
        self._write()

    def _write(self):
        """Writes what the export dialog chose; waits for the background separation when it needs the stems."""
        s, what = self.settings(), self.export
        if self._wait_for_stems(self._write, bool(s.levels) or s.fill or bool(what and what["parts"])):
            return
        info = self.info or io.probe(self.current.path)
        out = target_path(self.current.path, s, info) if what is None else multitrack_dir(self.current.path, s, info)
        ask = _("Файл {name} уже есть. Заменить?") if what is None else _("Папка {name} уже есть. Заменить файлы в ней?")
        if out.exists() and QMessageBox.question(self, "Count-In", ask.format(name=out.name)) != QMessageBox.Yes:
            return
        if what is None:
            self._run(save_job, self.current, s, self.stem_cache, on_done=self._saved)
        else:
            self._run(export_job, self.current, s, self.stem_cache, what, on_done=self._saved)

    def _saved(self, result):
        track, out, full, clipped = result
        self._finish()
        if track is not self.current:
            return
        if full:
            self.stem_cache = full
            self._update_backing()
        self._refresh()
        self.last_saved = out
        self.status.setText(" · ".join(x for x in (_("Сохранено: {name}", name=out.name), clip_note(clipped)) if x))
        self.reveal_b.show()
        self._enable()
        if pref_bool(OUT_REVEAL, True):
            reveal(out)

    # --- volume and defaults ---

    def _output_volume(self, v: int):
        QSettings().setValue(OUTPUT_VOLUME_KEY, v)
        self.player.volume = v / 100
        self._speaker_icon()

    def _speaker_icon(self):
        self.speaker.setPixmap(part_icon("muted" if self.out_volume.value() == 0 else "speaker", QColor(127, 127, 127), 20))

    def _save_defaults(self):
        """The current count-in, part levels and pause settings become the start for every track."""
        st = QSettings()
        st.setValue(DEFAULTS + "style", self.style_seg.value() if self.style_seg.value() != "midi" else "buildup")
        st.setValue(DEFAULTS + "bars", self.bars_seg.value())
        st.setValue(DEFAULTS + "meter", self.meter_seg.value())
        st.setValue(DEFAULTS + "volume", self.volume.value())
        for name, s in self.parts.items():
            st.setValue(DEFAULTS + "part/" + name, s.value())
        st.setValue(DEFAULTS + "trim", self.trim.isChecked())
        st.setValue(FILL_KEY, self.fill.isChecked())
        st.setValue(DEFAULTS + "click_all", self.click_all.isChecked())
        st.setValue(THRESHOLD_KEY, self.threshold.value())
        self.status.setText(_("Настройки сохранены как настройки по умолчанию"))

    def _icon_button(self, kind: str, tip: str, slot) -> QPushButton:
        b = QPushButton(objectName="iconButton", clicked=slot, toolTip=tip)
        b.setIcon(QIcon(part_icon(kind, QColor(127, 127, 127), 18)))
        b.setIconSize(QSize(18, 18))
        b.setFixedSize(30, 28)
        b.setFocusPolicy(Qt.NoFocus)
        b.setCursor(Qt.PointingHandCursor)
        return b

    def _reset_settings(self):
        """Back to the saved defaults, or to the factory settings when none are saved."""
        if QSettings().value(DEFAULTS + "style") is not None:
            self._load_defaults()
            self.status.setText(_("Настройки по умолчанию восстановлены"))
            return
        self.style_seg.set_value("buildup")
        self.bars_seg.set_value(2)
        self.meter_seg.set_value("4/4")
        self.volume.setValue(0)
        for s in self.parts.values():
            s.setValue(0)
        self.trim.setChecked(False)
        self.fill.setChecked(True)
        self.click_all.setChecked(False)
        self.click_db.setValue(0)
        self.threshold.setValue(int(zn.QUIET_DB))
        self.status.setText(_("Настройки сброшены"))

    def _load_defaults(self):
        """Saved defaults; a value that no longer fits (an old meter) is skipped."""
        st = QSettings()
        if st.value(DEFAULTS + "style") is None:
            return

        def num(key, default=0):
            try:
                return int(st.value(DEFAULTS + key, default))
            except (TypeError, ValueError):
                return default

        for seg, key, cast in ((self.style_seg, "style", str), (self.bars_seg, "bars", int), (self.meter_seg, "meter", str)):
            try:
                v = cast(st.value(DEFAULTS + key))
            except (TypeError, ValueError):
                continue
            if v in seg.values:
                seg.set_value(v)
        self.volume.setValue(num("volume"))
        for name, s in self.parts.items():
            s.setValue(num("part/" + name))
        self.trim.setChecked(st.value(DEFAULTS + "trim", False) in (True, "true"))
        self.click_all.setChecked(st.value(DEFAULTS + "click_all", False) in (True, "true"))

    # --- pauses ---

    def _filling(self) -> bool:
        return self.fill.isChecked() and not self.click_all.isChecked()

    def _click_sound(self, *_args):
        QSettings().setValue(CLICK_VOICE_KEY, self.click_voice.currentData())
        QSettings().setValue(CLICK_DB_KEY, self.click_db.value())
        self._refresh()
        if self.click_all.isChecked():
            self._restart_preview()

    def _click_all_toggled(self, on: bool):
        self._refresh()
        self._enable()
        if not on:
            self._auto_zones()
        self._restart_preview()

    def _fill_toggled(self, on: bool):
        QSettings().setValue(FILL_KEY, on)
        self._refresh()
        self._enable()
        if on:
            self._auto_zones()
        self._restart_preview()

    def _levels_settled(self):
        """Guitar, vocals or the threshold stopped moving: show the backing and search the pauses again."""
        self._update_backing()
        self._auto_zones()
        if self.sep_job:
            self._next_tip()  # says at once that the pauses wait for the separation

    # --- background separation ---

    def _start_separation(self):
        if self.sep_job or self._full_stems() is not None:
            return
        self.sep_job = Job(separate_job, self.current, self.pcm, self.sr)
        self.sep_job.signals.progress.connect(self._sep_progress)
        self.sep_job.signals.done.connect(self._separated)
        self.sep_job.signals.failed.connect(self._sep_failed)
        self.sep_started, self.sep_stage = None, None
        self.show_busy(_("Разделяю на дорожки"), _(BUSY_WHY["separation"]), None, _("Готовлю модель…"))
        self.dock_timer.start()
        self.wave._place_overlay()
        self.sep_pool.start(self.sep_job)

    def _cancel_separation(self):
        j, self.sep_job, self.after_sep = self.sep_job, None, []
        if j:
            j.cancelled.set()
            for sig in (j.signals.progress, j.signals.done, j.signals.failed):
                try:
                    sig.disconnect()
                except (RuntimeError, TypeError):
                    pass
        self.hide_busy()

    def _sep_progress(self, text: str, done: int, total: int):
        # The cloud goes through stages (upload, queue, separation, download), each named and counted from zero.
        local = text == _("Разделение на дорожки")
        title = _("Разделяю на дорожки") if local else text
        if text != self.sep_stage:
            self.sep_stage, self.sep_started = text, None
        if not total:
            if not local:
                self.show_busy(title, _(BUSY_WHY["separation"]), None, "", self.busy_docked)
            return
        # Time left from the chunks after the first; the first one carries model loading.
        if self.sep_started is None:
            self.sep_started = (time.monotonic(), done)
        t0, d0 = self.sep_started
        left = (total - done) * (time.monotonic() - t0) / (done - d0) if done > d0 else None
        rest = (_("осталось ~{m}:{s:02d}", m=int(left // 60), s=int(left % 60)) if left is not None
                else _("считаю время…"))
        self.show_busy(title, _(BUSY_WHY["separation"]), done / total, rest, self.busy_docked)

    def _separated(self, result):
        t, full, cached = result
        self.sep_job = None
        self.hide_busy()
        if t is not self.current:
            return
        self.stem_cache = full
        self._update_backing()
        from .core import worker

        if cached:
            self.status.setText(_("Дорожки взяты из кэша: эту песню уже разделяли"))
        elif worker.separator().last:
            sec, name = worker.separator().last
            log.info("parts ready in %.1f s with %s", sec, name)
            self.status.setText(_("Дорожки готовы за {m}:{s:02d} · {how}", m=int(sec // 60), s=int(sec % 60), how=name))
        todo, self.after_sep = self.after_sep, []
        for fn in todo:
            fn()
        self._auto_zones()
        self._restart_preview()  # a preview that played without stems picks them up now

    def _sep_failed(self, msg: str):
        self.sep_job, self.after_sep = None, []
        self.hide_busy()
        self.status.setText(msg)

    def _wait_for_stems(self, fn, needed: bool = True) -> bool:
        """While the background separation runs, an action that needs stems waits for it instead of separating
        on its own. True when fn got queued."""
        if not (needed and self.sep_job) or self._full_stems() is not None:
            return False
        if fn not in self.after_sep:
            self.after_sep.append(fn)
        # The waiting action shows in the centred card, which unfolds for a few seconds.
        self.wave.busy.wait.setText(_("Начнётся сразу, как только дорожки будут готовы"))
        self.wave.busy.wait.show()
        self._undock_busy()
        return True

    def _full_stems(self):
        return self.stem_cache[1] if self.pcm is not None and _covers(self.stem_cache, 0, len(self.pcm)) else None

    def _update_backing(self):
        """The waveform shows what will be heard: the song minus the turned-down stems, once they exist. With
        the fill on, the pauses get searched in the same mix. All of it runs in mix_job off the UI thread; a
        newer request makes an older result stale."""
        t, s = self.current, self.settings()
        if self.pcm is None or t is None:
            return
        st = self._full_stems()
        t.search_levels = search_levels(t, s)
        levels = dict(s.levels) if st is not None else {}
        sl = search_levels(t, s) if st is not None else {}
        search = None
        if t.analysis and s.fill and t.zones_key != search_key(t, s) and (st is not None or not search_levels(t, s)):
            search = (t.bars, s.threshold, sl, bool(sl) and not t.auto_done)
        if (id(st) if levels else None, tuple(sorted(levels.items()))) == self.backing_key and search is None:
            self.wave.update()
            return
        self.mix_seq += 1
        self.mix_wanted = (t, (id(st) if levels else None, tuple(sorted(levels.items()))), tuple(sorted(sl.items())))
        j = Job(mix_job, self.mix_seq, self.pcm, self.sr, st, levels, search)
        j.signals.done.connect(self._mixed)
        self.mix_pool.start(j)

    def _mixed(self, result):
        seq, backing, env, found, used = result
        if seq != self.mix_seq or self.mix_wanted[0] is not self.current:
            return  # the levels moved again or the track changed
        t, key, sl = self.mix_wanted
        self.backing_key, self.backing = key, backing
        self.wave.set_env(env, len(backing) / self.sr)
        if found is None:
            return
        threshold, fit = used
        if fit:
            t.auto_done = True
            if threshold != self.threshold.value():
                self.threshold.blockSignals(True)  # the search already ran with it; no second one
                self.threshold.setValue(int(threshold))
                self.threshold.blockSignals(False)
                self._labels()
                self.status.setText(_("Порог тишины подобран по разделённому треку: {v:+d} дБ", v=int(threshold)))
        self._set_found(t, found, (sl, float(threshold)))

    def _auto_zones(self):
        """With the fill on, pauses follow the levels and the threshold; nothing to press."""
        t, s = self.current, self.settings()
        if t:
            t.search_levels = search_levels(t, s)
        if not (t and t.analysis and self.pcm is not None and s.fill) or t.zones_key == search_key(t, s):
            return
        if self.job:
            self.zones_pending = True
            return
        st = self._full_stems()
        if st is None and search_levels(t, s) and self.sep_job:
            return  # _separated searches again
        if st is not None or not search_levels(t, s):
            self._update_backing()  # the search runs with the mix, off the UI thread
            return
        self._run(zones_job, t, s, self.pcm, self.sr, self.stem_cache, on_done=self._zones_found)

    def _set_found(self, t: Track, found: list, key: tuple):
        """Found pauses replace the earlier found ones; pauses the user made or edited stay, and one the user
        removed never comes back. The search always runs without the parts last turned down (search_levels), so
        turning the guitar back on keeps the pauses while a threshold change rebuilds them."""
        found = [z for z in found if not any(i in t.dismissed for i in range(z.first, z.last + 1))]
        zones = zn.merge([z for z in (t.zones or []) if z.kept], found)
        t.covered = _bars_of(zones)
        changed = zones != t.zones
        t.zones, t.zones_key = zones, key
        self._refresh()
        if changed:
            self._restart_preview()

    def _zones_found(self, result):
        track, zones, key, full = result
        self._finish()
        if track is not self.current:
            return
        self.stem_cache = full
        self._update_backing()
        self._set_found(track, zones, key)
        self._enable()
        self._auto_zones()  # the levels may have moved during the search
        self._restart_preview()  # a preview that played without stems picks them up now

    def _zones_edited(self, zones: list):
        if self.current:
            t, now = self.current, _bars_of(zones)
            t.dismissed = (t.dismissed | (t.covered - now)) - now
            t.covered = now
            self.current.zones = list(zones)
            self._refresh()
            self._restart_preview()

    def _span_selected(self, first: int, last: int):
        """A dragged span becomes a pause (replacing the ones it overlaps); its tempo and meter open at once."""
        t = self.current
        if not (t and t.bars):
            return
        self.click_all.setChecked(False)
        if not self.fill.isChecked():
            self.fill.setChecked(True)
        before = [zn.Zone(**vars(z)) for z in (t.zones or [])]
        kept = [z for z in (t.zones or []) if z.last < first or z.first > last]
        z = zn.Zone(first, last, manual=True)
        t.zones = sorted(kept + [z], key=lambda q: q.first)
        self._zones_edited(t.zones)
        if not self._edit_zone(t.zones.index(z)):
            t.zones = before  # cancel brings back what the selection covered
            self._zones_edited(t.zones)

    def _edit_zone(self, i: int) -> bool:
        """Tempo and meter of pause i; False when the dialog was cancelled."""
        t = self.current
        if not (t and t.zones and i < len(t.zones)):
            return False
        z = t.zones[i]
        a, b, bpm, n = zn.describe(t.bars, z)
        d = ZoneDialog(self, a, b, bpm, n)
        result = d.exec()
        if d.removed:
            t.zones.pop(i)
        elif d.reset:
            z.bpm = z.beats = None
            z.manual = True
        elif result == QDialog.Accepted:
            # Only what changed becomes an override; untouched fields keep following the song
            if abs(d.bpm.value() - bpm) > 0.05:
                z.bpm = d.bpm.value()
            if d.beats.value() != n:
                z.beats = d.beats.value()
            z.manual = True
        else:
            return False
        self._zones_edited(t.zones)
        return True

    # --- preview while editing ---

    def _restart_preview(self, separate: bool = False):
        """Rebuilds the playing preview after an edit and carries on from the same song position."""
        if self.play_start is None or self.job:
            return
        self.resume_at = self.play_start + self.player.position() * self.play_speed
        self._stop()
        s = self.settings()
        self._run(preview_job, self.current, s, self.pcm, self.sr, self.stem_cache, separate or bool(s.levels),
                  max(0.0, self.resume_at - 0.5), on_done=self._preview_ready)

    def _play_from(self, t: float):
        """Double-click on the waveform: plays from the bar before that point, so a pause can be heard in place."""
        if self.job or not (self.current and self.current.analysis and self.pcm is not None):
            return
        bars = self.current.bars
        i = next((k for k, b in enumerate(bars) if b.start <= t < b.end), None)
        self._play_at(bars[max(i - 1, 0)].start if i is not None else max(0.0, t - 2.0))

    def _seek(self):
        """A click that moves the playhead during playback carries on from there at once."""
        if self.play_start is not None and self.wave.play_t is not None:
            self._play_at(self.wave.play_t)

    def _play_at(self, start: float):
        """Plays from the song time start."""
        if self.job or not (self.current and self.current.analysis and self.pcm is not None):
            return
        self._stop()
        s = self.settings()
        self._run(preview_job, self.current, s, self.pcm, self.sr, self.stem_cache, self._separate_now(s), start,
                  on_done=self._preview_ready)

    def _separate_now(self, s) -> bool:
        """Whether a preview separates its own excerpt: whenever a part is turned down. During the whole-song
        separation the excerpt goes ahead of it in the separation process, so playback starts in seconds."""
        return bool(s.levels) and not cloud_on()

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


def is_dark(app: QApplication) -> bool:
    scheme = app.styleHints().colorScheme()
    if scheme == Qt.ColorScheme.Unknown:
        return app.palette().window().color().lightness() < 128
    return scheme == Qt.ColorScheme.Dark


def luminance(c: QColor) -> float:
    """WCAG relative luminance, 0 for black, 1 for white."""
    def lin(v: float) -> float:
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(c.redF()) + 0.7152 * lin(c.greenF()) + 0.0722 * lin(c.blueF())


def contrast(a: QColor, b: QColor) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def window_color(dark: bool) -> QColor:
    return QColor("#1e1e20" if dark else "#f5f5f7")


def accent_color(app: QApplication) -> QColor:
    """Selection blue in dark mode; in light mode that blue is too pale, so the system accent. An accent that
    blends into the window (white in the light theme, near-black in the dark one) falls back to system blue."""
    pal, dark = app.palette(), is_dark(app)
    c = pal.color(QPalette.Active, QPalette.Highlight) if dark else pal.color(QPalette.Accent)
    if contrast(c, window_color(dark)) < 1.6:
        c = QColor("#0a84ff" if dark else "#007aff")
    return c


def on_accent(app: QApplication) -> QColor:
    """Text and icons on the accent: white, as the system draws them, unless the accent is too light for it
    (a white, light grey or yellow accent), then near-black."""
    acc = accent_color(app)
    return QColor(255, 255, 255) if contrast(acc, QColor(255, 255, 255)) >= 3.0 else QColor(28, 28, 30)


def style_sheet(app: QApplication) -> str:
    """STYLE with colors for the system light or dark appearance."""
    dark = is_dark(app)
    colors = ({"@WINDOW@": "#1e1e20", "@SIDEBAR@": "#262628", "@CARD@": "#2c2c2e", "@SEP_CARD@": "rgba(44,44,46,0.9)", "@BORDER@": "#3a3a3c",
               "@FIELD@": "#a1a1a6", "@WARN@": "#ffb340", "@TIP_CARD@": "rgba(44,44,46,0.82)"} if dark else
              {"@WINDOW@": "#f5f5f7", "@SIDEBAR@": "#ececee", "@CARD@": "#ffffff", "@SEP_CARD@": "rgba(255,255,255,0.9)", "@BORDER@": "#d8d8dc",
               "@FIELD@": "#6e6e73", "@WARN@": "#a35a00", "@TIP_CARD@": "rgba(255,255,255,0.82)"})

    colors["@ACCENT@"] = accent_color(app).name()
    colors["@ACCENT_TEXT@"] = on_accent(app).name()
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
    # Only real files: the system may pass its own arguments at launch, and those are no reason for an error.
    w.open_files([p for p in map(Path, sys.argv[1:]) if p.is_file()])
    w.showMaximized()

    # CI smoke test: start, show the window, quit; a missing bundled icon fails it.
    if os.environ.get("COUNTIN_SMOKE"):
        QTimer.singleShot(2000, lambda: app.exit(1 if app.windowIcon().isNull() else 0))
    else:
        w.check_updates()
        QTimer.singleShot(400, lambda: ensure_assets(w))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

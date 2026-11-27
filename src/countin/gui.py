"""Count-In desktop app."""

import logging
import math
import os
import subprocess
import sys
import threading
import random
import time
from dataclasses import dataclass, field, replace
from functools import lru_cache
from html import escape
from itertools import groupby
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import quote

import numpy as np
from PySide6.QtCore import QEvent, QLineF, QLocale, QObject, QSettings, QStandardPaths, QUrl, QPointF, QRectF, QRunnable, QSize, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QAction, QCursor, QDesktopServices, QFont, QIcon, QImage, QKeySequence, QColor, QPainter, QPainterPath, QPen, QPalette, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QAbstractSpinBox, QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFrame,
    QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLayout, QLineEdit, QMenu, QPlainTextEdit, QMainWindow, QMessageBox, QProgressBar, QPushButton, QRadioButton, QScrollArea,
    QGraphicsDropShadowEffect, QListWidget, QListWidgetItem, QSlider, QSpinBox, QStackedWidget, QStatusBar, QTextBrowser, QToolTip, QVBoxLayout, QWidget,
)

from . import changes, i18n, update
from .i18n import _
from .help import HELP, TIPS
from .tempo_lane import TempoLane
from .core import analyze as an
from .core import io
from .core import render as rd
from .core import guitar_pro, songs, tempo, timeline
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
    "preview_job": "Готовлю к прослушиванию отсчёт и песню с вашими настройками.",
    "save_job": "Сохраняю трек с отсчётом и выбранным уровнем дорожек.",
    "export_job": "Сохраняю мультитрек. Дорожки, клик и темп начинаются с первого такта.",
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
#keyBig { font-size: 30px; font-weight: 600; }
#headerLabel { font-size: 15px; color: @FIELD@; }
QPushButton#iconButton { border: 1px solid @BORDER@; border-radius: 7px; background: rgba(127,127,127,0.12); padding: 0; }
QPushButton#iconButton:hover { background: rgba(127,127,127,0.24); }
#progOverlay { background: transparent; }
#sepWait { color: @WARN@; font-weight: 600; }
#progTitle { font-size: 26px; font-weight: 700; }
#progWhy, #progEta { font-size: 15px; color: @FIELD@; }
#progPhrase { font-size: 15px; font-style: italic; color: @FIELD@; }
#subtitle { color: @FIELD@; }
#warning { color: @WARN@; }
#drop { border: 2px dashed @BORDER@; border-radius: 18px; color: @FIELD@; font-size: 18px; }
QPushButton#recent { border: none; background: transparent; color: @ACCENT@; padding: 2px 0; text-align: left; }
QPushButton#recent:hover { text-decoration: underline; }
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
QPushButton#headBtn { border: 1px solid @BORDER@; border-radius: 9px; background: rgba(127,127,127,0.10);
                      font-size: 14px; font-weight: 600; min-height: 28px; padding: 0 12px; }
QPushButton#headBtn:hover { background: rgba(127,127,127,0.22); }
QPushButton#headBtn:disabled { color: rgba(127,127,127,0.6); }
QPushButton#headBtn[accent="true"] { background: @ACCENT@; color: @ACCENT_TEXT@; border-color: @ACCENT@; }
QPushButton#headBtn[accent="true"]:disabled { background: rgba(127,127,127,0.10); color: rgba(127,127,127,0.6);
                                              border-color: @BORDER@; }
QPushButton#solo { border: 1px solid rgba(127,127,127,0.3); border-radius: 6px; padding: 0; font-weight: 600; }
QPushButton#solo:checked { background: @ACCENT@; border-color: @ACCENT@; color: white; }
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
#pillNote { font-size: 14px; font-weight: 600; color: @WARN@; }
QFrame#transport { background: @CARD@; border: 1px solid @BORDER@; border-radius: 12px; }
QPushButton#tbtn { border: none; border-radius: 8px; background: transparent; min-width: 32px; min-height: 30px; padding: 0; }
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
SEEN_KEY = "app/seen_version"  # the version whose What's New the user has seen


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
    ref_db: float | None = None  # level of the whole song (zones.level_db)
    covered: set = field(default_factory=set)  # bars under the pauses as last shown
    dismissed: set = field(default_factory=set)  # bars whose pause the user removed; a search never brings them back
    tonal: tuple | None = None  # (tonic pitch class, minor, confidence) from analyze.detect_key
    key: str | None = None  # the file's content hash; what the user set is kept under it (core/songs.py)
    markers: list = field(default_factory=list)  # (song time, name) of the song's sections
    extra: np.ndarray | None = None  # the player's own guitar, EQ'd and lined up with the song; preview and save mix it in
    sections: list[tempo.Section] = field(default_factory=list)
    proposed: list[tempo.Section] = field(default_factory=list)
    dismissed_times: list = field(default_factory=list)
    energy: np.ndarray | None = None  # cumulative mean-square energy in ~10 ms blocks for changed bar edges
    edits: tuple[timeline.Edit, ...] = ()
    cut_fade_ms: float = 5.0
    saved_loops: tuple[timeline.Loop, ...] = ()


COUNT_VOICE_DEFAULT = "stick"
METRONOME_VOICE_DEFAULT = "hihat"


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
    click_all: bool = False  # a click on every beat outside enabled pause fills
    click_voice: str = METRONOME_VOICE_DEFAULT
    click_db: float = 0.0  # song click level, independent of the count-in
    fill_build: bool = False  # optionally leads into the entry in eighths
    speed: float = 1.0  # playback and export speed, pitch kept (0.8 = 20% slower)
    semis: float = 0.0  # transposition in semitones, tempo kept
    split: bool = False  # backing track with the click alone on the left and the music in mono on the right
    normalize: bool = False  # the saved backing track at −14 LUFS, peaks at most −1 dB
    threshold: float = zn.QUIET_DB  # dB below the song average that counts as a pause
    count_voice: str = COUNT_VOICE_DEFAULT
    practice: timeline.Edit | None = None
    training: tuple[float, float, int] | None = None


def audible_edits(t: Track, s: Settings) -> tuple[timeline.Edit, ...]:
    edits = t.edits
    if s.practice:
        p = s.practice
        edits = timeline.with_edit(edits, p.start, p.end, p.speed)
    if s.training:
        start, end, steps = s.training
        for a, b, rate in timeline.Timeline(t.duration, s.speed, edits).slices(start, end):
            if rate < 1:
                edits = timeline.with_edit(edits, a, b, min(1., rate + steps * .05))
    return edits


def edit_clock(t: Track, s: Settings, duration: float | None = None):
    return timeline.Timeline(t.duration if duration is None else duration, s.speed, audible_edits(t, s), t.cut_fade_ms)


def song_grid_beats(t: Track) -> np.ndarray:
    if t.analysis.manual:
        return np.arange(t.t0, t.duration, 60.0 / t.bpm)
    return t.analysis.beats


def count_pattern(s: Settings, entry: float, period: float, meter: str) -> list[rd.Click]:
    if s.style == "midi" and s.pattern:
        clicks = rd.pattern_grid(s.pattern, entry, period, meter)
    else:
        grid = rd.buildup_grid if s.style == "buildup" else rd.click_grid
        clicks = grid(entry, period, s.bars, meter)
    count_gain = 10 ** (s.volume_db / 20)
    return [replace(c, voice=s.count_voice, gain=c.gain * count_gain) for c in clicks]


def count_clicks(t: Track, s: Settings) -> list[rd.Click]:
    period = 60.0 / t.bpm
    clicks = count_pattern(s, t.t0, period, s.meter)
    pickup = t.sections[0].pickup_start if t.sections else None
    if pickup is None and t.analysis and abs(t.t0 - t.analysis.first_downbeat) < 0.001:
        pickup = t.analysis.pickup_start
    if pickup is not None and pickup < t.t0:
        clicks = rd.before_pickup(clicks, pickup, period * rd.meter_shape(s.meter)[0])
    return clicks


def pause_count(t: Track, s: Settings, start: float, end: float, clock: timeline.Timeline | None) -> list[rd.Click]:
    a, b = (clock.output_at(start), clock.output_at(end)) if clock else (start, end)
    if b >= (clock.length if clock else t.duration) - 1e-8 or b <= a:
        return []
    entry = clock.source_at(b) if clock else end
    section = t.sections[tempo.index(t.sections, entry)] if t.sections else None
    meter, period = (section.meter, section.period) if section else (s.meter, 60.0 / t.bpm)
    if clock:
        period /= clock.rate_at(entry)
    return [c for c in count_pattern(s, b, period, meter) if a - 1e-8 <= c.time < b]


def song_clicks_for(t: Track, s: Settings, clock: timeline.Timeline | None = None) -> list[rd.Click]:
    def place(clicks):
        return [replace(c, time=clock.output_at(c.time)) for c in clicks if clock.contains(c.time)] if clock else clicks

    period = 60.0 / t.bpm
    song_clicks = []
    if s.click_all and t.sections:
        gain = 10 ** (s.click_db / 20)
        song_clicks = [rd.Click(at, i == 0 and b.accent, gain, s.click_voice) for b in t.bars
                       for i, at in enumerate(b.beats)]
    elif s.click_all and (t.bars or (t.analysis and t.analysis.manual)):
        from .core import daw

        song_clicks = daw.song_clicks(song_grid_beats(t), t.t0, period, rd.meter_shape(s.meter)[0], s.click_voice,
                                      10 ** (s.click_db / 20))
    spans, fills = [], []
    for zone in t.zones or []:
        if not (s.fill or zone.count_in):
            continue
        a, b = zn.bounds(t.bars, zone)
        count = pause_count(t, s, a, b, clock) if zone.count_in else []
        if clock:
            a, b = clock.output_at(a), clock.output_at(b)
        start_count = min((c.time for c in count), default=b)
        if s.fill:
            hats = place(zn.zone_clicks(t.bars, [zone], t.t0, t.bar_db, s.fill_build))
            fills.extend(c for c in hats if not count or c.time < start_count - 1e-8)
            spans.append((a, b))
        elif count:
            spans.append((start_count, b))
        fills.extend(count)
    return [c for c in place(song_clicks) if not any(a - 1e-8 <= c.time < b - 1e-8 for a, b in spans)] + fills


def clicks_for(t: Track, s: Settings) -> list[rd.Click]:
    return count_clicks(t, s) + song_clicks_for(t, s)


def edited_clicks(t: Track, s: Settings, clock: timeline.Timeline) -> list[rd.Click]:
    downbeat = edited_downbeat(t, clock)
    rate = clock.rate_at(t.t0)
    lead = [replace(c, time=downbeat + (c.time - t.t0) / rate) for c in count_clicks(t, s)]
    return lead + song_clicks_for(t, s, clock)


def edited_downbeat(t: Track, clock: timeline.Timeline) -> float:
    at = clock.output_at(t.t0)
    return at if at < clock.length - .001 else 0.0


def search_levels(t: Track, s: Settings) -> dict:
    """The levels the pauses are searched with: each part at the lowest level it was set to on this track.
    Raising a part again, fully or partly, to check the hi-hat against it keeps the pauses where they were;
    only the threshold or the user's own edits change them."""
    out = dict(t.search_levels or {})
    for k, v in s.levels.items():
        if v < 0:  # a boosted part never makes a pause
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
    return zn.zones_from(zn.quiet_bars(backing, sr, t.bars, t.ref_db, s.threshold))


def export_zones(t, pcm, sr, st, s):
    if s.fill and t.bars and t.zones_key is None:
        found = find_zones(t, pcm, sr, st, s)
        kept = [z for z in t.zones or [] if z.kept]
        t.zones = zn.merge(kept, [z for z in found if not any(i in t.dismissed for i in range(z.first, z.last + 1))])


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
        bars, threshold, sl, fit, ref = search
        mix = backing if sl == levels else stems.attenuate(pcm, st, sl) if st and sl else pcm
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
    "beat grid starts later in the song; check the downbeat marker": "сетка начинается позже, проверьте маркер «1»",
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
    return _("Клики перегружают звук в {n} сэмплах, убавьте громкость отсчёта или метронома", n=clipped) if clipped else ""


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
        self.track_source = None
        self.signals = Job.Signals()
        self.cancelled = threading.Event()

    def report(self, text: str, done: int = 0, total: int = 0) -> None:
        if self.cancelled.is_set():
            raise Cancelled
        self.signals.progress.emit(text, done, total)

    def run(self):
        try:
            result = self.fn(self, *self.args)
            if self.track_source is not None:
                result = (self.track_source, *result[1:])
            self.signals.done.emit(result)
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


def analyze_job(job: Job, track: Track, default_meter="4/4"):
    """Returns (track, pcm, sr, waveform peaks); the UI keeps the PCM, so the file is decoded once and never on
    the UI thread, and the waveform peaks get made here too."""
    job.report(_("Анализ: {name}", name=track.path.name))
    pcm, sr, env = None, 0, None
    try:
        pcm, info = io.decode(track.path)
        sr = info.sr
        track.duration = len(pcm) / sr
        raw = an.detect(pcm, sr)
        try:
            track.analysis = an.analyze(pcm, sr, raw)
        except an.RhythmError:
            track.analysis = an.Analysis(raw[0], raw[1], track.bpm, track.t0, 0, 0, 0, 0, manual=True)
        track.key = songs.key(track.path)
        track.tonal = an.detect_key(pcm, sr)
        track.bars = zn.bars_from(track.analysis.beats, track.analysis.downbeats, track.duration)
        track.ref_db = zn.level_db(pcm)
        track.proposed = tempo.propose(track.analysis.beats, track.analysis.downbeats, track.duration,
                                       track.analysis.first_downbeat, default_meter)
        block = max(1, sr // 100)
        chunks = pcm[:len(pcm) // block * block].reshape(-1, block, pcm.shape[1])
        power = np.einsum("ijk,ijk->i", chunks, chunks) / (block * pcm.shape[1])
        track.energy = np.r_[0.0, np.cumsum(power, dtype=float)]
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
            try:
                tags = io.probe(song).tags
            except (OSError, RuntimeError):
                tags = {}
            title = tags.get("title", "").strip()
            artist = tags.get("artist", "").strip()
            track_title = " — ".join(filter(None, (artist, title))) if title else song.name
            st = cloud.separate(pcm, sr, stem_key(song), job.report, job.cancelled.is_set, cloud_overlap(),
                                track_title=track_title)
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


@dataclass
class PreviewState:
    start: float
    sound: tuple
    stem_cache: tuple | None
    extra: np.ndarray | None
    clock: timeline.Timeline | None = None
    origin: float = 0.0

    def seconds_at(self, source: float) -> float:
        return self.clock.output_at(source) - self.origin if self.clock else (source - self.start) / self.sound[0]

    def source_at(self, seconds: float) -> float:
        return self.clock.source_at(self.origin + seconds) if self.clock else self.start + seconds * self.sound[0]


def preview_job(job: Job, track: Track, s: Settings, pcm: np.ndarray, sr: int, cached, separate: bool,
                at: float | None = None):
    """The whole song from the count-in, with stems kept apart so the player can mix them live, rendered at
    s.speed; the player then jumps anywhere in it at once. Only when the parts have yet to be separated for it,
    it runs from song time `at` (or the count-in) and stops PREVIEW_FROM seconds after `at` (PREVIEW_AFTER after
    the downbeat).

    Returns (song with clicks, {stem: (array, output offset)}, sr, original-song time of the first sample, new
    stem cache, clipped samples, preview state). Output time runs at s.speed.
    """
    clicks = clicks_for(track, s)
    dur = len(pcm) / sr
    edits = audible_edits(track, s)
    clock = timeline.Timeline(dur, s.speed, edits, track.cut_fade_ms) if edits else None
    count_in = min(c.time for c in clicks)
    if not separate or _covers(cached, int(max(0.0, min(count_in, at or count_in) - 0.5) * sr), len(pcm)):
        first, end = min(count_in, at if at is not None else count_in), dur
    else:
        first = count_in if at is None else at
        end = (max(track.t0, clock.source_at(0) if clock else 0) + PREVIEW_AFTER
               if at is None else at + PREVIEW_FROM)
    i0 = int(max(0.0, first - 0.5) * sr)
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
    origin = round(clock.output_at(lo_t) * sr) / sr if clock else lo_t / s.speed
    if clock:
        limit = clock.output_at(min(end, dur))
        first_out = clock.output_at(first)
        all_clicks = edited_clicks(track, s, clock)
        win = [replace(c, time=c.time - origin) for c in all_clicks
               if c.time < limit and (at is None or c.time >= first_out - .35)]
    else:
        win = [replace(c, time=(c.time - lo_t) / s.speed) for c in clicks if first - 0.35 <= c.time < end]
    song, part = pcm[i0:i1], dict(part or {})
    if track.extra is not None:
        song = song + track.extra[i0:i1]
    if clock or s.speed != 1.0 or s.semis:
        # Stretching shifts phases, so a stretched song minus a stretched stem no longer cancels. The slowed
        # song gets rebuilt from its slowed pieces, every stem and the rest, so the player's live subtraction
        # stays exact.
        rest = song - sum(part.values()) if part else song
        tracks = [*part.values(), rest]
        *slow, rest = (clock.audio(tracks, sr, lo_t, i1 / sr, s.semis) if clock else
                       rd.stretch_many(tracks, sr, s.speed, s.semis))
        part = dict(zip(part, slow))
        song = rest + sum(part.values()) if part else rest
    hits = rd.load_samples(sr)
    r = rd.render(song, sr, win, 0.0, samples=hits)

    # The click plays as its own part at full level: mute, solo and its level work live, and over a loud master
    # it stays audible, while the saved file turns a click down to fit (render's limiter)
    click = np.zeros(len(r.pcm), dtype=np.float32)
    for c, at in zip(win, r.click_samples):
        pair = hits if c.voice == "stick" else rd.voice_samples(c.voice, sr)
        x = (pair[0] if c.accent else pair[1]) * c.gain
        n = min(len(x), len(click) - at)
        click[at : at + n] += x[:n]
    base = r.pcm - r.click[:, None] + click[:, None]

    # Stems play at the output positions of the song samples they came from.
    a = int(round(r.offset * sr))
    stems_out = {k: (v, a) for k, v in part.items()}
    stems_out["click"] = (click[:, None], 0)
    state = PreviewState(lo_t - r.offset * s.speed, (s.speed, s.semis, clicks, edits, track.cut_fade_ms),
                         new_cache if new_cache is not None else cached, track.extra, clock, origin - r.offset)
    return track, base, stems_out, sr, state.source_at(0.0), new_cache, r.clipped, state


def target_path(song: Path, s: Settings, info: io.AudioInfo) -> Path:
    """Where the saved file goes: the output folder from the preferences, optionally Artist/Year - Album."""
    folder = s.out_root
    if folder and s.group:
        folder = io.organized_dir(folder, info.tags, *s.group)
    out = io.output_path(song, s.fmt or info.format, s.levels, folder, s.speed, s.semis)
    return out.with_name(f"{out.stem}_stage{out.suffix}") if s.split else out


def save_job(job: Job, t: Track, s: Settings, cached, span=None, destination: Path | None = None):
    """Writes the track. Returns (track, saved path, (0, stems) when separated or None, clipped samples)."""
    from .core import stems

    job.report(_("{name}: чтение", name=t.path.name))
    pcm, info = io.decode(t.path)
    full, st = None, None
    if s.levels:
        st = cached[1] if _covers(cached, 0, len(pcm)) else _stems(job, pcm, info.sr, _("{name}: разделение", name=t.path.name), song=t.path)
        full = (0, st)
    export_zones(t, pcm, info.sr, st, s)
    if s.levels:
        pcm = stems.attenuate(pcm, st, s.levels)
    if t.extra is not None and len(t.extra) == len(pcm):
        pcm = pcm + t.extra
    job.report(_("{name}: запись", name=t.path.name))
    if s.speed != 1.0 or s.semis:
        job.report(_("{name}: замедление", name=t.path.name))
    if span:
        sr = info.sr
        clock = edit_clock(t, s, len(pcm) / sr)
        i, j = (round(at * sr) for at in span)
        start, end = i / sr, j / sr
        a, b = clock.output_at(start), clock.output_at(end)
        if round(b * sr) - round(a * sr) < 1:
            raise ValueError(_("В выделении нет аудио для экспорта."))
        entry = clock.source_at(a)
        section = t.sections[tempo.index(t.sections, entry)] if t.sections else None
        meter, period = (section.meter, section.period) if section else (s.meter, 60 / t.bpm)
        clicks = count_pattern(s, 0., period / clock.rate_at(entry), meter)
        origin = round(a * sr) / sr
        clicks.extend(replace(c, time=c.time - origin) for c in song_clicks_for(t, s, clock) if a <= c.time < b)
        pcm, = clock.audio([pcm[i:j]], sr, start, end, s.semis)
        r = rd.render(pcm, sr, clicks, 0.)
    elif t.edits:
        clock = edit_clock(t, s, len(pcm) / info.sr)
        pcm, = clock.audio([pcm], info.sr, 0, clock.duration, s.semis)
        r = rd.render(pcm, info.sr, edited_clicks(t, s, clock), edited_downbeat(t, clock), s.pre_roll)
    else:
        r = rd.render(pcm, info.sr, clicks_for(t, s), t.t0, s.pre_roll, speed=s.speed, semis=s.semis)
    out = destination or target_path(t.path, s, info)
    out.parent.mkdir(parents=True, exist_ok=True)
    pcm_out = r.pcm
    if s.split:  # stage mix: the click alone on the left, the music in mono on the right
        pcm_out = np.stack([r.click, (r.pcm - r.click[:, None]).mean(axis=1)], axis=1)
    if s.normalize:
        pcm_out = rd.normalize(pcm_out, info.sr)
    if destination:
        if out.resolve() == t.path.resolve() or (out.exists() and out.samefile(t.path)):
            raise ValueError(_("Выберите имя, отличное от исходного файла."))
        with TemporaryDirectory(dir=out.parent, prefix=".count-in-selection-") as tmp:
            staged = Path(tmp) / out.name
            io.encode(pcm_out, info, staged, tags_from=t.path)
            staged.replace(out)
    else:
        io.encode(pcm_out, info, out, tags_from=t.path)
    return t, out, full, r.clipped


def multitrack_dir(song: Path, s: Settings, info: io.AudioInfo) -> Path:
    """The folder of a multitrack export, where the backing track would go."""
    out = target_path(song, s, info)
    tag = (f"_{round(s.speed * 100)}pct" if s.speed != 1.0 else "") + (f"_{s.semis:+g}st" if s.semis else "")
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
    export_zones(t, pcm, info.sr, st, s)
    clicks = clicks_for(t, s)
    clock = edit_clock(t, s, len(pcm) / info.sr) if t.edits else None
    audio_start = min(t.t0, rd.leading_silence(pcm, info.sr)) if s.pre_roll == "trim" else min(t.t0, 0.0)
    earliest = min([audio_start] + [c.time for c in clicks])
    markers = t.markers
    if clock:
        clicks = edited_clicks(t, s, clock)
        start_out = min([clock.output_at(audio_start)] + [c.time for c in clicks])
        start_source = clock.source_at(start_out)
        earliest = min(earliest, start_source)
        markers = [(clock.output_at(at), name) for at, name in markers if clock.contains(at)]
    g = daw.grid(song_grid_beats(t), t.bars, t.zones if s.fill else None, t.t0, 60.0 / t.bpm, s.meter,
                 earliest)
    folder = multitrack_dir(t.path, s, info)
    backing = stems.attenuate(pcm, st, s.levels) if st and s.levels else pcm
    name = t.path.stem
    if what.get("clickwav") and name.casefold() == "click":
        name += "_backing"
    tracks = daw.parts(pcm, st) if what["parts"] else {name: backing}
    job.report(_("Сборка дорожек"))
    with daw.staged_export(folder) as stage:
        for name, x in zip(tracks, daw.place(list(tracks.values()), info.sr, g, s.speed, s.semis, clock=clock)):
            job.report(_("{name}: запись", name=f"{name}.wav"))
            daw.write_wav(stage / f"{name}.wav", x, info.sr)
        if what.get("clickwav"):
            start, end, speed = (clock.output_at(g.start), clock.length, 1.0) if clock else (g.start, t.duration, s.speed)
            n = round(max(end - start, 0) / speed * info.sr) + info.sr
            daw.write_wav(stage / "click.wav", rd.click_track(clicks, info.sr, start, n, speed=speed)[:, None], info.sr)
        if what["click"]:
            daw.write_clicks(stage / "click.mid", g, clicks, s.meter, 1.0 if clock else s.speed, clock=clock)
        if what["tempo"]:
            daw.write_tempo(stage / "tempo.mid", g, s.meter, 1.0 if clock else s.speed, markers, clock=clock)
    return t, folder, full, 0


def warm_models():
    """Starts the separation process and loads its models there, so the first separation does not wait for
    them; on a Mac's first run it also builds the Core ML model. They stay loaded for the life of the app.
    PortAudio loads here too, so the first Play does not wait about 0.3 s for it."""
    threading.Thread(target=lambda: __import__("sounddevice"), name="portaudio", daemon=True).start()
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
    m = np.maximum(pcm.max(axis=1), -pcm.min(axis=1))  # no full-size abs copy
    starts = (np.arange(int(len(m) * 1000 // sr)) * sr // 1000).astype(np.int64)
    return np.maximum.reduceat(m, starts) if len(starts) else m[:0]


def keep(slider_value: int) -> float:
    """Linear gain a level slider leaves on its stem."""
    return 0.0 if slider_value == MUTE else 10 ** (slider_value / 20)


class Player:
    """Mixes the song and its stems block by block, so stem levels change while it plays. Stems come as
    (array, offset): stem sample 0 sits at output sample `offset`, so the cache needs no aligned copies."""

    streams = 0  # open streams of all players

    def __init__(self):
        self.stream = None
        self.src: tuple[np.ndarray, dict] = (np.zeros((0, 2), np.float32), {})
        self.pos = 0
        self.done = False
        self.gains: dict[str, float] = {}
        self.volume = 1.0  # speaker level only; the saved file never sees it
        self.loop: tuple[int, int] | None = None  # output samples [start, end) played round
        self.ticks: list[tuple[int, np.ndarray]] = []  # clicks mixed live: (output sample, mono hit)
        self.laps = 0  # times the loop came round
        self.low_latency = False
        self.frames_played = 0
        self.periodic: tuple[float, float, np.ndarray] | None = None  # first sample, period, hit in unwrapped time
        self.solo: set[str] = set()  # stems played alone; empty plays the whole mix

    def play(self, base: np.ndarray, stems: dict, sr: int, start: int = 0):
        """Starts playback, or swaps the buffer of a running stream so a rebuilt preview carries on gaplessly."""
        import sounddevice as sd

        if self.stream is not None and (self.sr, self.src[0].shape[1]) != (sr, base.shape[1]):
            self.stop()
        self.src, self.sr, self.pos, self.done, self.ticks = (base, stems), sr, max(0, start), False, []
        self.frames_played = 0
        if self.stream is not None:
            return
        self.dry = self.bad = 0

        # PortAudio reads the devices and the system default once; reload them, so a switched or plugged-in
        # output gets used. Reloading closes every stream, so only when none is open.
        if not Player.streams:
            sd._terminate()
            sd._initialize()
        log.info("audio output: %s", sd.query_devices(kind="output")["name"])

        # A large buffer keeps audio smooth while the GUI thread holds the interpreter.
        self.stream = sd.OutputStream(samplerate=sr, channels=base.shape[1], dtype="float32",
                                      blocksize=256 if self.low_latency else 2048,
                                      latency="low" if self.low_latency else "high", callback=self._fill)
        Player.streams += 1
        self.stream.start()

    def _segment(self, base: np.ndarray, stems: dict, a: int, b: int) -> np.ndarray:
        solo = self.solo & stems.keys()
        x = np.zeros_like(base[a:b]) if solo else base[a:b].copy()
        for k, (st, off) in stems.items():
            g = self.gains.get(k, 1.0)
            lo, hi = max(a, off), min(b, off + len(st))
            if lo >= hi:
                continue
            if solo:
                if k in solo:
                    x[lo - a : hi - a] += (g or 1.0) * st[lo - off : hi - off]
            elif g != 1.0:
                x[lo - a : hi - a] -= (1.0 - g) * st[lo - off : hi - off]
        for p, hit in self.ticks:
            lo, hi = max(a, p), min(b, p + len(hit))
            if lo < hi:
                x[lo - a : hi - a] += hit[lo - p : hi - p, None]
        return x

    def _fill(self, out, frames, _time, status):
        if status.output_underflow:
            self.dry += 1
        base, stems = self.src
        out[:] = 0
        n = 0
        while n < frames and not self.done:
            a, lp = self.pos, self.loop
            end = lp[1] if lp and a < lp[1] else len(base)
            b = min(a + frames - n, end, len(base))
            out[n : n + b - a] = self._segment(base, stems, a, b)
            n += b - a
            self.pos = b
            if lp and b == lp[1]:
                self.pos, self.ticks = lp[0], []
                self.laps += 1
            elif b >= len(base):
                self.done = True
        x = out[:n]
        if (pulse := self.periodic) is not None and n:
            first, period, hit = pulse
            start, end = self.frames_played, self.frames_played + n
            k = max(0, int((start - first - len(hit)) // period + 1))
            while (at := round(first + k * period)) < end:
                lo, hi = max(at, start), min(at + len(hit), end)
                if lo < hi:
                    x[lo - start:hi - start] += hit[lo - at:hi - at, None]
                k += 1
        self.frames_played += n
        x *= self.volume
        if not np.isfinite(x).all():  # one NaN makes Core Audio click and fall silent for the rest of the stream
            self.bad += 1
            np.nan_to_num(x, copy=False, nan=0.0, posinf=1.0, neginf=-1.0)
        np.clip(x, -1.0, 1.0, out=x)

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
            b.setFocusPolicy(Qt.TabFocus)
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
    elif kind == "loop":  # two arrows chasing each other round
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(color, 1.8, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(QRectF(3, 4, 12, 10), 20 * 16, 140 * 16)
        p.drawArc(QRectF(3, 4, 12, 10), 200 * 16, 140 * 16)
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawPolygon(QPolygonF([QPointF(1.2, 6.2), QPointF(5.4, 6.2), QPointF(3.3, 9.6)]))
        p.drawPolygon(QPolygonF([QPointF(16.8, 11.8), QPointF(12.6, 11.8), QPointF(14.7, 8.4)]))
    elif kind == "fit":
        lines((2.5, 9, 15.5, 9), (2.5, 9, 5.5, 6), (2.5, 9, 5.5, 12), (15.5, 9, 12.5, 6), (15.5, 9, 12.5, 12))
    p.end()
    return QIcon(pix)


class ClickLabel(QLabel):
    clicked = Signal()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()


def add_buttons(h: QHBoxLayout, cancel: QPushButton, ok: QPushButton) -> None:
    """Cancel and the main button in the platform's order: the main one first on Windows, last on a Mac."""
    for b in (ok, cancel) if sys.platform == "win32" else (cancel, ok):
        h.addWidget(b)


def keys(text: str) -> str:
    """Mac key symbols in a line of text, spelled the Windows way off a Mac."""
    if sys.platform == "darwin":
        return text
    for mac, win in (("⌘", "Ctrl+"), ("⌥", "Alt"), ("⇧", "Shift")):
        text = text.replace(mac, win)
    return text


def glyph_button(kind: str, tip: str, slot, name: str = "tbtn", color: QColor | None = None, size: int = 18) -> QPushButton:
    b = QPushButton(objectName=name, clicked=slot, toolTip=tip, accessibleName=tip)
    b.setIcon(glyph(kind, color or QApplication.palette().text().color(), size))
    if color is None:
        b.setProperty("glyph", kind)  # redrawn in the new text colour when the appearance changes
    b.setIconSize(QSize(size, size))
    b.setFocusPolicy(Qt.TabFocus)
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
    """The whole song with section labels above the draggable waveform overview."""

    markerClicked = Signal(int)
    MARKER_HEIGHT = 20

    def __init__(self, wave: "Wave", height: int):
        super().__init__()
        self.wave = wave
        wave.overview = self
        self.setFixedHeight(height)
        self.setCursor(Qt.PointingHandCursor)
        self.grab_dt = None
        self._cache = (None, None)
        self.marker_boxes: list[tuple[QRectF, int, str]] = []
        self.pressed_marker = None

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

    def _pixmap(self, h: int) -> QPixmap:
        w, env = self.width(), self.wave.env
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
        self.marker_boxes.clear()
        top = self.MARKER_HEIGHT if wv.markers else 0
        w, h = self.width(), self.height() - top
        if top:
            self._paint_markers(p)
        p.translate(0, top)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, w, h), min(8, h / 2), min(8, h / 2))
        p.setClipPath(clip)
        p.fillRect(0, 0, w, h, QColor(127, 127, 127, 22))
        for i, z in enumerate(wv.zones):
            a, b = zn.bounds(wv.bars, z)
            xa, xb = self.x_of(a), self.x_of(b)
            c = QColor(GREEN)
            c.setAlpha(230 if i == wv.current_zone else 130)
            p.fillRect(QRectF(xa, 0, max(xb - xa, 2), h), c)
        p.drawPixmap(0, 0, self._pixmap(h))
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

    def _paint_markers(self, p: QPainter):
        lo, hi = self._span()
        w = self.width()
        groups = []
        for at, items in groupby(enumerate(self.wave.markers), key=lambda entry: entry[1][0]):
            if lo <= at < hi:
                rows = list(items)
                groups.append(((at - lo) * w / (hi - lo), rows[0][0], " · ".join(name for _, (_, name) in rows)))
        palette = self.palette()
        p.fillRect(0, 0, w, self.MARKER_HEIGHT - 2, palette.alternateBase())
        metrics = p.fontMetrics()
        for j, (left, i, name) in enumerate(groups):
            right = groups[j + 1][0] if j + 1 < len(groups) else w
            box = QRectF(left, 0, right - left, self.MARKER_HEIGHT - 1)
            self.marker_boxes.append((box, i, name))
            p.fillRect(QRectF(left, 2, min(2, box.width()), box.height() - 5), palette.highlight())
            available = int(box.width()) - 9
            if available >= metrics.horizontalAdvance("…"):
                p.setPen(palette.text().color())
                p.drawText(box.adjusted(5, 0, -4, 0), Qt.AlignVCenter, metrics.elidedText(name, Qt.ElideRight, available))

    def _marker_at(self, point):
        return next(((box, i, name) for box, i, name in self.marker_boxes if box.contains(point)), None)

    def event(self, e):
        if e.type() == QEvent.ToolTip:
            hit = self._marker_at(QPointF(e.pos()))
            if hit is not None:
                box, _, name = hit
                QToolTip.showText(e.globalPos(), f"<p>{escape(name)}</p>", self, box.toAlignedRect())
                return True
        return super().event(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and (hit := self._marker_at(e.position())) is not None:
            self.pressed_marker = hit[1]
            return
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
        if self.pressed_marker is not None:
            marker, self.pressed_marker = self.pressed_marker, None
            hit = self._marker_at(e.position())
            if hit is not None and hit[1] == marker:
                self.markerClicked.emit(marker)
            return
        self.grab_dt = None
        if abs(e.position().x() - getattr(self, "press_x", -1e9)) < 4:  # a click, not a drag: the playhead goes there
            self.wave.set_playhead(self.t_of(e.position().x()), snap=False)
            self.wave.playheadClicked.emit()


class Wave(QWidget):
    """Waveform with beat lines, count-in clicks, pauses, a time ruler, a draggable first-downbeat marker and
    the play position.

    Each pause carries a header with its number, meter and tempo; clicking it edits the pause, and its cross
    removes it. Right-click on a pause removes it, right-click elsewhere adds a one-bar
    pause; a pause edge has a handle and snaps to a beat. A plain click sets the play position.
    """

    t0Changed = Signal(float)
    zonesEdited = Signal(object)
    zoneClicked = Signal(int)
    spanSelected = Signal(bool)  # whether to open the actions menu after selection
    phaseDragged = Signal(float, float)
    markerMenu = Signal(int)  # a right-click on section marker i
    playFrom = Signal(float)
    playheadClicked = Signal()  # the user put the playhead somewhere by a click  # double-click: play from here
    navChanged = Signal()  # the play position or the current pause changed
    RULER = 20

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(220)
        self.setMouseTracking(True)
        self.setToolTip(_("Option / Alt: потяните сетку, край или середину паузы для свободного сдвига."))
        self.env = None  # peak per millisecond
        self.duration = 0.0
        self.v0, self.v1 = 0.0, 10.0
        self.beats, self.downs, self.clicks = [], [], []
        self.t0 = None
        self.bars, self.zones, self.fill = [], [], True
        self.sel: tuple[float, float] | None = None
        self.selection_mode = False
        self.edits: tuple[timeline.Edit, ...] = ()
        self.loop: tuple[float, float, bool] | None = None  # loop start, end and whether it plays round
        self.markers: list[tuple[float, str]] = []  # (song time, name) of the sections
        self.marker_boxes: list[tuple[QRectF, int]] = []  # hit boxes of the marker labels on the ruler
        self.peak, self.ref_db, self.threshold_db = 1.0, None, None  # for the pause threshold band
        self.threshold_full = False  # band over the whole track while the slider moves
        self.labels: list[tuple[QRectF, int]] = []
        self.overlay: QWidget | None = None  # progress card shown over the dimmed waveform during a job
        self.cursor_t = None
        self.drag = None
        self.selection_pointer = None
        self.selection_scroll = QTimer(self, interval=16, timeout=self._scroll_selection)
        self._cache = (None, None)
        self._grid: tuple = (None, None)  # beat lines and clicks layer, see _grid_pixmap

        self.overview: QWidget | None = None  # mini-map of the whole song, repainted with the waveform
        self.busy: QWidget | None = None  # centred progress card
        self.pill: QWidget | None = None  # the same progress folded into a capsule at the bottom
        self.tips: QWidget | None = None  # tips card at the bottom left while the app is busy
        self.tips_on = False
        self.tip_lines: list[QLabel] = []  # the centred cards' own tip lines, used when the card has no room
        self.play_t: float | None = None  # where playback starts; None means from the count-in
        self.current_zone: int | None = None
        self.hover_edge = None  # (pause index, side) under the pointer
        self.hover_close: int | None = None
        self.closes: list[tuple[QRectF, int]] = []  # remove buttons in pause headers
        self.press_x = 0.0
        self.lane = None

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
        if self.lane is not None:
            self.lane.update()
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

    def _grid_pixmap(self, accent: QColor) -> QPixmap:
        """Beat lines and clicks, cached like the waveform: playback repaints only draw this pixmap and the
        playhead, which keeps the interpreter free for the audio callback."""
        dpr = self.devicePixelRatioF()
        w, wh = self.width(), self.height() - self.RULER
        key = (w, wh, dpr, self.v0, self.v1, id(self.beats), id(self.downs), id(self.clicks), len(self.clicks),
               self.t0, accent.rgb())
        if self._grid[0] == key:
            return self._grid[1]
        pix = QPixmap(max(int(w * dpr), 1), max(int(wh * dpr), 1))
        pix.setDevicePixelRatio(dpr)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)

        # Only what is on screen, and no beat lines closer than 4 px: a whole song would be a grey wall
        px_per_s = w / max(self.v1 - self.v0, 1e-6)
        dense = len(self.beats) > 1 and (self.duration / len(self.beats)) * px_per_s < 4
        p.setPen(QPen(QColor(127, 127, 127, 70), 1))
        p.drawLines([QLineF(self.x_of(t), 0, self.x_of(t), wh) for t in ([] if dense else self._visible(self.beats))])
        p.setPen(QPen(QColor(127, 127, 127, 160), 1, Qt.DashLine if not dense else Qt.SolidLine))
        p.drawLines([QLineF(self.x_of(t), 0, self.x_of(t), wh) for t in self._visible(self.downs)])

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
            p.drawRoundedRect(QRectF(x - 2, 28, 4, hgt), 2, 2)
        p.end()
        self._grid = (key, pix)
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
            a, b = zn.bounds(self.bars, z)
            xa, xb = self.x_of(a), self.x_of(b)
            fill.setAlpha((78 if i == self.current_zone else 34) if self.fill else 18)
            p.fillRect(QRectF(xa, 0, xb - xa, wh), fill)
            p.setPen(QPen(edge, 3 if i == self.current_zone else 2))
            p.drawLine(QPointF(xa, 0), QPointF(xa, wh))
            p.drawLine(QPointF(xb, 0), QPointF(xb, wh))

        p.drawPixmap(0, 0, self._wave_pixmap())
        p.drawPixmap(0, 0, self._grid_pixmap(accent))

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
        if self.loop:
            la, lb, on = self.loop
            xa, xb = self.x_of(la), self.x_of(lb)
            band = QColor(accent)
            band.setAlpha(40 if on else 16)
            p.fillRect(QRectF(xa, 0, xb - xa, wh), band)
            band.setAlpha(230 if on else 110)
            p.setPen(Qt.NoPen)
            p.setBrush(band)
            p.drawRect(QRectF(xa, wh - 4, xb - xa, 4))
        for edit in self.edits:
            xa, xb = self.x_of(edit.start), self.x_of(edit.end)
            if edit.speed is None:
                p.fillRect(QRectF(xa, 0, xb - xa, wh), QColor(80, 80, 80, 155))
                p.setPen(QPen(QColor(210, 110, 95, 220), 2, Qt.DashLine))
                p.drawLine(QPointF(xa, wh / 2), QPointF(xb, wh / 2))
            else:
                p.fillRect(QRectF(xa, 0, xb - xa, wh), QColor(80, 145, 195, 55))
                p.setPen(self.palette().text().color())
                p.drawText(QRectF(xa + 4, wh - 26, max(xb - xa - 8, 0), 20), Qt.AlignLeft,
                           f"{round(edit.speed * 100)}%")
        if self.sel:
            a, b = self.sel
            xa, xb = self.x_of(a), self.x_of(b)
            sel = QColor(accent)
            sel.setAlpha(72)
            p.fillRect(QRectF(xa, 0, xb - xa, wh), sel)
            p.setPen(QPen(accent, 2))
            for x in (xa, xb):
                p.drawLine(QPointF(x, 0), QPointF(x, wh))

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
        p.setPen(QPen(QColor(127, 127, 127, 140), 1))
        if self.bars:
            # Bar numbers, as tabs and scores count them; every k-th bar when they would crowd
            px = (self.bars[min(1, len(self.bars) - 1)].end - self.bars[0].start) * w / max(self.v1 - self.v0, 1e-6)
            k = next((k for k in (1, 2, 4, 8, 16, 32, 64) if px * k >= 36), 128)
            for i, b in enumerate(self.bars):
                if i % k or not self.v0 <= b.start <= self.v1:
                    continue
                x = self.x_of(b.start)
                p.drawLine(QPointF(x, wh), QPointF(x, wh + 4))
                p.drawText(QRectF(x + 3, wh + 2, 60, self.RULER - 2), Qt.AlignLeft | Qt.AlignVCenter, str(i + 1))
        else:
            step = self._ruler_step()
            t = np.ceil(self.v0 / step) * step
            while t <= self.v1:
                x = self.x_of(t)
                p.drawLine(QPointF(x, wh), QPointF(x, wh + 4))
                p.drawText(QRectF(x + 3, wh + 2, 80, self.RULER - 2), Qt.AlignLeft | Qt.AlignVCenter,
                           f"{t:.2f}".rstrip("0").rstrip(".") + _(" с"))
                t += step
        self._paint_markers(p, w, wh)
        self._paint_playhead(p, wh)
        if self.overview is not None:
            self.overview.update()

    def _paint_markers(self, p: QPainter, w: float, wh: float):
        """Section markers: a faint line over the waveform and the name on the ruler; right-click edits."""
        self.marker_boxes = []
        accent = accent_color(QApplication.instance())
        fm = p.fontMetrics()
        for i, (t, name) in enumerate(self.markers):
            x = self.x_of(t)
            if not -200 <= x <= w:
                continue
            line = QColor(accent)
            line.setAlpha(110)
            p.setPen(QPen(line, 1, Qt.DashLine))
            p.drawLine(QPointF(x, 22), QPointF(x, wh))
            text = f"{i + 1} {name}" if i < 9 else name
            box = QRectF(x, wh + 2, fm.horizontalAdvance(text) + 12, self.RULER - 4)
            p.setPen(Qt.NoPen)
            p.setBrush(accent)
            p.drawRoundedRect(box, 6, 6)
            p.setPen(on_accent(QApplication.instance()))
            p.drawText(box, Qt.AlignCenter, text)
            self.marker_boxes.append((box, i))

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
            left, right = max(xa, 0.0), min(xb, w)
            if left >= right:
                continue
            cur = i == self.current_zone
            head = QRectF(xa, 0, xb - xa, 22)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(36, 138, 61, 245 if cur else 150))
            p.drawRect(head)
            meter = f"{n}/4" if z.beats else self.bars[z.first].meter or f"{n}/4"
            text = _("Пауза {n}", n=i + 1) + f" · {meter} · {bpm:.0f} BPM" + (" ✎" if z.kept else "")
            if z.count_in:
                text += " · " + _("Отсчёт")
            close_width = min(22.0, right - left)
            close = QRectF(right - close_width, 0, close_width, head.height())
            room = QRectF(left + 9, 0, max(0.0, close.left() - left - 12), head.height())
            p.setPen(white)
            p.drawText(room, Qt.AlignVCenter | Qt.AlignLeft,
                       p.fontMetrics().elidedText(text, Qt.ElideRight, int(room.width())))
            self.labels.append((QRectF(left, 0, close.left() - left, head.height()), i))
            self.closes.append((close, i))
            center, arm = close.center(), min(3.5, close_width / 4)
            p.setPen(QPen(QColor(255, 255, 255, 255 if i == self.hover_close else 190),
                          1.5, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(center + QPointF(-arm, -arm), center + QPointF(arm, arm))
            p.drawLine(center + QPointF(arm, -arm), center + QPointF(-arm, arm))
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
        """Index and side of the closest pause edge within 9 pixels."""
        best = min(((abs(x - px), (x <= px) != (side == "end"), i, side)
                    for i, z in enumerate(self.zones)
                    for side, at in zip(("start", "end"), zn.bounds(self.bars, z))
                    for px in (self.x_of(at),)), default=None)
        return best[2:] if best and best[0] < 9 else None

    def _snap_beat(self, at: float, lo: float, hi: float, fallback: float) -> float:
        return min((b for b in (*self.beats, 0.0, self.duration) if lo <= b <= hi),
                   key=lambda b: abs(b - at), default=fallback)

    def _selection_range(self, x: float, free: bool) -> tuple[float, float]:
        _, _, fixed, _, origin, _ = self.drag
        a = origin
        b = float(np.clip(self.t_of(max(0., min(x, self.width()))), 0., self.duration))
        if self.beats and not free:
            a, b = (self._snap_beat(t, 0.0, self.duration, t) for t in (a, b))
        return tuple(sorted((a if fixed is None else fixed, b)))

    def _start_selection(self, x: float, popup: bool):
        fixed = None
        if self.sel:
            edge = min(self.sel, key=lambda at: abs(self.x_of(at) - x))
            if abs(self.x_of(edge) - x) <= 7:
                fixed = self.sel[1] if edge == self.sel[0] else self.sel[0]
        origin = float(np.clip(self.t_of(x), 0., self.duration))
        self.drag = ("select", x, fixed, self.sel, origin, popup)
        self.selection_pointer = None

    def _scroll_selection(self):
        if not self.drag or self.drag[0] != "select" or self.selection_pointer is None:
            self.selection_scroll.stop()
            return
        x, free = self.selection_pointer
        edge = max(1., min(48., self.width() / 6))
        direction = min(0., (x - edge) / edge) + max(0., (x - self.width() + edge) / edge)
        if not direction:
            self.selection_scroll.stop()
            return
        step = .045 * (self.v1 - self.v0) * max(-2., min(direction, 2.))
        previous = self.v0
        self.show_range(self.v0 + step, self.v1 + step)
        if self.v0 == previous:
            self.selection_scroll.stop()
        self.sel = self._selection_range(x, free)
        self.update()

    def _resize_pause(self, x: float, free: bool) -> bool:
        _, z, side, x0, original = self.drag
        if not any(z is other for other in self.zones):
            return False
        a, b = zn.bounds(self.bars, original)
        neighbors = [zn.bounds(self.bars, other) for other in self.zones if other is not z]
        start = side == "start"
        if start:
            lo, hi = max((end for _, end in neighbors if end <= a), default=0.0), b - .001
        else:
            lo, hi = a + .001, min((first for first, _ in neighbors if first >= b), default=self.duration)
        old = a if start else b
        target = float(np.clip(old + self.t_of(x) - self.t_of(x0), lo, hi))
        if not free and self.beats:
            target = self._snap_beat(target, lo, hi, old)
        updated = original
        if abs(x - x0) > 2 and abs(target - old) > 1e-8:
            updated = zn.mapped(original, self.bars, target if start else a, b if start else target)
            updated.manual = True
            updated.free = original.free or free
        z.first, z.last, z.start, z.end = updated.first, updated.last, updated.start, updated.end
        z.manual, z.free = updated.manual, updated.free
        self.update()
        return z != original

    def _zone_at(self, bar: int) -> int | None:
        return next((i for i, z in enumerate(self.zones) if z.first <= bar <= z.last), None)

    def _zone_at_time(self, at: float) -> int | None:
        return next((i for i, z in enumerate(self.zones)
                     if (bounds := zn.bounds(self.bars, z))[0] <= at < bounds[1]), None)

    def _set_zones(self, zones: list):
        self.hover_close = None
        self.zones = sorted(zones, key=lambda z: z.first)
        self.zonesEdited.emit(self.zones)
        self.update()

    def _remove_pause_at(self, pos) -> bool:
        close = next((i for box, i in self.closes if box.contains(pos)), None)
        if close is None:
            return False
        self.current_zone = None
        self._set_zones(self.zones[:close] + self.zones[close + 1:])
        self.navChanged.emit()
        return True

    def add_zone(self, bar: int, length: int = 1):
        """A pause over bars bar..bar+length-1, unless one already covers it. Next to a pause that follows the
        song, that pause grows instead, so the hi-hat runs on without a count-in in between."""
        last = min(bar + length, len(self.bars)) - 1
        if not self.bars or any(self._zone_at(i) is not None for i in range(bar, last + 1)):
            return
        for z in self.zones:
            if z.bpm is None and z.beats is None and z.last == bar - 1:
                z.last, z.end, z.manual = last, self.bars[last].end, True
                return self._set_zones(self.zones)
            if z.bpm is None and z.beats is None and z.first == last + 1:
                z.first, z.start, z.manual = bar, self.bars[bar].start, True
                return self._set_zones(self.zones)
        self._set_zones(self.zones + [zn.Zone(bar, last, manual=True)])

    def mousePressEvent(self, e):
        if isinstance(QApplication.focusWidget(), QAbstractSpinBox):
            QApplication.focusWidget().clearFocus()  # a click on the waveform ends typing in a number field
        x, pos = e.position().x(), e.position()
        if e.button() == Qt.LeftButton and self.duration and (
                self.selection_mode or e.modifiers() & Qt.ShiftModifier or pos.y() > self.height() - self.RULER):
            self._start_selection(x, popup=not self.selection_mode)
            self.update()
            return
        if e.button() == Qt.LeftButton and e.modifiers() & Qt.AltModifier and self.bars:
            at = self.t_of(x)
            hit = self._near_edge(x)
            if hit:
                z = self.zones[hit[0]]
                self.current_zone = hit[0]
                self.drag = ("edge", z, hit[1], x, replace(z))
                return
            i = self._zone_at_time(at)
            if i is not None:
                z = self.zones[i]
                self.current_zone = i
                self.drag = ("free-zone", z, at, *zn.bounds(self.bars, z))
            elif self._near_marker(x) and pos.y() < 22:
                self.drag = "marker"
            else:
                self.drag = ("phase", at, x)
            return
        if e.button() == Qt.RightButton:
            if self.selection_mode and self.sel and self.sel[0] <= self.t_of(x) <= self.sel[1]:
                self.spanSelected.emit(True)
                return
            m = next((i for box, i in self.marker_boxes if box.contains(pos)), None)
            if m is not None:
                self.markerMenu.emit(m)
                return
            at = self.t_of(x)
            z = self._zone_at_time(at)
            if z is not None:
                self._set_zones(self.zones[:z] + self.zones[z + 1 :])
            elif (bar := self._bar_at(at)) is not None:
                self.add_zone(bar)
            return
        if e.button() == Qt.LeftButton and self._remove_pause_at(pos):
            return
        label = next((i for box, i in self.labels if box.contains(pos)), None)
        self.press_x = x
        hit = self._near_edge(x) if self.bars else None
        if label is not None and e.button() == Qt.LeftButton:
            z = self.zones[label]
            self.drag = ("zone-move", z, x, replace(z))
            return
        if e.button() == Qt.LeftButton and hit and (pos.y() > 22 or not self._near_marker(x)):
            z = self.zones[hit[0]]
            self.drag = ("edge", z, hit[1], x, replace(z))
        elif e.button() == Qt.LeftButton and self._near_marker(x) and pos.y() < 22:
            self.drag = "marker"
        elif e.button() == Qt.LeftButton and self.duration:
            self._start_selection(x, popup=False)
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
            self.update()
        elif self.drag and self.drag[0] == "free-zone":
            _, z, at, a, b = self.drag
            if z not in self.zones:
                self.drag = None
                return
            dt = self.t_of(x) - at
            lo = max((zn.bounds(self.bars, o)[1] for o in self.zones if o is not z and zn.bounds(self.bars, o)[1] <= a),
                     default=0.0)
            hi = min((zn.bounds(self.bars, o)[0] for o in self.zones if o is not z and zn.bounds(self.bars, o)[0] >= b),
                     default=self.duration)
            dt = float(np.clip(dt, lo - a, hi - b))
            a, b = a + dt, b + dt
            moved = zn.mapped(z, self.bars, a, b)
            z.first, z.last, z.start, z.end = moved.first, moved.last, a, b
            z.free = z.manual = True
            self.update()
        elif self.drag and self.drag[0] == "phase":
            self.setCursor(Qt.SizeHorCursor)
        elif self.drag and self.drag[0] == "select":
            if self.selection_pointer is not None or abs(x - self.drag[1]) > 4:
                free = bool(e.modifiers() & Qt.AltModifier)
                self.selection_pointer = (x, free)
                self.sel = self._selection_range(x, free)
                if not self.selection_scroll.isActive():
                    self.selection_scroll.start()
                self.update()
        elif self.drag and self.drag[0] == "zone-move":
            _, z, x0, original = self.drag
            if not any(z is other for other in self.zones):
                return
            first, last = zn.bounds(self.bars, original)
            bars = original.last - original.first
            neighbors = [zn.bounds(self.bars, other) for other in self.zones if other is not z]
            lo = max((b for _, b in neighbors if b <= first), default=0.0)
            hi = min((a for a, _ in neighbors if a >= last), default=self.duration)
            desired = first + self.t_of(x) - self.t_of(x0)
            bounds = ((bar.start, bar.start + last - first if z.free else self.bars[i + bars].end)
                      for i, bar in enumerate(self.bars[:len(self.bars) - bars]))
            picked = min(((a, b) for a, b in bounds if lo <= a < b <= hi),
                         key=lambda span: abs(span[0] - desired), default=None)
            if picked and picked != (first, last) and abs(x - x0) > 4:
                moved = zn.mapped(z, self.bars, *picked)
                z.first, z.last, z.start, z.end, z.manual = moved.first, moved.last, *picked, True
            else:
                z.first, z.last, z.start, z.end, z.manual = (original.first, original.last, original.start,
                                                            original.end, original.manual)
            self.update()
        elif self.drag and self.drag[0] == "edge":
            self._resize_pause(x, bool(e.modifiers() & Qt.AltModifier))
        elif self.drag:
            _kind, x0, a, b = self.drag
            dt = (x - x0) / max(self.width(), 1) * (b - a)
            self.show_range(a - dt, b - dt)
        else:
            edge = self.bars and self._near_edge(x)
            close = next((i for box, i in self.closes if box.contains(e.position())), None)
            if (edge or None) != self.hover_edge or close != self.hover_close:
                self.hover_edge = edge or None
                self.hover_close = close
                self.update()
            on_label = any(box.contains(e.position()) for box, _i in self.labels)
            on_downbeat = e.position().y() < 22 and self._near_marker(x)
            self.setCursor(Qt.PointingHandCursor if close is not None else
                           Qt.CrossCursor if self.selection_mode else
                           Qt.PointingHandCursor if on_label else
                           Qt.SizeHorCursor if on_downbeat or edge else Qt.CrossCursor)

    def leaveEvent(self, e):
        if self.hover_close is not None:
            self.hover_close = None
            self.update()
        super().leaveEvent(e)

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton and not self.selection_mode:
            self.playFrom.emit(self.t_of(e.position().x()))

    def clear_selection(self) -> bool:
        if self.drag and self.drag[0] == "select":
            self.drag = None
            self.selection_scroll.stop()
            self.selection_pointer = None
        if self.sel is None:
            return False
        self.sel = None
        self.spanSelected.emit(False)
        return True

    def _clear_selection_on_click(self, e):
        if (self.sel and e.button() == Qt.LeftButton and not e.modifiers()
                and e.position().y() < self.height() - self.RULER):
            a, b = self.sel
            if not a <= self.t_of(e.position().x()) <= b:
                self.clear_selection()

    def mouseReleaseEvent(self, e):
        if self.drag == "marker":
            self.t0Changed.emit(self.t0)
        elif self.drag and self.drag[0] == "phase":
            at, x0 = self.drag[1:]
            if abs(e.position().x() - x0) > 2:
                self.phaseDragged.emit(at, self.t_of(e.position().x()) - at)
            else:
                self.set_playhead(at, snap=False)
                self.playheadClicked.emit()
        elif self.drag and self.drag[0] == "select":
            _, x0, _, previous, _, popup = self.drag
            x = e.position().x()
            a, b = self._selection_range(x, bool(e.modifiers() & Qt.AltModifier))
            moved = self.selection_pointer is not None or abs(x - x0) > 4
            self.selection_scroll.stop()
            self.selection_pointer = None
            changed = moved and b - a >= .001
            self.sel = (a, b) if changed else previous
            self.drag = None
            self.update()
            if changed:
                self.spanSelected.emit(popup)
            elif not moved and not e.modifiers() & Qt.ShiftModifier:
                if not self._remove_pause_at(e.position()):
                    self._clear_selection_on_click(e)
                    if not self.selection_mode:
                        self.set_playhead(self.t_of(x), snap=not e.modifiers() & Qt.AltModifier)
                        self.playheadClicked.emit()
        elif self.drag and self.drag[0] == "zone-move":
            _, z, x0, original = self.drag
            if any(z is other for other in self.zones):
                if abs(e.position().x() - x0) > 4:
                    if z != original:
                        self.zonesEdited.emit(self.zones)
                else:
                    self.current_zone = next(i for i, other in enumerate(self.zones) if other is z)
                    self.zoneClicked.emit(self.current_zone)
        elif self.drag and self.drag[0] == "edge":
            if self._resize_pause(e.position().x(), bool(e.modifiers() & Qt.AltModifier)):
                self.zonesEdited.emit(self.zones)
        elif self.drag and self.drag[0] == "free-zone":
            self.zonesEdited.emit(self.zones)
        elif self.drag and self.drag[0] == "pan" and abs(e.position().x() - self.press_x) < 4:
            self._clear_selection_on_click(e)
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
        self.current_zone = self._zone_at_time(self.play_t)
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
        super().__init__(objectName="small", checkable=True, toolTip=tip, accessibleName=tip, focusPolicy=Qt.TabFocus)
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
    w = QWidget(accessibleName=text)
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
    name = label if isinstance(label, str) else label.toolTip() or label.accessibleName()
    for w in widgets:
        if name and not w.accessibleName() and not isinstance(w, QLabel):
            w.setAccessibleName(name)  # screen readers name the control after its row
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
UPDATE_CHANNEL_KEY = "updates/channel"
CLOUD_OVERLAP = "cloud/overlap"
FILL_BUILD_KEY = "fill/buildup"
PREROLL_KEY = "click/preroll"
GEOMETRY_KEY = "window/geometry"
RECENT_KEY = "recent/files"
BOOST = 6  # dB a part slider goes above the mix
TRAINER_LAPS = 2  # loop passes before the speed trainer adds 5%
OUTPUT_VOLUME_KEY = "playback/volume"
DEFAULTS = "defaults/"  # prefix for the settings saved with "Save as default"
THRESHOLD_KEY = "fill/threshold"
LEGACY_VOLUME_AUTO_KEY = "clicks/auto_volume"
COUNT_VOLUME_KEY = "count_in_volume"
OUT_LEVELS = {"output/artist": "Исполнитель", "output/year": "Год", "output/album": "Альбом"}


def output_volume() -> float:
    """Speaker level for previews, 0..1, remembered between runs."""
    return int(QSettings().value(OUTPUT_VOLUME_KEY, 80)) / 100


def pref_bool(key: str, default: bool) -> bool:
    return QSettings().value(key, default) in (True, "true", "1", 1)


def click_voice_picker(default: str) -> QComboBox:
    combo = QComboBox()
    for key, title in rd.VOICES.items():
        combo.addItem(_(title), key)
    combo.setCurrentIndex(combo.findData(default))
    return combo


def count_volume(stored, legacy=0) -> int:
    """Ignore the legacy level when the old Auto mode could have set it."""
    if stored is None:
        stored = 0 if pref_bool(LEGACY_VOLUME_AUTO_KEY, True) else legacy
    try:
        return int(stored)
    except (TypeError, ValueError):
        return 0


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
        text = QLabel(_("Count-In находит доли и разделяет песню на дорожки нейросетями. Их веса ({mb:.0f} МБ) скачиваются один раз и потом не меняются, поэтому обновления программы остаются маленькими. Если модели уже есть на флешке или в другой папке, укажите её. Папка assets рядом с программой подхватывается сама.", mb=mb), wordWrap=True, objectName="progWhy")
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

    def __init__(self, parent, pcm: np.ndarray, sr: int, start: float, end: float, bpm: float, speed: float,
                 prepared: np.ndarray | None = None):
        super().__init__(parent)
        self.setWindowTitle(_("Темп на слух"))
        self.setMinimumWidth(460)
        source = pcm[round(start * sr):round(end * sr)]
        self.pcm = prepared if prepared is not None else rd.stretch(source, sr, speed) if speed != 1 else source
        self.sr, self.bpm, self.speed = sr, bpm, speed
        self.taps: list[float] = []  # unwrapped elapsed playback time, including completed laps
        self.valid = False
        self.clicking = False
        self.player = Player()
        self.player.low_latency = True
        self.player.volume = output_volume()
        self.hit = rd.load_samples(sr)[1]
        self.wall0 = time.monotonic()

        v = QVBoxLayout(self)
        v.setSpacing(14)
        self.value = QLabel(f"{bpm:.1f}", alignment=Qt.AlignCenter)
        f = self.value.font()
        f.setPointSizeF(f.pointSizeF() * 4.5)
        f.setBold(True)
        self.value.setFont(f)
        self.half_b = QPushButton("÷2", objectName="tapPad", focusPolicy=Qt.TabFocus, autoDefault=False,
                                  toolTip=_("Уменьшить темп вдвое"), clicked=lambda: self._scale_bpm(.5))
        self.double_b = QPushButton("×2", objectName="tapPad", focusPolicy=Qt.TabFocus, autoDefault=False,
                                    toolTip=_("Удвоить темп"), clicked=lambda: self._scale_bpm(2))
        bpm_row = QHBoxLayout()
        bpm_row.addStretch()
        bpm_row.addWidget(self.half_b)
        bpm_row.addWidget(self.value)
        bpm_row.addWidget(self.double_b)
        bpm_row.addStretch()
        v.addLayout(bpm_row)
        v.addWidget(QLabel(_("Исходный BPM · {a}–{b} · {speed}%", a=fmt_short(start), b=fmt_short(end),
                             speed=round(100 * speed)), objectName="field", alignment=Qt.AlignCenter))
        self.hint = QLabel(objectName="subtitle", alignment=Qt.AlignCenter, wordWrap=True)
        v.addWidget(self.hint)
        h = QHBoxLayout()
        h.addStretch()
        pair = [QPushButton(text, objectName=name, clicked=slot, focusPolicy=Qt.TabFocus, autoDefault=False)
                for text, slot, name in ((_("Отмена"), self.reject, "tool"), (_("Применить"), self.accept, "primary"))]
        for button in (self.half_b, self.double_b, *pair):
            button.installEventFilter(self)
        add_buttons(h, *pair)
        self.apply_b = pair[1]
        self._set_measurement(None)
        v.addLayout(h)
        self._show_hint()

        self.timer = QTimer(self, interval=100, timeout=self._check)
        self.timer.start()
        self.player.loop = (0, len(self.pcm))
        try:
            self.player.play(self.pcm, {}, self.sr)
        except Exception as e:  # no output device
            self.hint.setText(_("Не удалось воспроизвести: {e}", e=e))
        self.wall0 = time.monotonic()

    def _now(self) -> float:
        """Estimate when the listener heard the tap using a monotonic clock and the output latency."""
        lag = self.player.stream.latency if self.player.stream else 0.0
        return max(0.0, time.monotonic() - self.wall0 - lag)

    def _show_hint(self):
        self.hint.setText(_("Стучите пробелом в такт, клик с вашим темпом пойдёт сам") if not self.clicking
                          else _("Играет клик с настуканным темпом. Пробел, чтобы настучать заново"))

    def _set_measurement(self, bpm: float | None):
        self.valid = bpm is not None
        if bpm is not None:
            self.bpm = bpm
            self.value.setText(f"{bpm:.1f}")
        self.apply_b.setEnabled(self.valid)
        self.half_b.setEnabled(self.valid and self.bpm >= 60)
        self.double_b.setEnabled(self.valid and self.bpm <= 160)

    def _scale_bpm(self, factor: float):
        self._set_measurement(self.bpm * factor)
        period = 60 * self.sr / (self.bpm * self.speed)
        anchor = self.taps[-1] * self.sr
        cursor = max(self.player.frames_played, self._now() * self.sr)
        first = anchor + max(0, math.ceil((cursor - anchor) / period)) * period
        self.player.periodic = (first, period, self.hit)
        self.clicking = True
        self._show_hint()

    def keyPressEvent(self, e):
        if e.key() != Qt.Key_Space:
            return super().keyPressEvent(e)
        e.accept()
        if e.isAutoRepeat():
            return
        now = self._now()
        if self.clicking or (self.taps and now - self.taps[-1] > max(2.5, 2.5 * 60 / (self.bpm * self.speed))):
            self.taps = []
            self.clicking = False
        self.player.periodic = None
        self.taps = (self.taps + [now])[-12:]
        self._set_measurement(tempo.tapped_bpm(self.taps, self.speed))
        if self.valid:
            period = 60 * self.sr / (self.bpm * self.speed)
            self.player.periodic = (now * self.sr + 2 * period, period, self.hit)
        self._show_hint()

    def eventFilter(self, obj, e):
        if e.type() in (QEvent.ShortcutOverride, QEvent.KeyPress, QEvent.KeyRelease) and e.key() == Qt.Key_Space:
            if e.type() == QEvent.KeyPress:
                self.keyPressEvent(e)
            else:
                e.accept()
            return True
        return super().eventFilter(obj, e)

    def _check(self):
        """Two beats without a tap: the tapping is over, the click takes over at the tapped tempo."""
        if self.player.done:
            self.timer.stop()
        elif self.valid and not self.clicking and self._now() - self.taps[-1] > 2 * 60.0 / (self.bpm * self.speed):
            self.clicking = True
            self._show_hint()

    def done(self, r):
        self.timer.stop()
        self.player.stop()
        super().done(r)


class ZoneDialog(QDialog):
    """Tempo and beats per bar of one pause; reset goes back to the detected bars."""

    def __init__(self, parent, start: float, end: float, bpm: float, beats: int, count_in: bool = False):
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
        self.count_in = QCheckBox(_("Отсчёт перед вступлением"), checked=count_in)
        self.count_in.setToolTip(_("Конец паузы займёт отсчёт в темпе и размере следующего фрагмента."))
        g.addWidget(self.count_in, 2, 0, 1, 2)
        h = QHBoxLayout()
        for text, slot in ((_("Убрать паузу"), self._remove), (_("Как в песне"), self._reset)):
            b = QPushButton(text, objectName="tool", clicked=slot)
            if slot == self._reset:
                b.setToolTip(_("Убрать ручной темп и размер, чтобы пауза снова шла по долям песни"))
            h.addWidget(b)
        h.addStretch()
        ok = QPushButton(_("Готово"), objectName="primary", clicked=self.accept, default=True)
        add_buttons(h, QPushButton(_("Отмена"), objectName="tool", clicked=self.reject), ok)
        g.addLayout(h, 3, 0, 1, 2)

    def _remove(self):
        self.removed = True
        self.reject()

    def _reset(self):
        self.reset = True
        self.reject()


EXPORT_MODE, EXPORT_PARTS, EXPORT_CLICK, EXPORT_TEMPO, EXPORT_FORMAT = (
    "export/mode", "export/parts", "export/click", "export/tempo", "export/format")
EXPORT_SPLIT, EXPORT_CLICKWAV, EXPORT_NORM = "export/split", "export/clickwav", "export/normalize"


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
        self.split = QCheckBox(_("Микс для сцены с кликом слева и музыкой справа"), checked=pref_bool(EXPORT_SPLIT, False))
        self.split.setToolTip(_("Клик уходит в наушники барабанщика или в ваши, музыка в зал. Музыка сводится в моно."))
        self.split.toggled.connect(self._update)
        fmt.addWidget(self.split)
        self.norm = QCheckBox(_("Ровная громкость −14 LUFS, как в стримингах"), checked=pref_bool(EXPORT_NORM, False))
        self.norm.setToolTip(_("Бэктреки из разных альбомов звучат одинаково громко. Пики не поднимаются выше −1 дБ, "
                               "поэтому динамичная песня может остаться чуть тише."))
        fmt.addWidget(self.norm)

        self.boxes = {
            EXPORT_PARTS: QCheckBox(_("Дорожки отдельными файлами (WAV)")),
            EXPORT_CLICK: QCheckBox(_("Сетка кликов (MIDI)")),
            EXPORT_TEMPO: QCheckBox(_("Темповая карта (MIDI)")),
            EXPORT_CLICKWAV: QCheckBox(_("Клик отдельным WAV")),
        }
        tips = {
            EXPORT_PARTS: _("Гитара, вокал, бас, барабаны, пианино и всё остальное, на полной громкости. Без галочки пишется один WAV с выбранными уровнями дорожек"),
            EXPORT_CLICK: _("Отсчёт и хай-хэт в паузах нотами на 10-м канале, General MIDI"),
            EXPORT_TEMPO: _("Темп и размер каждого такта, чтобы такты DAW совпали с песней"),
            EXPORT_CLICKWAV: _("Отсчёт и клик тем звуком, что звучит в прослушивании, с первого такта"),
        }
        files = QVBoxLayout()
        files.setSpacing(8)
        for key, box in self.boxes.items():
            box.setChecked(pref_bool(key, key != EXPORT_CLICKWAV))
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
        add_buttons(h, cancel, self.ok)
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
        st.setValue(EXPORT_SPLIT, self.split.isChecked())
        st.setValue(EXPORT_NORM, self.norm.isChecked())
        for key, box in self.boxes.items():
            st.setValue(key, box.isChecked())
        self.accept()

    def choice(self) -> dict | None:
        """None for the backing track, else which multitrack files to write."""
        if not self.multi.on:
            return None
        return {"parts": self.boxes[EXPORT_PARTS].isChecked(), "click": self.boxes[EXPORT_CLICK].isChecked(),
                "tempo": self.boxes[EXPORT_TEMPO].isChecked(), "clickwav": self.boxes[EXPORT_CLICKWAV].isChecked()}


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
        self.text = QPlainTextEdit(placeholderText=_("Например, после нажатия «Сохранить» программа зависла на 40%"))
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
        self.ok = QPushButton(_("Отправить"), objectName="primary", clicked=self._send, default=True)
        add_buttons(h, QPushButton(_("Отмена"), objectName="tool", clicked=self.reject), self.ok)
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
        v.addWidget(QLabel(_("Ключ включает разделение на дорожки на сервере Count-In. Песня уходит на сервер, исходный файл удаляется через сутки, дорожки хранятся 30 дней, чтобы та же песня открывалась без нового разделения."), objectName="field", wordWrap=True))
        self.edit = QLineEdit(cloud.key() or "", placeholderText="cin_…", echoMode=QLineEdit.Password)
        v.addWidget(self.edit)
        self.note = QLabel(objectName="field", wordWrap=True)
        v.addWidget(self.note)
        h = QHBoxLayout()
        self.remove = QPushButton(_("Удалить ключ"), objectName="tool", clicked=self._remove, enabled=bool(cloud.key()))
        h.addWidget(self.remove)
        h.addStretch()
        self.ok = QPushButton(_("Проверить и сохранить"), objectName="primary", clicked=self._check, default=True)
        add_buttons(h, QPushButton(_("Отмена"), objectName="tool", clicked=self.reject), self.ok)
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

        if pref_bool(CLOUD_KEY, False):
            warm_models()
        cloud.set_key(None)
        QSettings().setValue(CLOUD_KEY, False)
        self.accept()


class ProxyLinkDialog(QDialog):
    def __init__(self, parent, authority: str, username: str):
        super().__init__(parent)
        self.setWindowTitle(_("Прокси для облака"))
        self.authority, self.username = authority, username
        layout = QVBoxLayout(self)
        endpoint = QLabel(_("Прокси из ссылки: {host} · {user}", host=authority, user=username),
                          textFormat=Qt.PlainText, wordWrap=True)
        layout.addWidget(endpoint)
        self.password = QLineEdit(placeholderText=_("Пароль прокси"), echoMode=QLineEdit.Password)
        self.password.setAccessibleName(_("Пароль прокси"))
        layout.addWidget(self.password)
        self.note = QLabel(objectName="field", wordWrap=True)
        layout.addWidget(self.note)
        buttons = QHBoxLayout()
        buttons.addStretch()
        save = QPushButton(_("Сохранить"), objectName="primary", enabled=False, clicked=self._save)
        self.password.textChanged.connect(lambda value: save.setEnabled(bool(value)))
        add_buttons(buttons, QPushButton(_("Отмена"), objectName="tool", clicked=self.reject), save)
        layout.addLayout(buttons)
        self.password.setFocus()

    def _save(self):
        from .core import cloud

        password = self.password.text()
        if not password or not cloud_key():
            return
        value = f"socks5://{quote(self.username, safe='')}:{quote(password, safe='')}@{self.authority}"
        try:
            cloud.set_proxy_url(value)
        except ValueError:
            self.note.setText(_("Пароль прокси слишком длинный."))
        except Exception:
            self.note.setText(_("Не удалось сохранить прокси в системном хранилище."))
        else:
            self.password.clear()
            self.accept()


class PrefsDialog(QDialog):
    """Application preferences."""

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
        row(g2, _("Иконка программы"), self.icon)
        self.lang = QComboBox()
        for key, title in i18n.LANGUAGES.items():
            self.lang.addItem(_(title) if key == "system" else title, key)
        saved = st.value(LANG_KEY, "system")
        self.lang.setCurrentIndex(max(0, list(i18n.LANGUAGES).index(saved) if saved in i18n.LANGUAGES else 0))
        self.lang.currentIndexChanged.connect(self._lang)
        row(g2, _("Язык"), self.lang)
        self.lang_note = QLabel(_("Язык сменится после перезапуска программы"), objectName="field")
        self.lang_note.hide()
        g2.addWidget(self.lang_note, g2.rowCount(), 1)

        updates, update_grid = card(_("Обновления"))
        channel = QComboBox()
        channel.addItem("Stable", "stable")
        channel.addItem("Dev", "dev")
        channel.setCurrentIndex(channel.findData(st.value(UPDATE_CHANNEL_KEY, update.default_channel())))
        channel.currentIndexChanged.connect(lambda _i: parent._change_update_channel(channel.currentData()))
        row(update_grid, _("Канал обновлений"), channel)

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
        v.addWidget(updates)
        v.addWidget(cache)
        from .core import cloud

        if cloud_key():
            self.proxy_box, proxy_grid = card(_("Прокси для облака"))
            self.proxy_edit = QLineEdit(placeholderText="socks5://user:password@host:port",
                                        echoMode=QLineEdit.Password)
            self.proxy_note = QLabel(_("Оставь поле пустым, чтобы отключить SOCKS5."),
                                    objectName="field", wordWrap=True)
            try:
                self.proxy_edit.setText(cloud.proxy_url())
            except OSError:
                self.proxy_note.setText(_("Не удалось прочитать прокси из системного хранилища."))
            proxy_save = QPushButton(_("Сохранить"), objectName="small", clicked=self._save_proxy)
            row(proxy_grid, "SOCKS5", self.proxy_edit, proxy_save)
            proxy_grid.addWidget(self.proxy_note, proxy_grid.rowCount(), 0, 1, 2)
            v.addWidget(self.proxy_box)
            box, g4 = card(_("Ключ Count-In"))
            self.cloud = QCheckBox(_("Разделять песни на сервере Count-In"), checked=pref_bool(CLOUD_KEY, False))
            self.cloud.toggled.connect(self._cloud_toggled)
            g4.addWidget(self.cloud, 0, 0, 1, 2)
            g4.addWidget(QLabel(_("Перекрытие"), objectName="field"), 1, 0)
            self.overlap = QComboBox()
            for n, text in ((2, _("2 · быстрее")), (4, "4"), (8, _("8 · чище"))):
                self.overlap.addItem(text, n)
            self.overlap.setCurrentIndex(self.overlap.findData(cloud_overlap()))
            self.overlap.setToolTip(_("Сколько раз модель слушает каждый момент песни. Больше даёт чуть чище дорожки и дольше разделение. Новое значение действует на песни, открытые после смены; дорожки каждого значения хранятся отдельно."))
            self.overlap.currentIndexChanged.connect(lambda _i: QSettings().setValue(CLOUD_OVERLAP, self.overlap.currentData()))
            g4.addWidget(self.overlap, 1, 1)
            self.cloud_l = QLabel(_("проверяю ключ…"), objectName="field", wordWrap=True)
            g4.addWidget(self.cloud_l, 2, 0, 1, 2)
            self.music_l = QLabel(objectName="field", wordWrap=True)
            g4.addWidget(self.music_l, 3, 0, 1, 2)
            self.remove_key = QPushButton(_("Удалить ключ"), objectName="small", clicked=self._remove_key)
            g4.addWidget(self.remove_key, 4, 0, 1, 2, Qt.AlignLeft)
            self.key_box = box
            v.addWidget(box)
            self.cloud_job = Job(lambda _job: cloud.me())
            self.cloud_job.signals.done.connect(self._cloud_info)
            self.cloud_job.signals.failed.connect(self._cloud_refused)
            self.pool.start(self.cloud_job)
        if sys.platform != "darwin":  # Mac settings windows save at once and close by the title bar
            v.addWidget(close, 0, Qt.AlignRight)
        self._update()

    def _save_proxy(self):
        from .core import cloud

        if not cloud_key():
            return
        value = self.proxy_edit.text().strip()
        try:
            cloud.set_proxy_url(value)
        except ValueError:
            self.proxy_note.setText(_("Введи адрес вида socks5://user:password@host:port."))
        except Exception:
            self.proxy_note.setText(_("Не удалось сохранить прокси в системном хранилище."))
        else:
            self.proxy_edit.setText(value)
            self.proxy_note.setText(_("Прокси сохранён.") if value else _("SOCKS5 отключён."))

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
        self.music_l.setText(_("Поиск музыки доступен: кнопка «Поиск музыки…» рядом с «Открыть…». Он работает и "
                               "при разделении на этом компьютере.") if i.get("music_search")
                             else _("Поиск музыки для этого ключа не включён."))

    def _remove_key(self):
        from .core import cloud

        if QMessageBox.question(self, _("Удалить ключ"), _("Удалить ключ Count-In с этого компьютера? Разделение "
                                "пойдёт на этом компьютере, поиск музыки пропадёт. Вернуть ключ можно, вставив "
                                "его снова.")) != QMessageBox.StandardButton.Yes:
            return
        if pref_bool(CLOUD_KEY, False):
            warm_models()
        cloud.set_key(None)
        QSettings().setValue(CLOUD_KEY, False)
        self.key_box.hide()
        self.proxy_box.hide()
        self.proxy_edit.clear()
        self.parent().check_music_access()

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
        self.setWindowTitle(update.app_name(update.current_version()))
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
        self.ready_preview: PreviewState | None = None
        self.speed = 100  # playback and export speed in percent
        self.semis = 0  # transposition in semitones
        self.trainer_laps = 0  # loop passes counted for the speed trainer
        self.setlist: list[Path] = []  # songs opened together; N and P walk them
        self.resume_at = None  # song time a rebuilt preview carries on from
        self.preroll = False  # the pending preview starts with a bar of clicks before resume_at
        self.want_play = False  # a preview job is on its way to the speakers; Stop cancels that
        self.loop: tuple[float, float] | None = None  # song times played round while loop_on
        self.loop_on = False
        self.loop_rate: float | None = None
        self.trainer_steps = 0
        self.audition_end: float | None = None
        self.section_i = 0
        self.tempo_span = None  # an explicit selection, independent of the transport's stored loop
        self.job_shown = False  # the progress card shows only for jobs that take a while
        self.show_timer = QTimer(self, singleShot=True, interval=300, timeout=self._show_job)
        self.song_timer = QTimer(self, singleShot=True, interval=800, timeout=self._save_song)
        self.restoring = False  # a remembered song is being applied; nothing to save yet
        self.undo_stack: list = []  # earlier states of the track's edits (_edit_state), newest last
        self.redo_stack: list = []
        self.undo_base = None  # the state the next edit gets compared with
        self.undo_timer = QTimer(self, singleShot=True, interval=600, timeout=self._snap_undo)  # a drag is one step
        self.zones_pending = False  # a pause search waits for the running job
        self.models_warm = False  # the separation model starts loading with the first track
        self.backing_key, self.wave_source = None, None  # see _update_backing
        self.play_timer = QTimer(self, interval=30, timeout=self._tick)
        self.last_saved: Path | None = None
        self.release = None
        self.staged: Path | None = None  # unpacked update waiting for a restart or quit
        self._update_busy = False

        self.updateFound.connect(self._update_found)
        self.updateProgress.connect(self._update_progress)
        self.updateReady.connect(self._update_ready)
        self._build_menu()
        self._build_body()
        self._show_recent()
        self._build_status()
        self._load_defaults()
        self._labels()
        self._enable()
        self.player.volume = output_volume()

        # Space plays from anywhere except text fields, so focus on a slider or checkbox does not swallow it.
        QApplication.instance().installEventFilter(self)

    def eventFilter(self, obj, e):
        if (e.type() == QEvent.KeyPress and e.key() in (Qt.Key_Left, Qt.Key_Right)
                and e.modifiers() in (Qt.ShiftModifier, Qt.ShiftModifier | Qt.AltModifier)
                and isinstance(obj, QWidget) and obj.window() is self
                and not isinstance(obj, (QLineEdit, QAbstractSpinBox, QComboBox, QSlider))
                and QApplication.activePopupWidget() is None and QApplication.activeModalWidget() is None
                and self.wave.sel):
            self._nudge_selection(bool(e.modifiers() & Qt.AltModifier), 1 if e.key() == Qt.Key_Right else -1)
            return True
        if (e.type() == QEvent.KeyPress and e.key() == Qt.Key_Escape and not e.modifiers()
                and isinstance(obj, QWidget) and obj.window() is self
                and QApplication.activeModalWidget() is None and QApplication.activePopupWidget() is None
                and self.wave.clear_selection()):
            return True
        if (e.type() == QEvent.KeyPress and e.key() == Qt.Key_Space and not e.isAutoRepeat()
                and not e.modifiers() and QApplication.activeModalWidget() is None
                and QApplication.activePopupWidget() is None):
            # Key events go to the focused widget first, so obj is what would type the space.
            if not isinstance(obj, (QLineEdit, QAbstractSpinBox)):
                self._play()
                return True
        front = QApplication.activeWindow()
        if (e.type() == QEvent.KeyPress and (e.key() in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Home, Qt.Key_L, Qt.Key_M,
                                                         Qt.Key_N, Qt.Key_P, Qt.Key_PageUp, Qt.Key_PageDown)
                                             or Qt.Key_1 <= e.key() <= Qt.Key_9)
                and not e.modifiers() and QApplication.activeModalWidget() is None
                and QApplication.activePopupWidget() is None
                and (front in (self, None) or not front.isVisible())  # not while Help is in front
                and not any(isinstance(x, (QLineEdit, QAbstractSpinBox, QSlider, QComboBox))
                            for x in (obj, QApplication.focusWidget()))):
            # Arrows walk the pauses, Home goes back to the count-in.
            if e.key() == Qt.Key_L:
                self._toggle_loop()
            elif e.key() == Qt.Key_M:
                self._add_marker()
            elif e.key() in (Qt.Key_N, Qt.Key_P):
                self._step_setlist(1 if e.key() == Qt.Key_N else -1)
            elif e.key() == Qt.Key_PageDown:  # page-turner pedals: right plays and stops, left plays again
                self._play()
            elif e.key() == Qt.Key_PageUp:
                self._replay()
            elif Qt.Key_1 <= e.key() <= Qt.Key_9:
                self._goto_marker(e.key() - Qt.Key_1)
            elif e.key() == Qt.Key_Home:
                self._to_start()
            else:
                self._jump_pause(-1 if e.key() == Qt.Key_Left else 1)
            return True
        return super().eventFilter(obj, e)

    # --- layout ---

    def _build_menu(self):
        mb = self.menuBar()
        file = mb.addMenu(_("Файл"))
        self.open_a = file.addAction(_("Открыть…"), QKeySequence.Open, self._open_dialog)
        self.downloads_a = file.addAction(_("Скачанные песни…"), self._open_downloads)
        self.music_a = file.addAction(_("Поиск музыки…"), self._open_music)
        self.music_a.setVisible(False)
        self.music_token = None
        self.music_dialog = None
        self.music_check = None
        self.save_a = file.addAction(_("Сохранить"), QKeySequence.Save, self._save)
        self.close_a = file.addAction(_("Закрыть трек"), QKeySequence.Close, self._close_track)
        self.recent_m = file.addMenu(_("Открыть недавние"))
        file.addAction(_("Следующая песня сет-листа") + "\tN", lambda: self._step_setlist(1))
        file.addAction(_("Предыдущая песня сет-листа") + "\tP", lambda: self._step_setlist(-1))
        self.reveal_a = file.addAction(_("Показать сохранённый файл"), self._reveal)
        self.match_a = file.addAction(_("Подогнать мою гитару…"), self._guitar_match)
        file.addSeparator()
        file.addAction(_("Сохранить настройки как по умолчанию"), self._save_defaults)
        file.addAction(_("Вернуть настройки по умолчанию"), self._reset_settings)
        prefs = file.addAction(_("Настройки…"), QKeySequence.Preferences, self._prefs)
        prefs.setMenuRole(QAction.PreferencesRole)
        file.addSeparator()
        quit_a = file.addAction(_("Выход"), QKeySequence.Quit, QApplication.instance().quit)
        quit_a.setMenuRole(QAction.QuitRole)  # macOS moves it into the app menu

        # The keys after a tab show in the menu; the window's event filter handles them, so text fields keep them.
        edit = mb.addMenu(_("Правка"))
        edit.addAction(_("Отменить"), QKeySequence.Undo, self._undo)
        edit.addAction(_("Повторить"), QKeySequence.Redo, lambda: self._undo(redo=True))
        edit.addSeparator()
        self.select_end_a = edit.addAction(_("Выделить до конца песни"), QKeySequence("Shift+End"), self._select_to_end)
        self.select_end_a.setEnabled(False)
        self.import_gp_a = edit.addAction(_("Импортировать темпотрек из Guitar Pro…"), self._import_guitar_pro)
        self.import_gp_a.setEnabled(False)

        play = mb.addMenu(_("Воспроизведение"))
        self.play_a = play.addAction(_("Прослушать / Стоп") + "\t" + _("Пробел"), self._play)
        play.addAction(_("К отсчёту") + "\tHome", self._to_start)
        play.addAction(_("Предыдущая пауза") + "\t←", lambda: self._jump_pause(-1))
        play.addAction(_("Следующая пауза") + "\t→", lambda: self._jump_pause(1))
        play.addAction(_("Петля") + "\tL", self._toggle_loop)
        play.addSeparator()
        play.addAction(_("Играть сначала") + "\tPgUp", self._replay)
        play.addAction(_("Добавить метку части") + "\tM", self._add_marker)
        play.addAction(_("Перейти к такту…"), QKeySequence("Ctrl+G"), self._goto_bar)
        self.trainer_a = play.addAction(_("Тренажёр скорости (+5% каждые два круга)"))
        self.trainer_a.setCheckable(True)
        self.trainer_a.toggled.connect(self._reset_trainer)
        play.addAction(_("Медленнее"), QKeySequence("Ctrl+["), lambda: self._set_speed(self.speed - 5))
        play.addAction(_("Быстрее"), QKeySequence("Ctrl+]"), lambda: self._set_speed(self.speed + 5))

        view = mb.addMenu(_("Вид"))
        view.addAction(_("Приблизить"), QKeySequence.ZoomIn, lambda: self._zoom(0.5))
        view.addAction(_("Отдалить"), QKeySequence.ZoomOut, lambda: self._zoom(2.0))
        view.addAction(_("Весь трек"), QKeySequence("Ctrl+0"), self._zoom_fit)
        self.zoom_selection_a = view.addAction(_("Масштаб по выделению"), self._zoom_selection)
        self.zoom_selection_a.setEnabled(False)

        if sys.platform == "darwin":
            win = mb.addMenu(_("Окно"))
            win.addAction(_("Свернуть"), QKeySequence("Ctrl+M"), self.showMinimized)
            win.addAction(_("Изменить масштаб окна"), lambda: self.showNormal() if self.isMaximized() else self.showMaximized())

        help_ = mb.addMenu(_("Справка"))
        about = help_.addAction(_("О программе Count-In"), self._about)
        about.setMenuRole(QAction.AboutRole)

        # ApplicationSpecificRole puts it into the app menu on macOS, under Settings, as macOS apps do.
        self.update_a = help_.addAction(_("Проверить обновления…"), self._update_action)
        self.update_a.setMenuRole(QAction.ApplicationSpecificRole)
        help_.addAction(_("Справка Count-In"), QKeySequence.HelpContents, self._help)
        help_.addAction(_("Что нового"), lambda: self._whats_new(None))
        help_.addAction(_("Страница проекта"), lambda: QDesktopServices.openUrl(QUrl(site_url())))
        help_.addSeparator()
        help_.addAction(_("Сообщить о проблеме…"), lambda: ReportDialog(self).exec())

        # Not in any menu: the cloud key is for people the author gave one to.
        key_a = QAction(self, shortcut=QKeySequence("Ctrl+Alt+K"), triggered=self._cloud_key)
        key_a.setShortcutContext(Qt.ApplicationShortcut)
        self.addAction(key_a)

    def _header(self) -> QWidget:
        """Three cards like the ones under the waveform: the song and what the detector found, its key and
        file, then the speaker level over the file buttons."""
        head = QWidget(objectName="header")
        h = QHBoxLayout(head)
        h.setContentsMargins(22, 12, 22, 4)
        h.setSpacing(12)

        song = QFrame(objectName="card")
        sv = QVBoxLayout(song)
        sv.setContentsMargins(14, 12, 14, 12)
        sv.setSpacing(2)
        self.title = QLabel("Count-In", objectName="title")
        self.subtitle = QLabel(objectName="subtitle")
        for w in (self.title, self.subtitle, self.detector, self.warning):
            sv.addWidget(w)
        sv.addStretch()
        h.addWidget(song, 1)

        self.info_card, g = card(_("Тональность"))
        self.key_l = QLabel("—", objectName="keyBig")
        self.key_l.setToolTip(_("Тональность оценена по спектру всей песни, проверьте её на слух"))
        self.length_l = QLabel(objectName="subtitle")
        self.file_l = QLabel(objectName="subtitle")
        g.addWidget(self.key_l, 0, 0, 1, 2)
        g.addWidget(self.length_l, 1, 0, 1, 2)
        g.addWidget(self.file_l, 2, 0, 1, 2)
        self.info_card.setFixedWidth(250)  # a long file line never widens the card and shifts its neighbours
        self.length_l.setText("—")  # placeholders keep the header the same height with or without a song
        self.file_l.setText("—")
        h.addWidget(self.info_card, 0)

        # Speaker level of the playback; never part of the saved file
        self.out_pct = QLabel(objectName="unit")
        acts, g = card(_("Громкость вывода"), self.out_pct)
        self.speaker = QLabel(toolTip=_("Громкость прослушивания"))
        self.out_volume = QSlider(Qt.Horizontal, minimum=0, maximum=100, minimumWidth=120,
                                  toolTip=_("Громкость прослушивания"), accessibleName=_("Громкость прослушивания"))
        self.out_volume.setValue(int(output_volume() * 100))
        self.out_volume.setFocusPolicy(Qt.TabFocus)
        self.out_volume.valueChanged.connect(self._output_volume)
        self._speaker_icon()
        g.addWidget(self.speaker, 0, 0, Qt.AlignVCenter)
        g.addWidget(self.out_volume, 0, 1, Qt.AlignVCenter)

        # File buttons along the card's bottom edge, under the same rule the tempo card uses
        self.open_b = QPushButton(_("Открыть…"), objectName="headBtn", clicked=self._open_dialog)
        self.downloads_b = QPushButton(_("Скачанные песни…"), objectName="headBtn", clicked=self._open_downloads)
        self.save_b = QPushButton(_("Сохранить"), objectName="headBtn", clicked=self._save,
                                  toolTip=QKeySequence(QKeySequence.Save).toString(QKeySequence.NativeText))
        self.save_b.setProperty("accent", True)
        self.music_b = QPushButton(_("Поиск музыки…"), objectName="headBtn", clicked=self._open_music, visible=False)
        self.close_b = QPushButton(_("Закрыть"), objectName="headBtn", clicked=self._close_track,
                                   toolTip=QKeySequence(QKeySequence.Close).toString(QKeySequence.NativeText))
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        for b in (self.open_b, self.downloads_b, self.music_b, self.close_b, self.save_b):
            b.setFocusPolicy(Qt.TabFocus)
            buttons.addWidget(b, 1)
        acts.layout().addWidget(QFrame(objectName="hline"))
        acts.layout().addLayout(buttons)
        h.addWidget(acts, 0)
        return head

    def _build_body(self):
        self.stack = QStackedWidget()
        drop_page = QWidget()
        dv = QVBoxLayout(drop_page)
        dv.setContentsMargins(40, 20, 40, 40)
        drop = ClickLabel(_("Перетащите сюда трек или щёлкните, чтобы выбрать\n\nmp3, FLAC, ALAC, WAV, AIFF, OGG, M4A"),
                          objectName="drop", alignment=Qt.AlignCenter, cursor=Qt.PointingHandCursor)
        drop.clicked.connect(self._open_dialog)
        dv.addWidget(drop, 1)
        self.recent_box = QWidget()
        self.recent_v = QVBoxLayout(self.recent_box)
        self.recent_v.setContentsMargins(0, 12, 0, 0)
        self.recent_v.setSpacing(2)
        dv.addWidget(self.recent_box)
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
        self.cancel_b.setFocusPolicy(Qt.TabFocus)
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
        text = keys(_(random.choice(TIPS)))
        if self.sep_job and self._full_stems() is None and self._filling() and self.settings().levels:
            text = _("Паузы с хай-хэтом появятся после разделения на дорожки, потому что их ищут без убранных инструментов")
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
        self.loop_b = glyph_button("loop", _("Играть выделенные такты по кругу (L). Протяните по волне, чтобы выделить такты"),
                                   self._toggle_loop)
        self.loop_b.setCheckable(True)
        self.loop_b.setEnabled(False)
        h.addWidget(self.loop_b)
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

        h.addSpacing(6)
        h.addWidget(vsep())
        h.addSpacing(6)
        h.addWidget(QLabel(_("Паузы"), objectName="groupTitle", toolTip=_("Заполнение пауз")))
        h.addSpacing(4)

        # − count + as in the tempo card: minus removes the current pause, plus makes one at the play position
        h.addWidget(glyph_button("minus", _("Убрать текущую паузу"), self._remove_pause, "round"))
        chip = QFrame(objectName="lcd")
        ch = QHBoxLayout(chip)
        ch.setContentsMargins(10, 3, 10, 3)
        self.pause_l = QLabel(objectName="chipText", alignment=Qt.AlignCenter, minimumWidth=56)
        ch.addWidget(self.pause_l)
        h.addWidget(chip)
        h.addWidget(glyph_button("plus", keys(_("Сделать паузой такт под плейхедом. Чтобы взять несколько тактов, протяните по волне.")),
                                 self._add_pause, "round"))
        help_b = QPushButton("?", objectName="tbtn", clicked=self._help, toolTip=_("Справка Count-In"))
        help_b.setFocusPolicy(Qt.TabFocus)

        # Speed: − percent +, slower with the pitch kept; steps of 5% from 50% to 100%; below 80% the stretch smears the sound
        h.addSpacing(6)
        h.addWidget(vsep())
        h.addSpacing(6)
        h.addWidget(QLabel(_("Скорость"), objectName="groupTitle"))
        h.addSpacing(4)
        h.addWidget(glyph_button("minus", _("Медленнее на 5%, тон не меняется"), lambda: self._set_speed(self.speed - 5), "round"))
        schip = QFrame(objectName="lcd")
        sh = QHBoxLayout(schip)
        sh.setContentsMargins(10, 3, 10, 3)
        self.speed_l = QLabel("100%", objectName="chipText", alignment=Qt.AlignCenter, minimumWidth=48)
        self.speed_l.setToolTip(_("Скорость прослушивания и сохранения. Тон не меняется, отсчёт и хай-хэт встают на замедленную сетку. Ниже 80% звук размывается, но разбирать ноты это не мешает."))
        sh.addWidget(self.speed_l)
        h.addWidget(schip)
        h.addWidget(glyph_button("plus", _("Быстрее на 5%"), lambda: self._set_speed(self.speed + 5), "round"))
        self.speed_bpm_l = QLabel(objectName="lcdSub")  # the tempo it plays at
        self.speed_bpm_l.setToolTip(_("Темп с учётом скорости"))
        h.addSpacing(6)
        h.addWidget(self.speed_bpm_l)

        # Key: semitones up or down, tempo kept
        h.addSpacing(6)
        h.addWidget(vsep())
        h.addSpacing(6)
        h.addWidget(QLabel(_("Тон"), objectName="groupTitle"))
        h.addSpacing(4)
        h.addWidget(glyph_button("minus", _("На полтона ниже"), lambda: self._set_semis(self.semis - 1), "round"))
        tchip = QFrame(objectName="lcd")
        th = QHBoxLayout(tchip)
        th.setContentsMargins(10, 3, 10, 3)
        self.semis_l = QLabel("0", objectName="chipText", alignment=Qt.AlignCenter, minimumWidth=32)
        self.semis_l.setToolTip(_("Сдвиг тона в полутонах, темп не меняется. −1 подходит для строя Eb, −2 для строя D. Сохранённый файл тоже сдвигается."))
        th.addWidget(self.semis_l)
        h.addWidget(tchip)
        h.addWidget(glyph_button("plus", _("На полтона выше"), lambda: self._set_semis(self.semis + 1), "round"))
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

    def _step_setlist(self, d: int):
        t = self.current
        if not self.setlist:
            self.status.setText(_("Чтобы собрать сет-лист, откройте или перетащите несколько песен сразу"))
            return
        i = self.setlist.index(t.path) if t and t.path in self.setlist else -1
        j = i + d
        if 0 <= j < len(self.setlist) and not self.job:
            self.open_files([self.setlist[j]])
            self.status.setText(_("Сет-лист: песня {i} из {n}", i=j + 1, n=len(self.setlist)))

    def _replay(self):
        """From the loop start, the playhead or the count-in again, with the count-in bar before a mid-song start."""
        at = self.loop[0] if self.loop_on and self.loop else self.wave.play_t
        if at is None:
            self._stop()
            self._play()
        else:
            self._play_at(at, preroll=True)

    def _here(self) -> float | None:
        w = self.wave
        return w.cursor_t if w.cursor_t is not None else w.play_t

    def _set_markers(self, markers: list):
        t = self.current
        t.markers = sorted(markers)
        self.wave.markers = t.markers
        self._song_changed()
        self.wave.update()

    def _add_marker(self):
        """A named section at the play position: keys 1–9 jump to the first nine."""
        t, at = self.current, self._here()
        if not (t and t.analysis) or at is None:
            self.status.setText(_("Щёлкните по волне, где начинается часть, и добавьте метку"))
            return
        name, ok = QInputDialog.getText(self, _("Метка части"), _("Название части:"),
                                        text=_("Часть {n}", n=len(t.markers) + 1))
        if ok and name.strip():
            self._set_markers(t.markers + [(float(at), name.strip())])

    def _marker_menu(self, i: int):
        t = self.current
        menu = QMenu(self)
        rename, delete = menu.addAction(_("Переименовать…")), menu.addAction(_("Убрать метку"))
        chosen = menu.exec(QCursor.pos())
        at, name = t.markers[i]
        if chosen is rename:
            new, ok = QInputDialog.getText(self, _("Метка части"), _("Название части:"), text=name)
            if ok and new.strip():
                self._set_markers(t.markers[:i] + [(at, new.strip())] + t.markers[i + 1 :])
        elif chosen is delete:
            self._set_markers(t.markers[:i] + t.markers[i + 1 :])

    def _goto_marker(self, i: int):
        t = self.current
        if t and i < len(t.markers):
            self._go_to(t.markers[i][0])

    def _goto_bar(self):
        t = self.current
        if not (t and t.bars):
            return
        n, ok = QInputDialog.getInt(self, _("Перейти к такту"), _("Номер такта:"), 1, 1, len(t.bars))
        if ok:
            self._go_to(t.bars[n - 1].start)

    def _go_to(self, at: float):
        w = self.wave
        w.set_playhead(at, snap=False)
        span = w.v1 - w.v0
        w.show_range(at - 0.1 * span, at + 0.9 * span)
        if self.play_start is not None:
            self._play_at(at, preroll=True)

    def _click_mute(self, on: bool):
        self.click_mute.setIcon(QIcon(part_icon("muted" if on else "speaker", QColor(127, 127, 127), 16)))
        if hasattr(self, "parts"):  # the part sliders come later in the build
            self._gains()

    def _solo(self, name: str, on: bool):
        self.player.solo = {n for n, b in self.solo_b.items() if b.isChecked()} | (
            {"click"} if self.solo_b_click.isChecked() else set())
        if on and name != "click" and not self.player.src[1].keys() - {"click"} and self.play_start is not None:
            self._restart_preview(separate=True)  # the playing preview has no parts yet
        elif on and name != "click" and self._full_stems() is None and not self.sep_job:
            self.status.setText(_("Соло заработает, когда песня разделится на дорожки"))

    def _set_semis(self, n: int):
        """Transposition in semitones, −6..+6; a playing preview carries on in the new key."""
        n = int(np.clip(n, -6, 6))
        if n == self.semis:
            return
        self.semis = n
        self._song_changed()
        self.semis_l.setText(f"{n:+d}".replace("-", "−") if n else "0")
        self._show_detector()
        self._restart_preview()

    def _set_speed(self, pct: int):
        """Playback and export speed in percent, 50..100; a playing preview carries on at the new speed."""
        pct = int(np.clip(pct, 50, 100))
        if pct == self.speed:
            return
        self.speed = pct
        self._song_changed()
        self.speed_l.setText(f"{pct}%")
        self._refresh()
        self._restart_preview()

    def _speed_bpm(self):
        """The tempo the song plays at with the speed applied, next to the speed; empty at 100%."""
        t = self.current
        if t is None or not t.bpm or self.speed == 100:
            self.speed_bpm_l.setText("")
            return
        bpm = t.sections[self.section_i].bpm if t.sections else t.bpm
        self.speed_bpm_l.setText(_("→ {bpm} BPM", bpm=QLocale().toString(bpm * self.speed / 100, "f", 1)))

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
            starts = [zn.bounds(w.bars, z)[0] for z in w.zones]
            i = sum(a <= t for a in starts) - (1 if d < 0 else 0)
        self._go_pause(int(np.clip(i, 0, len(w.zones) - 1)))

    def _go_pause(self, i: int):
        """Makes pause i the current one, brings it into view and plays from the bar before it."""
        w = self.wave
        z = w.zones[i]
        a, b = zn.bounds(w.bars, z)
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
            if self.loop_on:
                self.loop_on = False
                self._reset_trainer()
                self._apply_loop()
                self._refresh()
            lead = min((c.time for c in w.clicks), default=t.t0)
            w.play_t = lead
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
        self.bpm_spin.setKeyboardTracking(False)
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
            b.setFocusPolicy(Qt.TabFocus)
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
        self.alt_b.setFocusPolicy(Qt.TabFocus)
        g.addWidget(self.alt_b, 5, 0, 1, 2)
        return tempo

    def _editor(self) -> QWidget:
        self.detector = QLabel(objectName="subtitle")
        # One reserved line, so notes appearing or going away never move the waveform
        self.warning = QLabel(objectName="warning")
        self.warning.setFixedHeight(self.warning.fontMetrics().height() + 4)
        self.wave = Wave()
        self.selection_b = QPushButton(_("Выделение"), objectName="tool", checkable=True, toggled=self._selection_mode)
        self.selection_b.setToolTip(_("Потяните по волне, чтобы выделить участок по долям. Option/Alt — без привязки. "
                                      "Esc или щелчок вне выделения снимает его."))
        self.selection_actions = QPushButton(_("Действия с выделением…"), objectName="tool", enabled=False,
                                             clicked=self._selection_menu)
        self.saved_loops_b = QPushButton(_("Петли…"), objectName="tool")
        self.saved_loops_menu = QMenu(self.saved_loops_b)
        self.saved_loops_menu.aboutToShow.connect(self._show_saved_loops)
        self.saved_loops_b.setMenu(self.saved_loops_menu)
        self.import_gp_b = QPushButton(_("Импорт Guitar Pro…"), objectName="tool", enabled=False, clicked=self._import_guitar_pro)
        self.add_marker_b = QPushButton(_("+ Метка"), objectName="tool", enabled=False, clicked=self._add_marker)
        self.add_marker_b.setToolTip(_("Добавить метку части в позиции плейхеда (M)"))
        self.selection_time = QLabel(objectName="field")
        edit_tools = QHBoxLayout()
        for button in (self.selection_b, self.selection_actions, self.saved_loops_b):
            edit_tools.addWidget(button)
        edit_tools.addWidget(self.selection_time)
        edit_tools.addStretch()
        edit_tools.addWidget(self.import_gp_b)
        edit_tools.addWidget(self.add_marker_b)
        self.lane = self.wave.lane = TempoLane(self.wave)
        self.lane.selected.connect(self._select_section)
        self.lane.splitAt.connect(self._split_section)
        self.lane.moved.connect(self._move_section)
        self.lane.joined.connect(self._join_sections)
        self.lane.shifted.connect(self._shift_section)
        self.lane.editRequested.connect(self._edit_tempo_section)
        self.wave.phaseDragged.connect(self._wave_phase)
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
        self.imported_meter_l = QLabel(objectName="field")
        self.imported_meter_l.hide()
        self.meter_seg.layout().insertWidget(len(self.meter_seg.values), self.imported_meter_l)
        row(g, _("Размер участка"), self.meter_seg)
        self.count_voice = click_voice_picker(COUNT_VOICE_DEFAULT)
        row(g, _("Звук отсчёта"), self.count_voice)
        self.volume = QSlider(Qt.Horizontal, minimum=-24, maximum=6, value=0, minimumWidth=90, tracking=False)
        self.volume_l = QLabel(minimumWidth=60)
        row(g, _("Громкость"), self.volume, self.volume_l)
        self.preroll_cb = QCheckBox(_("Такт отсчёта при старте с середины"), objectName="rowCheck",
                                    checked=pref_bool(PREROLL_KEY, True))
        self.preroll_cb.setToolTip(_("Когда песня играет с плейхеда или петля начинается заново, перед этим местом звучит такт клика в его темпе."))
        self.preroll_cb.toggled.connect(lambda on: QSettings().setValue(PREROLL_KEY, on))
        g.addWidget(self.preroll_cb, g.rowCount(), 0, 1, 2, Qt.AlignLeft | Qt.AlignVCenter)

        g.addWidget(QFrame(objectName="hline"), g.rowCount(), 0, 1, 2)
        g.addWidget(QLabel(_("Метроном"), objectName="cardTitle"), g.rowCount(), 0, 1, 2)
        self.click_all = QCheckBox(_("Клик на всю песню"), objectName="rowCheck")
        self.click_all.setToolTip(_("Клик на каждую долю после отсчёта, до конца песни. Заполнение пауз при этом не нужно."))
        g.addWidget(self.click_all, g.rowCount(), 0, 1, 2, Qt.AlignLeft | Qt.AlignVCenter)
        self.click_voice = click_voice_picker(METRONOME_VOICE_DEFAULT)
        row(g, _("Звук клика"), self.click_voice)
        self.click_db = QSlider(Qt.Horizontal, minimum=-12, maximum=12, minimumWidth=90, tracking=False)
        self.click_db_l = QLabel(minimumWidth=60)
        self.click_mute = QPushButton(objectName="small", checkable=True, focusPolicy=Qt.TabFocus, fixedSize=QSize(30, 22),
                                      toolTip=_("Выключить клик в прослушивании: отсчёт, паузы и клик песни"),
                                      accessibleName=_("Выключить клик"))
        self.click_mute.toggled.connect(self._click_mute)
        self._click_mute(False)
        click_solo = QPushButton("S", objectName="solo", checkable=True, focusPolicy=Qt.TabFocus, fixedSize=QSize(26, 22),
                                 toolTip=_("Соло: в прослушивании звучит только клик"), accessibleName=_("Соло: клик"))
        click_solo.toggled.connect(lambda on: self._solo("click", on))
        self.solo_b_click = click_solo
        row(g, _("Громкость клика"), self.click_mute, click_solo, self.click_db, self.click_db_l)
        self.pattern: Path | None = None

        save_b = self._icon_button("save", _("Сохранить эти настройки как настройки по умолчанию"), self._save_defaults)
        reset_b = self._icon_button("reset", _("Вернуть настройки по умолчанию"), self._reset_settings)
        mix, g = card(_("Бэктрек"), reset_b, save_b)
        self.parts: dict[str, QSlider] = {}
        self.solo_b: dict[str, QPushButton] = {}
        self.part_l: dict[str, QLabel] = {}
        for name, label, icon, tip in PARTS:
            s = self.parts[name] = QSlider(Qt.Horizontal, minimum=MUTE, maximum=BOOST, value=0, minimumWidth=110)
            self.part_l[name] = QLabel(minimumWidth=80)
            solo = QPushButton("S", objectName="solo", checkable=True, focusPolicy=Qt.TabFocus, fixedSize=QSize(26, 22),
                               toolTip=_("Соло оставляет в прослушивании только эту дорожку"),
                               accessibleName=_("Соло: {part}", part=_(label)))
            solo.toggled.connect(lambda on, n=name: self._solo(n, on))
            self.solo_b[name] = solo
            row(g, icon_label(icon, _(label)), MuteButton(s, _(tip)), solo, s, self.part_l[name])
        self.guitar, self.vocals = self.parts["guitar"], self.parts["vocals"]
        self.match_b = QPushButton(_("Подогнать мою гитару…"), clicked=self._guitar_match)
        self.match_b.setToolTip(_("Сравнить запись своей гитары с гитарой из песни и получить настройки эквалайзера"))
        g.addWidget(self.match_b, g.rowCount(), 0, 1, 2, Qt.AlignLeft | Qt.AlignVCenter)
        g.addWidget(QFrame(objectName="hline"), g.rowCount(), 0, 1, 2)
        g.addWidget(QLabel(_("Паузы"), objectName="cardTitle"), g.rowCount(), 0, 1, 2)
        self.fill = QCheckBox(_("Заполнять паузы хай-хэтом"), checked=pref_bool(FILL_KEY, True))
        self.fill.setToolTip(_("Такты, где без убранных дорожек тихо, получают хай-хэт на каждую долю. "
                               "Правый щелчок по паузе убирает её, рядом с паузой добавляет такт, края паузы тянутся мышью."))
        self.fill.setObjectName("rowCheck")
        g.addWidget(self.fill, g.rowCount(), 0, 1, 2, Qt.AlignLeft | Qt.AlignVCenter)
        self.fill_build = QCheckBox(_("Нарастание к концу паузы"), checked=pref_bool(FILL_BUILD_KEY, False), objectName="rowCheck")
        self.fill_build.setToolTip(_("Последний такт паузы ведёт к вступлению. Его вторая половина звучит восьмыми, последние три удара громче, как в отсчёте «Нарастание»."))
        self.fill_build.toggled.connect(lambda on: QSettings().setValue(FILL_BUILD_KEY, on))
        self.fill_build.toggled.connect(self._refresh)
        self.fill_build.toggled.connect(lambda _on: self._restart_preview())
        g.addWidget(self.fill_build, g.rowCount(), 0, 1, 2, Qt.AlignLeft | Qt.AlignVCenter)

        # How quiet a bar must be to count as a pause; the waveform shows the level as a dashed line
        self.threshold = QSlider(Qt.Horizontal, minimum=-36, maximum=-6, minimumWidth=110)
        self.threshold.setValue(int(QSettings().value(THRESHOLD_KEY, zn.QUIET_DB)))
        self.threshold.setToolTip(_("Насколько тише среднего уровня песни должен быть такт, чтобы считаться паузой. "
                                    "На волне этот уровень показан оранжевыми полосами у правого края."))
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
        self.count_voice.currentIndexChanged.connect(self._click_sound)
        self.click_voice.currentIndexChanged.connect(self._click_sound)
        self.volume.valueChanged.connect(self._click_sound)
        self.click_db.valueChanged.connect(self._labels)
        self.click_db.valueChanged.connect(self._click_sound)
        self.wave.zonesEdited.connect(self._zones_edited)
        self.wave.zoneClicked.connect(self._edit_zone)
        self.wave.spanSelected.connect(self._span_selected)
        self.wave.markerMenu.connect(self._marker_menu)
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
        v.addLayout(edit_tools)
        v.addWidget(self.lane)
        v.addWidget(self.wave, 1)
        overview = Overview(self.wave, 46)
        overview.markerClicked.connect(self._goto_marker)
        v.addWidget(overview)
        v.addWidget(self._transport())
        v.addSpacing(6)
        v.addLayout(cards)
        return page

    def _build_status(self):
        sb = QStatusBar()
        sb.setSizeGripEnabled(False)
        self.setStatusBar(sb)
        self.status = QLabel()
        self.reveal_b = QPushButton(_("Показать в Finder") if sys.platform == "darwin" else _("Показать в папке"),
                                    objectName="small", clicked=self._reveal)
        self.reveal_b.setFocusPolicy(Qt.TabFocus)
        self.version_l = QLabel(f"v{update.current_version()}")

        # Round blue badge left of the version, shown only when a newer release exists.
        self.update_b = QPushButton("↓", objectName="update", clicked=self._update_action)
        self.update_b.setFocusPolicy(Qt.TabFocus)
        self.update_b.setCursor(Qt.PointingHandCursor)
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
        self.check_music_access()

    def open_proxy_link(self, link: str):
        from .core import cloud

        if not cloud_key():
            self.status.setText(_("Чтобы настроить прокси, сначала добавьте ключ Count-In."))
            return
        try:
            authority, username = cloud.proxy_link(link)
        except ValueError:
            QMessageBox.warning(self, _("Прокси для облака"), _("Не удалось прочитать ссылку на прокси."))
            return
        dialog = getattr(self, "proxy_link_dialog", None)
        if dialog is not None:
            dialog.raise_()
            dialog.activateWindow()
            return
        dialog = self.proxy_link_dialog = ProxyLinkDialog(self, authority, username)
        dialog.accepted.connect(self.check_music_access)
        dialog.finished.connect(self._proxy_link_closed)
        dialog.open()
        dialog.raise_()
        dialog.activateWindow()

    def _proxy_link_closed(self, _result):
        self.proxy_link_dialog.deleteLater()
        self.proxy_link_dialog = None

    def _cloud_key(self):
        CloudKeyDialog(self).exec()
        self.check_music_access()

    # --- music search: shown once the server confirms the stored key allows it ---

    def check_music_access(self):
        from .core import cloud

        token = cloud.key()
        self.music_check = token
        if not token:
            self._music_access((None, False))
            return
        job = Job(lambda _job: (token, bool(cloud.me(token).get("music_search"))))
        job.signals.done.connect(self._music_access)
        job.signals.failed.connect(self._music_refused)
        self.music_job = job
        QThreadPool.globalInstance().start(job)

    def _music_access(self, result):
        token, allowed = result
        if token != self.music_check:  # a late answer for a key replaced since
            return
        self.music_token = token if allowed else None
        self.music_b.setVisible(allowed)
        self.music_a.setVisible(allowed)

    def _music_refused(self, _error: str):
        self._music_access((self.music_check, False))

    def _open_music(self):
        if not self.music_token or self.job or self.music_dialog is not None:
            return
        from .music_dialog import MusicDialog

        # open() rather than exec(): PySide6 crashed closing a nested exec() of this dialog
        self.music_dialog = MusicDialog(self, self.music_token, Job)
        self.music_dialog.finished.connect(self._music_closed)
        self.music_dialog.open()

    def _music_closed(self, result):
        dialog, self.music_dialog = self.music_dialog, None
        if result == QDialog.DialogCode.Accepted and dialog.path:
            self.open_files([dialog.path])
        dialog.deleteLater()

    def _about(self):
        QMessageBox.about(self, _("О программе Count-In"), (
            f"<h3>Count-In v{update.current_version()}</h3>"
            f"<p>{_('Барабанный отсчёт перед первой сильной долей и бэктреки без гитары или вокала.')}</p>"
            f'<p><a href="{site_url("changelog/")}">{_("Что нового")}</a> · '
            f'<a href="https://github.com/{update.REPO}">github.com/{update.REPO}</a></p>'
            "<p>beat_this (MIT), BS-RoFormer (MIT), FFmpeg (LGPL), Qt for Python (LGPL), PyTorch (BSD).</p>"))

    def _whats_new(self, seen: str | None):
        """Releases after `seen`, or the latest one, with a link to the full changelog on the site."""
        cur = update.current_version()
        releases = changes.since(seen, cur) if seen else [r for r in changes.RELEASES
                                                          if changes.version_key(r[0]) <= changes.version_key(cur)][:1]
        i = 0 if i18n.language() == "en" else 1
        body = "".join(f"<h3>{v}</h3><ul>" + "".join(f"<li>{changes.rich(it[i])}</li>" for it in items) + "</ul>"
                       for v, _date, items in releases)
        body += f'<p><a href="{site_url("changelog/")}">{_("Все изменения на сайте")}</a></p>'
        d = QDialog(self, windowTitle=_("Что нового в Count-In"))
        d.resize(560, 520)
        v = QVBoxLayout(d)
        text = QTextBrowser(openExternalLinks=True)
        text.setHtml(body)
        v.addWidget(text)
        ok = QPushButton(_("Готово"), default=True, clicked=d.accept)
        h = QHBoxLayout()
        h.addStretch()
        h.addWidget(ok)
        v.addLayout(h)
        d.exec()

    # --- updates ---

    def _change_update_channel(self, channel: str):
        QSettings().setValue(UPDATE_CHANNEL_KEY, channel)
        if self.staged:
            update.discard(self.staged)
            self.staged = None
        self.release = None
        self._show_version()
        self.check_updates()

    def check_updates(self, manual: bool = False):
        """Asks GitHub in a daemon thread; the answer comes back to the UI thread as a queued signal."""
        if self._update_busy:
            return
        self._update_busy = True
        self._manual_check = manual
        self._checked_channel = QSettings().value(UPDATE_CHANNEL_KEY, update.default_channel())
        self._update_menu(_("Проверка обновлений…"), enabled=False)
        threading.Thread(target=self._check_in_thread, args=(self._checked_channel,), daemon=True).start()

    def _check_in_thread(self, channel: str):
        try:
            release = update.check(channel)
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
        self._update_busy = False
        if self._checked_channel != QSettings().value(UPDATE_CHANNEL_KEY, update.default_channel()):
            self.check_updates()
            return
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
            self._update_busy = True
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
        self._emit(self.updateReady, (release, result))

    def _update_progress(self, pct: int):
        self._update_menu(_("Загрузка обновления… {p}%", p=pct), enabled=False)

    def _update_ready(self, response):
        release, result = response
        self._update_busy = False
        if self._checked_channel != QSettings().value(UPDATE_CHANNEL_KEY, update.default_channel()):
            if isinstance(result, Path):
                update.discard(result)
            self.check_updates()
            return
        self.release = release
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
        fs, _sel = QFileDialog.getOpenFileNames(self, _("Трек"), "", _("Аудио ({exts})", exts=exts))
        if fs:
            self.open_files([Path(f) for f in fs])  # several files make a set list

    def _open_downloads(self):
        from .music import imports_dir

        directory = imports_dir()
        d = QDialog(self, windowTitle=_("Скачанные песни"))
        d.resize(540, 360)
        v = QVBoxLayout(d)
        files = QListWidget()
        if directory.is_dir():
            for path in sorted(directory.iterdir(), key=lambda p: p.name.casefold()):
                if path.suffix.lower() in AUDIO_EXT and path.is_file():
                    item = QListWidgetItem(path.name, files)
                    item.setData(Qt.UserRole, path)
        if files.count():
            files.setCurrentRow(0)
        else:
            v.addWidget(QLabel(_("Скачанных песен пока нет."), objectName="field"))
        v.addWidget(files)

        def show_folder():
            try:
                directory.mkdir(parents=True, exist_ok=True)
                if QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory))):
                    return
            except OSError:
                pass
            QMessageBox.warning(d, _("Скачанные песни"), _("Не удалось открыть папку загрузок."))

        buttons = QHBoxLayout()
        buttons.addWidget(QPushButton(_("Открыть папку"), objectName="tool", clicked=show_folder))
        buttons.addStretch()
        buttons.addWidget(QPushButton(_("Закрыть"), objectName="tool", clicked=d.reject, autoDefault=False))
        buttons.addWidget(QPushButton(_("Открыть"), objectName="primary", clicked=d.accept,
                                      enabled=bool(files.count()), default=True))
        files.itemDoubleClicked.connect(d.accept)
        v.addLayout(buttons)
        if d.exec() == QDialog.Accepted and files.currentItem():
            self.open_files([files.currentItem().data(Qt.UserRole)])

    def _remember(self, path: Path):
        """Most recent first, eight at most; files that are gone drop out."""
        st = QSettings()
        old = [p for p in st.value(RECENT_KEY, []) or [] if isinstance(p, str)]
        st.setValue(RECENT_KEY, [str(path)] + [p for p in old if p != str(path)][:7])
        self._show_recent()

    def _show_recent(self):
        paths = [Path(p) for p in QSettings().value(RECENT_KEY, []) or [] if isinstance(p, str) and Path(p).is_file()]
        while self.recent_v.count():
            self.recent_v.takeAt(0).widget().deleteLater()
        self.recent_m.clear()
        if paths:
            self.recent_v.addWidget(QLabel(_("Недавние"), objectName="field"))
        for p in paths:
            b = QPushButton(p.stem, objectName="recent", toolTip=str(p), cursor=Qt.PointingHandCursor,
                            clicked=lambda _c=False, p=p: self.open_files([p]))
            self.recent_v.addWidget(b, 0, Qt.AlignLeft)
            self.recent_m.addAction(p.name, lambda p=p: self.open_files([p]))
        self.recent_box.setVisible(bool(paths))
        self.recent_m.setEnabled(bool(paths))

    def open_files(self, paths: list[Path]):
        """Opens the first audio file; the app works on one track at a time. Several files at once become a
        set list: N and P go to the next and previous song."""
        songs_in = [p for p in paths if p.is_file() and p.suffix.lower() in AUDIO_EXT]
        if len(songs_in) > 1:
            self.setlist = songs_in
        path = songs_in[0] if songs_in else None
        if not path or self.job:
            if paths and not path:
                self.status.setText(_("Это не аудиофайл. Подходят mp3, FLAC, ALAC, WAV, AIFF, OGG и M4A"))
            return
        self._flush_song()
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
        self._remember(path)
        self.setWindowTitle(path.name)
        self.setWindowFilePath(str(path))  # the file's icon in the macOS title bar
        self.subtitle.setText("")
        self.detector.setText(_("Анализ…"))
        self.show_busy(_("Анализирую трек"), _(BUSY_WHY["analysis"]), None, _("Пожалуйста, подождите…"))
        try:
            self.info = io.probe(path)
            self._show_file()
        except RuntimeError as e:
            self.file_l.setText(str(e)[:120])
        self.analysis_job = Job(analyze_job, t, self.meter_seg.value())
        self.analysis_job.signals.done.connect(self._analyzed)
        self.analysis_pool.start(self.analysis_job)
        self._enable()

    def _show_file(self):
        i = self.info
        tags = i.tags
        who = " — ".join(x for x in (tags.get("artist"), tags.get("album")) if x)
        year = (tags.get("date") or "")[:4]
        self.subtitle.setText(who + (f" ({year})" if year and who else year))
        line = io.describe(i)
        self.file_l.setText(self.file_l.fontMetrics().elidedText(line, Qt.ElideRight, 250 - 28))
        self.file_l.setToolTip(line)
        self.length_l.setText(fmt_time(i.duration)[:-4])

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
        t.sections = list(t.proposed)
        self._show_detector()
        notes = [translate(n) for n in a.notes]
        if a.manual:
            notes.append(_("Ритм не определён. Выставь BPM и маркер «1» вручную."))
        elif a.confidence < 0.4:
            notes.insert(0, _("детектор не уверен, проверьте маркер «1» на слух"))
        self.warning.setText("; ".join(notes))
        self.warning.setToolTip("; ".join(notes))

        self.alt_b.setVisible(a.alt_downbeat is not None)
        self._restore_song(t)
        self._regrid()
        self._show_detector()
        self.wave.markers = t.markers
        self.undo_stack, self.redo_stack, self.undo_base = [], [], self._edit_state()
        self._show_track_values()
        bar = 60.0 / t.bpm * rd.meter_shape(self.settings().meter)[0]
        self.wave.show_range(t.t0 - bar * self.bars_seg.value() - 1.0, t.t0 + 6.0)
        self._enable()
        if isinstance(QApplication.focusWidget(), QAbstractSpinBox):
            QApplication.focusWidget().clearFocus()  # a new track starts with plain numbers, no blinking cursor
        self._start_separation()
        self._auto_zones()

    def _show_detector(self):
        """Tempo, the "1", confidence and the key; with a transposition the key it now plays in."""
        t = self.current
        if not (t and t.analysis):
            return
        a = t.analysis
        text = _("Детектор: {bpm:.2f} BPM · «1» на {t} · уверенность {conf:.0%}",
                 bpm=a.bpm, t=fmt_time(a.first_downbeat), conf=a.confidence)
        if t.sections:
            sec = t.sections[min(self.section_i, len(t.sections) - 1)]
            origin = _("Вручную") if sec.fixed or sec.meter_manual else _("Авто") if sec.certain else _("Темп приблизительный")
            text = _("{origin} · {bpm:.1f} BPM · {meter}", origin=origin, bpm=sec.bpm,
                     meter=sec.meter if sec.meter_known else sec.meter + "?")
        self.detector.setText(text if t.sections or not a.manual else "")
        key = "—"
        if t.tonal:
            key = an.key_name(*t.tonal[:2])
            if self.semis:
                key += "  →  " + an.key_name(*t.tonal[:2], self.semis)
        self.key_l.setText(key)
        if t.bars:
            self.length_l.setText(_("{time} · тактов: {n}", time=fmt_time(t.duration)[:-4], n=len(t.bars)))

    def _song_changed(self, *_args):
        if self.current and self.current.analysis and not self.restoring:
            self.song_timer.start()
            self.undo_timer.start()

    def _edit_state(self) -> tuple:
        """What undo brings back: tempo, the "1", the pauses the user made or edited, removed pauses, markers."""
        t = self.current
        kept = tuple((z.first, z.last, z.bpm, z.beats, z.manual, z.start, z.end, z.free, z.count_in)
                     for z in t.zones or [] if z.kept)
        return (t.bpm, t.bpm_manual, t.t0, kept,
                tuple(t.dismissed_times), tuple(t.markers), tuple(t.sections), t.edits, t.cut_fade_ms, t.saved_loops)

    def _snap_undo(self):
        if not (self.current and self.current.analysis):
            return
        now = self._edit_state()
        if self.undo_base is not None and now != self.undo_base:
            self.undo_stack.append(self.undo_base)
            self.redo_stack.clear()
        self.undo_base = now

    def _undo(self, redo: bool = False):
        if self.undo_timer.isActive():
            self.undo_timer.stop()
            self._snap_undo()
        src, dst = (self.redo_stack, self.undo_stack) if redo else (self.undo_stack, self.redo_stack)
        if not (self.current and src):
            return
        dst.append(self.undo_base)
        self.undo_base = state = src.pop()
        t = self.current
        t.bpm, t.bpm_manual, t.t0 = state[0], state[1], state[2]
        t.zones = [zn.Zone(*row) for row in state[3]]
        t.dismissed_times, t.sections = list(state[4]), list(state[6])
        t.markers = list(state[5])
        t.edits = state[7]
        t.cut_fade_ms, t.saved_loops = state[8:10]
        self.wave.markers = t.markers
        self.restoring = True  # the views below report changes that are this very step
        try:
            self._regrid()
            self._show_track_values()
            self._refresh()
        finally:
            self.restoring = False
        self.song_timer.start()
        self._auto_zones()
        self._restart_preview()

    def _flush_song(self):
        if self.song_timer.isActive():
            self.song_timer.stop()
            self._save_song()

    def _save_song(self):
        t = self.current
        if not (t and t.key):
            return
        kept = [z for z in t.zones or [] if z.kept]
        state = {
            "rhythm_version": 1, "sections": [vars(s) for s in t.sections],
            "dismissed_times": t.dismissed_times,
            "bpm": t.bpm, "bpm_manual": t.bpm_manual, "t0": t.t0,
            "style": self.style_seg.value() if self.style_seg.value() != "midi" else "buildup",
            "bars": self.bars_seg.value(), "meter": self.settings().meter, "volume": self.volume.value(),
            COUNT_VOLUME_KEY: self.volume.value(), "count_voice": self.count_voice.currentData(),
            "parts": {k: s.value() for k, s in self.parts.items()}, "speed": self.speed, "semis": self.semis,
            "click_all": self.click_all.isChecked(), "click_voice": self.click_voice.currentData(),
            "click_db": self.click_db.value(), "fill": self.fill.isChecked(),
            "zones": [{k: v for k, v in vars(z).items() if k != "count_in"} for z in kept],
            "pause_count_ins": [i for i, z in enumerate(kept) if z.count_in], "dismissed": sorted(t.dismissed),
            "loop": list(self.loop) if self.loop else None, "loop_rate": self.loop_rate,
            "saved_loops": [vars(loop) for loop in t.saved_loops],
            "cut_fade_ms": t.cut_fade_ms, "markers": [list(m) for m in t.markers],
            "audio_edits": [vars(e) for e in t.edits], "selection": self.wave.sel,
        }
        try:
            songs.save(t.key, state)
        except OSError:
            log.exception("song settings not saved")

    def _restore_song(self, t: Track):
        """Puts back what was set for this song last time; a value that no longer fits is skipped."""
        d = songs.load(t.key) if t.key else None
        if not d:
            return
        self.restoring = True
        try:
            t.bpm, t.bpm_manual, t.t0 = float(d["bpm"]), bool(d.get("bpm_manual")), float(d["t0"])
            for seg, k in ((self.style_seg, "style"), (self.bars_seg, "bars"), (self.meter_seg, "meter")):
                if d.get(k) in seg.values:
                    seg.set_value(d[k])
            self.volume.setValue(count_volume(d.get(COUNT_VOLUME_KEY), d.get("volume", 0)))
            voice = self.count_voice.findData(d.get("count_voice"))
            if voice >= 0:
                self.count_voice.setCurrentIndex(voice)
            for k, v in (d.get("parts") or {}).items():
                if k in self.parts:
                    self.parts[k].setValue(int(v))
            self._set_speed(int(d.get("speed", 100)))
            self._set_semis(int(d.get("semis", 0)))
            self.click_all.setChecked(bool(d.get("click_all")))
            i = self.click_voice.findData(d.get("click_voice"))
            if i >= 0:
                self.click_voice.setCurrentIndex(i)
            self.click_db.setValue(int(d.get("click_db", self.click_db.value())))
            self.fill.setChecked(bool(d.get("fill", True)))
            saved = tempo.from_json(d.get("sections"), t.duration)
            if saved:
                t.sections = saved
            else:
                lead = tempo.index(t.sections, t.t0)
                t.sections[lead] = replace(t.sections[lead], anchor=t.t0)
                if t.bpm_manual and 30 <= t.bpm <= 320:
                    t.sections = [tempo.Section(0, t.bpm, self.meter_seg.value(), t.t0, meter_known=True)]
                elif d.get("meter") in rd.METERS:
                    t.sections = [replace(s, meter=d["meter"]) if not s.meter_known else s for s in t.sections]
            old = t.bars  # the detector grid whose indices were used by the legacy file
            t.zones = []
            for i, row in enumerate(d.get("zones") or []):
                z = zn.Zone(**row)
                if i in (d.get("pause_count_ins") or []):
                    z.count_in = True
                if z.start is not None and z.end is not None and 0 <= z.start < z.end <= t.duration + 1e-6:
                    t.zones.append(z)
                elif 0 <= z.first <= z.last < len(old):
                    a, b = zn.bounds(old, z)
                    t.zones.append(replace(z, start=a, end=b))
            if "dismissed_times" in d:
                t.dismissed_times = [(a, b) for a, b in d["dismissed_times"] if 0 <= a < b <= t.duration + 1e-6]
            else:
                t.dismissed_times = [(old[i].start, old[i].end) for i in d.get("dismissed") or [] if 0 <= i < len(old)]
            t.covered = _bars_of(t.zones)
            t.markers = [(float(a), str(b)) for a, b in d.get("markers") or []]
            t.edits = timeline.from_json(d.get("audio_edits"), t.duration)
            fade = d.get("cut_fade_ms")
            if isinstance(fade, (int, float)) and 0 <= fade <= 100:
                t.cut_fade_ms = float(fade)
            saved_loops = []
            for row in d.get("saved_loops") or []:
                try:
                    loop = timeline.Loop(**row)
                    if isinstance(loop.name, str) and loop.name and 0 <= loop.start < loop.end <= t.duration and .5 <= loop.speed <= 1.5:
                        saved_loops.append(loop)
                except (TypeError, ValueError):
                    continue
            t.saved_loops = tuple(saved_loops)
            selected = d.get("selection")
            if isinstance(selected, (tuple, list)) and len(selected) == 2 and all(isinstance(x, (int, float)) for x in selected):
                a, b = selected
                if 0 <= a < b <= t.duration:
                    self.wave.sel = (a, b)
                    self._span_selected(False)
            if d.get("loop"):
                self.loop, self.loop_on = tuple(d["loop"]), False
                rate = d.get("loop_rate")
                self.loop_rate = float(rate) if isinstance(rate, (int, float)) and .5 <= rate <= 1.5 else None
                self._apply_loop()
            self.status.setText(_("Настройки этой песни восстановлены"))
        except (KeyError, TypeError, ValueError):
            log.exception("remembered song settings skipped")
        finally:
            self.restoring = False

    def _show_track_values(self):
        t = self.current
        self._section_values()
        self.t0_spin.blockSignals(True)
        self.t0_spin.setValue(t.t0)
        self.t0_spin.blockSignals(False)
        self._refresh()

    # --- edits ---

    def _section_values(self):
        t = self.current
        if not t or not t.sections:
            return
        self.section_i = min(self.section_i, len(t.sections) - 1)
        section = t.sections[self.section_i]
        self.bpm_spin.blockSignals(True)
        self.bpm_spin.setRange(min(30., section.bpm), max(320., section.bpm))
        self.bpm_spin.setValue(section.bpm)
        self.bpm_spin.blockSignals(False)
        standard = section.meter in self.meter_seg.values
        for button in self.meter_seg.group.buttons():
            button.setVisible(standard)
        self.imported_meter_l.setVisible(not standard)
        if standard:
            self.meter_seg.set_value(section.meter)
        else:
            self.imported_meter_l.setText(section.meter)
        self.lane.set_sections(t.sections, self.section_i)

    def _regrid(self):
        t = self.current
        kept = [(z, zn.bounds(t.bars, z)) for z in t.zones or [] if z.kept]
        t.bars = tempo.bars(t.sections, t.analysis.beats, t.t0, t.duration)
        t.bpm = t.sections[tempo.index(t.sections, t.t0)].bpm
        t.zones = [zn.mapped(z, t.bars, a, b) for z, (a, b) in kept] if t.bars else []
        t.dismissed = {i for i, bar in enumerate(t.bars)
                       if any(a <= (bar.start + bar.end) / 2 < b for a, b in t.dismissed_times)}
        t.covered = _bars_of(t.zones)
        t.zones_key = None
        self.mix_seq += 1
        if t.energy is not None and len(t.energy) > 1:
            scale = self.sr / max(1, self.sr // 100)
            edges = np.clip(np.rint(np.array([b.start for b in t.bars] + [t.duration]) * scale).astype(int),
                            0, len(t.energy) - 1)
            power = np.diff(t.energy[edges]) / np.maximum(1, np.diff(edges))
            t.bar_db = (10 * np.log10(np.maximum(power, 1e-24)) - t.ref_db).tolist()
        self.wave.bars = t.bars
        self.wave.beats = [p for b in t.bars for p in b.beats]
        self.wave.downs = [b.start for b in t.bars if b.accent]
        self.wave.current_zone = None
        self._section_values()

    def _set_map(self, sections, t0=None, markers=None):
        t = self.current
        if not sections or (sections == t.sections and (t0 is None or t0 == t.t0)
                            and (markers is None or markers == t.markers)):
            return
        old_lead = min((c.time for c in self.wave.clicks), default=self.current.t0)
        self._snap_undo()
        if t0 is not None:
            t.t0 = t0
        if markers is not None:
            t.markers = self.wave.markers = markers
        t.sections = sections
        self._regrid()
        self._show_track_values()
        self._show_detector()
        self._snap_undo()
        self._auto_zones()
        if self.play_start is not None:
            new_lead = min((c.time for c in self.wave.clicks), default=self.current.t0)
            if new_lead < min(old_lead, self.play_start) - .001:
                self._restart_preview()
            else:
                self._swap_clicks()

    def _import_guitar_pro(self):
        t = self.current
        if not (t and t.analysis and self.pcm is not None) or self.job:
            return
        title = _("Импорт темпотрека Guitar Pro")
        types = " ".join("*" + ext for ext in sorted(guitar_pro.EXTENSIONS))
        name, _filter = QFileDialog.getOpenFileName(self, title, str(t.path.parent), f"Guitar Pro ({types})")
        if not name:
            return
        try:
            score = guitar_pro.read(Path(name))
        except Exception as e:
            log.warning("Guitar Pro import failed: %s", e)
            QMessageBox.warning(self, title, _("Не удалось прочитать Guitar Pro: {error}", error=e))
            return

        dialog = QDialog(self, windowTitle=title)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(_("Совместите такт табулатуры с нужным местом записи. Можно щёлкнуть по волне или ввести время."),
                                wordWrap=True))
        fields = QGridLayout()
        measure = QSpinBox(minimum=1, maximum=len(score.starts), value=2 if score.pickup else 1)
        at = QDoubleSpinBox(minimum=0, maximum=max(0., t.duration - .001), decimals=3, singleStep=.01,
                            value=max(0., self.wave.play_t if self.wave.play_t is not None else t.t0))
        second = QCheckBox(_("Вторая точка привязки"), enabled=len(score.starts) > 1)
        first_point = QRadioButton(_("Точка 1"), checked=True)
        second_point = QRadioButton(_("Точка 2"), enabled=False)
        last_marker = score.markers[-1][0] if score.markers else score.starts[-1]
        if last_marker <= score.starts[measure.value() - 1]:
            last_marker = score.starts[-1]
        later = QSpinBox(minimum=1, maximum=len(score.starts), value=score.starts.index(last_marker) + 1, enabled=False)
        later_at = QDoubleSpinBox(minimum=0, maximum=at.maximum(), decimals=3, singleStep=.01, enabled=False)
        fields.addWidget(QLabel(_("Такт табулатуры по порядку звучания")), 0, 1)
        fields.addWidget(QLabel(_("Место в записи, секунды")), 0, 2)
        for row, (point, bar, seconds) in enumerate(((first_point, measure, at), (second_point, later, later_at)), 1):
            fields.addWidget(point, row, 0)
            fields.addWidget(bar, row, 1)
            fields.addWidget(seconds, row, 2)
        fields.addWidget(second, 3, 0, 1, 3)
        layout.addLayout(fields)

        class AlignmentWave(Wave):
            def mousePressEvent(wave, event):
                if event.button() == Qt.LeftButton:
                    (later_at if second_point.isChecked() else at).setValue(wave.t_of(event.position().x()))

        wave = AlignmentWave()
        wave.setToolTip(_("Щелчок по волне совмещает с этим местом выбранный такт."))
        wave.setMinimumWidth(620)
        wave.env, wave.peak, wave.duration = self.wave.env, self.wave.peak, t.duration
        lane = wave.lane = TempoLane(wave)
        lane.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(lane)
        layout.addWidget(wave)
        summary = QLabel(wordWrap=True, textFormat=Qt.PlainText)
        layout.addWidget(summary)
        if score.has_jumps or score.has_ramps:
            details = []
            if score.has_jumps:
                details.append(_("Переходы D.C., D.S. и Coda читаются в порядке записи тактов."))
            if score.has_ramps:
                details.append(_("Плавные изменения темпа импортируются как ступенчатые."))
            layout.addWidget(QLabel(" ".join(details), wordWrap=True))
        buttons = QHBoxLayout()
        buttons.addStretch()
        apply = QPushButton(_("Применить"), objectName="primary", default=True, clicked=dialog.accept)
        add_buttons(buttons, QPushButton(_("Отмена"), objectName="tool", clicked=dialog.reject), apply)
        layout.addLayout(buttons)

        def anchors():
            return (later.value() - 1, later_at.value()) if second.isChecked() else None

        def update_preview(_value=None):
            other = anchors()
            ordered = other is None or (other[0] > measure.value() - 1 and other[1] > at.value())
            end = 0.
            try:
                bar, time = measure.value() - 1, at.value()
                sections, first, marks = score.aligned(bar, time, t.duration, other)
                offset, scale = score.alignment(bar, time, other)
                end = score.duration * scale + offset
            except ValueError:
                sections, first, marks = [], None, []
            bars = tempo.bars(sections, (), first, t.duration) if sections else []
            wave.t0, wave.bars, wave.markers = first, bars, marks
            wave.beats = [beat for bar in bars for beat in bar.beats]
            wave.downs = [bar.start for bar in bars if bar.accent]
            focus_at = later_at.value() if second_point.isChecked() else at.value()
            wave.play_t = focus_at
            lane.set_sections(sections, tempo.index(sections, focus_at) if sections else -1)
            wave.show_range(focus_at - 4, focus_at + 8)
            summary.setText(_("В записи: участков — {sections}, меток — {markers}. Конец табулатуры — {end}.",
                              sections=len(sections), markers=len(marks), end=fmt_short(end)) if bars else
                            _("Выберите привязку, при которой сетка попадает в запись.") if ordered else
                            _("Вторая точка должна быть позже первой и в табулатуре, и в записи."))
            apply.setEnabled(bool(bars))

        def enable_second(on):
            for control in (second_point, later, later_at):
                control.setEnabled(on)
            (second_point if on else first_point).setChecked(True)
            update_preview()

        later_at.setValue(at.value() + score.starts[later.value() - 1] - score.starts[measure.value() - 1])
        for control in (measure, at, later, later_at):
            control.valueChanged.connect(update_preview)
        first_point.toggled.connect(update_preview)
        second_point.toggled.connect(update_preview)
        second.toggled.connect(enable_second)
        update_preview()
        if dialog.exec() != QDialog.Accepted or t is not self.current:
            return
        sections, first, marks = score.aligned(measure.value() - 1, at.value(), t.duration, anchors())
        previous = score.previous_markers(t.sections, t.duration)
        combined = [(stamp, label) for stamp, label in t.markers
                    if not any(abs(stamp - old) <= .001 and label == text for old, text in previous)]
        for stamp, label in marks:
            if not any(abs(stamp - old) <= .001 and label == text for old, text in combined):
                combined.append((stamp, label))
        self._set_map(sections, t0=first, markers=sorted(combined))
        self.wave.show_range(at.value() - 4, at.value() + 8)
        self.status.setText(_("Темпотрек Guitar Pro импортирован"))

    def _select_section(self, i):
        self.section_i = i
        self.tempo_span = self._section_bounds()
        self._section_values()
        self._show_detector()
        self._speed_bpm()

    def _edit_tempo_section(self, i, position, span=None):
        t = self.current
        if not t or not 0 <= i < len(t.sections):
            return
        self._select_section(i)
        sections = t.sections
        section = sections[i]
        a, b = span or self._section_bounds()

        popup = QDialog(self, Qt.Popup)
        popup.setAttribute(Qt.WA_DeleteOnClose)
        popup.setWindowTitle(_("Темп и размер"))
        layout = QVBoxLayout(popup)
        layout.setContentsMargins(0, 0, 0, 0)
        panel, grid = card(_("Темп и размер"))
        panel.setMinimumWidth(280)
        layout.addWidget(panel)
        grid.addWidget(QLabel(f"{fmt_short(a)} – {fmt_short(b)}", objectName="field"), 0, 0, 1, 2)
        bpm = QDoubleSpinBox(minimum=min(30., section.bpm), maximum=max(320., section.bpm), decimals=2,
                             singleStep=.1, value=section.bpm)
        bpm.setAccessibleName("BPM")
        bpm.setStyleSheet("QDoubleSpinBox { font-size: 22px; font-weight: 650; }")
        initial_bpm = bpm.value()
        unit = QLabel("♩. BPM" if rd.meter_shape(section.meter)[1] == 3 else "BPM", objectName="field")
        grid.addWidget(unit, 1, 0)
        grid.addWidget(bpm, 1, 1)
        meter = QComboBox()
        meter.addItems(list(rd.METERS))
        if section.meter not in rd.METERS:
            meter.addItem(section.meter)
        meter.setCurrentText(section.meter)
        meter.setAccessibleName(_("Размер участка"))
        grid.addWidget(QLabel(_("Размер участка"), objectName="field"), 2, 0)
        grid.addWidget(meter, 2, 1)
        meter_touched = False

        def choose_meter(_index):
            nonlocal meter_touched
            meter_touched = True
            unit.setText("♩. BPM" if rd.meter_shape(meter.currentText())[1] == 3 else "BPM")

        meter.activated.connect(choose_meter)

        def tap():
            popup.reject()
            QTimer.singleShot(0, lambda: self._tap_span(t, (a, b)) if t.sections is sections else None)

        grid.addWidget(QPushButton(_("Настучать темп участка"), objectName="tool", clicked=tap), 3, 0, 1, 2)
        buttons = QHBoxLayout()
        buttons.addStretch()
        add_buttons(buttons,
                    QPushButton(_("Отмена"), objectName="tool", clicked=popup.reject),
                    QPushButton(_("Готово"), objectName="primary", default=True, clicked=popup.accept))
        grid.addLayout(buttons, 4, 0, 1, 2)

        def apply():
            if t is not self.current or sections is not t.sections:
                return
            bpm.interpretText()
            new_bpm = bpm.value() if bpm.value() != initial_bpm else None
            new_meter = meter.currentText() if meter_touched else None
            if new_bpm is not None or new_meter is not None:
                self._set_map(tempo.edit_span(sections, a, b, t.duration, bpm=new_bpm, meter=new_meter, grid=t.bars))

        popup.accepted.connect(apply)
        popup.adjustSize()
        screen = QApplication.screenAt(position) or self.screen()
        area = screen.availableGeometry()
        x = min(max(position.x(), area.left()), max(area.left(), area.right() - popup.width() + 1))
        y = position.y() + 8
        if y + popup.height() > area.bottom():
            y = position.y() - self.lane.height() - popup.height()
        popup.move(x, max(area.top(), y))
        popup.show()
        bpm.setFocus()
        bpm.selectAll()

    def _split_section(self, at):
        t = self.current
        if t and t.sections and 0 < at < t.duration:
            sections = tempo.split(t.sections, at, t.duration)
            self.section_i, self.tempo_span = tempo.index(sections, at), None
            self._set_map(sections)
            self.tempo_span = self._section_bounds()

    def _move_section(self, i, at):
        t = self.current
        if not t or not 0 < i < len(t.sections):
            return
        end = t.sections[i + 1].start if i + 1 < len(t.sections) else t.duration
        at = float(np.clip(at, t.sections[i - 1].start + .02, end - .02))
        if t.sections[i - 1].start < at < end:
            self._set_map([*t.sections[:i], replace(t.sections[i], start=at), *t.sections[i + 1:]])
            self._select_section(i)

    def _join_sections(self, first, last):
        t = self.current
        if t and 0 <= first < last < len(t.sections):
            self.section_i = first
            self._set_map([*t.sections[:first + 1], *t.sections[last + 1:]])
            self._select_section(first)

    def _shift_section(self, i, dt):
        t = self.current
        if t and t.sections and abs(dt) > 1e-6:
            s = t.sections[i]
            self._select_section(i)
            shift = float(np.clip(s.shift + dt, -t.duration, t.duration))
            self._set_map([*t.sections[:i], replace(s, shift=shift), *t.sections[i + 1:]])

    def _wave_phase(self, at, dt):
        if self.current and self.current.sections:
            self._shift_section(tempo.index(self.current.sections, at), dt)

    def _section_bounds(self):
        t, i = self.current, self.section_i
        return t.sections[i].start, t.sections[i + 1].start if i + 1 < len(t.sections) else t.duration

    def _bpm_edited(self, v: float):
        t = self.current
        if t and t.sections and not self.restoring:
            a, b = self._section_bounds()
            self._set_map(tempo.edit_span(t.sections, a, b, t.duration, bpm=v, grid=t.bars))

    def _scale_bpm(self, k: float):
        self._bpm_edited(float(np.clip(self.bpm_spin.value() * k, 30, 320)))

    def _tap(self):
        """Opens the tap window over the playing song; Apply sets the tempo."""
        t = self.current
        if not (t and t.analysis and self.pcm is not None):
            return
        span = self.wave.sel or (self.loop if self.loop_on else None) or self.tempo_span
        if not span:
            at = self._here()
            at = t.t0 if at is None else max(0, at)
            i = self.wave._bar_at(at)
            if i is None:
                span = (at, min(t.duration, at + 8 * t.sections[tempo.index(t.sections, at)].period))
            else:
                span = (t.bars[i].start, t.bars[min(len(t.bars) - 1, i + 3)].end)
        self._tap_span(t, span)

    def _tap_span(self, t: Track, span: tuple[float, float]):
        if t is not self.current or not t.sections or self.pcm is None:
            return
        a, b = max(0.0, span[0]), min(t.duration, span[1])
        if b - a < .02:
            return
        sections = t.sections
        settings = self._playback_settings()
        speed, prepared = settings.speed, None
        if t.edits or settings.practice or settings.training:
            clock = edit_clock(t, settings)
            lo, hi = round(a * self.sr), round(b * self.sr)
            length = clock.output_at(hi / self.sr) - clock.output_at(lo / self.sr)
            if length < .02:
                return
            audible = sum(end - start for start, end, _ in clock.slices(lo / self.sr, hi / self.sr))
            speed = audible / length
            prepared, = clock.audio([self.pcm[lo:hi]], self.sr, lo / self.sr, hi / self.sr, 0.0)
        self._stop()
        s = t.sections[tempo.index(t.sections, a)]
        d = TapDialog(self, self.pcm, self.sr, a, b, s.bpm, speed, prepared)
        if d.exec() == QDialog.Accepted and d.valid and t is self.current and t.sections is sections:
            updated = tempo.edit_span(sections, a, b, t.duration, bpm=d.bpm, grid=t.bars)
            self.section_i = tempo.index(updated, a)
            self.tempo_span = (a, b)
            self._set_map(updated)

    def _meter_changed(self, meter: str):
        t = self.current
        if t and t.sections and not self.restoring:
            a, b = self._section_bounds()
            self._set_map(tempo.edit_span(t.sections, a, b, t.duration, meter=meter, grid=t.bars))

    def _t0_edited(self, v: float):
        self._marker_moved(v)

    def _marker_moved(self, t0: float):
        t = self.current
        if t and t.sections:
            at = float(np.clip(t0, 0, max(0, t.duration - .001)))
            i = tempo.index(t.sections, at)
            sec = replace(t.sections[i], anchor=at, shift=0)
            self._set_map([*t.sections[:i], sec, *t.sections[i + 1:]], t0=at)

    def _shift_beat(self, k: int):
        if self.current:
            self._marker_moved(self.current.t0 + k * 60.0 / self.current.bpm)

    def _reset_tempo(self):
        """The tempo the detector found, for the meter now chosen."""
        t = self.current
        if t and t.proposed:
            i = self.section_i
            own = t.sections[i]
            detected = t.proposed[tempo.index(t.proposed, own.start)]
            self._set_map([*t.sections[:i], replace(detected, start=own.start), *t.sections[i + 1:]])

    def _reset_t0(self):
        """The first downbeat the detector found."""
        t = self.current
        if t and t.analysis:
            self._marker_moved(t.analysis.first_downbeat)

    def _reset_count(self):
        """The count-in from the saved defaults, or the factory one when none are saved."""
        st = QSettings()
        saved = st.value(DEFAULTS + "style") is not None
        for seg, key, cast, factory in ((self.style_seg, "style", str, "buildup"), (self.bars_seg, "bars", int, 2)):
            try:
                v = cast(st.value(DEFAULTS + key)) if saved else factory
            except (TypeError, ValueError):
                v = factory
            seg.set_value(v if v in seg.values else factory)
        self.volume.setValue(count_volume(st.value(DEFAULTS + COUNT_VOLUME_KEY), st.value(DEFAULTS + "volume", 0))
                             if saved else 0)
        voice = self.count_voice.findData(st.value(DEFAULTS + "count_voice", COUNT_VOICE_DEFAULT))
        self.count_voice.setCurrentIndex(voice if voice >= 0 else self.count_voice.findData(COUNT_VOICE_DEFAULT))

    def _use_alt(self):
        if self.current and self.current.analysis.alt_downbeat is not None:
            self._marker_moved(self.current.analysis.alt_downbeat)

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
        self.player.gains["click"] = 0.0 if self.click_mute.isChecked() else 1.0
        self._song_changed()

        # A preview started with all parts as they are has no stems to turn down: rebuild it with them. With
        # the pause fill on, the pause search separates the whole song anyway and rebuilds the preview after,
        # so a second, excerpt-only separation would only cost time.
        if (self.play_start is not None and not self.player.src[1].keys() - {"click"}
                and any(g < 1 for k, g in self.player.gains.items() if k != "click")
                and not (self._filling() and self._full_stems() is None) and not cloud_on()):
            self._restart_preview(separate=True)

    def _labels(self):
        self.volume_l.setText(_("{v:+d} дБ", v=self.volume.value()))
        self.click_db_l.setText(_("{v:+d} дБ", v=self.click_db.value()))
        self.threshold_l.setText(_("{v:+d} дБ", v=self.threshold.value()))
        for s, l in ((self.parts[n], self.part_l[n]) for n in self.parts):
            v = s.value()
            l.setText(_("как есть") if v == 0 else _("убрать") if v == MUTE else _("{v:+d} дБ", v=v))

    def settings(self) -> Settings:
        levels = {}
        for name, s in self.parts.items():
            if s.value() != 0:
                levels[name] = float("-inf") if s.value() == MUTE else float(s.value())
        out_root, group = output_prefs()
        t = self.current
        lead_meter = t.sections[tempo.index(t.sections, t.t0)].meter if t and t.sections else self.meter_seg.value()
        return Settings(
            style=self.style_seg.value(), bars=self.bars_seg.value(), meter=lead_meter,
            pattern=self.pattern, volume_db=float(self.volume.value()),
            levels=levels, pre_roll="trim" if self.trim.isChecked() else "keep", fmt=export_format(),
            out_root=out_root, group=group, fill=self._filling(), click_all=self.click_all.isChecked(),
            click_voice=self.click_voice.currentData(), click_db=float(self.click_db.value()), fill_build=self.fill_build.isChecked(),
            threshold=float(self.threshold.value()), speed=self.speed / 100, semis=float(self.semis),
            split=pref_bool(EXPORT_SPLIT, False), normalize=pref_bool(EXPORT_NORM, False),
            count_voice=self.count_voice.currentData(),
        )

    def _playback_settings(self) -> Settings:
        s = self.settings()
        if self.loop_on and self.loop and self.audition_end is None:
            if self.loop_rate is not None:
                s.practice = timeline.Edit(*self.loop, self.loop_rate)
            if self.trainer_steps:
                s.training = (*self.loop, self.trainer_steps)
        return s

    def _reset_trainer(self, _on: bool = False):
        self.player.laps = 0
        if self.trainer_steps:
            self.trainer_steps = 0
            self._refresh()
            self._restart_preview()

    def _advance_trainer(self):
        self.player.laps = 0
        t = self.current
        before = edit_clock(t, self._playback_settings())
        if not any(rate < 1 - 1e-8 for _, _, rate in before.slices(*self.loop)):
            return
        self.trainer_steps += 1
        self._refresh()
        self._restart_preview()
        after = edit_clock(t, self._playback_settings())
        rates = [round(rate * 100) for _, _, rate in after.slices(*self.loop)]
        shown = str(min(rates)) if min(rates) == max(rates) else f"{min(rates)}–{max(rates)}"
        self.status.setText(_("Тренажёр: скорость {pct}%", pct=shown))

    def _refresh(self, *_args):
        self._song_changed()
        self._speed_bpm()
        t = self.current
        if not t or not t.analysis:
            return
        try:
            s = self._playback_settings()
            if audible_edits(t, s):
                clock = edit_clock(t, s)
                clicks = [replace(c, time=clock.source_at(c.time)) for c in edited_clicks(t, s, clock)]
            else:
                clicks = clicks_for(t, s)
            if clicks != self.wave.clicks:  # a new list redraws the cached click layer
                self.wave.clicks = clicks
        except Exception as e:  # unreadable or empty MIDI pattern
            self.wave.clicks = []
            self.status.setText(str(e))
        self.wave.t0 = t.t0
        self.wave.zones = t.zones or []
        self.wave.edits = t.edits
        self.wave.fill = self._filling()
        self.wave.threshold_db = float(self.threshold.value())
        self.wave.ref_db = t.ref_db
        if self.wave.current_zone is not None and self.wave.current_zone >= len(self.wave.zones):
            self.wave.current_zone = None
        self._nav_changed()
        self.wave.update()

    def _threshold_moved(self, v):
        # Only the band on the waveform follows the slider; the pauses get searched again once it stops
        self.wave.threshold_db = float(v)
        self.wave.threshold_full = True
        self.wave.update()
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
        self.click_voice.setEnabled(self.click_all.isChecked())
        self.click_db.setEnabled(self.click_all.isChecked())
        self.threshold.setEnabled(self._filling())
        self.fill_build.setEnabled(self._filling())
        self.save_a.setEnabled(ready and not busy)
        self.select_end_a.setEnabled(ready)
        self.import_gp_a.setEnabled(ready and not busy)
        self.import_gp_b.setEnabled(self.import_gp_a.isEnabled())
        self.add_marker_b.setEnabled(ready and not busy)
        self.open_b.setEnabled(not busy)
        self.downloads_b.setEnabled(not busy)
        self.downloads_a.setEnabled(not busy)
        self.close_b.setEnabled(self.current is not None)
        self.close_a.setEnabled(self.current is not None)
        self.open_a.setEnabled(not busy)
        self.music_b.setEnabled(not busy)
        self.music_a.setEnabled(not busy)
        self.reveal_a.setEnabled(self.last_saved is not None)
        self.match_b.setEnabled(ready)
        self.match_a.setEnabled(ready)
        self.prog_panel.setVisible(busy and self.job_shown)
        self._nav_changed()
        self._show_tips()
        self.wave.update()

    def _show_job(self):
        self.job_shown = self.job is not None
        self._enable()

    def _run(self, fn, *args, on_done):
        self.reveal_b.hide()
        self.job_shown = False
        self.show_timer.start()
        track = args[0]
        snapshot = replace(track, zones=[replace(z) for z in track.zones] if track.zones is not None else None,
                           markers=list(track.markers))
        self.job = Job(fn, snapshot, *args[1:])
        self.job.track_source = track
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
        self.audition_end = None
        self.status.setText(msg)
        QMessageBox.warning(self, "Count-In", msg)

    def _cancelled(self):
        self._finish()
        self.resume_at = None
        self.audition_end = None
        self.status.setText(_("Отменено"))

    def _reset_track(self):
        """Forgets the current track: its audio, stems, waveform and pauses."""
        self.current, self.pcm, self.info, self.stem_cache, self.last_saved = None, None, None, None, None
        self.ready_preview = None
        self.resume_at, self.zones_pending = None, False
        self.wave.play_t, self.wave.current_zone, self.wave.hover_edge = None, None, None
        self.wave.selection_scroll.stop()
        self.wave.drag, self.wave.selection_pointer = None, None
        self.wave.sel, self.wave.edits = None, ()
        self._span_selected(False)
        self.loop, self.loop_on = None, False
        self.loop_rate = None
        self.trainer_steps = 0
        self.audition_end = None
        self.section_i, self.tempo_span = 0, None
        self.lane.drag = None
        self.lane.set_sections([], 0)
        self._apply_loop()
        self._cancel_separation()
        self.backing_key, self.wave_source = None, None
        self.wave.env, self.wave.t0, self.wave.beats, self.wave.downs, self.wave.clicks = None, None, [], [], []
        self.wave.bars, self.wave.zones, self.wave.ref_db, self.wave.markers = [], [], None, []
        self.wave._cache = (None, None)
        self.wave.update()
        for label in (self.subtitle, self.detector, self.warning, self.status):
            label.setText("")
        for label in (self.key_l, self.file_l, self.length_l):
            label.setText("—")
        self.reveal_b.hide()

    def _close_track(self):
        """Back to the drop page; the app keeps running and the models stay loaded. A running job is cancelled
        and cut loose at once, so a new file can open right away instead of waiting for its next step."""
        if self.current is None:
            return
        self._flush_song()
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
        self.setWindowTitle(update.app_name(update.current_version()))
        self.setWindowFilePath("")
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
        if self.loop_on and self.loop:
            return self._play_at(self.loop[0], preroll=True)
        if self.wave.play_t is not None:
            return self._play_at(self.wave.play_t, preroll=True)
        s = self._playback_settings()
        if self._reuse_preview(s):
            return
        self.want_play = True
        self._run(preview_job, self.current, s, self.pcm, self.sr, self.stem_cache, self._separate_now(s),
                  on_done=self._preview_ready)

    def _preview_ready(self, result):
        snapshot, settings = self.job.args[:2]
        self._finish()
        track, base, stems, sr, _source_start, new_cache, clipped, state = result
        if track is not self.current or not self.want_play:
            return
        if new_cache:
            self.stem_cache = new_cache
        s = self._playback_settings()
        if (snapshot.sections is not track.sections or snapshot.edits != track.edits
                or snapshot.cut_fade_ms != track.cut_fade_ms or settings != s):
            self._run(preview_job, track, s, self.pcm, self.sr, self.stem_cache, self._separate_now(s), self.resume_at,
                      on_done=self._preview_ready)
            return
        self.status.setText(clip_note(clipped) or (_("Уровни дорожек можно менять на ходу") if stems else
                            _("Пока дорожки разделяются, играет оригинал") if self.sep_job and s.levels
                            else ""))
        resume, self.resume_at = self.resume_at, None
        preroll, self.preroll = self.preroll, False
        self.ready_preview = state
        self._start_buffer(base, stems, sr, resume, preroll)

    def _start_buffer(self, base, stems, sr, resume, preroll):
        self._gains()
        state = self.ready_preview
        sample = 0 if resume is None else round(state.seconds_at(resume) * sr)
        try:
            self.player.play(base, stems, sr, start=sample if 0 <= sample < len(base) else 0)
        except Exception as e:  # no output device, unsupported channel count
            log.exception("audio output failed")
            self.status.setText(_("Не удалось воспроизвести: {e}", e=e))
            self.ready_preview = None
            return
        self.play_start = state.source_at(0.0)
        if resume is not None:
            self._jump(resume, preroll)
        self._apply_loop()
        self.play_timer.start()
        self._enable()

    def _tick(self):
        if self.player.done:
            self._stop()
            return
        if (self.audition_end is None and self.trainer_a.isChecked() and self.loop_on and self.loop
                and self.player.laps >= TRAINER_LAPS and not self.job):
            self._advance_trainer()
        w = self.wave
        w.cursor_t = self.ready_preview.source_at(self.player.position())
        if self.audition_end is not None and w.cursor_t >= self.audition_end:
            self._stop()
            return

        # The view pages along with the playhead, as in a DAW, unless the user is moving it by hand
        span = w.v1 - w.v0
        if w.drag is None and w.overview.grab_dt is None and not w.v0 <= w.cursor_t <= w.v1 - 0.03 * span:
            w.show_range(w.cursor_t - 0.05 * span, w.cursor_t + 0.95 * span)
        self._nav_changed()
        w.update()

    def _stop(self):
        self.want_play = False
        self.audition_end = None
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
        if out.exists():
            box = QMessageBox(QMessageBox.Question, "Count-In", ask.format(name=out.name), parent=self)
            replace_b = box.addButton(_("Заменить"), QMessageBox.AcceptRole)
            box.setDefaultButton(box.addButton(QMessageBox.Cancel))
            box.exec()
            if box.clickedButton() is not replace_b:
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
        self.out_pct.setText(f"{self.out_volume.value()}%")

    def _save_defaults(self):
        """The current count-in, part levels and pause settings become the start for every track."""
        st = QSettings()
        st.setValue(DEFAULTS + "style", self.style_seg.value() if self.style_seg.value() != "midi" else "buildup")
        st.setValue(DEFAULTS + "bars", self.bars_seg.value())
        st.setValue(DEFAULTS + "meter", self.settings().meter)
        st.setValue(DEFAULTS + "volume", self.volume.value())
        st.setValue(DEFAULTS + COUNT_VOLUME_KEY, self.volume.value())
        st.setValue(DEFAULTS + "count_voice", self.count_voice.currentData())
        st.setValue(DEFAULTS + "click_voice", self.click_voice.currentData())
        st.setValue(DEFAULTS + "click_db", self.click_db.value())
        for name, s in self.parts.items():
            st.setValue(DEFAULTS + "part/" + name, s.value())
        st.setValue(DEFAULTS + "trim", self.trim.isChecked())
        st.setValue(FILL_KEY, self.fill.isChecked())
        st.setValue(DEFAULTS + "click_all", self.click_all.isChecked())
        st.setValue(THRESHOLD_KEY, self.threshold.value())
        self.status.setText(_("Настройки сохранены как настройки по умолчанию"))

    def _icon_button(self, kind: str, tip: str, slot) -> QPushButton:
        b = QPushButton(objectName="iconButton", clicked=slot, toolTip=tip, accessibleName=tip)
        b.setIcon(QIcon(part_icon(kind, QColor(127, 127, 127), 18)))
        b.setIconSize(QSize(18, 18))
        b.setFixedSize(30, 28)
        b.setFocusPolicy(Qt.TabFocus)
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
        if not (self.current and self.current.sections):
            self.meter_seg.set_value("4/4")
        self.volume.setValue(0)
        self.count_voice.setCurrentIndex(self.count_voice.findData(COUNT_VOICE_DEFAULT))
        self.click_voice.setCurrentIndex(self.click_voice.findData(METRONOME_VOICE_DEFAULT))
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
        self.volume.setValue(count_volume(st.value(DEFAULTS + COUNT_VOLUME_KEY), st.value(DEFAULTS + "volume", 0)))
        for combo, key, default in ((self.count_voice, "count_voice", COUNT_VOICE_DEFAULT),
                                    (self.click_voice, "click_voice", METRONOME_VOICE_DEFAULT)):
            voice = combo.findData(st.value(DEFAULTS + key, default))
            combo.setCurrentIndex(voice if voice >= 0 else combo.findData(default))
        self.click_db.setValue(count_volume(st.value(DEFAULTS + "click_db", 0)))
        self.click_all.setChecked(pref_bool(DEFAULTS + "click_all", False))
        if st.value(DEFAULTS + "style") is None:
            return

        def num(key, default=0):
            try:
                return int(st.value(DEFAULTS + key, default))
            except (TypeError, ValueError):
                return default

        for seg, key, cast in ((self.style_seg, "style", str), (self.bars_seg, "bars", int), (self.meter_seg, "meter", str)):
            if seg is self.meter_seg and self.current and self.current.sections:
                continue
            try:
                v = cast(st.value(DEFAULTS + key))
            except (TypeError, ValueError):
                continue
            if v in seg.values:
                seg.set_value(v)
        for name, s in self.parts.items():
            s.setValue(num("part/" + name))
        self.trim.setChecked(st.value(DEFAULTS + "trim", False) in (True, "true"))

    # --- pauses ---

    def _filling(self) -> bool:
        return self.fill.isChecked()

    def _click_sound(self, *_args):
        if not self.restoring:
            self._refresh()
            self._swap_clicks()

    def _click_all_toggled(self, _on: bool):
        self._refresh()
        self._enable()
        self._swap_clicks()

    def _swap_clicks(self):
        """Rebuilds only the click part of the playing buffer, so a click change is heard at once."""
        if self.play_start is None:
            return
        base, stems = self.player.src
        if "click" not in stems:
            self._restart_preview()
            return
        sr, s = self.player.sr, self._playback_settings()
        click = np.zeros(len(base), dtype=np.float32)
        clicks = clicks_for(self.current, s)
        state = self.ready_preview
        if state.sound[3] != audible_edits(self.current, s):
            self._restart_preview()
            return
        points = ((c, c.time - state.origin) for c in edited_clicks(self.current, s, state.clock)) if state.clock else (
            (c, state.seconds_at(c.time)) for c in clicks)
        for c, seconds in points:
            at = round(seconds * sr)
            if not 0 <= at < len(click):
                continue
            pair = self.stick_hits(sr) if c.voice == "stick" else rd.voice_samples(c.voice, sr)
            x = (pair[0] if c.accent else pair[1]) * c.gain
            n = min(len(x), len(click) - at)
            click[at : at + n] += x[:n]
        old = stems["click"][0]
        base = base - old + click[:, None]
        self.player.src = (base, {**stems, "click": (click[:, None], 0)})
        state.sound = (*state.sound[:2], clicks, *state.sound[3:])

    @staticmethod
    @lru_cache(maxsize=4)
    def stick_hits(sr: int):
        return rd.load_samples(sr)

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
            self.status.setText(_("Дорожки взяты из кэша, эту песню уже разделяли"))
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

    def _guitar_match(self):
        """Opens the take-vs-original window once the whole-song stems exist."""
        from .guitarmatch import GuitarMatchDialog

        if self.pcm is None or self._wait_for_stems(self._guitar_match):
            return
        st = self._full_stems()
        if st is None:
            self.status.setText(_("Дорожки ещё не разделены, попробуйте через минуту"))
            return
        GuitarMatchDialog(self, self.pcm, self.sr, st, self.current.path.stem).show()

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
            search = (t.bars, s.threshold, sl, bool(sl) and not t.auto_done, t.ref_db)
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
        self.backing_key = key
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
        snapshot = self.job.args[0]
        self._finish()
        if track is not self.current:
            return
        self.stem_cache = full
        self._update_backing()
        if snapshot.bars is track.bars:
            self._set_found(track, zones, key)
        self._enable()
        self._auto_zones()  # the levels may have moved during the search
        self._restart_preview()  # a preview that played without stems picks them up now

    def _zones_edited(self, zones: list):
        if self.current:
            t, now = self.current, _bars_of(zones)
            t.dismissed = (t.dismissed | (t.covered - now)) - now
            # Persist the original source times. Only newly removed/restored bars affect old dismissals.
            added = now - t.covered
            spans = t.dismissed_times + [(t.bars[i].start, t.bars[i].end) for i in t.covered - now]
            for i in added:
                a, b = t.bars[i].start, t.bars[i].end
                spans = [(x, y) for lo, hi in spans for x, y in ((lo, min(hi, a)), (max(lo, b), hi)) if x < y]
            t.dismissed_times = sorted(set(spans))
            t.covered = now
            t.zones = [zn.mapped(z, t.bars, *zn.bounds(t.bars, z)) if z.kept else z for z in zones]
            self._refresh()
            self._restart_preview()

    def _selection_mode(self, on: bool):
        self.wave.selection_mode = on
        self.wave.setCursor(Qt.CrossCursor if on else Qt.OpenHandCursor)

    def _span_selected(self, show_menu: bool):
        span = self.wave.sel
        self.selection_actions.setEnabled(span is not None)
        self.zoom_selection_a.setEnabled(span is not None)
        self.selection_time.setText(f"{fmt_short(span[0])} – {fmt_short(span[1])}" if span else "")
        self.wave.update()
        self._song_changed()
        if show_menu:
            self._selection_menu()

    def _zoom_selection(self):
        if self.wave.sel:
            a, b = self.wave.sel
            pad = max(.05, (b - a) * .06)
            self.wave.show_range(a - pad, b + pad)

    def _select_to_end(self):
        w = self.wave
        if not (self.current and self.current.analysis):
            return
        here = self._here()
        start = max(0., self.current.t0 if here is None else here)
        if start < w.duration:
            w.sel = (start, w.duration)
            self._span_selected(False)

    def _nudge_selection(self, end: bool, direction: int):
        w = self.wave
        span = list(w.sel)
        side = int(end)
        choices = [x for x in (0., *w.beats, w.duration) if direction * (x - span[side]) > 1e-8]
        if choices:
            span[side] = min(choices, key=lambda x: abs(x - span[side]))
            if span[0] < span[1]:
                w.sel = tuple(span)
                self._span_selected(False)

    def _edit_selection_bounds(self):
        w = self.wave
        original = w.sel
        if original is None:
            return
        dialog = QDialog(self, windowTitle=_("Границы выделения"))
        box = QVBoxLayout(dialog)
        note = QLabel(_("Введите секунды (12.500) или такт:долю (5:2). Shift+←/→ двигает начало, Option/Alt+Shift+←/→ — конец."),
                      wordWrap=True)
        box.addWidget(note)
        grid = QGridLayout()
        fields = [QLineEdit(f"{at:.3f}") for at in original]
        for i, (label, edit) in enumerate(zip((_("Начало"), _("Конец")), fields)):
            grid.addWidget(QLabel(label), i, 0)
            grid.addWidget(edit, i, 1)
        box.addLayout(grid)
        error = QLabel(objectName="warning")
        box.addWidget(error)

        def read(edit, old):
            value = edit.text().strip()
            if value == f"{old:.3f}":
                return old
            if ":" in value:
                bar, beat = (int(n) for n in value.split(":"))
                if not 1 <= bar <= len(w.bars) or not 1 <= beat <= len(w.bars[bar - 1].beats):
                    raise ValueError
                return w.bars[bar - 1].beats[beat - 1]
            return float(value.replace(",", "."))

        def apply():
            try:
                a, b = (read(edit, old) for edit, old in zip(fields, original))
                if not 0 <= a < b <= w.duration:
                    raise ValueError
            except ValueError:
                error.setText(_("Проверьте начало и конец выделения."))
                return
            w.sel = (a, b)
            self._span_selected(False)
            dialog.accept()

        buttons = QHBoxLayout()
        buttons.addStretch()
        add_buttons(buttons, QPushButton(_("Отмена"), objectName="tool", clicked=dialog.reject),
                    QPushButton(_("Применить"), objectName="primary", default=True, clicked=apply))
        box.addLayout(buttons)
        dialog.exec()

    def _export_selection(self, span):
        if self.job:
            return
        t, s = self.current, self._playback_settings()
        base = target_path(t.path, replace(s, fmt="wav"), self.info or io.probe(t.path))
        filters = {"WAV (*.wav)": ".wav", "FLAC (*.flac)": ".flac", "MP3 (*.mp3)": ".mp3", "ALAC (*.m4a)": ".m4a"}
        name, selected = QFileDialog.getSaveFileName(self, _("Экспортировать выделение"),
                                                     str(base.with_name(f"{t.path.stem}_selection.wav")), ";;".join(filters))
        if not name:
            return
        out = Path(name)
        if not out.suffix:
            out = out.with_suffix(filters.get(selected, ".wav"))
        try:
            io.format_of(out)
        except ValueError as e:
            QMessageBox.warning(self, "Count-In", str(e))
            return
        self._stop()
        self._run(save_job, t, s, self.stem_cache, span, out, on_done=self._saved)

    def _selection_menu(self):
        t, span = self.current, self.wave.sel
        if not (t and t.analysis and span):
            return
        a, b = span
        clock = timeline.Timeline(t.duration, self.speed / 100, t.edits)
        audible = clock.output_at(b) - clock.output_at(a) >= .02
        menu = QMenu(self)
        menu.setStyleSheet("QMenu::item { font-size: 16px; min-width: 240px; padding: 8px 16px; }")
        loop = menu.addAction(_("Играть по кругу"))
        named_loop = menu.addAction(_("Сохранить именованную петлю…"))
        tap = menu.addAction(_("Настучать темп выделения"))
        tempo_edit = menu.addAction(_("Изменить темп и размер…"))
        pause = menu.addAction(_("Сделать паузой с хай-хэтом"))
        menu.addSeparator()
        cut = menu.addAction(_("Вырезать выделение"))
        speed = menu.addAction(_("Скорость участка…"))
        uncut = menu.addAction(_("Вернуть вырезанное"))
        reset_speed = menu.addAction(_("Сбросить скорость участка"))
        menu.addSeparator()
        exact = menu.addAction(_("Границы выделения…"))
        to_end = menu.addAction(_("Выделить до конца песни"))
        zoom = menu.addAction(_("Масштаб по выделению"))
        save = menu.addAction(_("Экспортировать выделение…"))
        joins = [end for (_, end, *_), (start, *_) in zip(clock.pieces, clock.pieces[1:])
                 if end < start and a < start and end < b]
        audition = menu.addAction(_("Прослушать стык"))
        fade = menu.addAction(_("Сглаживание стыков…"))
        audition.setEnabled(bool(joins))
        fade.setEnabled(any(e.speed is None for e in t.edits))
        clear = menu.addAction(_("Снять выделение"))
        for action in (loop, named_loop, tap, cut, save):
            action.setEnabled(audible)
        tempo_edit.setEnabled(bool(t.sections))
        pause.setEnabled(bool(t.bars))
        overlaps = [e for e in t.edits if e.start < b and a < e.end]
        uncut.setEnabled(any(e.speed is None for e in overlaps))
        reset_speed.setEnabled(any(e.speed is not None for e in overlaps))
        chosen = menu.exec(QCursor.pos())
        if chosen is loop:
            self._activate_loop(span)
        elif chosen is named_loop:
            self._name_loop(span)
        elif chosen is pause:
            self._make_pause(a, b)
        elif chosen is tap:
            self._tap_span(t, span)
        elif chosen is tempo_edit:
            self._edit_tempo_section(tempo.index(t.sections, a), QCursor.pos(), span)
        elif chosen is cut:
            self._set_audio_edits(timeline.with_edit(t.edits, a, b, None))
        elif chosen is speed:
            value, ok = QInputDialog.getInt(self, _("Скорость участка"), _("Процент от исходной скорости"),
                                            round(clock.rate_at(a) * 100), 50, 150)
            if ok:
                self._set_audio_edits(timeline.with_edit(t.edits, a, b, value / 100))
        elif chosen is uncut or chosen is reset_speed:
            self._set_audio_edits(timeline.without_edit(t.edits, a, b, cutting=chosen is uncut))
        elif chosen is exact:
            self._edit_selection_bounds()
        elif chosen is to_end:
            self._select_to_end()
        elif chosen is zoom:
            self._zoom_selection()
        elif chosen is save:
            self._export_selection(span)
        elif chosen is audition:
            seam = clock.output_at(min(joins, key=lambda at: abs(at - (a + b) / 2)))
            self._play_at(clock.source_at(max(0., seam - 1)), audition_end=clock.source_at(min(clock.length, seam + 1)))
        elif chosen is fade:
            value, ok = QInputDialog.getInt(self, _("Сглаживание стыков"), _("Миллисекунд с каждой стороны"),
                                            round(t.cut_fade_ms), 0, 100)
            if ok and value != t.cut_fade_ms:
                self._snap_undo()
                t.cut_fade_ms = value
                self._song_changed()
                self._snap_undo()
                self._restart_preview()
        elif chosen is clear:
            self.wave.clear_selection()

    def _activate_loop(self, span: tuple[float, float], rate: float | None = None):
        t = self.current
        clock = edit_clock(t, self.settings())
        if clock.output_at(span[1]) - clock.output_at(span[0]) < .02:
            self.status.setText(_("В выделении нет аудио для петли."))
            return
        self.loop, self.loop_on, self.loop_rate = span, True, rate
        self.trainer_steps = self.player.laps = 0
        self._apply_loop()
        self._refresh()
        self._play_at(span[0], preroll=True)

    def _name_loop(self, span: tuple[float, float]):
        t = self.current
        name, ok = QInputDialog.getText(self, _("Сохранить именованную петлю"), _("Название"))
        name = name.strip()
        if not ok or not name:
            return
        rate = edit_clock(t, self._playback_settings()).rate_at(span[0])
        speed, ok = QInputDialog.getInt(self, _("Скорость петли"), _("Процент от исходной скорости"), round(rate * 100), 50, 150)
        if ok:
            self._snap_undo()
            others = tuple(loop for loop in t.saved_loops if loop.name.casefold() != name.casefold())
            t.saved_loops = (*others, timeline.Loop(name, *span, speed / 100))
            self._song_changed()
            self._snap_undo()

    def _show_saved_loops(self):
        menu, t = self.saved_loops_menu, self.current
        menu.clear()
        loops = t.saved_loops if t else ()
        for loop in loops:
            label = f"{loop.name.replace('&', '&&')}  ·  {fmt_short(loop.start)}–{fmt_short(loop.end)}  ·  {round(loop.speed * 100)}%"
            menu.addAction(label, lambda loop=loop: self._activate_loop((loop.start, loop.end), loop.speed))
        if loops:
            menu.addSeparator()
            menu.addAction(_("Удалить сохранённую петлю…"), self._remove_saved_loop)
        else:
            menu.addAction(_("Сохранённых петель пока нет")).setEnabled(False)

    def _remove_saved_loop(self):
        t = self.current
        name, ok = QInputDialog.getItem(self, _("Удалить сохранённую петлю"), _("Название"),
                                        [loop.name for loop in t.saved_loops], 0, False)
        if ok:
            self._snap_undo()
            t.saved_loops = tuple(loop for loop in t.saved_loops if loop.name != name)
            self._song_changed()
            self._snap_undo()

    def _set_audio_edits(self, edits: tuple[timeline.Edit, ...]):
        t = self.current
        if not t or t.edits == edits:
            return
        clock = timeline.Timeline(t.duration, self.speed / 100, edits)
        if clock.length < .02:
            self.status.setText(_("После подрезки должен остаться хотя бы один фрагмент аудио."))
            return
        self._snap_undo()
        t.edits = edits
        if self.loop_on and self.loop and clock.output_at(self.loop[1]) - clock.output_at(self.loop[0]) < .02:
            self.loop_on = False
            self._apply_loop()
        self._refresh()
        self._snap_undo()
        self._restart_preview()

    def _make_pause(self, start: float, end: float):
        """A span becomes a pause (replacing the ones it overlaps); its tempo and meter open at once."""
        t = self.current
        z = zn.mapped(zn.Zone(0, 0, manual=True, free=True), t.bars, start, end)
        if self._edit_pause(z) != "keep":
            return
        kept = [old for old in t.zones or [] if (bounds := zn.bounds(t.bars, old))[1] <= start or end <= bounds[0]]
        self._zones_edited(sorted([*kept, z], key=lambda q: zn.bounds(t.bars, q)[0]))

    def _edit_zone(self, i: int) -> bool:
        """Tempo and meter of pause i; False when the dialog was cancelled."""
        t = self.current
        if not (t and t.zones and i < len(t.zones)):
            return False
        z = t.zones[i]
        action = self._edit_pause(z)
        if action == "cancel":
            return False
        if action == "remove":
            t.zones.pop(i)
        self._zones_edited(t.zones)
        return True

    def _edit_pause(self, z):
        t = self.current
        a, b, bpm, n = zn.describe(t.bars, z)
        d = ZoneDialog(self, a, b, bpm, n, z.count_in)
        result = d.exec()
        if d.removed:
            return "remove"
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
            return "cancel"
        z.count_in = d.count_in.isChecked()
        return "keep"

    # --- preview while editing ---

    def _restart_preview(self, separate: bool = False):
        """Rebuilds the playing preview after an edit and carries on from the same song position."""
        if self.play_start is None or self.job:
            return
        self.resume_at = self._playable(self.ready_preview.source_at(self.player.position()))
        self.preroll, self.want_play = False, True  # the old buffer plays on until the new one replaces it
        s = self._playback_settings()
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
        """A click sets the local tempo selection and seeks during playback."""
        if self.current and self.current.sections and self.wave.play_t is not None:
            self.section_i = tempo.index(self.current.sections, self.wave.play_t)
            self.tempo_span = None
            self._section_values()
            self._show_detector()
            self._speed_bpm()
        if self.play_start is not None and self.wave.play_t is not None:
            self._play_at(self.wave.play_t)

    def _play_at(self, start: float, preroll: bool = False, audition_end: float | None = None):
        """Plays from the song time start, at once when the playing buffer holds it; with preroll a bar of
        clicks in the local tempo leads in."""
        if self.job or not (self.current and self.current.analysis and self.pcm is not None):
            return
        start = self._playable(start)
        preroll = preroll and start > min((c.time for c in self.wave.clicks), default=start)
        s = self._playback_settings()
        if (self.play_start is not None and audition_end is None and self.audition_end is None
                and self.ready_preview.sound[3] == audible_edits(self.current, s) and self._jump(start, preroll)):
            return
        self._stop()
        self.audition_end = audition_end
        s = self._playback_settings()
        if self._reuse_preview(s, start, preroll):
            return
        self.resume_at, self.preroll, self.want_play = start, preroll, True
        self._run(preview_job, self.current, s, self.pcm, self.sr, self.stem_cache, self._separate_now(s), start,
                  on_done=self._preview_ready)

    def _reuse_preview(self, settings: Settings, start: float | None = None, preroll: bool = False) -> bool:
        state = self.ready_preview
        t = self.current
        if state is None or state.stem_cache is not self.stem_cache or state.extra is not t.extra:
            return False
        base, stems = self.player.src
        if len(stems) <= 1 and self._separate_now(settings):
            return False
        try:
            clicks = clicks_for(t, settings)
        except (OSError, ValueError, EOFError):  # the normal worker path reports a missing or invalid MIDI pattern
            return False
        if state.sound != (settings.speed, settings.semis, clicks, audible_edits(t, settings), t.cut_fade_ms):
            return False
        sr = self.player.sr
        if start is not None:
            seconds = state.seconds_at(start)
        elif state.clock:
            seconds = min(c.time for c in edited_clicks(t, settings, state.clock)) - state.origin
        else:
            seconds = state.seconds_at(min(c.time for c in clicks))
        sample = round(seconds * sr)
        if not 0 <= sample < len(base):
            return False
        if self.loop_on and self.loop and self.audition_end is None:
            a, b = (round(state.seconds_at(at) * sr) for at in self.loop)
            if not 0 <= a < b <= len(base):
                return False
        self.want_play = True
        self._start_buffer(base, stems, sr, start, preroll)
        return True

    def _playable(self, at: float) -> float:
        t = self.current
        if t.edits:
            clock = edit_clock(t, self._playback_settings())
            if not clock.contains(at):
                return clock.source_at(min(clock.output_at(at), clock.length - 1 / self.sr))
        return at

    def _out(self, t: float) -> int:
        """Output sample of song time t in the playing buffer."""
        return round(self.ready_preview.seconds_at(t) * self.player.sr)

    def _jump(self, t: float, preroll: bool = False) -> bool:
        """Moves playback to song time t inside the playing buffer; False when the buffer does not hold it."""
        i = self._out(t)
        if not 0 <= i < len(self.player.src[0]):
            return False
        ticks = []
        if preroll and self.preroll_cb.isChecked() and self.current.bars:
            bars = self.current.bars
            bar = min(bars, key=lambda b: abs(b.start - t) if b.start <= t + 0.05 else 1e9)
            section = self.current.sections[tempo.index(self.current.sections, t)]
            n, period = rd.meter_shape(section.meter)[0], bar.pulse or bar.period
            state = self.ready_preview
            speed = state.clock.rate_at(t) if state.clock else state.sound[0]
            lead = [i - round(k * period / speed * self.player.sr) for k in range(n, 0, -1)]
            if lead[0] >= 0:
                hi, lo = rd.voice_samples(self.count_voice.currentData(), self.player.sr)
                g = 10 ** (self.volume.value() / 20)
                ticks = [(at, (hi if k == 0 else lo) * g) for k, at in enumerate(lead)]
                i = ticks[0][0]
        self.player.ticks, self.player.pos, self.player.done = ticks, i, False
        return True

    def _apply_loop(self):
        playing = self.play_start is not None
        loop = (self._out(self.loop[0]), self._out(self.loop[1])) if playing and self.loop_on and self.loop and self.audition_end is None else None
        self.player.loop = loop if loop and 0 <= loop[0] < loop[1] <= len(self.player.src[0]) else None
        self.wave.loop = (*self.loop, self.loop_on) if self.loop else None
        self.loop_b.setChecked(self.loop_on)
        self._song_changed()
        self.loop_b.setEnabled(self.loop is not None)
        self.wave.update()

    def _toggle_loop(self):
        if not self.loop:
            return
        self.loop_on = not self.loop_on
        self.trainer_steps = 0
        self.player.laps = 0
        self._apply_loop()
        self._refresh()
        if (self.loop_on and self.play_start is not None
                and not self.loop[0] <= self.ready_preview.source_at(self.player.position()) < self.loop[1]):
            self._play_at(self.loop[0], preroll=True)
        elif self.play_start is not None and self.ready_preview.sound[3] != audible_edits(self.current, self._playback_settings()):
            self._restart_preview()

    def _separate_now(self, s) -> bool:
        """Whether a preview separates its own excerpt: whenever a part is turned down. During the whole-song
        separation the excerpt goes ahead of it in the separation process, so playback starts in seconds."""
        return bool(s.levels) and not cloud_on()

    def _reveal(self):
        if self.last_saved:
            reveal(self.last_saved)

    def closeEvent(self, e):
        self._flush_song()
        QSettings().setValue(GEOMETRY_KEY, self.saveGeometry())
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
    """Receives file and URL events from macOS."""

    window: Window | None = None
    pending_proxy_link: str | None = None
    dev_links = False

    def event(self, e):
        if e.type() == QEvent.FileOpen:
            url = e.url()
            if self.dev_links and url.scheme() == "countin-dev":
                self.receive_proxy_link(bytes(url.toEncoded()).decode("ascii"))
                return True
            if self.window and e.file():
                self.window.open_files([Path(e.file())])
                return True
        return super().event(e)

    def receive_proxy_link(self, link: str):
        if self.window is None:
            self.pending_proxy_link = link
        else:
            QTimer.singleShot(0, lambda: self.window.open_proxy_link(link))


def register_windows_proxy_links():
    import winreg

    # The ZIP build has no installer. Register the current executable for this Windows user.
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\countin-dev") as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "URL:Count-In Dev")
        winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
        with winreg.CreateKey(key, r"shell\open\command") as command:
            winreg.SetValueEx(command, "", 0, winreg.REG_SZ, f'"{sys.executable}" --proxy-link "%1"')


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
    """The system accent, as the system's own checkboxes draw it. An accent that blends into the window (white in
    the light theme, near-black in the dark one) falls back to system blue."""
    pal, dark = app.palette(), is_dark(app)
    c = pal.color(QPalette.Accent)
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


def migrate_dev_settings():
    settings = QSettings()
    marker = "migration/dev-profile-v1"
    if settings.value(marker, False, type=bool):
        return

    def copy_missing(profile: str, keys=None):
        source = QSettings(settings.format(), QSettings.UserScope, "Count-In", profile)
        source.setFallbacksEnabled(False)
        for key in source.allKeys() if keys is None else keys:
            if source.contains(key) and not settings.contains(key):
                settings.setValue(key, source.value(key))

    copy_missing("Count-In Tempo Dev")
    if sys.platform == "win32":
        copy_missing("Count-In", (CLOUD_KEY, CLOUD_OVERLAP))
    settings.setValue(marker, True)
    settings.sync()


def main() -> int:
    name = update.app_name(update.current_version())
    App.dev_links = name == "Count-In Dev"
    app = App(sys.argv)
    app.setOrganizationName("Count-In")
    app.setApplicationName(name)
    app.setApplicationDisplayName(name)
    if App.dev_links:
        migrate_dev_settings()
    apply_icon(app)

    setup_log()
    i18n.set_language(QSettings().value(LANG_KEY, "system"))
    if app.dev_links and sys.platform == "win32" and getattr(sys, "frozen", False):
        try:
            register_windows_proxy_links()
        except OSError:
            log.warning("Could not register Count-In dev links")

    # The window restyles with the system appearance.
    app.styleHints().colorSchemeChanged.connect(lambda _scheme: app.setStyleSheet(style_sheet(app)))
    app.setStyleSheet(style_sheet(app))
    w = app.window = Window()
    # Only real files: the system may pass its own arguments at launch, and those are no reason for an error.
    args = sys.argv[1:]
    if app.dev_links and args and args[0] == "--proxy-link":
        if len(args) == 2:
            app.receive_proxy_link(args[1])
    else:
        w.open_files([path for path in map(Path, args) if path.is_file()])
    # Size and place from the last session; the first start fills the screen.
    if not w.restoreGeometry(QSettings().value(GEOMETRY_KEY, b"")):
        w.showMaximized()
    else:
        w.show()
    if app.pending_proxy_link is not None:
        app.receive_proxy_link(app.pending_proxy_link)
        app.pending_proxy_link = None

    # CI smoke test: start, show the window, quit; a missing bundled icon fails it.
    if os.environ.get("COUNTIN_SMOKE"):
        QTimer.singleShot(2000, lambda: app.exit(1 if app.windowIcon().isNull() else 0))
    else:
        w.check_updates()
        QTimer.singleShot(400, lambda: ensure_assets(w))

        # What's New opens once after an update; a fresh install has nothing to compare with
        st, cur = QSettings(), update.current_version()
        seen = st.value(SEEN_KEY) or ("0" if st.contains(GEOMETRY_KEY) else None)
        if seen and changes.version_key(seen) < changes.version_key(cur):
            QTimer.singleShot(800, lambda: w._whats_new(seen if seen != "0" else None))
        st.setValue(SEEN_KEY, cur)
        w.check_music_access()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

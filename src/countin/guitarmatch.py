"""The "Match my guitar" window: the player's take against the song's separated guitar, as EQ settings."""

from pathlib import Path

import numpy as np
import soundfile as sf
import soxr
from PySide6.QtCore import QPointF, QRectF, Qt, QThreadPool
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QHeaderView, QLabel, QPushButton,
    QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .core import eqmatch as eq
from .core import io
from .i18n import _

F_LO, F_HI = 60.0, 10000.0
ORIGINAL, MINE, MATCHED = QColor("#e8913a"), QColor("#7f8c99"), QColor("#3fb36b")
KINDS = {"peak": "Пик", "low": "Полка НЧ", "high": "Полка ВЧ"}


def octaves(q: float) -> float:
    """Bandwidth in octaves of a peaking filter with this Q, the unit some processors show instead."""
    return 2 / np.log(2) * np.arcsinh(1 / (2 * q))


def load_take(job, path: Path, sr: int, channels: int, guitar: np.ndarray):
    """Decodes the take, brings it to the song's rate and channels and lines it up with the guitar stem.
    Returns (take at song rate, aligned take of the song's length, offset in samples, band levels of both)."""
    job.report(_("Читаю {name}", name=path.name))
    x, info = io.decode(path)
    if info.sr != sr:
        x = soxr.resample(x, info.sr, sr).astype(np.float32)
    x = np.repeat(x[:, :1], channels, axis=1) if x.shape[1] < channels else x[:, :channels]
    job.report(_("Сравниваю с гитарой из песни"))
    ref = eq.mono(guitar)
    d = eq.offset(eq.mono(x), ref, sr)
    aligned = np.zeros_like(guitar)
    src = x[max(0, -d):]
    n = min(len(src), len(aligned) - max(0, d))
    if n > 0:
        aligned[max(0, d):max(0, d) + n] = src[:n]
    # The take is compared where it overlaps the song, so a long tail or count-in does not count.
    return x, aligned, d, eq.bands(eq.mono(aligned), sr), eq.bands(ref, sr)


def render_job(job, take: np.ndarray, sr: int, moves: list, level: float):
    job.report(_("Применяю эквалайзер"))
    return moves, eq.apply(take, sr, moves, level)


class Spectrum(QWidget):
    """Band levels of the original guitar, the take and the take through the EQ, with the EQ curve under them."""

    def __init__(self):
        super().__init__(minimumHeight=300)
        self.result: eq.Result | None = None
        self.sr = 44100

    def set(self, result: eq.Result | None, sr: int):
        self.result, self.sr = result, sr
        self.update()

    def _x(self, f, w):
        return 48 + (np.log(f / F_LO) / np.log(F_HI / F_LO)) * (w - 60)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        text = self.palette().windowText().color()
        grid = QColor(text)
        grid.setAlpha(40)
        w, h = self.width(), self.height()
        top = QRectF(0, 8, w, h * 0.62)
        bottom = QRectF(0, h * 0.62 + 28, w, h * 0.38 - 50)
        p.setPen(QPen(grid, 1))
        for f in (100, 200, 500, 1000, 2000, 5000, 10000):
            x = self._x(f, w)
            p.drawLine(QPointF(x, top.top()), QPointF(x, bottom.bottom()))
            p.setPen(text)
            p.drawText(QRectF(x - 30, bottom.bottom() + 2, 60, 18), Qt.AlignCenter, f"{f // 1000}k" if f >= 1000 else str(f))
            p.setPen(QPen(grid, 1))
        r = self.result
        if r is None:
            p.setPen(text)
            p.drawText(top, Qt.AlignCenter, _("Здесь появятся спектры вашей гитары и гитары из песни"))
            return
        fs = eq.CENTERS[r.used]
        eqdb = sum((eq.response_db(m, eq.CENTERS, self.sr) for m in r.moves), np.zeros(len(eq.CENTERS)))
        curves = [(r.target_db, ORIGINAL), (r.source_db + r.level_db, MINE),
                  (r.source_db + r.level_db + eqdb, MATCHED)]
        vals = np.concatenate([c[r.used] for c, _col in curves])
        lo, hi = vals.min() - 3, vals.max() + 3
        y = lambda v: top.bottom() - (v - lo) / (hi - lo) * top.height()
        for c, col in curves:
            self._line(p, [QPointF(self._x(f, w), y(v)) for f, v in zip(fs, c[r.used])], col, 2.5 if col == MATCHED else 2)

        # EQ curve, ±12 dB around a zero line
        p.setPen(QPen(grid, 1))
        mid = bottom.center().y()
        p.drawLine(QPointF(48, mid), QPointF(w - 12, mid))
        p.setPen(text)
        for v in (12, 0, -12):
            p.drawText(QRectF(0, mid - v / 12 * bottom.height() / 2 - 9, 42, 18), Qt.AlignRight | Qt.AlignVCenter, f"{v:+d}" if v else "0")
        p.drawText(QRectF(48, bottom.top() - 22, w, 18), Qt.AlignLeft, _("Эквалайзер, дБ"))
        f = np.geomspace(F_LO, F_HI, 200)
        db = sum((eq.response_db(m, f, self.sr) for m in r.moves), np.zeros(len(f)))
        yb = lambda v: mid - np.clip(v, -12, 12) / 12 * bottom.height() / 2
        self._line(p, [QPointF(self._x(a, w), yb(v)) for a, v in zip(f, db)], text, 2)
        for i, m in enumerate(r.moves, 1):
            x, yy = self._x(m.freq, w), yb(m.gain_db)
            p.setBrush(MATCHED)
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(x, yy), 9, 9)
            p.setPen(QColor("white"))
            p.drawText(QRectF(x - 9, yy - 9, 18, 18), Qt.AlignCenter, str(i))
        p.end()

    @staticmethod
    def _line(p: QPainter, pts, color, width):
        if len(pts) < 2:
            return
        path = QPainterPath(pts[0])
        for q in pts[1:]:
            path.lineTo(q)
        p.setPen(QPen(color, width))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)


def legend(color: QColor, text: str) -> QLabel:
    return QLabel(f"<span style='color:{color.name()}'>━━</span> {text}")


class GuitarMatchDialog(QDialog):
    """Drop a take, see the EQ that brings it to the original guitar, hear it over the backing, save it."""

    def __init__(self, window, pcm: np.ndarray, sr: int, stems: dict[str, np.ndarray], song: str):
        from .gui import Player

        super().__init__(window)
        self.setWindowTitle(_("Подогнать мою гитару"))
        self.setAcceptDrops(True)
        self.resize(820, 760)
        self.window_, self.sr, self.guitar = window, sr, stems["guitar"]
        self.backing = pcm - self.guitar
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.player = Player()
        self.take = self.aligned = None
        self.result: eq.Result | None = None
        self.levels = None  # (take bands, original bands)
        self.rendered = None  # (moves, aligned take through them)
        self.job = None

        v = QVBoxLayout(self)
        intro = QLabel(_("Сравнивает вашу запись гитары с гитарой из песни «{song}» и подсказывает, что покрутить "
                         "в эквалайзере гитарного процессора. Запишите ту же партию через свой звук (усилитель, "
                         "кабинет, эффекты), а не чистый сигнал с гитары.", song=song), wordWrap=True)
        v.addWidget(intro)

        drop = QFrame(objectName="card")
        dh = QHBoxLayout(drop)
        self.file_l = QLabel(_("Перетащите сюда запись своей гитары (WAV, FLAC, MP3)"), wordWrap=True)
        pick = QPushButton(_("Выбрать файл…"), clicked=self._pick)
        dh.addWidget(self.file_l, 1)
        dh.addWidget(pick)
        v.addWidget(drop)

        self.spectrum = Spectrum()
        v.addWidget(self.spectrum, 1)
        lg = QHBoxLayout()
        for col, t in ((ORIGINAL, _("Гитара в песне")), (MINE, _("Ваша гитара")), (MATCHED, _("Ваша гитара с эквалайзером"))):
            lg.addWidget(legend(col, t))
        lg.addStretch()
        v.addLayout(lg)

        ctl = QHBoxLayout()
        self.count = QSpinBox(minimum=2, maximum=8, value=5)
        self.count.setToolTip(_("Сколько полос эквалайзера есть в вашем процессоре"))
        self.cap = QSpinBox(minimum=3, maximum=15, value=6, suffix=_(" дБ"))
        self.cap.setToolTip(_("Самое большое усиление или ослабление одной полосы"))
        for w in (self.count, self.cap):
            w.valueChanged.connect(self._refit)
        ctl.addWidget(QLabel(_("Полос")))
        ctl.addWidget(self.count)
        ctl.addSpacing(16)
        ctl.addWidget(QLabel(_("Не больше")))
        ctl.addWidget(self.cap)
        ctl.addStretch()
        v.addLayout(ctl)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels([_("Тип"), _("Частота"), _("Усиление"), _("Q"), _("Ширина")])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setMaximumHeight(8 * 24 + 30)
        v.addWidget(self.table)
        self.note = QLabel(wordWrap=True)
        v.addWidget(self.note)

        bh = QHBoxLayout()
        self.play_b = QPushButton(_("Слушать с минусовкой"), clicked=self._play)
        self.eq_on = QCheckBox(_("Эквалайзер включён"), checked=True)
        self.eq_on.toggled.connect(self._gains)
        self.copy_b = QPushButton(_("Копировать настройки"), clicked=self._copy)
        self.save_b = QPushButton(_("Сохранить гитару с эквалайзером…"), clicked=self._save)
        self.add_b = QPushButton(_("Добавить в трек"), clicked=self._add)
        self.add_b.setToolTip(_("Ваша гитара с эквалайзером, выровненная по песне, войдёт в прослушивание и в "
                                "сохранённый трек с отсчётом"))
        for b in (self.play_b, self.eq_on):
            bh.addWidget(b)
        bh.addStretch()
        for b in (self.copy_b, self.save_b, self.add_b, QPushButton(_("Закрыть"), clicked=self.close)):
            bh.addWidget(b)
        v.addLayout(bh)
        self._enable()

    # --- loading ---

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [Path(u.toLocalFile()) for u in e.mimeData().urls()]
        if paths:
            self.open(paths[0])

    def _pick(self):
        from .gui import AUDIO_EXT

        exts = " ".join(f"*{x}" for x in sorted(AUDIO_EXT))
        f, _sel = QFileDialog.getOpenFileName(self, _("Запись гитары"), "", _("Аудио ({exts})", exts=exts))
        if f:
            self.open(Path(f))

    def open(self, path: Path):
        self.player.stop()
        self.path = path
        self.file_l.setText(_("Читаю {name}", name=path.name))
        self._start(load_take, path, self.sr, self.backing.shape[1], self.guitar, done=self._loaded)

    def _start(self, fn, *args, done):
        from .gui import Job

        self.job = Job(fn, *args)
        self.job.signals.progress.connect(self._progress)
        self.job.signals.done.connect(done)
        self.job.signals.failed.connect(self._failed)
        self.pool.start(self.job)
        self._enable()

    def _progress(self, text, _d, _n):
        self.note.setText(text)

    def _failed(self, msg):
        self.job = None
        self.note.setText(_("Не получилось: {e}", e=msg))
        self._enable()

    def _loaded(self, res):
        self.job = None
        self.take, self.aligned, d, *self.levels = res
        self.rendered = None
        self.file_l.setText(_("{name} · сдвиг относительно песни {s:+.2f} с", name=self.path.name, s=d / self.sr))
        self._refit()

    # --- fit ---

    def _refit(self):
        if not self.levels:
            return
        self.result = eq.fit(*self.levels, self.sr, max_moves=self.count.value(), cap_db=self.cap.value())
        r = self.result
        self.spectrum.set(r, self.sr)
        self.table.setRowCount(len(r.moves))
        for i, m in enumerate(r.moves):
            cells = [f"{i + 1}. {_(KINDS[m.kind])}", f"{self._hz(m.freq)}", f"{m.gain_db:+.1f} {_('дБ')}",
                     f"{m.q:.1f}", "—" if m.kind != "peak" else _("{o:.1f} окт.", o=octaves(m.q))]
            for c, text in enumerate(cells):
                self.table.setItem(i, c, QTableWidgetItem(text))
        self.note.setText(self._describe())
        if self.player.stream:
            self._play(restart=True)
        self._enable()

    def _describe(self) -> str:
        r = self.result
        lines = [_("Громкость в миксе: {g:+.1f} дБ к вашей записи.", g=r.level_db)]
        if r.left_db > 6:
            lines.append(_("Даже после эквалайзера звук отличается до {d:.0f} дБ. Скорее всего, нужен другой "
                           "усилитель, кабинет или звукосниматель, эквалайзер это не исправит.", d=r.left_db))
        elif not r.moves:
            lines.append(_("Тембр уже почти как в песне, эквалайзер не нужен."))
        else:
            lines.append(_("После эквалайзера остаётся разница до {d:.1f} дБ.", d=r.left_db))
        return " ".join(lines)

    @staticmethod
    def _hz(f: float) -> str:
        return _("{f:.1f} кГц", f=f / 1000) if f >= 1000 else _("{f:.0f} Гц", f=f)

    # --- listening and saving ---

    def _play(self, restart: bool = False):
        if self.player.stream and not restart:
            self.player.stop()
            self.play_b.setText(_("Слушать с минусовкой"))
            return
        r = self.result
        key = (tuple(r.moves), r.level_db)
        if self.rendered and self.rendered[0] == key:
            return self._play_rendered()
        self._start(render_job, self.aligned, self.sr, r.moves, r.level_db, done=self._rendered)

    def _rendered(self, res, then=None):
        self.job = None
        moves, y = res
        self.rendered = ((tuple(moves), self.result.level_db), y)
        self.note.setText(self._describe())
        (then or self._play_rendered)()

    def _add(self):
        t = self.window_.current
        if t.extra is not None:
            t.extra = None
            self._added()
            return
        r = self.result
        if self.rendered and self.rendered[0] == (tuple(r.moves), r.level_db):
            return self._put()
        self._start(render_job, self.aligned, self.sr, r.moves, r.level_db,
                    done=lambda res: self._rendered(res, self._put))

    def _put(self):
        self.window_.current.extra = self.rendered[1]
        self._added()

    def _added(self):
        on = self.window_.current.extra is not None
        self.add_b.setText(_("Убрать из трека") if on else _("Добавить в трек"))
        self.window_.status.setText(_("Ваша гитара в треке: она войдёт в прослушивание и в сохранение") if on
                                    else _("Ваша гитара убрана из трека"))
        self.window_._auto_volume()
        self.window_._restart_preview()
        self._enable()

    def _play_rendered(self):
        y = self.rendered[1]
        raw = self.aligned * np.float32(10 ** (self.result.level_db / 20))
        self.window_.player.stop()
        pos = self.player.pos if self.player.stream else 0
        self.player.play(self.backing + raw, {"eq": raw - y}, self.sr, pos)
        self._gains()
        self.play_b.setText(_("Стоп"))
        self._enable()

    def _gains(self):
        # Player subtracts (1 - gain) * stem: gain 0 swaps the raw take for the EQ'd one.
        self.player.gains = {"eq": 0.0 if self.eq_on.isChecked() else 1.0}

    def _copy(self):
        r = self.result
        lines = [_("Эквалайзер для «{name}»", name=self.path.name)]
        for i, m in enumerate(r.moves, 1):
            width = "" if m.kind != "peak" else _(", ширина {o:.1f} окт.", o=octaves(m.q))
            lines.append(f"{i}. {_(KINDS[m.kind])} {self._hz(m.freq)} {m.gain_db:+.1f} {_('дБ')}, Q {m.q:.1f}{width}")
        lines.append(_("Громкость в миксе: {g:+.1f} дБ к вашей записи.", g=r.level_db))
        QGuiApplication.clipboard().setText("\n".join(lines))
        self.note.setText(_("Настройки скопированы"))

    def _save(self):
        out, _sel = QFileDialog.getSaveFileName(self, _("Сохранить гитару с эквалайзером"),
                                                str(self.path.with_name(self.path.stem + " EQ.wav")), "WAV (*.wav)")
        if not out:
            return
        r = self.result
        sf.write(out, np.clip(eq.apply(self.take, self.sr, r.moves, 0.0), -1, 1), self.sr, subtype="PCM_24")
        self.note.setText(_("Сохранено: {name}", name=Path(out).name))

    def _enable(self):
        busy = self.job is not None
        ready = self.result is not None and not busy
        for w in (self.play_b, self.copy_b, self.save_b, self.add_b, self.count, self.cap):
            w.setEnabled(ready or (w is self.play_b and self.player.stream is not None))

    def closeEvent(self, e):
        self.player.stop()
        if self.job:
            self.job.cancelled.set()
        super().closeEvent(e)

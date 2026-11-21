"""The tempo lane shares the waveform's viewport and source-time coordinates."""

from PySide6.QtCore import QLineF, QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QMenu, QWidget

from .core.tempo import index
from .i18n import _


class TempoLane(QWidget):
    selected = Signal(int)
    splitAt = Signal(float)
    moved = Signal(int, float)
    joined = Signal(int)
    shifted = Signal(int, float)
    editRequested = Signal(int, QPoint)

    def __init__(self, wave):
        super().__init__()
        self.wave = wave
        self.sections, self.active, self.drag = [], 0, None
        self.hot_value = self.pressed_value = None
        self.setFixedHeight(52)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName(_("Дорожка темпа"))
        self.setToolTip(_("Щёлкните по плашке BPM и размера, чтобы изменить участок. Двойной щелчок по фону делит его. "
                          "Потяните границу или сдвиньте сетку с Option / Alt."))

    def _badge(self, i):
        section = self.sections[i]
        end = self.sections[i + 1].start if i + 1 < len(self.sections) else self.wave.duration
        left = max(0, self.wave.x_of(section.start)) + 10
        right = min(self.width(), self.wave.x_of(end)) - 8
        width = right - left
        if width < 18:
            return QRectF(), ""

        bpm = f"{section.bpm:.1f}".rstrip("0").rstrip(".")
        note = "♩." if section.meter == "6/8" else "♩"
        tempo = f"{note} {'' if section.certain else '≈'}{bpm} BPM"
        meter = section.meter + ("" if section.meter_known else "?")
        fm = self.fontMetrics()
        label = fm.elidedText(f"{tempo} · {meter}", Qt.ElideRight, int(width - 14))
        return QRectF(left, 22, min(width, fm.horizontalAdvance(label) + 14), 24), label

    def paintEvent(self, _event):
        p = QPainter(self)
        palette = self.palette()
        p.setPen(palette.text().color())
        p.drawText(QRectF(4, 0, self.width() - 8, 17), Qt.AlignVCenter, _("Дорожка темпа"))
        for i, s in enumerate(self.sections):
            end = self.sections[i + 1].start if i + 1 < len(self.sections) else self.wave.duration
            x = self.wave.x_of(s.start)
            right = self.wave.x_of(end)
            if right < 0 or x >= self.width():
                continue
            selected = i == self.active
            color = QColor(palette.highlight().color()) if selected else QColor(100, 135, 160)
            color.setAlpha(75 if selected else 30)
            p.fillRect(QRectF(x, 18, right - x, 32), color)
            p.setPen(QPen(palette.highlight().color() if selected else palette.mid().color(), 2 if selected else 1))
            p.drawLine(QLineF(x, 18, x, 50))
            if selected:
                p.drawLine(QLineF(x, 49, right, 49))
            rect, label = self._badge(i)
            if not rect.isEmpty():
                hot = self.hot_value == i
                border = QColor(palette.highlight().color() if hot else palette.mid().color())
                fill = QColor(palette.highlight().color() if hot else palette.text().color())
                fill.setAlpha(65 if hot else 15)
                p.setPen(border)
                p.setBrush(fill)
                p.drawRoundedRect(rect, 5, 5)
                p.setPen(palette.text().color())
                p.drawText(rect, Qt.AlignCenter, label)
        if self.drag:
            p.setPen(QPen(palette.highlight().color(), 2, Qt.DashLine))
            p.drawLine(QLineF(self.drag[3], 17, self.drag[3], 51))

    def _edge(self, x):
        return next((i for i, s in enumerate(self.sections) if i and abs(self.wave.x_of(s.start) - x) <= 7), None)

    def mousePressEvent(self, e):
        self.pressed_value = None
        if not self.sections:
            return
        x = e.position().x()
        t = self.wave.t_of(x)
        if not 0 <= t < self.wave.duration:
            return
        i = index(self.sections, t)
        if e.button() == Qt.RightButton:
            m = QMenu(self)
            edit = m.addAction(_("Изменить темп и размер…"))
            m.addSeparator()
            cut = m.addAction(_("Разделить участок здесь"))
            join = m.addAction(_("Объединить с предыдущим")) if i else None
            chosen = m.exec(e.globalPosition().toPoint())
            if chosen is edit:
                self.editRequested.emit(i, e.globalPosition().toPoint())
            elif chosen is cut:
                self.splitAt.emit(t)
            elif join is not None and chosen is join:
                self.joined.emit(i)
            return
        if e.button() != Qt.LeftButton:
            return
        self.selected.emit(i)
        edge = self._edge(x)
        if e.modifiers() & Qt.AltModifier:
            self.drag = ("phase", i, x, x)
        elif edge is not None:
            self.drag = ("edge", edge, x, x)
        elif self._badge(i)[0].contains(e.position()):
            self.pressed_value = i

    def mouseMoveEvent(self, e):
        x = e.position().x()
        if self.drag:
            self.drag = (*self.drag[:3], x)
            self.update()
        i = index(self.sections, self.wave.t_of(x)) if self.sections else None
        hot = i if i is not None and self._edge(x) is None and self._badge(i)[0].contains(e.position()) else None
        if hot != self.hot_value:
            self.hot_value = hot
            self.update()
        self.setCursor(Qt.SizeHorCursor if self.drag or self._edge(x) is not None
                       or e.modifiers() & Qt.AltModifier else Qt.PointingHandCursor)

    def mouseReleaseEvent(self, e):
        pressed, self.pressed_value = self.pressed_value, None
        if self.drag:
            kind, i, x0, x = self.drag
            self.drag = None
            if abs(x - x0) > 2:
                if kind == "phase":
                    self.shifted.emit(i, self.wave.t_of(x) - self.wave.t_of(x0))
                else:
                    self.moved.emit(i, self.wave.t_of(x))
            self.update()
        elif pressed is not None and pressed < len(self.sections) and e.button() == Qt.LeftButton:
            rect, _ = self._badge(pressed)
            if rect.contains(e.position()):
                self.editRequested.emit(pressed, self.mapToGlobal(rect.bottomLeft().toPoint()))

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton and not e.modifiers():
            if not self.sections or self._badge(index(self.sections, self.wave.t_of(e.position().x())))[0].contains(e.position()):
                return
            self.drag = None
            self.splitAt.emit(self.wave.t_of(e.position().x()))

    def leaveEvent(self, e):
        self.hot_value = None
        self.update()
        super().leaveEvent(e)

    def keyPressEvent(self, e):
        if self.sections and e.key() in (Qt.Key_Return, Qt.Key_Enter):
            rect, _ = self._badge(self.active)
            pos = rect.bottomLeft().toPoint() if not rect.isEmpty() else QPoint(10, self.height())
            self.editRequested.emit(self.active, self.mapToGlobal(pos))
            return
        super().keyPressEvent(e)

    def wheelEvent(self, e):
        self.wave.wheelEvent(e)

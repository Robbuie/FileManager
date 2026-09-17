"""The transfer pill: a floating readout over the bottom of the window.

0.29 replaces the status bar's transfer line with this. A copy is the thing a
person glances at while doing something else, so it gets a shape of its own in
the middle of the window rather than a strip of text at the edge of it, and it
slides away when nothing is running.

Collapsed, it is a ring and two lines: what is being done, and how far, how
fast and how long. Clicked, it opens upwards into a card with the last minute
of speed as a line and the jobs waiting behind this one, and a button to the
full queue (Ctrl+J), which is still where holding and reordering live.

Painted, except for its buttons, for the reason the rows and the rail are.
"""

from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QToolButton, QWidget

from app.core.listing import format_size
from app.core.transfers import format_eta
from app.ui import glyphs
from app.ui.rows import parse_colour

PILL_H = 48
PILL_W = 460
CARD_H = 168
MARGIN_BOTTOM = 44
SLIDE_MS = 220


def readout(queue) -> tuple[str, str, float]:
    """The pill's two lines and its fraction, from the queue. Pure enough to
    test: it reads the queue and formats."""
    job = queue.current()
    if job is None:
        return "", "", 0.0
    first = job.brief
    if queue.paused:
        first = "Paused  ·  " + first
    bits = [f"{job.percent}%"]
    if job.counts_items:
        if job.total:
            bits.append(f"{job.done:,} of {job.total:,}")
    else:
        if job.total:
            bits.append(f"{format_size(job.done)} of {format_size(job.total)}")
        rate = job.speed
        if rate > 0:
            bits.append(f"{format_size(int(rate))}/s")
        eta = format_eta(job.remaining_seconds)
        if eta:
            bits.append(eta)
    waiting = len(queue.active) - 1
    if waiting > 0:
        bits.append(f"{waiting} queued")
    fraction = job.percent / 100.0
    return first, "  ·  ".join(bits), fraction


class TransferPill(QWidget):
    opened = Signal()   # the full queue

    def __init__(self, queue, parent: QWidget) -> None:
        super().__init__(parent)
        self._queue = queue
        self._tokens: dict[str, str] = {}
        self.expanded = False
        self._shown = False
        self.setCursor(Qt.PointingHandCursor)
        self.hide()

        self._pause = self._button("Pause", self._toggle_pause)
        self._cancel = self._button("Cancel", self._cancel_current)
        self._queue_button = QToolButton(self)
        self._queue_button.setText("Open queue")
        self._queue_button.setProperty("role", "status")
        self._queue_button.setFocusPolicy(Qt.NoFocus)
        self._queue_button.clicked.connect(self.opened)
        self._queue_button.hide()

        self._slide = QPropertyAnimation(self, b"geometry", self)
        self._slide.setDuration(SLIDE_MS)
        self._slide.setEasingCurve(QEasingCurve.OutCubic)
        self._slide.finished.connect(self._slid)

        queue.changed.connect(self.refresh)
        parent.installEventFilter(self)

    def set_motion(self, on: bool) -> None:
        self._slide.setDuration(SLIDE_MS if on else 0)

    def _button(self, name: str, slot) -> QToolButton:
        button = QToolButton(self)
        button.setProperty("role", "pillbutton")
        button.setAccessibleName(name)
        button.setToolTip(name)
        button.setFocusPolicy(Qt.NoFocus)
        button.setFixedSize(32, 32)
        button.clicked.connect(slot)
        return button

    def apply_tokens(self, tokens: dict[str, str]) -> None:
        self._tokens = tokens
        self._paint_buttons()
        self.update()

    def _paint_buttons(self) -> None:
        if not self._tokens:
            return
        tone = self._tokens["txt_1"]
        self._cancel.setIcon(glyphs.icon("close", colour=tone, muted=self._tokens["txt_2"],
                                         size=14))
        glyph = "play" if self._queue.paused else "pause"
        self._pause.setIcon(glyphs.icon(glyph, colour=tone, muted=self._tokens["txt_2"],
                                        size=14))
        self._pause.setToolTip("Resume" if self._queue.paused else "Pause")

    def _toggle_pause(self) -> None:
        self._queue.resume() if self._queue.paused else self._queue.pause()

    def _cancel_current(self) -> None:
        job = self._queue.current()
        if job is not None:
            self._queue.cancel(job.id)

    # ------------------------------------------------------------- geometry

    def target_rect(self, visible: bool = True) -> QRect:
        parent = self.parentWidget()
        width = min(PILL_W, parent.width() - 40)
        height = PILL_H + (CARD_H if self.expanded else 0)
        bottom = parent.height() - self._bottom_clearance() - MARGIN_BOTTOM
        x = (parent.width() - width) // 2
        if not visible:
            return QRect(x, parent.height() + 4, width, height)
        return QRect(x, bottom - height, width, height)

    def _bottom_clearance(self) -> int:
        bar = getattr(self.parentWidget(), "statusBar", None)
        return bar().height() if callable(bar) else 0

    def _layout_buttons(self) -> None:
        top = self.height() - PILL_H + (PILL_H - 32) // 2
        self._cancel.move(self.width() - 8 - 32, top)
        self._pause.move(self.width() - 8 - 32 - 4 - 32, top)
        self._queue_button.setVisible(self.expanded)
        if self.expanded:
            self._queue_button.adjustSize()
            self._queue_button.move(self.width() - 14 - self._queue_button.width(), 12)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._layout_buttons()
        super().resizeEvent(event)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if watched is self.parentWidget() and event.type() == event.Type.Resize \
                and self._shown and self._slide.state() != QPropertyAnimation.Running:
            self.setGeometry(self.target_rect())
        return super().eventFilter(watched, event)

    # ---------------------------------------------------------------- state

    def refresh(self) -> None:
        busy = self._queue.current() is not None
        if busy and not self._shown:
            self._shown = True
            self.expanded = False
            self.setGeometry(self.target_rect(visible=False))
            self.show()
            self.raise_()
            self._animate(self.target_rect())
        elif not busy and self._shown:
            self._shown = False
            self._animate(self.target_rect(visible=False))
        self._pause.setEnabled(bool(busy and self._queue.current().interruptible)
                               if busy else False)
        self._paint_buttons()
        self.update()

    def _animate(self, to: QRect) -> None:
        self._slide.stop()
        self._slide.setStartValue(self.geometry())
        self._slide.setEndValue(to)
        self._slide.start()

    def _slid(self) -> None:
        if not self._shown:
            self.hide()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.expanded = not self.expanded
            self._animate(self.target_rect())
        super().mouseReleaseEvent(event)

    # -------------------------------------------------------------- drawing

    def paintEvent(self, event) -> None:  # noqa: N802
        if not self._tokens:
            return
        t = self._tokens
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        body = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        shape = QPainterPath()
        radius = PILL_H / 2 if not self.expanded else 18
        shape.addRoundedRect(body, radius, radius)
        painter.fillPath(shape, parse_colour(t.get("bg_3")))
        painter.setPen(QPen(parse_colour(t.get("line")), 1))
        painter.drawPath(shape)

        first, second, fraction = readout(self._queue)
        row_top = self.height() - PILL_H
        ring = QRectF(12, row_top + 10, 28, 28)
        painter.setPen(QPen(parse_colour(t.get("bg_4")), 4))
        painter.drawEllipse(ring)
        if fraction > 0:
            pen = QPen(parse_colour(t.get("accent")), 4)
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            painter.drawArc(ring, 90 * 16, int(-360 * 16 * fraction))

        text_left = 52
        room = self.width() - text_left - 90
        base = QFont(self.font())
        base.setPixelSize(13)
        small = QFont(self.font())
        small.setFamilies(["Cascadia Mono", "Consolas", "monospace"])
        small.setPixelSize(11)
        painter.setFont(base)
        painter.setPen(parse_colour(t.get("txt_0")))
        painter.drawText(QRectF(text_left, row_top + 6, room, 20),
                         int(Qt.AlignVCenter | Qt.AlignLeft),
                         QFontMetrics(base).elidedText(first, Qt.ElideMiddle, room))
        painter.setFont(small)
        painter.setPen(parse_colour(t.get("txt_2")))
        painter.drawText(QRectF(text_left, row_top + 24, room, 18),
                         int(Qt.AlignVCenter | Qt.AlignLeft),
                         QFontMetrics(small).elidedText(second, Qt.ElideRight, room))

        if self.expanded:
            self._card(painter, small, base)
        painter.end()

    def _card(self, painter: QPainter, small: QFont, base: QFont) -> None:
        t = self._tokens
        job = self._queue.current()
        painter.setFont(base)
        painter.setPen(parse_colour(t.get("txt_1")))
        painter.drawText(QRectF(16, 10, self.width() - 140, 22),
                         int(Qt.AlignVCenter | Qt.AlignLeft), "Speed, last minute")
        chart = QRectF(16, 38, self.width() - 32, 50)
        speeds = list(job.speeds) if job is not None else []
        painter.setPen(QPen(parse_colour(t.get("line_soft")), 1))
        painter.drawLine(QPointF(chart.left(), chart.bottom()), QPointF(chart.right(), chart.bottom()))
        if len(speeds) >= 2:
            top = max(speeds) or 1.0
            step = chart.width() / (len(speeds) - 1)
            line = QPainterPath()
            for i, value in enumerate(speeds):
                point = QPointF(chart.left() + i * step,
                                chart.bottom() - chart.height() * (value / top))
                line.moveTo(point) if i == 0 else line.lineTo(point)
            pen = QPen(parse_colour(t.get("accent")), 1.6)
            painter.setPen(pen)
            painter.drawPath(line)
        waiting = [queued for queued in self._queue.active if queued is not job][:3]
        painter.setFont(small)
        y = chart.bottom() + 10
        if not waiting:
            painter.setPen(parse_colour(t.get("txt_2")))
            painter.drawText(QRectF(16, y, self.width() - 32, 20),
                             int(Qt.AlignVCenter | Qt.AlignLeft), "Nothing queued behind it")
        for queued in waiting:
            painter.setPen(parse_colour(t.get("txt_1")))
            painter.drawText(QRectF(16, y, self.width() - 110, 20),
                             int(Qt.AlignVCenter | Qt.AlignLeft),
                             QFontMetrics(small).elidedText(queued.label, Qt.ElideMiddle,
                                                            int(self.width() - 110)))
            painter.setPen(parse_colour(t.get("txt_2")))
            painter.drawText(QRectF(self.width() - 100, y, 84, 20),
                             int(Qt.AlignVCenter | Qt.AlignRight),
                             "held" if queued.held else "queued")
            y += 22
        rule = QColor(parse_colour(t.get("line_soft")))
        painter.fillRect(QRectF(12, self.height() - PILL_H, self.width() - 24, 1), rule)

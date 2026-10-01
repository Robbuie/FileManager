"""Two things that float over the listing rather than sit in the layout.

0.49. Both are about what the pane says while somebody is in the middle of
something, and both are switches in Options.

**The selection pill.** With rows marked, the count and the total size sit over
the bottom of the listing with a copy to the other pane beside them. The status
line already says this, at the bottom edge of a pane where nobody's eyes are
while they are marking; the pill is where the eyes are.

**Placeholder rows.** A share folder that takes a while to answer used to show
an empty listing until the first batch landed, which reads as "nothing here" or
"stuck". Grey rows and the folder being read say "on its way". They appear only
after `DELAY_MS`, so a folder that answers at once never flickers them.

Neither touches the model's data beyond counting what the pane hands them, and
neither takes focus: the buttons are `NoFocus` so the listing keeps the
keyboard, and the pill calls the pane's claim when used, the rule every control
in a pane follows.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QPainter, QPainterPath
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QToolButton, QWidget

from app.ui.rows import parse_colour

#: How long a folder may take before placeholder rows appear.
DELAY_MS = 250

#: Placeholder rows drawn, and the widths they are drawn at, as a share of the
#: name column -- uneven so they read as names rather than as stripes.
SHAPES = (0.62, 0.48, 0.71, 0.55, 0.66, 0.42, 0.58, 0.69, 0.51, 0.6)


class SelectionPill(QFrame):
    """Count, total and a copy across, over the bottom of the listing."""

    copyRequested = Signal()
    clearRequested = Signal()
    used = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setProperty("role", "selection-pill")
        self.setAttribute(Qt.WA_StyledBackground, True)
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 4, 6, 4)
        row.setSpacing(8)
        self._text = QLabel("")
        self._text.setProperty("role", "pill-text")
        row.addWidget(self._text)
        self._copy = QToolButton()
        self._copy.setText("Copy to other pane")
        self._copy.setToolTip("The same as F5: the marked rows into the other "
                              "pane's folder, after the usual prompt.")
        self._clear = QToolButton()
        self._clear.setText("Clear")
        self._clear.setToolTip("Unmark everything (Ctrl+Shift+A).")
        for button, signal in ((self._copy, self.copyRequested),
                               (self._clear, self.clearRequested)):
            button.setProperty("role", "pill-button")
            button.setFocusPolicy(Qt.NoFocus)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(self.used)
            button.clicked.connect(signal)
            row.addWidget(button)
        self.hide()
        parent.installEventFilter(self)

    def show_selection(self, text: str) -> None:
        """Show `text`, or hide for an empty one."""
        if not text:
            self.hide()
            return
        self._text.setText(text)
        self.adjustSize()
        self._place()
        self.show()
        self.raise_()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt naming
        if watched is self.parent() and event.type() == QEvent.Resize and self.isVisible():
            self._place()
        return False

    def _place(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        width = min(self.sizeHint().width(), max(120, parent.width() - 24))
        self.resize(width, self.sizeHint().height())
        self.move((parent.width() - width) // 2,
                  parent.height() - self.height() - 14)


class Placeholders(QWidget):
    """Grey rows and "Reading ..." over an empty listing that is still arriving."""

    def __init__(self, viewport: QWidget) -> None:
        super().__init__(viewport)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._tokens: dict = {}
        self._text = ""
        self._row_h = 22
        self._phase = 0
        self._motion = True
        self._delay = QTimer(self)
        self._delay.setSingleShot(True)
        self._delay.setInterval(DELAY_MS)
        self._delay.timeout.connect(self._appear)
        self._pulse = QTimer(self)
        self._pulse.setInterval(90)
        self._pulse.timeout.connect(self._step)
        self.hide()
        viewport.installEventFilter(self)

    def apply_tokens(self, tokens: dict) -> None:
        self._tokens = tokens
        self.update()

    def set_row_height(self, height: int) -> None:
        self._row_h = max(14, int(height))

    def set_motion(self, on: bool) -> None:
        self._motion = bool(on)

    def want(self, waiting: bool, text: str = "") -> None:
        """Whether the listing is empty and still arriving."""
        self._text = text
        if waiting:
            if not self.isVisible() and not self._delay.isActive():
                self._delay.start()
            self.update()
            return
        self._delay.stop()
        self._pulse.stop()
        self.hide()

    @property
    def waiting(self) -> bool:
        return self.isVisible() or self._delay.isActive()

    def _appear(self) -> None:
        parent = self.parentWidget()
        if parent is not None:
            self.setGeometry(parent.rect())
        self.show()
        self.raise_()
        if self._motion:
            self._pulse.start()

    def _step(self) -> None:
        self._phase = (self._phase + 1) % 40
        self.update()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt naming
        if watched is self.parentWidget() and event.type() == QEvent.Resize:
            self.setGeometry(watched.rect())
        return False

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if not self._tokens:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        bar = parse_colour(self._tokens.get("bg_3"))
        ink = parse_colour(self._tokens.get("txt_2"))
        # A slow wave down the rows: each is a little brighter as it passes.
        wave = self._phase / 40.0
        top = 8.0
        small = QFont(self.font())
        small.setPixelSize(12)
        painter.setFont(small)
        if ink.isValid() and self._text:
            painter.setPen(ink)
            painter.drawText(QRectF(12, top, self.width() - 24, 18),
                             int(Qt.AlignLeft | Qt.AlignVCenter), self._text)
        top += 26
        name_w = max(80.0, self.width() * 0.55)
        for number, share in enumerate(SHAPES):
            y = top + number * self._row_h
            if y + self._row_h > self.height():
                break
            colour = bar
            if colour.isValid():
                lift = max(0.0, 1.0 - abs(((number / len(SHAPES)) - wave)) * 4)
                colour = parse_colour(self._tokens.get("bg_4")) if lift > 0.5 else bar
                path = QPainterPath()
                path.addRoundedRect(QRectF(14, y + 5, name_w * share, self._row_h - 10), 4, 4)
                path.addRoundedRect(QRectF(self.width() - 150, y + 5, 60, self._row_h - 10), 4, 4)
                painter.fillPath(path, colour)
        painter.end()

"""Photo details beside a picture (0.54), and quick view's strip of neighbours.

`PhotoInfo` draws what `app/io/photo.py` read: when the picture was taken, the
camera and lens, the exposure, the location as numbers, and a brightness
histogram. It is used under the picture in the preview panel and the peek
card, and as a side column in quick view. It reads nothing; the details arrive
on the `Preview` with the picture.

`Strip` is the row of the files either side of the cursor at the bottom of
quick view. Its pictures come from the pane's own thumbnail cache, the one the
grid uses -- the same bounded, one-request-per-folder path, asked for only
while quick view is open on a picture.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QBoxLayout,
    QFrame,
    QGridLayout,
    QLabel,
    QSizePolicy,
    QWidget,
)

#: The rows, in order, and the keys each is made of.
ROWS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Taken", ("taken",)),
    ("Camera", ("camera",)),
    ("Lens", ("lens",)),
    ("Exposure", ("exposure", "aperture", "iso")),
    ("Focal length", ("focal",)),
    ("Location", ("location",)),
    ("Software", ("software",)),
)


def rows(details: dict[str, Any]) -> list[tuple[str, str]]:
    """(label, value) for every row the details have something for."""
    out = []
    for label, keys in ROWS:
        value = "  ".join(str(details[key]) for key in keys if details.get(key))
        if value:
            out.append((label, value))
    return out


class Histogram(QWidget):
    """Brightness as bars, dark on the left. Painted, so it takes colours from
    the token set rather than a stylesheet."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._bars: list[int] = []
        self._bar = QColor(0, 0, 0, 0)
        self._base = QColor(0, 0, 0, 0)
        self.setMinimumHeight(48)
        self.setMaximumHeight(72)

    def set_colours(self, bar: str, base: str) -> None:
        self._bar = QColor(bar)
        self._base = QColor(base)
        self.update()

    def set_bars(self, bars: list[int]) -> None:
        self._bars = list(bars or [])
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        del event
        if not self._bars:
            return
        painter = QPainter(self)
        top = max(self._bars) or 1
        count = len(self._bars)
        width = self.width() / count
        height = self.height() - 1
        for index, value in enumerate(self._bars):
            bar = max(1, round(height * value / top)) if value else 0
            painter.fillRect(QRect(round(index * width), height - bar,
                                   max(1, round(width) - 1), bar), self._bar)
        painter.setPen(QPen(self._base, 1))
        painter.drawLine(0, height, self.width(), height)


class PhotoInfo(QFrame):
    """The details of one photograph, as label and value rows."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "photoinfo")
        self._grid = QGridLayout()
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(10)
        self._grid.setVerticalSpacing(3)
        self._histogram = Histogram()
        self._layout = QBoxLayout(QBoxLayout.TopToBottom, self)
        self._layout.setContentsMargins(2, 4, 2, 2)
        self._layout.setSpacing(8)
        self._layout.addLayout(self._grid, 1)
        self._layout.addWidget(self._histogram)
        self._stretch_at = self._layout.count()
        self._layout.addStretch(1)
        #: Pairs of label and value per line: one in a column, two in a band.
        self._pairs = 1
        self._details: dict[str, Any] | None = None
        self.hide()

    def set_band(self, band: bool) -> None:
        """A band under a picture (two pairs a line, histogram to the right) or
        a column beside one (one pair a line, histogram under)."""
        pairs = 2 if band else 1
        self._layout.setDirection(QBoxLayout.LeftToRight if band else QBoxLayout.TopToBottom)
        self._histogram.setFixedWidth(220 if band else 16777215)
        if pairs != self._pairs:
            self._pairs = pairs
            self.show_details(self._details)

    def apply_tokens(self, tokens: dict[str, str]) -> None:
        self._histogram.set_colours(tokens.get("txt_2", ""), tokens.get("line", ""))

    def show_details(self, details: dict[str, Any] | None) -> None:
        """Fill from a `Preview.details` of kind "photo", or hide."""
        while self._grid.count():
            widget = self._grid.takeAt(0).widget()
            if widget is not None:
                # Off the screen now, not at the next turn of the event loop:
                # a redraw in the same turn would otherwise paint both sets.
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        self._details = details
        if not details or details.get("kind") != "photo":
            self.hide()
            return
        for index, (label, value) in enumerate(rows(details)):
            row, pair = divmod(index, self._pairs)
            key = QLabel(label)
            key.setProperty("role", "photokey")
            text = QLabel(value)
            text.setProperty("role", "photovalue")
            text.setWordWrap(True)
            text.setMinimumWidth(110)
            text.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
            self._grid.addWidget(key, row, pair * 2, Qt.AlignTop)
            text.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            self._grid.addWidget(text, row, pair * 2 + 1)
        for pair in range(self._pairs):
            self._grid.setColumnStretch(pair * 2 + 1, 1)
        self._histogram.set_bars(details.get("histogram") or [])
        self._histogram.setVisible(bool(details.get("histogram")))
        self.show()


class Strip(QWidget):
    """The files either side of the cursor, small, with the current one ringed."""

    #: Cell size, and the gap between cells.
    CELL = QSize(76, 56)
    GAP = 6

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cells: list[tuple[QPixmap | None, bool]] = []
        self._ring = QColor(0, 0, 0, 0)
        self._ground = QColor(0, 0, 0, 0)
        self.setFixedHeight(self.CELL.height() + 6)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)

    def set_colours(self, ring: str, ground: str) -> None:
        self._ring = QColor(ring)
        self._ground = QColor(ground)
        self.update()

    def set_cells(self, cells: list[tuple[QPixmap | None, bool]]) -> None:
        """(picture or None while it is coming, whether it is the current file)."""
        self._cells = list(cells)
        self.setVisible(bool(cells))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        del event
        if not self._cells:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        step = self.CELL.width() + self.GAP
        total = len(self._cells) * step - self.GAP
        left = max(0, (self.width() - total) // 2)
        for index, (picture, current) in enumerate(self._cells):
            box = QRect(left + index * step, 3, self.CELL.width(), self.CELL.height())
            painter.fillRect(box, self._ground)
            if picture is not None and not picture.isNull():
                scaled = picture.size().scaled(box.size(), Qt.KeepAspectRatio)
                target = QRect(0, 0, scaled.width(), scaled.height())
                target.moveCenter(box.center())
                painter.drawPixmap(target, picture)
            if current:
                painter.setPen(QPen(self._ring, 2))
                painter.drawRect(box.adjusted(-1, -1, 1, 1))

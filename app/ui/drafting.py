"""The Blueprint drafting grid, drawn inside the listings.

0.35 drew it on the Deck -- the splitter behind the two panes -- and the panes
are opaque, so it only ever showed in the few pixels of gap around them. The
user's report after running it was that it was "mostly not visible", which was
an accurate description of where it had been put.

So from 0.39 it is drawn in each listing's viewport, between the background the
style sheet fills and the rows the delegate paints. That order is what an
event filter on the viewport's Paint event gets for free: Qt paints a widget's
styled background before it delivers the event, and the view draws its items
inside the event. Rows that paint their own band -- hover, selection, the
recency glow -- cover the grid where they are, which is correct: it is the
paper, not a layer over the work.

The grid moves with the content. Scrolling a view moves the pixels already on
screen and repaints only the strip that came into view, so a grid anchored to
the viewport would tear at every scroll. It is anchored to the content's
origin instead, read from the header offsets on a table and the scroll bars on
the thumbnail grid.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QAbstractItemView, QTableView

#: The cell, in logical pixels. A heavier line every fifth, as on graph paper.
CELL = 24
MAJOR_EVERY = 5


def lines(start: int, length: int, offset: int) -> list[tuple[int, bool]]:
    """Where the grid lines fall across one axis of the viewport.

    `offset` is how far the content has scrolled. Returns each line's pixel
    position within `start .. start + length` and whether it is a major line.
    Pure arithmetic, so the part that decides alignment is tested without a
    window.
    """
    if length <= 0:
        return []
    first = (start + offset) // CELL
    out: list[tuple[int, bool]] = []
    index = first
    while True:
        position = index * CELL - offset
        if position >= start + length:
            break
        if position >= start:
            out.append((position, index % MAJOR_EVERY == 0))
        index += 1
    return out


def _offsets(view: QAbstractItemView) -> tuple[int, int]:
    if isinstance(view, QTableView):
        return view.horizontalHeader().offset(), view.verticalHeader().offset()
    return view.horizontalScrollBar().value(), view.verticalScrollBar().value()


class DraftingGrid(QObject):
    """One grid, shared by every listing it is attached to."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._minor: QColor | None = None
        self._major: QColor | None = None
        self._views: list[QAbstractItemView] = []

    @property
    def shown(self) -> bool:
        return self._minor is not None

    def attach(self, view: QAbstractItemView) -> None:
        if view in self._views:
            return
        self._views.append(view)
        view.viewport().installEventFilter(self)
        view.destroyed.connect(lambda _obj=None, v=view: self._forget(v))

    def _forget(self, view) -> None:
        self._views = [each for each in self._views if each is not view]

    def set_colours(self, minor: QColor | None, major: QColor | None = None) -> None:
        if minor is None or not minor.isValid():
            self._minor = self._major = None
        else:
            self._minor = QColor(minor)
            self._major = QColor(major) if major is not None and major.isValid() \
                else QColor(minor)
        for view in list(self._views):
            try:
                view.viewport().update()
            except RuntimeError:  # the C++ side is already gone
                self._forget(view)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt naming
        if event.type() == QEvent.Paint and self._minor is not None:
            view = watched.parent()
            if isinstance(view, QAbstractItemView):
                self._paint(watched, view, event.rect())
        return False

    def _paint(self, viewport, view: QAbstractItemView, area) -> None:
        dx, dy = _offsets(view)
        painter = QPainter(viewport)
        try:
            top, height = area.top(), area.height()
            left, width = area.left(), area.width()
            for x, major in lines(left, width, dx):
                painter.fillRect(x, top, 1, height, self._major if major else self._minor)
            for y, major in lines(top, height, dy):
                painter.fillRect(left, y, width, 1, self._major if major else self._minor)
        finally:
            painter.end()

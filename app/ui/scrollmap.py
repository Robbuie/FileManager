"""The listing's scrollbar, with ticks for rows that are not on screen (0.34).

A `QScrollBar` that paints what `app.core.scrollmap` worked out on top of
itself. Only the painting is here; which rows and where they land is decided
without Qt, and the pane decides when to ask (debounced, because marking rows
with Ins held down is dozens of selection changes a second).

The ticks are narrow and translucent enough that the handle still reads as the
handle, and they are drawn whether or not the handle covers them: a mark under
the handle is on screen already, so hiding it would cost nothing and saying it
costs nothing either.
"""

from __future__ import annotations

from PySide6.QtCore import QRect
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QScrollBar, QStyle, QStyleOptionSlider

from app.core import scrollmap
from app.theme import sheet
from app.ui.rows import parse_colour

#: Height of a tick. Two pixels reads as a mark on a scrollbar eleven wide;
#: one reads as a rendering fault.
TICK = 2


class MapScrollBar(QScrollBar):

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._marks: dict[str, list[float]] = {}
        self._on = True
        self._colours = {}
        self.apply_tokens(sheet.tokens())

    def apply_tokens(self, tokens: dict[str, str]) -> None:
        # Which colour says which thing. Marked rows are the accent, because a
        # mark is a selection and selection is the accent everywhere else;
        # today is the age chip's green; a search hit is the warn yellow, the
        # colour the status line already gives a search.
        self._colours = {
            "marked": parse_colour(tokens.get("accent")),
            "today": parse_colour(tokens.get("good")),
            "hits": parse_colour(tokens.get("warn")),
        }
        self.update()

    def set_enabled_map(self, on: bool) -> None:
        self._on = bool(on)
        self.update()

    def set_marks(self, marks: dict[str, list[float]]) -> None:
        if marks != self._marks:
            self._marks = marks
            self.update()

    @property
    def marks(self) -> dict[str, list[float]]:
        return self._marks

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().paintEvent(event)
        if not self._on or not self._marks:
            return
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        groove = self.style().subControlRect(QStyle.CC_ScrollBar, option,
                                             QStyle.SC_ScrollBarGroove, self)
        if not groove.isValid() or groove.height() <= 0:
            groove = QRect(0, 0, self.width(), self.height())
        painter = QPainter(self)
        left = groove.left() + 2
        width = max(2, groove.width() - 4)
        for kind in scrollmap.KINDS:
            positions = self._marks.get(kind)
            colour = self._colours.get(kind)
            if not positions or colour is None or not colour.isValid():
                continue
            colour.setAlphaF(0.85)
            for y in scrollmap.pixels(positions, groove.top(),
                                      groove.height() - TICK):
                painter.fillRect(left, y, width, TICK, colour)
        painter.end()

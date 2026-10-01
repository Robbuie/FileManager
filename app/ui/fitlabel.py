"""A one-line label that gives way to the space it is given.

0.50.4. A plain `QLabel` asks its layout for at least the width of its whole
text. The status line under each pane holds the selection, the folder totals
and anything being counted, so it routinely runs to 500 px and more -- and
because it sits in the pane, that became the narrowest the pane could be made.
Dragging the divider to give the other pane the room stopped dead wherever the
sentence ended, and the pane read as locked to its size.

This label asks for no width at all. When the text does not fit it is cut
with an ellipsis at the end (or the start, for right-aligned text, so the
figure that matters stays in view), and the whole of it is the tooltip.
`text()` still answers with the full text.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPainter, QPalette
from PySide6.QtWidgets import QFrame, QLabel, QSizePolicy, QWidget


def elide_mode(alignment: Qt.AlignmentFlag) -> Qt.TextElideMode:
    """Where to cut: the end, unless the text is pinned to the right."""
    return Qt.ElideLeft if alignment & Qt.AlignRight else Qt.ElideRight


class FitLabel(QLabel):
    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(0)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        return QSize(0, super().minimumSizeHint().height())

    def shown_text(self) -> str:
        """The text as it is drawn at the current width."""
        room = max(0, self.contentsRect().width())
        return self.fontMetrics().elidedText(
            self.text(), elide_mode(self.alignment()), room)

    def setText(self, text: str) -> None:  # noqa: N802 - Qt naming
        super().setText(text)
        self._sync_tip()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self._sync_tip()

    def _sync_tip(self) -> None:
        full = self.text()
        self.setToolTip(full if full and self.shown_text() != full else "")

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        # The frame (and any styled background) the way QLabel would, then
        # the text cut to fit, in the colour the style sheet gave the label.
        QFrame.paintEvent(self, event)
        painter = QPainter(self)
        self.style().drawItemText(
            painter, self.contentsRect(), int(self.alignment()), self.palette(),
            self.isEnabled(), self.shown_text(), QPalette.WindowText)
        painter.end()

"""The key hints along the bottom of the window, drawn as keycaps.

The user chose hints over a clickable function-key bar (16 September), so these
are not buttons and never take a click. They sit in the status bar as an
ordinary widget, which means a status message covers them while it is showing
and they come back when it goes -- the hints are there when nothing else has
anything to say, which is the only time anybody reads them.

Painted rather than built from labels because fourteen labels and seven styled
frames is a lot of widgets for one line of text, and the keycap's heavier
bottom edge is a thing a stylesheet can only fake.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import QWidget

#: What the bar says, in order. Keys first, because a person scanning the row
#: is looking for a key they half remember.
HINTS: tuple[tuple[str, str], ...] = (
    ("F3", "View"),
    ("F5", "Copy"),
    ("F6", "Move"),
    ("F7", "New folder"),
    ("F2", "Rename"),
    ("Del", "Recycle"),
    ("Ctrl B", "Flat view"),
    ("Ctrl L", "Go to"),
)

_PAD_X = 5.0
_GAP_CAP = 7.0
_GAP_ITEM = 18.0


class HintBar(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._tokens: dict[str, str] = {}
        self._hints = HINTS

    def apply_tokens(self, tokens: dict[str, str]) -> None:
        self._tokens = tokens
        self.updateGeometry()
        self.update()

    def _fonts(self) -> tuple[QFont, QFont]:
        label = QFont(self.font())
        label.setPixelSize(12)
        key = QFont(label)
        key.setFamilies(["Cascadia Mono", "Consolas", "monospace"])
        key.setPixelSize(10)
        return label, key

    def layout_items(self) -> list[tuple[QRectF, float, str, str]]:
        """Each hint's keycap rectangle and where its label starts."""
        label_font, key_font = self._fonts()
        label_metrics = QFontMetricsF(label_font)
        key_metrics = QFontMetricsF(key_font)
        height = 17.0
        top = max(0.0, (self.height() - height) / 2)
        x = 8.0
        out = []
        for key, label in self._hints:
            cap = max(height, key_metrics.horizontalAdvance(key) + 2 * _PAD_X)
            rect = QRectF(x, top, cap, height)
            text_x = x + cap + _GAP_CAP
            out.append((rect, text_x, key, label))
            x = text_x + label_metrics.horizontalAdvance(label) + _GAP_ITEM
        return out

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        items = self.layout_items()
        if not items:
            return QSize(0, 20)
        label_font, _ = self._fonts()
        rect, text_x, _key, label = items[-1]
        width = text_x + QFontMetricsF(label_font).horizontalAdvance(label) + 8
        return QSize(int(width), 22)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(0, 20)

    def paintEvent(self, event) -> None:  # noqa: N802
        if not self._tokens:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        label_font, key_font = self._fonts()
        edge = QColor(self._tokens["line"])
        for rect, text_x, key, label in self.layout_items():
            if rect.right() > self.width():
                break
            painter.setPen(QPen(edge, 1.0))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 4, 4)
            # The heavier bottom edge is what makes a rounded box a key.
            painter.drawLine(rect.left() + 3, rect.bottom() - 0.5,
                             rect.right() - 3, rect.bottom() - 0.5)
            painter.setFont(key_font)
            painter.setPen(QColor(self._tokens["txt_1"]))
            painter.drawText(rect.adjusted(0, -1, 0, 0), Qt.AlignCenter, key)
            painter.setFont(label_font)
            painter.setPen(QColor(self._tokens["txt_2"]))
            painter.drawText(QRectF(text_x, 0, 400, self.height()),
                             Qt.AlignVCenter | Qt.AlignLeft, label)
        painter.end()

"""The command palette: a box over the window that reaches everything.

See `app/core/palette.py` for what it offers and how it ranks. This is the
widget: a child of the window rather than a popup window, so it cannot end up
on another screen, behind the window, or outliving it, and so the title bar
and the panes stay visible around it the way the mockup shows.

It holds the keyboard while it is open. Up and Down move, Enter runs, Esc and
a click outside close. The rows are painted, for the reason the listing's are:
a label, a detail in the muted grey and a keycap on the right is three styles
on one line, which an item view's stylesheet cannot do.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QFont, QFontMetrics, QPainter, QPainterPath
from PySide6.QtWidgets import QFrame, QLineEdit, QVBoxLayout, QWidget

from app.core import palette as core
from app.ui import glyphs
from app.ui.rows import parse_colour

WIDTH = 640
ROW = 44
SECTION = {"command": "Command", "favorite": "Saved", "recent": "Recent",
           "here": "Here", "path": "Path"}


class _Rows(QWidget):
    def __init__(self, owner: "CommandPalette") -> None:
        super().__init__(owner)
        self._owner = owner
        self.setMouseTracking(True)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        return QSize(WIDTH, max(ROW, len(self._owner.items) * ROW) + 8)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        row = int((event.position().y() - 4) // ROW)
        if 0 <= row < len(self._owner.items) and row != self._owner.current:
            self._owner.current = row
            self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._owner.run_current()

    def paintEvent(self, event) -> None:  # noqa: N802
        tokens = self._owner.tokens
        if not tokens:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        base = QFont(self.font())
        base.setPixelSize(14)
        small = QFont(self.font())
        small.setPixelSize(11)
        key_font = QFont(small)
        key_font.setFamilies(["Cascadia Mono", "Consolas", "monospace"])
        key_font.setPixelSize(10)
        if not self._owner.items:
            painter.setFont(base)
            painter.setPen(parse_colour(tokens.get("txt_2")))
            painter.drawText(QRect(16, 4, self.width() - 32, ROW),
                             int(Qt.AlignVCenter | Qt.AlignLeft), "Nothing matches")
            painter.end()
            return
        ratio = float(self.devicePixelRatioF() or 1.0)
        for row, item in enumerate(self._owner.items):
            rect = QRectF(8, 4 + row * ROW, self.width() - 16, ROW - 2)
            if row == self._owner.current:
                band = QPainterPath()
                band.addRoundedRect(rect, 9, 9)
                painter.fillPath(band, parse_colour(tokens.get("accent_soft")))
            icon_box = QRectF(rect.left() + 10, rect.center().y() - 13, 26, 26)
            chip = QPainterPath()
            chip.addRoundedRect(icon_box, 7, 7)
            current = row == self._owner.current
            painter.fillPath(chip, parse_colour(tokens.get("accent" if current else "bg_3")))
            glyph = {"command": "mark", "path": "forward"}.get(item.kind, "folder")
            picture = glyphs.icon(glyph, colour=tokens.get("bg_0" if current else "txt_1", ""),
                                  muted=tokens.get("txt_2", ""), size=14, ratio=ratio)
            painter.drawPixmap(int(icon_box.center().x() - 7), int(icon_box.center().y() - 7),
                               picture.pixmap(14, 14))

            right = rect.right() - 12
            if item.shortcut:
                painter.setFont(key_font)
                width = QFontMetrics(key_font).horizontalAdvance(item.shortcut) + 12
                cap = QRectF(right - width, rect.center().y() - 9, width, 18)
                painter.setPen(parse_colour(tokens.get("line")))
                painter.setBrush(Qt.NoBrush)
                painter.drawRoundedRect(cap, 4, 4)
                painter.setPen(parse_colour(tokens.get("txt_1")))
                painter.drawText(cap, int(Qt.AlignCenter), item.shortcut)
                right = cap.left() - 10

            text_left = icon_box.right() + 12
            label = item.label
            if item.checked is not None:
                label = f"{label}  ({'on' if item.checked else 'off'})"
            painter.setFont(base)
            painter.setPen(parse_colour(tokens.get("txt_0")))
            metrics = QFontMetrics(base)
            room = int(right - text_left)
            shown = metrics.elidedText(label, Qt.ElideRight, room)
            painter.drawText(QRectF(text_left, rect.top(), room, rect.height()),
                             int(Qt.AlignVCenter | Qt.AlignLeft), shown)
            detail = item.detail or SECTION.get(item.kind, "")
            if detail:
                used = metrics.horizontalAdvance(shown) + 12
                painter.setFont(small)
                painter.setPen(parse_colour(tokens.get("txt_2")))
                painter.drawText(
                    QRectF(text_left + used, rect.top(), max(0, room - used), rect.height()),
                    int(Qt.AlignVCenter | Qt.AlignLeft),
                    QFontMetrics(small).elidedText(detail, Qt.ElideMiddle, max(0, room - used)))
        painter.end()


class CommandPalette(QFrame):
    #: An item was chosen. The window decides what that means.
    chosen = Signal(object)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setProperty("role", "palette")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.hide()
        self.tokens: dict[str, str] = {}
        self.items: list[core.Item] = []
        self.current = 0
        self._sources: list[core.Item] = []

        self.field = QLineEdit()
        self.field.setProperty("role", "palettefield")
        self.field.setPlaceholderText("Go anywhere, run anything     > commands   @ saved   # recent   / here")
        self.field.textChanged.connect(self._refilter)
        self.field.installEventFilter(self)
        self.rows = _Rows(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)
        layout.addWidget(self.field)
        layout.addWidget(self.rows)
        parent.installEventFilter(self)

    def apply_tokens(self, tokens: dict[str, str]) -> None:
        self.tokens = tokens
        self.rows.update()

    def open(self, sources: list[core.Item], text: str = "") -> None:
        self._sources = sources
        self.field.setText(text)
        self._refilter(text)
        self._place()
        self.show()
        self.raise_()
        self.field.setFocus(Qt.PopupFocusReason)
        self.field.selectAll()

    def close_palette(self) -> None:
        self.hide()
        self._sources = []
        parent = self.parentWidget()
        if parent is not None and hasattr(parent, "focus_active_pane"):
            parent.focus_active_pane()

    def _refilter(self, text: str) -> None:
        self.items = core.rank(text, self._sources)
        self.current = 0
        self.rows.updateGeometry()
        self._place()
        self.rows.update()

    def _place(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        width = min(WIDTH, parent.width() - 40)
        height = self.field.sizeHint().height() + self.rows.sizeHint().height() + 24
        self.setGeometry((parent.width() - width) // 2, 64, width, height)

    def move_current(self, step: int) -> None:
        if self.items:
            self.current = (self.current + step) % len(self.items)
            self.rows.update()

    def run_current(self) -> None:
        if not self.items:
            return
        item = self.items[self.current]
        self.close_palette()
        self.chosen.emit(item)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt naming
        if watched is self.field and event.type() == QEvent.FocusOut \
                and event.reason() != Qt.PopupFocusReason and self.isVisible():
            # Clicking anywhere else -- a pane, the rail, the title bar -- is
            # the other way to close it. The rows take no focus, so a click on
            # one keeps it on the field.
            self.hide()
        if watched is self.field and event.type() == QEvent.KeyPress:
            key = event.key()
            if key == Qt.Key_Down:
                self.move_current(1)
                return True
            if key == Qt.Key_Up:
                self.move_current(-1)
                return True
            if key in (Qt.Key_Return, Qt.Key_Enter):
                self.run_current()
                return True
            if key == Qt.Key_Escape:
                self.close_palette()
                return True
        if watched is self.parentWidget() and self.isVisible():
            if event.type() == QEvent.Resize:
                self._place()
        return super().eventFilter(watched, event)

"""The row at the top of the window that replaces the title bar and the menu bar.

Three things live in it and nothing else, because a title bar is also the
thing somebody drags the window by, and every control added to it is a piece of
it that no longer drags:

- the application mark at the left, which opens every menu the menu bar used
  to show, as one menu;
- a wide "go anywhere" button, which opens the command palette (Ctrl+K);
- the three caption buttons.

The tabs stay in the panes. There are two panes and each has its own tabs, so
there is no single strip that could move up here without losing which side a
tab belongs to.

The maximise button's clicks are answered by `winframe.NativeFrame` on Windows,
not by Qt -- see that module for why. Off Windows it is an ordinary button.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolButton,
    QWidget,
)

from app.ui import glyphs

HEIGHT = 42


class TitleBar(QWidget):
    menuRequested = Signal(QPoint)
    goRequested = Signal()
    minimizeRequested = Signal()
    maximizeRequested = Signal()
    closeRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "titlebar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(HEIGHT)

        self.mark = QToolButton()
        self.mark.setProperty("role", "mark")
        self.mark.setToolTip("Menu")
        self.mark.setAccessibleName("Menu")
        self.mark.setFocusPolicy(Qt.NoFocus)
        self.mark.clicked.connect(
            lambda: self.menuRequested.emit(
                self.mark.mapToGlobal(QPoint(0, self.mark.height() + 4))))

        self.go = QPushButton()
        self.go.setProperty("role", "gobox")
        self.go.setFocusPolicy(Qt.NoFocus)
        self.go.setToolTip("Go anywhere, run anything (Ctrl+K)")
        self.go.clicked.connect(self.goRequested)
        inner = QHBoxLayout(self.go)
        inner.setContentsMargins(10, 0, 6, 0)
        inner.setSpacing(8)
        self._go_icon = QLabel()
        self._go_icon.setProperty("role", "goicon")
        self._go_text = QLabel("Go anywhere, run anything")
        self._go_text.setProperty("role", "gotext")
        self._go_key = QLabel("Ctrl K")
        self._go_key.setProperty("role", "keycap")
        for label in (self._go_icon, self._go_text, self._go_key):
            label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        inner.addWidget(self._go_icon)
        inner.addWidget(self._go_text, 1)
        inner.addWidget(self._go_key)
        self.go.setFixedWidth(300)

        self.min_button = self._caption("Minimize", self.minimizeRequested)
        self.max_button = self._caption("Maximize", self.maximizeRequested)
        self.close_button = self._caption("Close", self.closeRequested)
        self.close_button.setProperty("kind", "close")

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self.mark)
        row.addStretch(1)
        row.addWidget(self.go)
        row.addSpacing(12)
        row.addWidget(self.min_button)
        row.addWidget(self.max_button)
        row.addWidget(self.close_button)

        self._tokens: dict[str, str] = {}
        self._maximized = False

    def _caption(self, name: str, signal) -> QToolButton:
        button = QToolButton()
        button.setProperty("role", "caption")
        button.setAccessibleName(name)
        button.setToolTip(name)
        button.setFocusPolicy(Qt.NoFocus)
        button.setFixedSize(46, HEIGHT)
        button.clicked.connect(signal)
        return button

    def apply_tokens(self, tokens: dict[str, str]) -> None:
        self._tokens = tokens
        ratio = float(self.devicePixelRatioF() or 1.0)
        self.mark.setIcon(glyphs.icon("mark", colour=tokens["bg_0"],
                                      muted=tokens["bg_0"], size=14, ratio=ratio))
        self._go_icon.setPixmap(glyphs.icon(
            "search", colour=tokens["txt_2"], muted=tokens["txt_2"], size=14,
            ratio=ratio).pixmap(14, 14))
        self._paint_captions()

    def set_maximized(self, maximized: bool) -> None:
        self._maximized = maximized
        self.max_button.setToolTip("Restore" if maximized else "Maximize")
        self._paint_captions()

    def set_max_hover(self, hot: bool) -> None:
        """The native frame's hover, since Qt never sees the pointer there."""
        if self.max_button.property("hot") == ("true" if hot else "false"):
            return
        self.max_button.setProperty("hot", "true" if hot else "false")
        self.max_button.style().unpolish(self.max_button)
        self.max_button.style().polish(self.max_button)

    def _paint_captions(self) -> None:
        if not self._tokens:
            return
        ratio = float(self.devicePixelRatioF() or 1.0)
        tone = self._tokens["txt_1"]
        for button, name in ((self.min_button, "minimize"),
                             (self.max_button, "restore" if self._maximized else "maximize"),
                             (self.close_button, "close")):
            button.setIcon(glyphs.icon(name, colour=tone, muted=tone, size=14,
                                       ratio=ratio))

    def is_caption(self, pos: QPoint) -> bool:
        """True where a press should drag the window: any part of this row
        that is not one of its buttons."""
        if not self.rect().contains(pos):
            return False
        child = self.childAt(pos)
        return child is None or child is self

    # Off Windows, or when the native frame could not attach, the row still
    # has to move the window. On Windows these never fire over the caption,
    # because the native frame has told Windows that region is a caption.
    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.button() == Qt.LeftButton and self.is_caption(event.position().toPoint()):
            handle = self.window().windowHandle()
            if handle is not None:
                handle.startSystemMove()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and self.is_caption(event.position().toPoint()):
            self.maximizeRequested.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

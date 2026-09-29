"""The basket's tray: what is in it, and the two things to do with it (0.38).

A small card over the bottom left of the window, shown while the basket has
anything in it and gone when it is emptied. It does no transfer itself: Copy
here and Move here are signals, and the window puts up the same prompt F5
does, with the pane that has the keyboard as the destination -- so a basket is
never a path from a click to a written file that no dialog saw.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

#: Rows the tray lists before it only counts.
SHOWN = 6
WIDTH = 330
MARGIN = 16


class BasketTray(QFrame):
    copyRequested = Signal()
    moveRequested = Signal()

    def __init__(self, basket, parent: QWidget) -> None:
        super().__init__(parent)
        self._basket = basket
        self._enabled = True
        self.setProperty("role", "basket")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.NoFocus)

        self._title = QLabel("")
        self._title.setProperty("role", "baskettitle")
        self._list = QListWidget()
        self._list.setFocusPolicy(Qt.NoFocus)
        self._list.setSelectionMode(QListWidget.NoSelection)
        self._list.setProperty("role", "basketlist")

        copy = QPushButton("Copy here")
        move = QPushButton("Move here")
        empty = QPushButton("Empty")
        for button in (copy, move, empty):
            button.setFocusPolicy(Qt.NoFocus)
            button.setAutoDefault(False)
        copy.setToolTip("Copy everything in the basket into the folder of the "
                        "pane that has the keyboard, after the usual prompt.")
        move.setToolTip("The same, moving instead.")
        copy.clicked.connect(self.copyRequested)
        move.clicked.connect(self.moveRequested)
        empty.clicked.connect(basket.clear)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        buttons.addWidget(copy)
        buttons.addWidget(move)
        buttons.addStretch(1)
        buttons.addWidget(empty)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)
        layout.addWidget(self._title)
        layout.addWidget(self._list)
        layout.addLayout(buttons)

        basket.changed.connect(self.refresh)
        parent.installEventFilter(self)
        self.hide()

    def set_enabled(self, on: bool) -> None:
        self._enabled = bool(on)
        self.refresh()

    def refresh(self) -> None:
        count = len(self._basket)
        if not count or not self._enabled:
            self.hide()
            return
        folders = self._basket.folders()
        self._title.setText(
            f"Basket  ·  {count:,} item{'s' if count != 1 else ''}"
            + (f" from {folders:,} folders" if folders > 1 else ""))
        self._list.clear()
        names = self._basket.names()
        self._list.addItems(names[:SHOWN])
        if count > SHOWN:
            self._list.addItem(f"and {count - SHOWN:,} more")
        row = self._list.sizeHintForRow(0) if self._list.count() else 18
        self._list.setFixedHeight(self._list.count() * row + 8)
        self.adjustSize()
        self._place()
        self.show()
        self.raise_()

    def _place(self) -> None:
        parent = self.parentWidget()
        bar = getattr(parent, "statusBar", None)
        bottom = parent.height() - (bar().height() if callable(bar) else 0) - MARGIN
        height = self.sizeHint().height()
        self.setGeometry(MARGIN, bottom - height, WIDTH, height)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt naming
        if watched is self.parentWidget() and event.type() == event.Type.Resize \
                and self.isVisible():
            self._place()
        return super().eventFilter(watched, event)

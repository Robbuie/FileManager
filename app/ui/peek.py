"""The peek card: Space on a file, a large preview over the window, Space again
to put it away (0.36, when Options > Listing > Space on a file is set to Peek).

Somewhere between the preview pane and the viewer. The pane is always there
and small; the viewer is a window of its own with zoom and a walk through the
folder. The card is neither: it is the answer to "what is this one?" asked
with one key, big enough to read a drawing's title block, and gone the moment
the key is pressed again. The arrow keys move the cursor in the listing behind
it and the card follows, which is how a folder of drawings gets looked through
without opening anything.

It is a child of the window rather than a window of its own, so it can never
be left behind on another screen, sit under the window, or take the taskbar's
attention; and it reuses `PreviewPanel` for the body so a picture, a text file
and a Logix export look the way they do in the pane, only larger.

It reads nothing itself. The window asks `core.previews` for the file and
hands the answer in, exactly as it does for the preview pane.
"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QRect, Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from app.io.protocol import Preview
from app.ui.preview import PreviewPanel

#: How much of the window the card covers, each way.
SHARE_W = 0.72
SHARE_H = 0.82
GROW_MS = 160


class PeekCard(QFrame):
    """The floating card. Keys: Space or Esc to close, Up and Down to step,
    Enter to open the file, F3 for the viewer."""

    closed = Signal()
    stepRequested = Signal(int)
    openRequested = Signal()
    viewRequested = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setProperty("role", "peek")
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._motion = True
        self._animation = QPropertyAnimation(self, b"geometry", self)
        self._animation.setDuration(GROW_MS)
        self._animation.setEasingCurve(QEasingCurve.OutCubic)

        self._body = PreviewPanel()
        self._body.setProperty("role", "peekbody")

        hints = QHBoxLayout()
        hints.setSpacing(16)
        for key, what in (("Space", "close"), ("Up  Down", "next file"),
                          ("F3", "viewer"), ("Enter", "open")):
            cap = QLabel(key)
            cap.setProperty("role", "keycap")
            label = QLabel(what)
            label.setProperty("role", "note")
            pair = QHBoxLayout()
            pair.setSpacing(6)
            pair.addWidget(cap)
            pair.addWidget(label)
            hints.addLayout(pair)
        hints.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 10)
        layout.setSpacing(8)
        layout.addWidget(self._body, 1)
        layout.addLayout(hints)
        self.hide()

    def apply_tokens(self, tokens: dict[str, str]) -> None:
        self._body.apply_tokens(tokens)

    def set_motion(self, on: bool) -> None:
        self._motion = bool(on)

    # ------------------------------------------------------------ showing

    def target_rect(self) -> QRect:
        parent = self.parentWidget()
        width = int(parent.width() * SHARE_W)
        height = int(parent.height() * SHARE_H)
        box = QRect(0, 0, width, height)
        box.moveCenter(parent.rect().center())
        return box

    def open_from(self, origin: QRect) -> None:
        """Show the card, growing out of `origin` when there is one and motion
        is on. `origin` is the cursor's row in the window's coordinates."""
        end = self.target_rect()
        self.raise_()
        self.show()
        self.setFocus(Qt.OtherFocusReason)
        if self._motion and origin.isValid() and not origin.isEmpty():
            self._animation.stop()
            self._animation.setStartValue(origin)
            self._animation.setEndValue(end)
            self._animation.start()
        else:
            self.setGeometry(end)

    def waiting(self, path: str, name: str) -> None:
        self._body.waiting(path, name)

    def show_answer(self, path: str, name: str, answer: Preview) -> None:
        if path == self._body.path:
            self._body.show_preview(path, name, answer)

    def problem(self, path: str, why: str) -> None:
        if path == self._body.path:
            self._body.problem(path, why)

    def not_a_file(self, name: str) -> None:
        """The cursor stepped onto a folder: say so rather than show the last
        file under the wrong name."""
        self._body.clear(f"{name} is a folder")

    @property
    def path(self) -> str:
        return self._body.path

    def close_card(self) -> None:
        if self.isVisible():
            self._animation.stop()
            self.hide()
            self.closed.emit()

    # ------------------------------------------------------------ keys

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        key = event.key()
        if key in (Qt.Key_Space, Qt.Key_Escape):
            self.close_card()
            return
        if key in (Qt.Key_Down, Qt.Key_Up):
            self.stepRequested.emit(1 if key == Qt.Key_Down else -1)
            return
        if key in (Qt.Key_Return, Qt.Key_Enter):
            self.close_card()
            self.openRequested.emit()
            return
        if key == Qt.Key_F3:
            self.close_card()
            self.viewRequested.emit()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: N802 - Qt naming
        # A click anywhere else puts it away: a card that stays up behind a
        # dialog, or over a pane somebody has clicked into, is in the way. A
        # popup (a tooltip, a menu) borrowing the focus for a moment is not
        # somebody leaving.
        super().focusOutEvent(event)
        if self.isVisible() and event.reason() != Qt.PopupFocusReason:
            self.close_card()

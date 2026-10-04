"""Quick view (0.52): the other pane becomes a preview of the file under the
cursor, as Double Commander's Ctrl+Q does.

The preview panel was always going to be small. It lives inside a pane, beside
that pane's listing, and is capped at half of it -- so in a dual-pane window it
is a quarter of the screen, and a photograph or a drawing in a quarter of the
screen is a thumbnail with a caption. Quick view takes the other route: in a
dual-pane file manager, while one pane is being read the other is usually only
waiting to be a destination, so for as long as somebody is looking through
files it can be the place they are shown.

Three decisions shape it.

  * **It is a child of the window laid over the other pane, not a page inside
    that pane.** The active pane is decided by which pane holds the keyboard
    (`MainWindow._on_focus_changed`), and a preview that lived *inside* the
    covered pane would make that pane active the moment somebody clicked the
    picture to zoom it -- and F5 would then copy from the pane nobody is
    reading. Laid over it from outside, the covered pane is untouched: its tabs,
    its folder and its cursor are all where they were when quick view closes.
  * **The keyboard stays in the listing.** Up and Down walk the folder in the
    pane being read and the picture follows. Tab moves into the picture, where
    the keys zoom and pan, and Tab or Esc comes back; nothing here owns a key
    the listing needs.
  * **It reads nothing.** The window asks `core.previews` for the file, the same
    single-request, debounced, cancelled-at-the-worker path the preview panel
    uses, and hands the answer in.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.core.previews import describe
from app.io.protocol import Preview, PreviewForm
from app.ui.preview import is_logix
from app.ui.viewer import ImageView, hex_dump


class QuickView(QFrame):
    """The panel laid over the other pane."""

    #: Esc or Tab inside the panel: give the keyboard back to the listing.
    backRequested = Signal()
    #: Up or Down inside the panel: move the cursor in the listing being read.
    stepRequested = Signal(int)
    openRequested = Signal()
    viewRequested = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setProperty("role", "quickview")
        self.setAttribute(Qt.WA_StyledBackground, True)
        # Click focus only: Tab is the window's "other pane" key and is routed
        # here explicitly, so the panel never joins the tab chain by itself.
        self.setFocusPolicy(Qt.ClickFocus)
        self._path = ""
        self._answer: Preview | None = None

        tag = QLabel("Quick view")
        tag.setProperty("role", "quicktag")
        self._name = QLabel("")
        self._name.setProperty("role", "quickname")
        self._name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._where = QLabel("")
        self._where.setProperty("role", "note")
        self._zoom = QLabel("")
        self._zoom.setProperty("role", "note")
        head = QHBoxLayout()
        head.setContentsMargins(4, 0, 4, 0)
        head.setSpacing(10)
        head.addWidget(tag)
        head.addWidget(self._name, 1)
        head.addWidget(self._where)
        head.addWidget(self._zoom)

        self._image = ImageView()
        self._image.zoomChanged.connect(self._show_zoom)
        self._text = QPlainTextEdit()
        self._text.setReadOnly(True)
        self._text.setProperty("role", "preview")
        self._text.setLineWrapMode(QPlainTextEdit.NoWrap)
        self._text.setFocusPolicy(Qt.NoFocus)
        self._note = QLabel("")
        self._note.setProperty("role", "note")
        self._note.setAlignment(Qt.AlignCenter)
        self._note.setWordWrap(True)
        from app.ui.logixcard import LogixCard

        self._card = LogixCard()
        self._stack = QStackedWidget()
        self._stack.setProperty("role", "quickbody")
        for widget in (self._image, self._text, self._note, self._card):
            self._stack.addWidget(widget)

        self._about = QLabel("")
        self._about.setProperty("role", "space")
        # Ignored, so a long line is cut at the panel's edge rather than
        # widening the panel past the pane it is laid over.
        self._about.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._keys = QLabel("")
        self._keys.setProperty("role", "note")
        foot = QHBoxLayout()
        foot.setContentsMargins(4, 0, 4, 0)
        foot.setSpacing(12)
        foot.addWidget(self._about, 1)
        foot.addWidget(self._keys)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 8)
        layout.setSpacing(8)
        layout.addLayout(head)
        layout.addWidget(self._stack, 1)
        layout.addLayout(foot)
        self._sync_keys()
        self.hide()

    # ------------------------------------------------------------ chrome

    def apply_tokens(self, tokens: dict[str, str]) -> None:
        # No literal colour as a fallback; see `app/ui/viewer.py`.
        self._image.set_backdrop(tokens.get("bg_0", ""))
        mono = tokens.get("mono", "monospace")
        font = self._text.font()
        font.setFamilies([part.strip().strip('"') for part in mono.split(",")])
        self._text.setFont(font)

    def set_side(self, reading: str) -> None:
        """Which pane is being read, said once in the header."""
        self._where.setText(f"{reading} pane")

    # ------------------------------------------------------------ content

    @property
    def path(self) -> str:
        return self._path

    def waiting(self, path: str, name: str) -> None:
        self._path = path
        self._name.setText(name)
        if self._answer is None:
            # Only the first time. Once something is on screen it stays until
            # the next answer replaces it, so arrowing down a folder of photos
            # reads as pictures changing rather than as a panel flashing.
            self._note.setText("reading...")
            self._stack.setCurrentWidget(self._note)

    def show_answer(self, path: str, name: str, answer: Preview) -> None:
        if path != self._path:
            return
        self._answer = answer
        self._name.setText(name)
        if answer.form is PreviewForm.IMAGE:
            picture = QPixmap()
            if picture.loadFromData(answer.image or b"", "PNG") and not picture.isNull():
                self._image.set_picture(picture)
                self._stack.setCurrentWidget(self._image)
            else:
                self._say("the picture did not decode")
                return
        elif is_logix(answer):
            self._card.show_summary(answer.details, "")
            self._stack.setCurrentWidget(self._card)
        elif answer.form is PreviewForm.TEXT:
            self._text.setPlainText(answer.text)
            self._stack.setCurrentWidget(self._text)
        elif answer.form is PreviewForm.HEX:
            self._text.setPlainText(hex_dump(answer.data or b""))
            self._stack.setCurrentWidget(self._text)
        else:
            self._say(answer.note or "no preview")
            return
        self._about.setText(describe(answer))
        self._show_zoom()

    def problem(self, path: str, why: str) -> None:
        if path == self._path:
            self._say(why)

    def not_a_file(self, name: str) -> None:
        """The cursor is on a folder or `..`. Said, rather than leaving the last
        file on screen under a name it no longer has."""
        self._path = ""
        self._answer = None
        self._name.setText(name)
        self._say("a folder" if name and name != ".." else "nothing to show")

    def _say(self, why: str) -> None:
        self._answer = None
        self._note.setText(why)
        self._stack.setCurrentWidget(self._note)
        self._about.setText("")
        self._zoom.setText("")

    def _show_zoom(self, *_args) -> None:
        if self._stack.currentWidget() is self._image:
            self._zoom.setText("fit" if self._image.fitting
                               else f"{self._image.zoom * 100:.0f}%")
        else:
            self._zoom.setText("")

    def _sync_keys(self) -> None:
        if self.hasFocus():
            self._keys.setText("+ - zoom   F fit   1 one to one   Tab back")
        else:
            self._keys.setText("Tab zoom   Ctrl+Q close")

    # ------------------------------------------------------------ keys

    def focusInEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().focusInEvent(event)
        self.setProperty("focused", "true")
        self.style().unpolish(self)
        self.style().polish(self)
        self._sync_keys()

    def focusOutEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().focusOutEvent(event)
        self.setProperty("focused", "false")
        self.style().unpolish(self)
        self.style().polish(self)
        self._sync_keys()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        key = event.key()
        if key in (Qt.Key_Escape, Qt.Key_Tab, Qt.Key_Backtab):
            self.backRequested.emit()
            return
        if key in (Qt.Key_Down, Qt.Key_Up):
            self.stepRequested.emit(1 if key == Qt.Key_Down else -1)
            return
        if key in (Qt.Key_Return, Qt.Key_Enter):
            self.openRequested.emit()
            return
        if key == Qt.Key_F3:
            self.viewRequested.emit()
            return
        if self._stack.currentWidget() is self._image:
            if key in (Qt.Key_Plus, Qt.Key_Equal):
                self._image.step_zoom(1)
                return
            if key == Qt.Key_Minus:
                self._image.step_zoom(-1)
                return
            if key == Qt.Key_F:
                self._image.fit()
                self._show_zoom()
                return
            if key in (Qt.Key_1, Qt.Key_0):
                self._image.set_zoom(1.0)
                return
        if self._stack.currentWidget() is self._text:
            if key in (Qt.Key_PageDown, Qt.Key_PageUp, Qt.Key_Home, Qt.Key_End):
                self._text.keyPressEvent(event)
                return
        super().keyPressEvent(event)

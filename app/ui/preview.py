"""The preview pane: what the cursor is standing on, beside the listing.

The same three shapes the viewer draws and the same decoder behind them, at a
fraction of the size and without any of the keys. What it adds is that nobody
asked for it: the viewer is opened deliberately and this follows the cursor, so
every decision here is about not being expensive and not being in the way.

  * **It is inside the pane**, on a splitter, rather than being one panel for
    the window. In a dual-pane file manager the question is always "which side",
    and a single shared preview would be showing the other pane's file half the
    time -- the same argument that put the favourites bar in the pane.
  * **It takes no focus, and it claims the pane.** Every control in a pane has
    to, or clicking it in the *inactive* pane leaves the keyboard in the other
    one. See `PaneWidget._claim`.
  * **It shows a picture, a few lines of text, or a short dump, and nothing
    else.** No zoom, no scrollbar on an image, no selection in the text: a
    preview that can be interacted with is a viewer in a space too small to be
    one, and F3 is one key away.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QPlainTextEdit,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.core.previews import describe
from app.io.protocol import Preview, PreviewForm

#: How much of a text file the pane asks for. Far less than the viewer: what
#: fits in a panel three hundred pixels wide is a few dozen lines, and reading
#: a quarter of a megabyte off a share to show forty of them is the cost this
#: whole feature is supposed to be careful about.
PANE_TEXT_BYTES = 16 * 1024

#: Rows of hex. Eight lines of eight bytes is enough to see a magic number,
#: which is all a preview pane can usefully say about a binary file.
PANE_HEX_ROWS = 10
PANE_HEX_COLUMNS = 8


class Thumb(QWidget):
    """A picture, scaled to fit and never enlarged, on the theme's backdrop.

    A `QLabel` with a scaled pixmap would do this in three lines and gets one
    thing wrong that shows immediately: it re-scales on every resize event, so
    dragging the splitter re-scales a decoded photograph on every pixel of
    mouse movement. Here the scale is worked out in the paint.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self._backdrop = QColor(0, 0, 0, 0)
        self.setMinimumHeight(80)

    def set_backdrop(self, colour: str) -> None:
        self._backdrop = QColor(colour)
        self.update()

    def set_picture(self, pixmap: QPixmap | None) -> None:
        self._pixmap = pixmap
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        del event
        painter = QPainter(self)
        painter.fillRect(self.rect(), self._backdrop)
        if self._pixmap is None or self._pixmap.isNull():
            return
        size, box = self._pixmap.size(), self.size()
        scale = min(box.width() / max(1, size.width()),
                    box.height() / max(1, size.height()), 1.0)
        drawn = size * scale
        target = self._pixmap.rect()
        target.setSize(drawn)
        target.moveCenter(self.rect().center())
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.drawPixmap(target, self._pixmap)


class PreviewPanel(QFrame):
    """The panel beside a listing, showing whatever the cursor is on."""

    #: Emitted on any click in the panel, so the pane it belongs to can claim
    #: the keyboard. Every `NoFocus` control in a pane owes this.
    activated = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "previewpanel")
        self.setFrameShape(QFrame.NoFrame)
        self.setFocusPolicy(Qt.NoFocus)
        self._path = ""

        self._name = QLabel("")
        self._name.setProperty("role", "previewname")
        self._name.setWordWrap(True)
        # Two lines at most. A panel is narrow, and a file with a forty-word
        # name would otherwise push the picture off the bottom of it.
        self._name.setMaximumHeight(34)

        self._thumb = Thumb()
        self._text = QPlainTextEdit()
        self._text.setReadOnly(True)
        self._text.setProperty("role", "preview")
        self._text.setLineWrapMode(QPlainTextEdit.NoWrap)
        self._text.setFocusPolicy(Qt.NoFocus)
        self._text.setTextInteractionFlags(Qt.NoTextInteraction)
        self._note = QLabel("")
        self._note.setProperty("role", "note")
        self._note.setAlignment(Qt.AlignCenter)
        self._note.setWordWrap(True)

        self._stack = QStackedWidget()
        for widget in (self._thumb, self._text, self._note):
            self._stack.addWidget(widget)

        self._about = QLabel("")
        self._about.setProperty("role", "space")
        self._about.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(5)
        layout.addWidget(self._name)
        layout.addWidget(self._stack, 1)
        layout.addWidget(self._about)
        self.clear("")

    def apply_tokens(self, tokens: dict[str, str]) -> None:
        # No literal colour as a fallback. See `app/ui/viewer.py` for why a
        # fallback is the worst form of the rule CLAUDE.md states.
        self._thumb.set_backdrop(tokens.get("bg_1", ""))
        mono = tokens.get("mono", "monospace")
        font = self._text.font()
        font.setFamilies([part.strip().strip('"') for part in mono.split(",")])
        # A size smaller than the listing's. The point of text here is the
        # shape of the file -- is this a CSV, is this XML, does it start with a
        # header -- and more lines says that better than larger ones.
        font.setPointSizeF(max(7.0, font.pointSizeF() - 1.0))
        self._text.setFont(font)

    @property
    def path(self) -> str:
        """The file the panel is showing or waiting for."""
        return self._path

    def waiting(self, path: str, name: str) -> None:
        """A file has been asked about. Says so rather than blanking.

        The name goes up straight away and only the body waits, which is the
        difference between a panel that is loading and one that looks broken
        while somebody arrows down a folder on a slow share.
        """
        self._path = path
        self._name.setText(name)
        self._note.setText("reading...")
        self._stack.setCurrentWidget(self._note)
        self._about.setText("")

    def clear(self, why: str = "") -> None:
        self._path = ""
        self._name.setText("")
        self._note.setText(why or "nothing selected")
        self._stack.setCurrentWidget(self._note)
        self._about.setText("")

    def show_preview(self, path: str, name: str, answer: Preview) -> None:
        self._path = path
        self._name.setText(name)
        if answer.form is PreviewForm.IMAGE:
            picture = QPixmap()
            if picture.loadFromData(answer.image or b"", "PNG") \
                    and not picture.isNull():
                self._thumb.set_picture(picture)
                self._stack.setCurrentWidget(self._thumb)
            else:
                self._stack.setCurrentWidget(self._note)
                self._note.setText("the picture did not decode")
        elif answer.form is PreviewForm.TEXT:
            self._text.setPlainText(answer.text)
            self._stack.setCurrentWidget(self._text)
        elif answer.form is PreviewForm.HEX:
            self._text.setPlainText(_short_dump(answer.data or b""))
            self._stack.setCurrentWidget(self._text)
        else:
            self._note.setText(answer.note or "no preview")
            self._stack.setCurrentWidget(self._note)
        self._about.setText(describe(answer))

    def problem(self, path: str, why: str) -> None:
        self._path = path
        self._note.setText(why)
        self._stack.setCurrentWidget(self._note)
        self._about.setText("")

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self.activated.emit()
        super().mousePressEvent(event)


def _short_dump(data: bytes) -> str:
    """A few rows of hex, eight bytes wide.

    Eight rather than the viewer's sixteen because the panel is narrow, and a
    sixteen-wide dump in a three-hundred pixel panel is a dump with its
    character column cut off -- which is the half worth reading.
    """
    lines = []
    for at in range(0, min(len(data), PANE_HEX_ROWS * PANE_HEX_COLUMNS),
                    PANE_HEX_COLUMNS):
        chunk = data[at:at + PANE_HEX_COLUMNS]
        shown = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{at:04x}  {chunk.hex(' '):<{PANE_HEX_COLUMNS * 3 - 1}}  {shown}")
    return "\n".join(lines)

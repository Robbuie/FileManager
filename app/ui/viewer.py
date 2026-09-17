"""The F3 viewer: one file, large, with the arrow keys walking the folder.

Presentation and input like everything else under `app/ui/`. It holds no path
arithmetic and makes no filesystem call: the list of files it walks is handed to
it by the pane that opened it, and every picture in it arrives through
`core.Previews`.

Three shapes, one window. An image with zoom and fit, text with the encoding it
was decoded as, and a hex dump for everything else -- chosen by what came back
rather than by the extension, which is what makes a `.dat` that is really a
photograph open as a photograph. They are three widgets in a stack rather than
three windows, because what a person is doing is *looking through a folder*, and
stepping from a JPEG to the readme beside it should not close one window and
open another.

The thing that makes it feel like a viewer rather than a dialog is that Left and
Right walk the folder. It is handed the names once, in the order the listing was
sorted in, so the walk matches what the eye just saw -- sorting them here would
put a viewer opened from a folder sorted by date into alphabetical order, which
reads as the wrong file opening.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.core.previews import describe
from app.io.protocol import PREVIEW_TEXT_BYTES, VIEWER_BOX, Preview, PreviewForm
from app.ui import glyphs
from app.ui.dialogs import Dialog

#: Zoom steps, as multiples of the picture's own size. Fixed steps rather than a
#: continuous factor so that 1.0 is always reachable by pressing the key, which
#: is what somebody checking whether a line is one pixel or two actually needs.
ZOOMS = (0.05, 0.08, 0.12, 0.17, 0.25, 0.33, 0.5, 0.67, 1.0,
         1.5, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0, 16.0)

#: Columns of a hex dump. Sixteen, because that is what every other hex dump in
#: the world uses and an offset in one should be comparable with an offset in
#: another.
HEX_COLUMNS = 16


class ImageView(QWidget):
    """A picture, centred, zoomable, and draggable once it does not fit.

    Painted rather than put in a `QLabel` inside a `QScrollArea`, which is the
    usual way and gets two things wrong that matter here. A label scaled with
    `setPixmap` re-scales the whole picture on every zoom step and every window
    resize, which on a 6,000 pixel photograph is a visible stall per keystroke;
    and a scroll area's two bars appear and disappear as the zoom crosses the
    fitting point, which moves the picture sideways while somebody is looking at
    it. Here the transform is three numbers and the paint is one
    `drawPixmap` with smoothing on, so a zoom step costs one repaint.
    """

    zoomChanged = Signal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self._zoom = 1.0
        self._fitting = True
        self._offset = QPoint(0, 0)
        self._drag: QPoint | None = None
        self._backdrop = QColor(0, 0, 0, 0)
        self.setMouseTracking(True)
        # The keyboard belongs to the dialog: every key this widget would want
        # is one the dialog already handles, and two handlers for the arrows
        # would be one of them silently winning.
        self.setFocusPolicy(Qt.NoFocus)

    def set_backdrop(self, colour: str) -> None:
        self._backdrop = QColor(colour)
        self.update()

    def set_picture(self, pixmap: QPixmap | None) -> None:
        self._pixmap = pixmap
        self._offset = QPoint(0, 0)
        self._fitting = True
        self._apply_fit()
        self.update()

    @property
    def zoom(self) -> float:
        return self._zoom

    @property
    def fitting(self) -> bool:
        return self._fitting

    def fit(self) -> None:
        self._fitting = True
        self._offset = QPoint(0, 0)
        self._apply_fit()
        self.update()

    def set_zoom(self, zoom: float) -> None:
        """Zoom to a number, which stops the picture following the window.

        Fitting is a mode rather than a zoom value, which is why setting one
        leaves the other: a window resized while fitting re-fits, and a window
        resized at 200 per cent stays at 200 per cent. Conflating them makes a
        deliberate zoom quietly undo itself the next time the window moves.
        """
        self._fitting = False
        self._zoom = max(ZOOMS[0], min(ZOOMS[-1], zoom))
        self.zoomChanged.emit(self._zoom)
        self.update()

    def step_zoom(self, direction: int) -> None:
        """The next step up or down from wherever the zoom actually is.

        From the current value rather than from an index, so stepping up out of
        a fit that landed at 43 per cent goes to 50 rather than jumping to
        whatever step an index happened to be pointing at.
        """
        if direction > 0:
            nxt = next((z for z in ZOOMS if z > self._zoom * 1.001), ZOOMS[-1])
        else:
            nxt = next((z for z in reversed(ZOOMS) if z < self._zoom * 0.999),
                       ZOOMS[0])
        self.set_zoom(nxt)

    # ------------------------------------------------------------------ paint

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.fillRect(self.rect(), self._backdrop)
        if self._pixmap is None or self._pixmap.isNull():
            return
        if self._fitting:
            self._apply_fit()
        painter.setRenderHint(QPainter.SmoothPixmapTransform,
                              # Off above 1:1. Smoothing an enlarged picture is
                              # what turns "is this line one pixel or two" into
                              # a blur, and somebody at 400 per cent is asking
                              # exactly that question.
                              self._zoom <= 1.0)
        painter.drawPixmap(self._target(), self._pixmap)

    def _target(self) -> QRect:
        assert self._pixmap is not None
        size = self._pixmap.size() * self._zoom
        box = QRect(QPoint(0, 0), size)
        box.moveCenter(self.rect().center() + self._offset)
        return box

    def _apply_fit(self) -> None:
        """Scale to the window, and never above 1:1 on its own.

        A 40 by 40 icon blown up to fill a 1,200 pixel window is not what
        "fit" means to anybody -- it is a wall of soft squares. Enlarging is
        something a person asks for with a key.
        """
        if self._pixmap is None or self._pixmap.isNull():
            return
        size, box = self._pixmap.size(), self.size()
        if size.width() <= 0 or size.height() <= 0:
            return
        scale = min(box.width() / size.width(), box.height() / size.height(), 1.0)
        changed = abs(scale - self._zoom) > 0.0001
        self._zoom = max(ZOOMS[0], scale)
        if changed:
            self.zoomChanged.emit(self._zoom)

    # ------------------------------------------------------------------ mouse

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.button() == Qt.LeftButton and not self._fitting:
            self._drag = event.position().toPoint()
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if self._drag is None:
            return
        here = event.position().toPoint()
        self._offset += here - self._drag
        self._drag = here
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt naming
        del event
        self._drag = None
        self.unsetCursor()

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """The wheel zooms, with or without Ctrl.

        Unlike a document viewer, and deliberately: there is nothing to scroll
        here that dragging does not do better, and a wheel that scrolled a
        picture by a line at a time in a window with no scrollbars would look
        like it was doing nothing at all.
        """
        steps = event.angleDelta().y()
        if steps:
            self.step_zoom(1 if steps > 0 else -1)
        event.accept()


class Viewer(Dialog):
    """One file at a time, with the folder to walk through it."""

    #: The path now on screen, so whoever opened it can follow along -- the
    #: pane moves its cursor to match, which is what makes closing the viewer
    #: leave the listing where the eye already is.
    showing = Signal(str)

    def __init__(self, previews, tokens: dict[str, str],
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._previews = previews
        self._tokens = dict(tokens)
        self._folder = ""
        self._names: list[str] = []
        self._at = 0
        self._page = 0
        self._pages = 0
        self._answer: Preview | None = None

        self.setWindowTitle("View")
        self.setProperty("role", "viewer")
        # Not modal. The whole point of a viewer in a file manager is that the
        # listing behind it is still there: pressing F3, looking, and clicking
        # back into the pane has to work without a close.
        self.setModal(False)
        self.setSizeGripEnabled(True)

        self._name = QLabel("")
        self._name.setProperty("role", "viewername")
        self._where = QLabel("")
        self._where.setProperty("role", "viewerwhere")
        self._where.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self._image = ImageView()
        self._image.zoomChanged.connect(self._render_footer)
        self._text = QPlainTextEdit()
        self._text.setReadOnly(True)
        self._text.setProperty("role", "preview")
        # No wrapping. A wrapped log file or a wrapped source file is unreadable
        # in a way that is hard to name and obvious to look at: every line after
        # the first long one is at a different indent from the one it belongs to.
        self._text.setLineWrapMode(QPlainTextEdit.NoWrap)
        self._hex = QPlainTextEdit()
        self._hex.setReadOnly(True)
        self._hex.setProperty("role", "preview")
        self._hex.setLineWrapMode(QPlainTextEdit.NoWrap)
        self._nothing = QLabel("")
        self._nothing.setAlignment(Qt.AlignCenter)
        self._nothing.setProperty("role", "note")
        self._nothing.setWordWrap(True)

        self._stack = QStackedWidget()
        for widget in (self._image, self._text, self._hex, self._nothing):
            self._stack.addWidget(widget)

        self._status = QLabel("")
        self._status.setProperty("role", "status")
        self._keys = QLabel("")
        self._keys.setProperty("role", "space")
        self._keys.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)
        head.addWidget(self._name, 1)
        head.addWidget(self._where)

        foot = QHBoxLayout()
        foot.setContentsMargins(0, 0, 0, 0)
        foot.setSpacing(8)
        foot.addWidget(self._status, 1)
        foot.addWidget(self._keys)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)
        layout.addLayout(head)
        layout.addWidget(self._stack, 1)
        layout.addLayout(foot)

        self.resize(1100, 760)
        self.apply_tokens(self._tokens)
        self._previews.ready.connect(self._on_ready)
        self._previews.unavailable.connect(self._on_unavailable)

    # ----------------------------------------------------------------- chrome

    def apply_tokens(self, tokens: dict[str, str]) -> None:
        """Take the token set the sheet was rendered from.

        The backdrop behind a picture is the one colour here that a stylesheet
        cannot set, because the widget paints it. `bg_0` rather than the pane's
        `bg_2`: a photograph wants the darkest surface in the set behind it, and
        on the light themes the same rule gives it the quietest one.
        """
        # No literal colour as a fallback, here or anywhere outside
        # `app/theme/`. A hex written in a widget is a spot that stops
        # following the theme picker, which reads to the user as a picker that
        # half works -- and a *fallback* is the worst version of it, because it
        # only appears on the day the token set changes and looks like a
        # rendering fault rather than a missing key.
        self._tokens = dict(tokens)
        self._image.set_backdrop(tokens.get("bg_0", ""))
        mono = tokens.get("mono", "monospace")
        for widget in (self._text, self._hex):
            font = widget.font()
            font.setFamilies([part.strip().strip('"')
                              for part in mono.split(",")])
            widget.setFont(font)
        self.setWindowIcon(glyphs.icon("filter", colour=tokens.get("txt_1", ""),
                                       muted=tokens.get("txt_2", "")))

    # ------------------------------------------------------------------ what

    def show_file(self, folder: str, names: list[str], at: int) -> None:
        """Open on one file, with the folder's files to walk.

        `names` is files only and in the order the listing had them -- both
        decided by the caller, because both are questions about what the person
        was looking at rather than about what is on disk. Folders are left out
        because there is nothing to view in one, and stepping through a listing
        that jumped over every folder in it would read as the arrow keys
        skipping.
        """
        self._folder = folder
        self._names = list(names)
        self._at = max(0, min(at, len(self._names) - 1)) if self._names else 0
        self._page = 0
        self._request()

    def step(self, direction: int) -> None:
        """The next file, or the previous. Stops at the ends rather than wrapping.

        No wrap, unlike the quick search, and for the opposite reason: a search
        that wraps has found you the only match there is, while a viewer that
        wrapped from the last file to the first would look exactly like the key
        having done nothing.
        """
        if not self._names:
            return
        at = self._at + direction
        if not 0 <= at < len(self._names):
            return
        self._at = at
        self._page = 0
        self._request()

    def step_page(self, direction: int) -> None:
        if self._pages <= 1:
            return
        page = self._page + direction
        if not 0 <= page < self._pages:
            return
        self._page = page
        self._request()

    @property
    def path(self) -> str:
        from app.io import paths

        if not self._names or not self._folder:
            return ""
        return paths.join(self._folder, self._names[self._at])

    # ------------------------------------------------------------------- keys

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """Every key the viewer has, in one place.

        A `QDialog` answers Escape by closing and Enter by accepting, and the
        second one is a trap here: a viewer is not a form, so Enter must not
        dismiss it silently while somebody is looking at a picture. Both are
        taken explicitly.
        """
        key = event.key()
        modifiers = event.modifiers()

        if key in (Qt.Key_Escape, Qt.Key_F3):
            self.close()
            return
        if key in (Qt.Key_Return, Qt.Key_Enter):
            return                      # not a form; Enter means nothing here
        if key in (Qt.Key_Right, Qt.Key_Down, Qt.Key_Space, Qt.Key_PageDown):
            if key == Qt.Key_PageDown and self._pages > 1:
                self.step_page(1)
            else:
                self.step(1)
            return
        if key in (Qt.Key_Left, Qt.Key_Up, Qt.Key_Backspace, Qt.Key_PageUp):
            if key == Qt.Key_PageUp and self._pages > 1:
                self.step_page(-1)
            else:
                self.step(-1)
            return
        if key == Qt.Key_Home and self._names:
            self._at, self._page = 0, 0
            self._request()
            return
        if key == Qt.Key_End and self._names:
            self._at, self._page = len(self._names) - 1, 0
            self._request()
            return

        if self._stack.currentWidget() is self._image:
            if key in (Qt.Key_Plus, Qt.Key_Equal):
                self._image.step_zoom(1)
                return
            if key in (Qt.Key_Minus, Qt.Key_Underscore):
                self._image.step_zoom(-1)
                return
            if key == Qt.Key_0 or key == Qt.Key_1:
                # 1 as well as 0, because "one to one" and "one hundred per
                # cent" are the same thing and different people reach for
                # different keys for it.
                self._image.set_zoom(1.0)
                return
            if key == Qt.Key_F or (key == Qt.Key_Asterisk and not modifiers):
                self._image.fit()
                return
        super().keyPressEvent(event)

    # -------------------------------------------------------------- internals

    def _request(self) -> None:
        path = self.path
        if not path:
            self._show_nothing("there is nothing in this folder to view")
            return
        self._name.setText(self._names[self._at])
        self._where.setText(f"{self._at + 1} of {len(self._names)}")
        self._status.setText("reading...")
        self.showing.emit(path)
        # No debounce. Nothing is sweeping past rows here: a person pressed a
        # key on one file, and a fifth of a second before anything happens is
        # the difference between a viewer and a lag.
        self._previews.ask(path, box=VIEWER_BOX, text_bytes=PREVIEW_TEXT_BYTES,
                           delay_ms=0)

    def _on_ready(self, path: str, answer: Preview) -> None:
        if path != self.path:
            return          # an answer for a file the viewer has stepped off
        self._answer = answer
        self._pages = answer.pages
        if answer.form is PreviewForm.IMAGE:
            picture = QPixmap()
            if picture.loadFromData(answer.image or b"", "PNG") \
                    and not picture.isNull():
                self._image.set_picture(picture)
                self._stack.setCurrentWidget(self._image)
            else:
                self._show_nothing("the picture did not survive being decoded")
                return
        elif answer.form is PreviewForm.TEXT:
            self._text.setPlainText(answer.text)
            self._stack.setCurrentWidget(self._text)
        elif answer.form is PreviewForm.HEX:
            self._hex.setPlainText(hex_dump(answer.data or b""))
            self._stack.setCurrentWidget(self._hex)
        else:
            self._show_nothing(answer.note or "nothing could be made of this file")
            return
        self._render_footer()

    def _on_unavailable(self, path: str, why: str) -> None:
        if path == self.path:
            self._show_nothing(why)

    def _show_nothing(self, why: str) -> None:
        self._answer = None
        self._pages = 0
        self._nothing.setText(why)
        self._stack.setCurrentWidget(self._nothing)
        self._status.setText(why)
        self._render_keys()

    def _render_footer(self, *_args) -> None:
        answer = self._answer
        if answer is None:
            return
        line = describe(answer)
        if self._stack.currentWidget() is self._image:
            zoom = self._image.zoom
            shown = "fit" if self._image.fitting else f"{zoom * 100:.0f}%"
            line = f"{line}   {shown}"
        if self._pages > 1:
            line = f"{line}   page {self._page + 1} of {self._pages}"
        self._status.setText(line)
        self._render_keys()

    def _render_keys(self) -> None:
        """The keys, in the window rather than only in a menu.

        A viewer opened with a function key is a window somebody arrives in
        without having read anything, and the keys it answers to are not the
        ones the rest of the application uses. So they are on the footer, where
        they cost a line and answer the question without a menu.
        """
        keys = ["arrows  next file"]
        if self._stack.currentWidget() is self._image:
            keys.append("+ -  zoom")
            keys.append("F  fit")
        if self._pages > 1:
            keys.append("PgUp PgDn  page")
        keys.append("Esc  close")
        self._keys.setText("   ".join(keys))


def hex_dump(data: bytes, columns: int = HEX_COLUMNS) -> str:
    """Bytes as offset, hex and printable characters.

    Here rather than in `io` because how wide a dump is laid out is a question
    about a window, which is why the worker sends bytes and not a string.

    A byte outside the printable range draws as a full stop, which is what every
    hex dump does and is worth one sentence of defence: the alternative is a
    control picture or a box glyph, and a column of those is unreadable at the
    size this is drawn. What the column is for is spotting the readable parts --
    a magic number, a path, a version string -- and a full stop is the quietest
    thing that lets them stand out.
    """
    lines = []
    for at in range(0, len(data), columns):
        chunk = data[at:at + columns]
        hexed = chunk.hex(" ")
        shown = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{at:08x}  {hexed:<{columns * 3 - 1}}  {shown}")
    return "\n".join(lines)

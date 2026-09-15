"""One file's preview at a time, for the viewer and the preview pane.

The grid's thumbnails are in `core/thumbnails.py` and are a different object
for a reason worth stating, because the two look like they should be one. A
grid asks about a screenful at once and keeps the answers for as long as the
folder is on screen, so it is a batched cache -- `core/fileicons.py` with a
decoder behind it. A preview pane asks about exactly one file, the one under
the cursor, and the question changes every time an arrow key is pressed. Those
are opposite shapes, and the thing they share is the decoder, which is in
`app/io/decode.py` where both of them can reach it.

So this is `core/siblings.py`'s shape rather than `core/fileicons.py`'s, and
the three bounds are the same three:

  * **One outstanding request, and the previous one is cancelled at the
    worker.** Holding Down through a folder must leave one decode running, not
    forty -- and the thirty-nine abandoned ones have to be withdrawn where they
    are rather than merely ignored here, because an abandoned decode still
    holds the volume the next one wants.
  * **Debounced.** The cursor crosses thirty rows on the way to row thirty-one
    and the pane is not asked about any of them. Without this, one keypress
    held down is a request per row that arrives, each cancelling the last, and
    the file somebody stopped on is the one that has to wait for the queue to
    drain.
  * **Its own short deadline.** Nothing is waiting on a preview: a share that
    has gone quiet should lose the picture rather than hold a worker while
    somebody scrolls.

The one addition is a small cache, and it is here rather than in the pane
because of what a person actually does with the arrow keys: down four rows and
back up three. Keyed on the file's own mtime and size as well as its path, for
`core/fileicons.py`'s reason -- a picture cannot change unless the file does --
and on the box that was asked for, because a preview decoded for a 200-pixel
pane is not the one the viewer wants. Small on purpose: these are decoded
images, and a hundred of them is real memory.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

from app.io import paths
from app.io.protocol import (
    PREVIEW_BOX,
    PREVIEW_TEXT_BYTES,
    Op,
    Preview,
    PreviewForm,
    Reply,
    Status,
)

#: Milliseconds between the cursor landing on a row and that row being asked
#: about. Long enough that a held arrow key crosses a folder without asking
#: about anything on the way, short enough that a deliberate move feels like it
#: answered the keystroke rather than a moment later.
SETTLE_MS = 180

#: How many decoded previews are kept. Twelve covers arrowing up and down a
#: screenful, which is the movement this exists for, and stops well short of
#: the folder -- these are images, and the point of the box is that a preview
#: is small, not free.
CACHE_SIZE = 12


class Previews(QObject):
    """The one-file-at-a-time preview, shared by both panes and the viewer."""

    #: path, and the `Preview` for it. The path is carried because by the time
    #: an answer arrives the cursor has very likely moved, and the widget has
    #: to be able to tell that this is not the file it is showing any more.
    ready = Signal(str, object)
    #: path, and one line saying why there is nothing.
    unavailable = Signal(str, str)

    def __init__(self, bridge: Any, config: Any,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._config = config
        self._request_id: int | None = None
        self._path: str | None = None
        #: (normalised path, mtime, size, box) -> Preview, oldest first.
        self._cache: "OrderedDict[tuple, Preview]" = OrderedDict()
        #: What the debounce timer will ask for when it fires.
        self._queued: tuple | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fire)

    @property
    def pending(self) -> str | None:
        """The file being decoded, or None. For a widget that wants to say so."""
        return self._path

    def ask(self, path: str, *, box: int = PREVIEW_BOX,
            text_bytes: int = PREVIEW_TEXT_BYTES,
            mtime: float = 0.0, size: int = 0, delay_ms: int = SETTLE_MS) -> None:
        """Show this file. Replaces whatever was being asked about before.

        `mtime` and `size` come from the row the caller is standing on rather
        than being looked up, which is the same trick `core/fileicons.py` uses
        and for the same two reasons: the listing already carries them, and
        looking them up here would be a filesystem call from `core`.

        A cached answer is delivered immediately and synchronously. That matters
        more than it sounds: arrowing back up to a file that was just shown has
        to be instant, and going through the timer would put a visible blink
        between the cursor moving and the picture that was already in hand.

        `delay_ms=0` is what the viewer uses. There is no cursor sweeping past
        rows there -- somebody pressed a key on one file -- so the debounce
        would only be a delay.
        """
        if not path:
            return
        key = (paths.normalize(path).lower(), float(mtime), int(size), int(box))
        known = self._cache.get(key)
        if known is not None:
            self.cancel()
            self._cache.move_to_end(key)
            self.ready.emit(path, known)
            return
        self.cancel()
        self._queued = (path, key, int(box), int(text_bytes))
        if delay_ms > 0:
            self._timer.start(delay_ms)
        else:
            self._fire()

    def cancel(self) -> None:
        """Stop waiting for whatever was asked for.

        Cancelled at the worker rather than merely forgotten: an abandoned
        decode is a file still being read off a volume, and the next row wants
        that worker.
        """
        self._timer.stop()
        self._queued = None
        if self._request_id is not None:
            self._bridge.cancel(self._request_id)
            self._bridge.forget(self._request_id)
        self._request_id = None
        self._path = None

    def clear(self) -> None:
        """Forget every decoded picture. What a theme change does not need and
        a settings change that alters the box does."""
        self._cache.clear()

    def fire_now(self) -> None:
        """Send the queued request without waiting for the timer.

        Public for `flush`'s reason in the three icon caches: a test has no
        event loop to run a timer with.
        """
        if self._queued is not None:
            self._timer.stop()
            self._fire()

    # -------------------------------------------------------------- internals

    def _fire(self) -> None:
        queued, self._queued = self._queued, None
        if queued is None:
            return
        path, key, box, text_bytes = queued
        self._path = path
        self._request_id = self._bridge.submit(
            Op.PREVIEW, path,
            timeout=float(self._config.get("timeout.preview")),
            on_reply=self._replier(path, key),
            args={"box": box, "text_bytes": text_bytes,
                  "shell": bool(self._config.get("preview.shell"))},
        )

    def _replier(self, path: str, key: tuple):
        def handle(reply: Reply) -> None:
            self._on_reply(path, key, reply)
        return handle

    def _on_reply(self, path: str, key: tuple, reply: Reply) -> None:
        if path != self._path:
            return  # an answer about a row nobody is standing on any more
        self._request_id = None
        self._path = None
        if reply.status is Status.CANCELLED:
            return
        answer = reply.payload
        if reply.status is Status.OK and isinstance(answer, Preview):
            # Cached whatever it says, including a `NONE` with a note on it.
            # "This file has no preview" is an answer, and re-asking for it
            # every time the cursor passes would be a read per pass for a
            # sentence that cannot change unless the file does.
            self._remember(key, answer)
            self.ready.emit(path, answer)
            return
        # Not cached. A share that comes back should show its pictures without
        # somebody having to navigate away and back.
        self.unavailable.emit(path, _explain(reply))

    def _remember(self, key: tuple, answer: Preview) -> None:
        self._cache[key] = answer
        self._cache.move_to_end(key)
        while len(self._cache) > CACHE_SIZE:
            self._cache.popitem(last=False)


def _explain(reply: Reply) -> str:
    """One short line for a panel with nothing in it.

    Short because it is drawn in the middle of the preview pane, which may be
    two hundred pixels wide. The `Preview.note` a successful reply carries is
    the wordier half of this and is written where the file was actually looked
    at; this is only for the cases where nothing got that far.
    """
    if reply.status is Status.GONE:
        return "not reachable"
    if reply.status is Status.DENIED:
        return "no permission to read this"
    if reply.status is Status.TIMEOUT:
        return "no answer in time"
    return reply.message or "could not be read"


def describe(answer: Preview) -> str:
    """The line under a preview, in the terms the form makes sense in.

    Here rather than in the viewer because the preview pane wants the same
    sentence, and two versions of it would drift apart within a release. What it
    says is deliberately about the *file* rather than about the preview, except
    for the one case where those differ and the difference matters: a picture
    shown smaller than it is says so, because otherwise somebody zooms in,
    finds softness, and goes looking for it in the file.
    """
    if answer.form is PreviewForm.IMAGE:
        parts = [f"{answer.width} x {answer.height}"]
        if answer.pages:
            parts.append(f"{answer.pages} pages")
        if answer.shown and answer.shown < max(answer.width, answer.height):
            parts.append(f"shown at {answer.shown} px")
        parts.append(_bytes(answer.size))
        return "  ".join(parts)
    if answer.form is PreviewForm.TEXT:
        lines = f"{answer.lines:,} lines" + (" so far" if answer.truncated else "")
        return f"{lines}  {answer.encoding}  {_bytes(answer.size)}"
    if answer.form is PreviewForm.HEX:
        return f"not text and not a picture  {_bytes(answer.size)}"
    return answer.note or "nothing to show"


def _bytes(size: int) -> str:
    """A size, in the units a person reads. Not `listing.format_size`: that one
    right-aligns into a column and pads, which in a sentence reads as a typo."""
    if size < 1024:
        return f"{size:,} bytes"
    for unit, cut in (("KB", 1024), ("MB", 1024 ** 2), ("GB", 1024 ** 3)):
        if size < cut * 1024:
            return f"{size / cut:,.1f} {unit}"
    return f"{size / 1024 ** 4:,.1f} TB"

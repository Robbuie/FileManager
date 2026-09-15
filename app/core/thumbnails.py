"""Small pictures for the grid view, a screenful at a time.

`core/fileicons.py` with a decoder behind it instead of the shell's icon call,
and near enough the same file -- which is the argument for it being a separate
one. The bookkeeping is identical because the cost is identical in shape: a
request that carries a path, one per folder, coalesced, keyed on the row's own
mtime and size, one picture per distinct picture. What differs is the bound and
the size, and those are exactly the two things worth being able to change
without touching the icons that every listing draws.

The bound is `draws_a_thumbnail`, and it does more work here than
`carries_own_icon` does there. A folder of text files sends nothing -- ninety
cells of grey lines at 128 pixels are ninety identical grey squares, and the
icon for the kind says more in less space. A folder of source code sends
nothing. A folder of photographs sends all of it, which is the case the grid
exists for.

The other difference is that this is only ever asked at all when somebody has
switched a tab to the grid. That is the real bound on the whole feature: a
listing in the ordinary view never builds one of these, so the folder of 50,000
drawings costs what it cost in 0.15 unless a person deliberately asks to look
at the pictures in it.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QImage, QPixmap

from app.core.icons import COALESCE_MS
from app.io import paths
from app.io.protocol import Entry, Op, Reply, Status, draws_a_thumbnail

#: How many cells go in one request. A screenful of the smallest cells on a
#: tall window is around a hundred and fifty; this is the ceiling for a fast
#: scroll, so a flick through a folder cannot turn into a request naming a
#: thousand files.
MAX_PER_REQUEST = 160

#: How many pictures are kept. A grid cell's picture is tens of kilobytes
#: rather than the hundreds a preview costs, so this can be generous -- but not
#: unbounded: scrolling a folder of 50,000 photographs from top to bottom would
#: otherwise end with all of them in memory.
MAX_IMAGES = 600


class Thumbnails(QObject):
    """The grid's picture cache, shared by both panes."""

    changed = Signal()

    def __init__(self, bridge: Any, config: Any,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._config = config
        #: (folder, name) -> (mtime, size, image key). The key is "" for a file
        #: nothing could be made of, which is an answer like any other: without
        #: it that cell is read again on every repaint.
        self._rows: dict[tuple[str, str], tuple[float, int, str]] = {}
        self._images: dict[str, QPixmap] = {}
        #: The order keys were last used in, so the oldest can go first. A
        #: plain list rather than an OrderedDict because the hot path is
        #: "append on arrival" and the cold path is "trim", and a grid scrolled
        #: back over old cells re-requests them cheaply from the worker's own
        #: caches anyway.
        self._recent: list[str] = []
        self._pending: dict[str, dict[str, tuple[float, int]]] = {}
        self._size = 128
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(COALESCE_MS)
        self._timer.timeout.connect(self.flush)

    @property
    def size(self) -> int:
        return self._size

    def set_size(self, size: int) -> None:
        """Change the cell size, which throws everything away.

        Everything, and not a rescale of what is held: a 96-pixel picture blown
        up to 240 is the soft square that makes a grid look broken, and the
        decode that produces a sharp one is the thing being paid for. This is
        why the sizes are four fixed steps rather than a slider -- each step is
        a fresh read of every file on screen.
        """
        size = max(16, int(size))
        if size == self._size:
            return
        self._size = size
        self.reload()

    def reload(self) -> None:
        """Forget every picture and draw again."""
        self._rows.clear()
        self._images.clear()
        self._recent.clear()
        self._pending.clear()
        self.changed.emit()

    @property
    def enabled(self) -> bool:
        return bool(self._config.get("preview.thumbnails"))

    def picture(self, folder: str, entry: Entry) -> QPixmap | None:
        """This cell's picture, now, or None to draw the icon for its kind.

        Called during a paint, so it answers from what it has and records what
        it does not. None covers every case the caller treats the same way: not
        a kind that could draw one, not asked yet, asked and still in flight,
        and asked and nothing could be made of it.
        """
        if not self.enabled or not folder or not draws_a_thumbnail(entry):
            return None
        known = self._rows.get(_row_key(folder, entry.name))
        if known is None or known[0] != entry.mtime or known[1] != entry.size:
            # Either never asked, or asked about a version of this file that is
            # not the one on screen. An edited photograph takes the second path
            # and gets its new picture without anything having to know it was
            # edited.
            self.want(folder, entry)
            return None
        return self._images.get(known[2]) if known[2] else None

    def want(self, folder: str, entry: Entry) -> None:
        if not self.enabled or not folder or not draws_a_thumbnail(entry):
            return
        waiting = self._pending.setdefault(folder, {})
        if len(waiting) < MAX_PER_REQUEST or entry.name in waiting:
            waiting[entry.name] = (entry.mtime, entry.size)
        if not self._timer.isActive():
            self._timer.start()

    def flush(self) -> None:
        """Send what has gathered, one request per folder."""
        self._timer.stop()
        if not self.enabled:
            self._pending.clear()
            return
        pending, self._pending = self._pending, {}
        for folder, stamps in pending.items():
            if not stamps:
                continue
            asked = sorted(stamps)
            self._bridge.submit(
                Op.THUMBNAIL, folder,
                timeout=float(self._config.get("timeout.thumbnail")),
                on_reply=self._replier(folder, {n: stamps[n] for n in asked}),
                args={"names": asked, "size": self._size,
                      "shell": bool(self._config.get("preview.shell"))},
            )

    # -------------------------------------------------------------- internals

    def _replier(self, folder: str, stamps: dict[str, tuple[float, int]]):
        def handle(reply: Reply) -> None:
            self._on_reply(folder, stamps, reply)
        return handle

    def _on_reply(self, folder: str, stamps: dict[str, tuple[float, int]],
                  reply: Reply) -> None:
        """Take the answer, including a partial one.

        `core/fileicons.py` explains the timeout case and it applies here
        unchanged: the worker checks its deadline between files, so a partial
        answer means "these are done, the rest were never reached", and
        recording the rest as having no picture would key that mistake to their
        mtime and hold it for as long as the files do not change. So a timeout
        records only what came back.
        """
        payload = reply.payload if isinstance(reply.payload, dict) else {}
        if reply.status not in (Status.OK, Status.TIMEOUT):
            return
        for key, encoded in (payload.get("images") or {}).items():
            if key in self._images:
                continue
            picture = _pixmap(encoded)
            if picture is not None:
                self._images[str(key)] = picture
                self._recent.append(str(key))
        rows = payload.get("rows") or {}
        settled = reply.status is Status.OK
        for name, (mtime, size) in stamps.items():
            key = str(rows.get(name) or "")
            if key or settled:
                self._rows[_row_key(folder, name)] = (mtime, size, key)
        self._trim()
        if rows:
            self.changed.emit()

    def _trim(self) -> None:
        """Drop the oldest pictures once there are too many.

        The rows that pointed at them are dropped too, or a cell would look up
        a key whose picture has gone and draw nothing forever -- which is worse
        than asking again, because asking again is one read and drawing nothing
        is permanent.
        """
        if len(self._images) <= MAX_IMAGES:
            return
        going = set(self._recent[: len(self._images) - MAX_IMAGES])
        self._recent = [k for k in self._recent if k not in going]
        for key in going:
            self._images.pop(key, None)
        self._rows = {row: value for row, value in self._rows.items()
                      if value[2] not in going}


def _row_key(folder: str, name: str) -> tuple[str, str]:
    """Folders and names are compared case-insensitively, because Windows is."""
    return paths.normalize(folder).lower(), name.lower()


def _pixmap(encoded: Any) -> QPixmap | None:
    """PNG bytes as something a cell can draw.

    A `QPixmap` rather than the `QIcon` the three icon caches hold, and the
    difference is what each is for. An icon is asked to produce itself at
    whatever size a view wants and carries several renderings to do it; a
    thumbnail is one picture at one size, already decoded to that size by the
    worker, and wrapping it in an icon would invite Qt to scale it again.
    """
    if not isinstance(encoded, (bytes, bytearray)) or not encoded:
        return None
    image = QImage()
    if not image.loadFromData(bytes(encoded), "PNG"):
        return None
    picture = QPixmap.fromImage(image)
    return None if picture.isNull() else picture

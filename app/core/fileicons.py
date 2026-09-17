"""The icon a file carries itself: executables, shortcuts, .ico files.

`core/icons.py` answers from the extension, which is why it is free -- every
`.pdf` on every share draws the same picture and the shell never opens
anything. A handful of kinds are not like that. What an `.exe` looks like is
inside the `.exe`, so the only way to find out is to read it, and a folder of
installers or a Start-menu folder full of shortcuts is exactly the folder where
every row being the generic picture makes the listing useless.

So this is the second request in the application that carries a path, after the
overlays, and like them it is bounded by hand rather than by construction:

  * **Only the kinds that carry one.** `carries_own_icon` is the filter and it
    is what makes the whole thing affordable: a folder of 50,000 documents
    sends nothing at all, because none of them is a kind that could answer
    differently from its type.
  * **Only the rows on screen.** `icon` is called from `data()` while the view
    paints, so a folder of 50,000 shortcuts still costs a screenful at a time.
  * **One request per folder, coalesced,** against that folder's own worker,
    with a short deadline. A share that goes quiet costs the pictures and
    nothing else.
  * **One picture per distinct picture.** The worker keys images on a digest,
    so forty shortcuts to the same program are one image.

What is cached is keyed on the file's mtime and size as well as its name,
which is the one place this differs from the overlays and it is worth saying
why. A badge changes while the file does not -- a commit turns forty red marks
green without an mtime moving -- so overlays have to be thrown away whenever a
folder is listed again. An icon is the opposite: it is inside the file, so it
cannot change unless the file does. Keying on what the listing already carries
means a rebuilt executable gets its new icon the moment its new row arrives,
and a refresh of a folder that has not changed costs nothing.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QIcon, QImage, QPixmap

from app.core.icons import COALESCE_MS, ROW_ICON
from app.io import paths
from app.io.protocol import Entry, Op, Reply, Status, carries_own_icon

#: How many rows go in one request. A screenful of shortcuts is a few dozen;
#: this is the ceiling for a fast scroll, and it is here so that a paint storm
#: cannot turn into a request naming a thousand files.
MAX_PER_REQUEST = 120

#: How many pictures are kept, and how many rows may point at them. Both are
#: here for `core/thumbnails.py`'s reason and were missing for longer: the
#: keying that makes this cache good -- an answer is valid until the file's
#: mtime or size moves, so nothing is dropped when a folder is listed again --
#: is exactly what stops anything ever being dropped at all. A session spent
#: walking a Program Files tree would otherwise end with every icon in it still
#: in memory. An icon is a 16 or 32 pixel square, so this is generous; a row is
#: two strings and a tuple, so that one can afford to be more so.
MAX_IMAGES = 400
MAX_ROWS = 20_000


class FileIcons(QObject):
    """The per-file icon cache, shared by both panes."""

    changed = Signal()

    def __init__(self, bridge: Any, config: Any, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._config = config
        #: (folder, name) -> (mtime, size, image key). The key is "" for a file
        #: the shell had no icon of its own for, which is an answer like any
        #: other: without it that row is asked about again on every repaint.
        self._rows: dict[tuple[str, str], tuple[float, int, str]] = {}
        self._images: dict[str, QIcon] = {}
        #: Image keys in the order they arrived, so `_trim` has an oldest to
        #: drop. A list rather than an `OrderedDict` of the images themselves
        #: because a re-used key is deliberately *not* moved to the end: what
        #: is being bounded is how many pictures are held, and re-reading one
        #: that has been dropped is one file read, not a listing.
        self._recent: list[str] = []
        #: folder -> {name: (mtime, size)}. The stamp is remembered from the
        #: paint that asked rather than read again when the answer lands: what
        #: the reply describes is the file as the listing saw it.
        self._pending: dict[str, dict[str, tuple[float, int]]] = {}
        self._size = ROW_ICON
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(COALESCE_MS)
        self._timer.timeout.connect(self.flush)

    @property
    def enabled(self) -> bool:
        """These need the icons they replace.

        With the shell icons off the listing draws no pictures at all, and one
        row with an icon on it would be the only picture in the window.
        """
        return bool(self._config.get("icons.per_file")
                    and self._config.get("icons.shell"))

    def start(self, scale: float = 1.0) -> None:
        self._size = 32 if scale > 1.25 else ROW_ICON

    def reload(self) -> None:
        """Forget everything and draw again. What the View toggle runs.

        The pictures go too. They did not until 0.29.12, and "forget
        everything" that kept every icon it had ever decoded was the whole of
        the leak: turning the setting off and on again was the one moment this
        cache could have been emptied and the one moment it looked as though
        it had been.
        """
        self._rows.clear()
        self._images.clear()
        self._recent.clear()
        self._pending.clear()
        self.changed.emit()

    def icon(self, folder: str, entry: Entry) -> QIcon | None:
        """This row's own icon, now, or None to draw the one for its kind.

        Called during a paint, so it answers from what it has and records what
        it does not. None covers every case the caller treats the same way:
        not a kind that carries one, not asked yet, asked and still in flight,
        and asked and the file had none.
        """
        if not self.enabled or not folder or not carries_own_icon(entry):
            return None
        known = self._rows.get(_row_key(folder, entry.name))
        if known is None or known[0] != entry.mtime or known[1] != entry.size:
            # Either never asked, or asked about a version of this file that
            # is not the one on screen. A rebuilt executable takes the second
            # path and gets its new picture without anything having to know
            # that it was rebuilt.
            self.want(folder, entry)
            return None
        return self._images.get(known[2]) if known[2] else None

    def want(self, folder: str, entry: Entry) -> None:
        if not self.enabled or not folder or not carries_own_icon(entry):
            return
        waiting = self._pending.setdefault(folder, {})
        if len(waiting) < MAX_PER_REQUEST or entry.name in waiting:
            waiting[entry.name] = (entry.mtime, entry.size)
        if not self._timer.isActive():
            self._timer.start()

    def flush(self) -> None:
        """Send what has gathered, one request per folder.

        Public for the reason `Icons.flush` and `Overlays.flush` are: a test
        has no event loop to run the timer.
        """
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
                Op.FILE_ICON, folder,
                timeout=float(self._config.get("timeout.file_icon")),
                on_reply=self._replier(folder, {n: stamps[n] for n in asked}),
                args={"names": asked, "size": self._size},
            )

    # -------------------------------------------------------------- internals

    def _replier(self, folder: str, stamps: dict[str, tuple[float, int]]):
        def handle(reply: Reply) -> None:
            self._on_reply(folder, stamps, reply)
        return handle

    def _on_reply(self, folder: str,
                  stamps: dict[str, tuple[float, int]], reply: Reply) -> None:
        """Take the answer, including a partial one.

        On a complete answer every name asked about is recorded, with or
        without a picture: the empty answer is what stops a file the shell had
        nothing for being read again on every repaint.

        A timeout is the one case where that would be wrong, and it is wrong
        in a way the overlays do not have to care about. The worker checks its
        deadline between files, so a partial answer is "these are done, the
        rest were never reached" -- and recording the rest as having no icon
        would key that mistake to their mtime and keep it for as long as the
        files do not change. So a timeout records only what came back, and the
        rows it did not reach are read again the next time they are painted.
        A failed request records nothing at all, so a share that comes back
        gets its icons without a navigation.
        """
        payload = reply.payload if isinstance(reply.payload, dict) else {}
        if reply.status not in (Status.OK, Status.TIMEOUT):
            return
        size = int(payload.get("size") or self._size)
        for key, pixels in (payload.get("images") or {}).items():
            if key in self._images:
                continue
            icon = _icon_from(pixels, size)
            if icon is not None:
                self._images[str(key)] = icon
                self._recent.append(str(key))
        rows = payload.get("rows") or {}
        settled = reply.status is Status.OK
        for name, (mtime, entry_size) in stamps.items():
            key = str(rows.get(name) or "")
            if key or settled:
                self._rows[_row_key(folder, name)] = (mtime, entry_size, key)
        self._trim()
        if rows:
            self.changed.emit()

    def _trim(self) -> None:
        """Drop the oldest pictures, and the rows left pointing at them.

        `core/thumbnails.py` gives the reason the rows have to go with the
        pictures: a row whose key names an image that is no longer held looks
        up nothing and draws nothing, for as long as the file does not change.
        Asking again is one read; drawing nothing is permanent.

        The rows have a ceiling of their own as well, which the grid does not
        need. A row here is recorded for every file asked about including the
        ones the shell had no icon for -- that empty answer is what stops those
        being re-read on every repaint -- so the rows outgrow the pictures
        rather than tracking them, and bounding only the pictures would leave
        the larger of the two caches unbounded.
        """
        if len(self._images) > MAX_IMAGES:
            going = set(self._recent[: len(self._images) - MAX_IMAGES])
            self._recent = [key for key in self._recent if key not in going]
            for key in going:
                self._images.pop(key, None)
            self._rows = {row: value for row, value in self._rows.items()
                          if value[2] not in going}
        if len(self._rows) > MAX_ROWS:
            # Insertion order, which for a dict is the order they were learned.
            for row in list(self._rows)[: len(self._rows) - MAX_ROWS]:
                del self._rows[row]


def _row_key(folder: str, name: str) -> tuple[str, str]:
    """Folders and names are compared case-insensitively, because Windows is."""
    return paths.normalize(folder).lower(), name.lower()


def _icon_from(pixels: Any, size: int) -> QIcon | None:
    """The same conversion the other two do, kept separate for their reason.

    `core/icons.py` explains the device pixel ratio and the copy. This one is
    handed a picture read out of a file rather than one drawn from the system
    image list, and the day the two need different handling is the day they
    should not have shared a function.
    """
    if not isinstance(pixels, (bytes, bytearray)) or len(pixels) != size * size * 4:
        return None
    image = QImage(bytes(pixels), size, size, size * 4,
                   QImage.Format_ARGB32_Premultiplied).copy()
    if image.isNull():
        return None
    pixmap = QPixmap.fromImage(image)
    if pixmap.isNull():
        return None
    pixmap.setDevicePixelRatio(size / ROW_ICON)
    return QIcon(pixmap)

"""Icon overlays: the badge on the corner of a row's icon.

The shared-folder arrow, the OneDrive tick, the green and red marks source
control puts on a working copy. They come from the same shell extensions the
context menu does, and unlike the icons in `core/icons.py` they are a fact
about the file rather than about its type: two `.cs` files in one folder can
carry different badges, and the only way to find out is to ask the shell about
each of them by name.

That makes this the one icon request in the application that carries a path,
which is the thing `CLAUDE.md` says not to do -- so everything here is about
keeping it bounded.

  * **Only the rows on screen.** `icon` is called from `data()` while the view
    paints, so what gets asked about is what is visible, not the folder. A
    50,000-row folder scrolled through slowly costs a few dozen lookups per
    screen and nothing at all for the rows nobody looks at.
  * **One request per folder, coalesced.** The names gathered during a paint
    go out together, against that folder's own worker, with a short deadline.
    A share that goes quiet costs the badges and nothing else.
  * **A picture per badge on a kind, not per file.** The worker answers with a
    key made of the system icon index and the overlay index, so a working copy
    of 400 modified files is one image, not 400.
  * **Off is a real option.** `View > Icon overlays` stops all of it, and the
    listing goes back to the icons in `core/icons.py`, which never touch a
    path at all.

What is cached is the answer for a path, and it is dropped when the folder is
listed again -- a badge is exactly the thing that changes while the file does
not, so carrying it across a refresh would show yesterday's status.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QIcon, QImage, QPixmap

from app.core.icons import COALESCE_MS, ROW_ICON
from app.io import paths
from app.io.protocol import Op, Reply, Status

#: How many rows are asked about in one request. A screen holds a few dozen;
#: this is the ceiling for a fast scroll through a folder, and it is here so
#: that a paint storm cannot turn into a request naming ten thousand files.
MAX_PER_REQUEST = 200


class Overlays(QObject):
    """The overlay cache, shared by both panes."""

    changed = Signal()

    def __init__(self, bridge: Any, config: Any, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._config = config
        #: (folder, name) -> image key, or "" for a row the shell had no
        #: badge for. Both are answers; absent means "not asked yet".
        self._rows: dict[tuple[str, str], str] = {}
        self._images: dict[str, QIcon] = {}
        self._pending: dict[str, set[str]] = {}
        self._size = ROW_ICON
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(COALESCE_MS)
        self._timer.timeout.connect(self.flush)

    @property
    def enabled(self) -> bool:
        """Overlays need the icons they sit on.

        The shell composites the badge onto the file's own icon and hands back
        one picture, so an overlay with the icons turned off would be the only
        picture in the listing.
        """
        return bool(self._config.get("icons.overlays")
                    and self._config.get("icons.shell"))

    def start(self, scale: float = 1.0) -> None:
        self._size = 32 if scale > 1.25 else ROW_ICON

    def reload(self) -> None:
        """Forget everything and draw again. What the View toggle runs."""
        self._rows.clear()
        self._pending.clear()
        self.changed.emit()

    def icon(self, folder: str, name: str) -> QIcon | None:
        """The badged icon for a row, now, or None to draw the ordinary one.

        Called during a paint, so it answers from what it has and records what
        it does not. None means three different things -- not asked, asked and
        the shell said no badge, or asked and still in flight -- and the
        caller wants the same thing for all three.
        """
        if not self.enabled:
            return None
        key = self._rows.get(_row_key(folder, name))
        if key is None:
            self.want(folder, name)
            return None
        return self._images.get(key) if key else None

    def want(self, folder: str, name: str) -> None:
        if not self.enabled or not folder or not name:
            return
        waiting = self._pending.setdefault(folder, set())
        if len(waiting) < MAX_PER_REQUEST:
            waiting.add(name)
        if not self._timer.isActive():
            self._timer.start()

    def forget(self, folder: str) -> None:
        """Drop what is known about a folder, because it is being listed again.

        A badge is the thing that changes while the file does not: a commit
        turns forty red marks green without a single mtime moving. Keeping the
        answers across a refresh would show the state before the commit until
        the application was restarted.
        """
        marker = _folder_key(folder)
        for key in [k for k in self._rows if k[0] == marker]:
            del self._rows[key]
        self._pending.pop(folder, None)

    def flush(self) -> None:
        """Send what has gathered, one request per folder.

        Public for the same reason `Icons.flush` is: a test has no event loop
        to run the timer.
        """
        self._timer.stop()
        if not self.enabled:
            self._pending.clear()
            return
        pending, self._pending = self._pending, {}
        for folder, names in pending.items():
            if not names:
                continue
            asked = sorted(names)
            self._bridge.submit(
                Op.OVERLAY, folder,
                timeout=float(self._config.get("timeout.overlay")),
                on_reply=self._replier(folder, asked),
                args={"names": asked, "size": self._size},
            )

    # -------------------------------------------------------------- internals

    def _replier(self, folder: str, asked: list[str]):
        def handle(reply: Reply) -> None:
            self._on_reply(folder, asked, reply)
        return handle

    def _on_reply(self, folder: str, asked: list[str], reply: Reply) -> None:
        """Take the answer, including a partial one.

        Every name that was asked about is recorded, whether or not it came
        back with a badge -- the empty answer is the useful one, since it is
        what stops the same row being asked about on every repaint. The
        exception is a failed request: nothing is recorded, so the rows are
        asked about again the next time the view paints, which is the
        behaviour a share that came back wants.
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
        rows = payload.get("rows") or {}
        for name in asked:
            self._rows[_row_key(folder, name)] = str(rows.get(name) or "")
        if rows:
            self.changed.emit()


def _folder_key(folder: str) -> str:
    """Folders are compared case-insensitively, because Windows is."""
    return paths.normalize(folder).lower()


def _row_key(folder: str, name: str) -> tuple[str, str]:
    return _folder_key(folder), name.lower()


def _icon_from(pixels: Any, size: int) -> QIcon | None:
    """The same conversion the row icons use, and for the same reasons.

    Kept here rather than imported so that a change to one cannot silently
    change the other: this one is handed a composite the shell drew, and the
    day the two need different handling is the day they should not share a
    function.
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

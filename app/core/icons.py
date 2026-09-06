"""Shell icons, asked for by kind and kept for the life of the window.

An icon here is a fact about this machine rather than about the folder being
looked at: every .pdf in every folder on every share draws the same picture,
and that picture comes from the local association database. So the cache is
keyed on the kind -- an extension, a folder, a file with no extension -- and a
folder of 50,000 rows costs one request per distinct extension rather than one
per row. The second folder of the day usually costs nothing at all.

Two things follow from that and both matter more than they look.

The request goes to the **local** worker, whatever volume the listing came
from, because the shell is asked with SHGFI_USEFILEATTRIBUTES and never reads
the file. Icons for a listing of a share that is answering slowly are not
behind that share; they cannot be, since nothing about them is on it.

Nothing here is asked for on the UI thread and nothing is asked for per row.
`icon` is called from `data()` while the view paints, which is the one place
in the application where a blocking call would be felt immediately, so the
call records the kind and returns what is already known -- the generic file or
folder icon until the real one lands. The kinds collected during a paint are
sent as one request a moment later.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QIcon, QImage, QPixmap

from app.io.protocol import ICON_FILE, ICON_FOLDER, Entry, Op, Reply, Status, icon_key

#: The path an ICON request carries. The op ignores it; the pool does not --
#: an empty path keys the local worker, which is where an association lookup
#: belongs whatever volume the rows came from.
LOCAL = ""

#: How long kinds gather before they are asked for, in milliseconds. Short
#: enough to be invisible, long enough that one scroll through a mixed folder
#: is one request rather than one per row that came into view.
COALESCE_MS = 30

#: The size an icon occupies in a row, in logical pixels. The shell is asked
#: for 32 on a scaled display and the pixmap is told it is a double-density
#: version of this, so a high-DPI screen gets the sharp one and the row height
#: does not change either way.
ROW_ICON = 16


class Icons(QObject):
    """The icon cache, shared by both panes."""

    changed = Signal()

    def __init__(self, bridge: Any, config: Any, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._config = config
        self._cache: dict[str, QIcon] = {}
        #: Kinds already sent. Kept whatever came back, so a kind the shell has
        #: no icon for is asked about once rather than on every repaint.
        self._asked: set[str] = set()
        self._pending: set[str] = set()
        self._size = ROW_ICON
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(COALESCE_MS)
        self._timer.timeout.connect(self.flush)

    @property
    def enabled(self) -> bool:
        return bool(self._config.get("icons.shell"))

    @property
    def size(self) -> int:
        return self._size

    def start(self, scale: float = 1.0) -> None:
        """Fetch the two generic icons, at the density this screen wants.

        The generics come first and on their own because they are what stands
        in for everything else: a row whose real icon has not arrived draws the
        generic one, so the name never shifts sideways when it does.
        """
        self._size = 32 if scale > 1.25 else ROW_ICON
        self.reload()

    def reload(self) -> None:
        """Ask again for anything missing, and have the panes draw again.

        This is what turning the setting back on runs. Nothing asked for the
        generics while it was off, and they are what every other row falls
        back to, so they have to be fetched before the rest is worth having.
        Turning it off runs the same call: nothing is asked for, and the
        repaint is the point.
        """
        self.want(ICON_FOLDER)
        self.want(ICON_FILE)
        self.flush()
        self.changed.emit()

    def icon(self, entry: Entry) -> QIcon | None:
        """What to draw for a row, now, without waiting for anything.

        Called from `data()` during a paint. It records what it did not have
        and returns the nearest thing it did.
        """
        if not self.enabled:
            return None
        key = icon_key(entry)
        found = self._cache.get(key)
        if found is not None:
            return found
        self.want(key)
        return self._cache.get(ICON_FOLDER if entry.is_dir else ICON_FILE)

    def folder_icon(self) -> QIcon | None:
        """For the parent row, which is a folder without being an entry."""
        if not self.enabled:
            return None
        self.want(ICON_FOLDER)
        return self._cache.get(ICON_FOLDER)

    def want(self, key: str) -> None:
        if not self.enabled or key in self._cache or key in self._asked:
            return
        self._pending.add(key)
        if not self._timer.isActive():
            self._timer.start()

    def flush(self) -> None:
        """Send whatever has gathered. Public because the timer is not the only
        thing that should be able to make this happen -- a test has no event
        loop to run it, and `start` wants the generics immediately.
        """
        self._timer.stop()
        if not self._pending:
            return
        keys = sorted(self._pending)
        self._pending.clear()
        self._asked.update(keys)
        self._bridge.submit(
            Op.ICON, LOCAL,
            timeout=float(self._config.get("timeout.icon")),
            on_reply=self._on_reply,
            args={"keys": keys, "size": self._size},
        )

    def _on_reply(self, reply: Reply) -> None:
        """Take whatever arrived, including a partial answer to a timeout.

        A timed-out request still carries the icons the shell managed before
        the deadline, and there is no reason to throw those away -- the keys
        it did not reach are in `_asked` and simply keep their generic icon,
        which is a worse-looking listing rather than a broken one.
        """
        payload = reply.payload if isinstance(reply.payload, dict) else {}
        if reply.status not in (Status.OK, Status.TIMEOUT):
            return
        size = int(payload.get("size") or self._size)
        added = False
        for key, pixels in (payload.get("icons") or {}).items():
            icon = _icon_from(pixels, size)
            if icon is not None:
                self._cache[str(key)] = icon
                added = True
        if added:
            self.changed.emit()


def _icon_from(pixels: bytes, size: int) -> QIcon | None:
    """Premultiplied BGRA from a worker, as something a view can draw.

    `.copy()` is not a tidiness: a QImage built over a buffer does not own it,
    and the bytes here are a reply that is about to go out of scope.

    The device pixel ratio is what keeps a 32-pixel icon from drawing at twice
    the size of the row it is in. It says the picture is a denser version of
    the same 16 logical pixels, which is the whole reason for asking the shell
    for the larger one on a scaled display.
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

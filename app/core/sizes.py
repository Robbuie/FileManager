"""Recursive folder sizes, computed only when somebody asks for one.

The listing never fills the size column for a folder. It cannot: a folder's
size is a walk of everything under it, which on a share is minutes rather than
milliseconds and is exactly the kind of call this application keeps off the
path between a user and their rows. So the column says `<DIR>` until it is
asked, and this is what asks.

Three pieces of bookkeeping keep it affordable, and all three matter:

  * one walk at a time. A folder walk holds that volume's worker for its whole
    duration, so twenty selected folders are a queue rather than twenty
    requests -- otherwise the tab next door waits behind all of them for its
    listing.
  * a walk is withdrawn, not merely forgotten, when the folder it is in is
    left. `cancel` reaches the worker; dropping the handler would leave it
    walking a tree nobody is looking at.
  * answers are keyed on the full path and dropped when their folder is
    listed again, because a size is exactly the thing that changes while the
    folder's own mtime does not.

A partial answer is kept rather than discarded. A walk that hit its deadline
has counted most of a large tree, and `4.2 G+` is more use to somebody deciding
what to copy than `<DIR>` is.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from app.core.listing import format_size
from app.io import paths
from app.io.protocol import Op, Reply, Status

#: What the size column shows while a walk is in flight.
WORKING = "..."


class FolderSizes(QObject):
    """The sizes that have been asked for, and the queue of the ones pending."""

    changed = Signal()

    def __init__(self, bridge, config, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._config = config
        #: path -> the text the column shows. `WORKING` while in flight.
        self._known: dict[str, str] = {}
        self._queue: list[str] = []
        self._current: str | None = None
        self._request_id: int | None = None

    # ------------------------------------------------------------------ state

    @property
    def busy(self) -> int:
        """How many walks are outstanding, the one running included."""
        return len(self._queue) + (1 if self._current else 0)

    def known(self, folder: str, name: str) -> str | None:
        """What to draw in the size column for a row, or None for `<DIR>`."""
        return self._known.get(paths.join(folder, name))

    # --------------------------------------------------------------- asking

    def request(self, folder: str, names) -> None:
        """Queue a walk for each named folder that has not already had one.

        Already had one includes one still running: pressing the key twice on
        the same folder is a repeat of the question, not a second walk of the
        tree.
        """
        started = False
        for name in names:
            target = paths.join(folder, name)
            if target in self._known:
                continue
            self._known[target] = WORKING
            self._queue.append(target)
            started = True
        if started:
            self.changed.emit()
            self._pump()

    def forget(self, folder: str) -> None:
        """Drop what is known about the contents of one folder.

        Called when that folder is listed again. A size is the thing that
        changes while the folder itself does not, so a cached one survives
        exactly as long as the listing it was shown against.
        """
        prefix = paths.normalize(folder).rstrip("\\").lower() + "\\"
        gone = [path for path in self._known
                if path.lower().startswith(prefix)
                and "\\" not in path[len(prefix):]]
        if not gone:
            return
        for path in gone:
            self._known.pop(path, None)
            if path in self._queue:
                self._queue.remove(path)
        if self._current in gone:
            self.cancel()
        self.changed.emit()

    def cancel(self) -> None:
        """Withdraw everything. What the user gets for pressing Escape.

        The running walk is cancelled at the worker rather than dropped here:
        a walk nobody is waiting for still holds the volume.
        """
        if self._request_id is not None:
            self._bridge.cancel(self._request_id)
            self._bridge.forget(self._request_id)
        outstanding = list(self._queue)
        if self._current is not None:
            outstanding.append(self._current)
        for path in outstanding:
            if self._known.get(path) == WORKING:
                # Back to `<DIR>`, not to a dash. A cancelled walk leaves the
                # question unanswered, and a row that reads as answered is
                # the one thing worse than a row that reads as unasked.
                self._known.pop(path, None)
        self._queue.clear()
        self._current = None
        self._request_id = None
        self.changed.emit()

    def reload(self) -> None:
        """Forget everything, for a folder that has been listed again."""
        self.cancel()
        self._known.clear()
        self.changed.emit()

    # ------------------------------------------------------------- internals

    def _pump(self) -> None:
        """Start the next walk, if nothing is walking."""
        if self._current is not None or not self._queue:
            return
        target = self._queue.pop(0)
        self._current = target
        self._request_id = self._bridge.submit(
            Op.DIR_SIZE, target,
            timeout=float(self._config.get("timeout.dir_size")),
            on_reply=self._replier(target),
        )

    def _replier(self, target: str):
        def handle(reply: Reply) -> None:
            self._on_reply(target, reply)
        return handle

    def _on_reply(self, target: str, reply: Reply) -> None:
        if self._current == target:
            self._current = None
            self._request_id = None
        if reply.status is Status.CANCELLED:
            self._known.pop(target, None)
        else:
            self._known[target] = _text(reply)
        self.changed.emit()
        self._pump()

    def __len__(self) -> int:
        return len(self._known)


def _text(reply: Reply) -> str:
    """What one answer looks like in a size column.

    A failure is a short word rather than a sentence: this is a cell in a
    table, and the status line is where an explanation belongs.
    """
    payload = reply.payload if isinstance(reply.payload, dict) else {}
    total = payload.get("bytes")
    if reply.status is Status.OK and isinstance(total, int):
        return format_size(total)
    if reply.status is Status.TIMEOUT and isinstance(total, int):
        # A walk that ran out of time has counted most of a large tree, and
        # a floor is more use than nothing to somebody deciding what to copy.
        return format_size(total) + "+"
    if reply.status is Status.DENIED:
        return "denied"
    return "?"

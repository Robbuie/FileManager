"""The folders beside this one, for a breadcrumb chevron.

The chevrons between crumbs were separators in 0.11 and are targets now: each
one drops down what is inside the crumb to its left, so getting from
`\\\\vault\\projects\\2026\\04` to `...\\2026\\05` is one click rather than up
and back down.

This was deliberately left out of the chrome release, and the reason is the
whole of this module: a dropdown is a filesystem call. It is not a label, it is
a scan of somebody's job folder over SMB, and it happens because a mouse
crossed a chevron. So it goes through a worker like everything else, and it is
bounded the way the overlay path is bounded rather than merely being allowed to
be slow:

  * **one request at a time, and the previous one is cancelled.** Clicking
    four chevrons in a row must leave one scan running, not four -- and the
    three abandoned ones are withdrawn at the worker rather than dropped here,
    or they go on holding that volume.
  * **capped in the worker.** `Op.FOLDERS` stops at its limit rather than
    enumerating the folder, so a chevron on a 50,000-row folder costs the
    first two hundred names.
  * **its own short deadline.** Nothing is waiting on this: a share that is
    answering slowly should lose its dropdown rather than hold anything up,
    and a timeout still delivers the names it got.
  * **not cached.** A sibling list is a menu that is open for a second, and a
    cache would have to be invalidated by the same folder changes the listing
    already re-lists for. The answer is used once and forgotten.

The reply carries the folder it was asked about, because by the time it comes
back the pane may be somewhere else entirely and the menu it was for may never
open.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from app.io.protocol import Op, Reply, Status


class Siblings(QObject):
    """One outstanding "what is in this folder" question at a time."""

    #: folder, names, whether the worker stopped short of the whole folder.
    ready = Signal(str, list, bool)
    #: folder, and what to say instead of a list.
    unavailable = Signal(str, str)

    def __init__(self, bridge, config, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._config = config
        self._request_id: int | None = None
        self._folder: str | None = None

    @property
    def pending(self) -> str | None:
        """The folder being asked about, or None."""
        return self._folder

    def ask(self, folder: str) -> None:
        """What folders are in here. Replaces whatever was being asked before."""
        if not folder:
            return
        self.cancel()
        self._folder = folder
        self._request_id = self._bridge.submit(
            Op.FOLDERS, folder,
            timeout=float(self._config.get("timeout.siblings")),
            on_reply=self._replier(folder),
            args={"limit": int(self._config.get("siblings.limit"))},
        )

    def cancel(self) -> None:
        """Withdraw the outstanding question at the worker.

        Cancelled rather than forgotten: an abandoned scan still holds the
        volume it is scanning, and the next chevron wants that worker.
        """
        if self._request_id is not None:
            self._bridge.cancel(self._request_id)
            self._bridge.forget(self._request_id)
        self._request_id = None
        self._folder = None

    # ------------------------------------------------------------- internals

    def _replier(self, folder: str):
        def handle(reply: Reply) -> None:
            self._on_reply(folder, reply)
        return handle

    def _on_reply(self, folder: str, reply: Reply) -> None:
        if folder != self._folder:
            return  # an answer to a chevron nobody is waiting on any more
        self._request_id = None
        self._folder = None
        payload = reply.payload if isinstance(reply.payload, dict) else {}
        names = payload.get("names")
        if reply.status in (Status.OK, Status.TIMEOUT) and isinstance(names, list):
            self.ready.emit(folder, list(names), bool(payload.get("more")))
            return
        if reply.status is Status.CANCELLED:
            return
        self.unavailable.emit(folder, _explain(reply))


def _explain(reply: Reply) -> str:
    """One short line for a menu that has nothing in it.

    Short because it is drawn as a disabled menu entry rather than in the
    status line: a sentence there wraps a dropdown to the width of the window.
    """
    if reply.status is Status.GONE:
        return "not reachable"
    if reply.status is Status.DENIED:
        return "no permission"
    if reply.status is Status.TIMEOUT:
        return "no answer"
    return "could not be read"

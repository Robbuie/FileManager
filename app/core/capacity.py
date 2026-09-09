"""How full a drive is, for the meters in the rail, without probing anything
that would hang.

A capacity meter is the one part of the rail that opens a volume. `disk_usage`
on a mapped drive whose server has gone is the 30-45 second block this whole
application is built around, so the rule here is narrow and worth stating
plainly:

  **a drive is measured only when somebody has asked for it, and the only
  automatic ask is for local fixed disks.**

That is what `measure_local` does, and it is called when the rail is first
shown rather than at startup. Removable drives are left alone because an empty
card reader and a CD tray with no disc both answer slowly; network drives are
left alone because that is the startup probe with a different name. Both are
one right-click away -- `measure` takes any drive -- and the meter fills when
the answer comes back.

The other three pieces of bookkeeping, which are the same ones `sizes.py`
needs and for the same reasons:

  * one request per drive in flight, so holding the key down is one question;
  * a failure is remembered as a failure, so a dead share is not asked again
    every time the rail redraws;
  * `forget` drops everything, which is what a drive rescan means -- the
    letters may not be the same letters.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal

from app.io.protocol import Op, Reply, Status

#: Drive types measured without being asked. Fixed disks and RAM disks are
#: local and answer immediately; everything else is asked for by hand.
AUTOMATIC = frozenset({"fixed", "ramdisk"})


@dataclass(frozen=True, slots=True)
class Usage:
    """One drive's answer. `share` is what the meter draws."""

    total: int
    used: int
    free: int

    @property
    def share(self) -> float:
        """Used, as a fraction of total, clamped. A total of zero is a drive
        that answered without being a volume, and draws empty rather than
        full."""
        if self.total <= 0:
            return 0.0
        return max(0.0, min(1.0, self.used / self.total))


class Capacity(QObject):
    """The drive capacities that have been asked for."""

    changed = Signal()

    def __init__(self, bridge, config, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._config = config
        #: letter -> Usage once answered, None while in flight, absent when it
        #: has never been asked. A drive that failed is remembered as a Usage
        #: of zeroes rather than re-asked, which is what stops a dead share
        #: being probed on every redraw.
        self._known: dict[str, Usage | None] = {}
        self._requests: dict[str, int] = {}

    # ------------------------------------------------------------------ state

    def usage(self, letter: str) -> Usage | None:
        """What is known about a drive, or None while it is unasked or in
        flight. The rail draws a row without a meter for both."""
        return self._known.get(_key(letter))

    def pending(self, letter: str) -> bool:
        return _key(letter) in self._requests

    def measured(self, letter: str) -> bool:
        """Whether this drive has been asked about at all, answered or not."""
        return _key(letter) in self._known or self.pending(letter)

    # --------------------------------------------------------------- asking

    def measure(self, letter: str) -> None:
        """Ask about one drive. Asking twice while one is in flight is one ask.

        The path is the drive root, so the request keys on that volume's own
        worker: a mapped drive that has gone holds its own worker for the
        deadline and nothing else in the window waits on it.
        """
        key = _key(letter)
        if not key or key in self._requests:
            return
        root = key + "\\"
        self._requests[key] = self._bridge.submit(
            Op.FREE_SPACE, root,
            timeout=float(self._config.get("timeout.free_space")),
            on_reply=self._replier(key),
        )

    def measure_local(self, drives) -> None:
        """Ask about every local fixed drive that has not been asked about.

        This is the only automatic measurement in the application, and the
        filter is what makes it safe: a remote letter is never in it, so a
        rail opening can never touch a server.
        """
        for drive in drives:
            if drive.get("type") in AUTOMATIC and not self.measured(drive["letter"]):
                self.measure(drive["letter"])

    def refresh(self, letter: str) -> None:
        """Ask again about a drive already answered. What the right-click
        entry does, and what a drive rescan does to the ones on screen."""
        key = _key(letter)
        self._known.pop(key, None)
        self.measure(letter)

    def forget(self) -> None:
        """Drop everything, in flight included.

        A drive rescan means the letters may not name the same volumes, so
        every answer held here is about a question that may no longer exist.
        """
        for request_id in self._requests.values():
            self._bridge.forget(request_id)
        self._requests.clear()
        self._known.clear()
        self.changed.emit()

    # ------------------------------------------------------------- internals

    def _replier(self, key: str):
        def handle(reply: Reply) -> None:
            self._on_reply(key, reply)
        return handle

    def _on_reply(self, key: str, reply: Reply) -> None:
        self._requests.pop(key, None)
        payload = reply.payload if isinstance(reply.payload, dict) else {}
        if reply.status is Status.OK and isinstance(payload.get("total"), int):
            self._known[key] = Usage(total=int(payload["total"]),
                                     used=int(payload.get("used", 0)),
                                     free=int(payload.get("free", 0)))
        else:
            # Remembered as a zero rather than dropped. Dropping it would mean
            # the next redraw asks again, and the drive that fails is exactly
            # the drive that costs the deadline to ask.
            self._known[key] = Usage(0, 0, 0)
        self.changed.emit()


def _key(letter: str) -> str:
    """One spelling per drive: `S:`, upper case, no separator."""
    return (letter or "").rstrip("\\").upper()

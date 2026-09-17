"""Getting this application off a drive before asking Windows to eject it.

`io/eject.py` makes the request. Most of what decides whether it succeeds is
here, because the most likely thing holding a USB drive open is the file
manager that is showing it:

- **A pane standing on the drive** is re-listed on an interval (`Pane.check`),
  so its folder is opened every few seconds. Every pane in front on that drive
  is moved to a safe folder first -- only the ones in front, because a tab
  behind is not listed and holds nothing.
- **A transfer to or from the drive** is refused outright rather than
  cancelled. Somebody who pressed Eject in the middle of a copy almost
  certainly forgot the copy, and the answer that loses nothing is to say so.

Then a short pause for the moved panes' last listing of the drive to finish,
and the request goes to the local worker.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

from app.io.protocol import Op, Reply, Status

#: How long the moved panes are given to let go before the request is made.
SETTLE_MS = 400


def on_drive(path: str, letter: str) -> bool:
    head = letter.rstrip("\\").upper()
    return (path or "")[:2].upper() == head and (len(path) == 2 or path[2:3] in ("\\", "/"))


def jobs_on(letter: str, jobs) -> list:
    """The transfers that read from or write to the drive."""
    found = []
    for job in jobs:
        places = [job.destination, *job.sources]
        if any(on_drive(place, letter) for place in places if place):
            found.append(job)
    return found


def refuge(letter: str, drives: list[dict]) -> str:
    """Where a pane goes when it is moved off the drive: the first fixed disk
    that is not itself about to be ejected, else C:."""
    for drive in drives:
        if drive.get("type") == "fixed" and not drive.get("ejectable") \
                and drive.get("letter", "").upper() != letter.upper():
            return drive["letter"].rstrip("\\") + "\\"
    return "C:\\"


class Ejector(QObject):
    #: A sentence for the status line, and whether the drive can now be pulled.
    finished = Signal(str, bool)

    def __init__(self, bridge: Any, config: Any, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._config = config
        self._pending: str | None = None

    @property
    def busy(self) -> bool:
        return self._pending is not None

    def eject(self, letter: str, panes, transfers, volumes) -> str:
        """Start ejecting. Returns "" when started, or why it was not."""
        letter = letter.rstrip("\\").upper()
        if self._pending is not None:
            return f"still ejecting {self._pending}"
        running = jobs_on(letter, list(transfers.active)) if transfers is not None else []
        if running:
            count = len(running)
            return (f"{letter} was not ejected: {count} transfer"
                    f"{'s' if count != 1 else ''} still using it")
        safe = refuge(letter, list(getattr(volumes, "drives", []) or []))
        for pane in panes:
            if on_drive(pane.current.path, letter):
                pane.navigate(safe)
        self._pending = letter
        QTimer.singleShot(SETTLE_MS, self._send)
        return ""

    def _send(self) -> None:
        letter = self._pending
        if letter is None:
            return
        self._bridge.submit(
            Op.EJECT, "",
            timeout=float(self._config.get("timeout.eject")),
            on_reply=self._on_reply,
            args={"letter": letter},
        )

    def _on_reply(self, reply: Reply) -> None:
        letter = self._pending or ""
        self._pending = None
        if reply.status is Status.OK:
            message = (reply.payload or {}).get("message") or f"{letter} can be removed"
            self.finished.emit(message, True)
        elif reply.status is Status.TIMEOUT:
            self.finished.emit(f"{letter} was not ejected: Windows did not answer in time", False)
        else:
            self.finished.emit(reply.message or f"{letter} was not ejected", False)

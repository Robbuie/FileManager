"""How each network location this session has used is answering (0.37).

A dot and a number beside each share in the rail: green and a few
milliseconds while it is healthy, amber when it is slow, red when it has
stopped answering. It turns the most common complaint about working over a
share -- the copy that crawls, the folder that takes a minute -- into something
visible *before* it is walked into.

What makes it acceptable in an application whose first rule is not to touch a
share nobody asked about:

  * **Only shares this session has already opened are measured.** A place
    listed in the rail and never visited is never pinged. The window tells
    this object which folders the panes are showing; nothing here enumerates
    anything, and the rail's own rule -- no place in it is checked for
    existence -- still holds.
  * **One request per share, and never a second while the first is out.** A
    share that has stopped answering accumulates one outstanding stat, not one
    per tick, and that one is under its own short deadline.
  * **The request is a stat of the share's root**, one SMB round trip, sent to
    the worker that already serves that share. It measures what a person would
    feel: when that worker is busy with a 50,000-row listing the reading is
    slow, which is true.
  * **It can be turned off**, and the interval is a setting.
"""

from __future__ import annotations

import time
from collections import deque

from PySide6.QtCore import QObject, QTimer, Signal

from app.io import paths
from app.io.protocol import Op, Reply, Status

#: How many readings each share keeps, for the history in its tooltip.
KEEP = 40
#: Seconds a ping may take before the share counts as not answering. Short on
#: purpose: a share that needs longer than this to stat its own root is one a
#: person would call down.
DEADLINE = 5.0

GOOD, SLOW, DOWN, UNKNOWN = "good", "slow", "down", "unknown"


class Reading:
    """What is known about one share."""

    def __init__(self) -> None:
        self.history: deque[float | None] = deque(maxlen=KEEP)
        self.last_down: float | None = None

    @property
    def latest(self) -> float | None:
        """Milliseconds, or None for no answer, or None before the first."""
        return self.history[-1] if self.history else None

    def usual(self) -> float | None:
        """The median of the answers that came back -- "usually N ms"."""
        answered = sorted(value for value in self.history if value is not None)
        if not answered:
            return None
        return answered[len(answered) // 2]

    def state(self, amber_ms: float) -> str:
        if not self.history:
            return UNKNOWN
        latest = self.history[-1]
        if latest is None:
            return DOWN
        return SLOW if latest >= amber_ms else GOOD


def key_for(path: str, drives) -> str | None:
    """The share a path is on, as the rail names it, or None if it is local.

    A UNC path is keyed on `\\\\server\\share`; a mapped letter on the letter
    itself (`S:`), because that is the row it is drawn beside. `drives` is the
    volumes' list, which says which letters are network drives -- a local
    disk has no share to measure.
    """
    root = paths.share_root(path)
    if root:
        return root.lower()
    letter = paths.drive_letter(path)
    if not letter:
        return None
    for drive in drives or []:
        if str(drive.get("letter", "")).upper() == letter.upper() and drive.get("unc"):
            return letter.upper()
    return None


class ShareHealth(QObject):
    changed = Signal()

    def __init__(self, bridge, config, parent=None) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._config = config
        self._readings: dict[str, Reading] = {}
        self._targets: dict[str, str] = {}
        self._out: dict[str, int] = {}
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.tick)
        self.configure()

    def configure(self) -> None:
        """Start, stop or re-pace after a setting changed."""
        seconds = float(self._config.get("network.ping_seconds"))
        on = bool(self._config.get("network.ping")) and seconds > 0
        if on:
            self._timer.start(int(seconds * 1000))
        else:
            self._timer.stop()
            for request_id in self._out.values():
                self._bridge.cancel(request_id)
                self._bridge.forget(request_id)
            self._out.clear()
        self.changed.emit()

    @property
    def enabled(self) -> bool:
        return self._timer.isActive()

    def amber_ms(self) -> float:
        return float(self._config.get("network.amber_ms"))

    def watch(self, path: str, drives=None) -> None:
        """A pane is showing `path`. If it is on a share, measure that share
        from now on -- and once straight away, so the dot appears now."""
        key = key_for(path, drives)
        if key is None or key in self._targets:
            return
        # A letter's root is `S:\\`; `S:` on its own means the current
        # directory on S:, which is not a place.
        self._targets[key] = key + "\\" if key.endswith(":") else key
        self._readings.setdefault(key, Reading())
        if self.enabled:
            self._ping(key)

    def reading(self, key: str) -> Reading | None:
        """What is known about a share, by the key `key_for` gives."""
        key = key or ""
        return self._readings.get(key.upper() if key.endswith(":") else key.lower())

    def watched(self) -> list[str]:
        return list(self._targets)

    def tick(self) -> None:
        if not self.enabled:
            return
        for key in list(self._targets):
            if key not in self._out:
                self._ping(key)

    def _ping(self, key: str) -> None:
        started = time.monotonic()
        self._out[key] = self._bridge.submit(
            Op.STAT, self._targets[key], timeout=DEADLINE,
            on_reply=lambda reply, k=key, t=started: self._on_reply(k, t, reply))

    def _on_reply(self, key: str, started: float, reply: Reply) -> None:
        self._out.pop(key, None)
        reading = self._readings.setdefault(key, Reading())
        if reply.status is Status.CANCELLED:
            return
        if reply.status in (Status.OK, Status.DENIED):
            # A refusal is still an answer: the share is there and quick to
            # say no, which is what this is measuring.
            reading.history.append((time.monotonic() - started) * 1000.0)
        else:
            reading.history.append(None)
            reading.last_down = time.time()
        self.changed.emit()

"""The drive letters, read once and shared by both panes.

Enumeration is cheap and local -- it reads the session table and a bitmask, and
probes no volume -- but it is still a filesystem call, so it goes through a
worker like every other one. The alternative is a picker that opens instantly
until the day a mapped server is down, which is the failure this application is
about.

One object rather than one per pane, because the answer is the same on both
sides and two callers asking the same question of the same worker is one
question too many.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, Signal

from app.io.protocol import Op, Reply, Status

#: The path a DRIVES request carries. The op ignores it; the pool does not --
#: it keys the worker on it, and an empty path keys the local volume, which is
#: where a read of the local session table belongs.
LOCAL = ""


class Volumes(QObject):
    """The drive letters this session has, as the session table describes them."""

    changed = Signal()

    def __init__(self, bridge: Any, config: Any, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._config = config
        self._request_id: int | None = None
        self.drives: list[dict] = []

    def refresh(self, *, rescan: bool = False) -> None:
        """Ask for the list again.

        `rescan` bypasses the cached letter-to-UNC table, which is what a user
        who has just mapped a drive in Explorer wants and what nobody else
        needs to pay for.
        """
        if self._request_id is not None:
            self._bridge.forget(self._request_id)
        self._request_id = self._bridge.submit(
            Op.DRIVES, LOCAL,
            timeout=float(self._config.get("timeout.drives")),
            on_reply=self._on_reply,
            args={"refresh": rescan},
        )

    def letter_for(self, path: str) -> str | None:
        """The listed letter a path sits on, or None.

        Compared case-insensitively against the letters we were given rather
        than parsed here, so a path on a volume this session does not have --
        a UNC typed straight into the path bar -- selects nothing instead of
        selecting the wrong thing.
        """
        head = (path or "")[:2].upper()
        for drive in self.drives:
            if drive["letter"].upper() == head:
                return drive["letter"]
        return None

    def _on_reply(self, reply: Reply) -> None:
        self._request_id = None
        if reply.status is not Status.OK or not isinstance(reply.payload, list):
            return  # keep the last good list; an empty picker is worse than a stale one
        self.drives = list(reply.payload)
        self.changed.emit()

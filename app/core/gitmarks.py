"""Git's marks for the folder on screen, asked for once and kept (0.38).

The overlays' bookkeeping, for the overlays' reason: this is a question about
files that costs a program run, so it is bounded by hand. One request per
folder, answered from what is already here while that folder stays on screen,
asked again at most every `REFRESH` seconds as the folder is re-listed; local
volumes only, because running git across a share is a walk of the share; and
nothing at all when the setting is off.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QObject, Signal

from app.io import paths
from app.io.protocol import Op, Reply, Status

#: Seconds an answer is good for while its folder is on screen. A live check
#: re-lists every couple of seconds; git need not be run that often.
REFRESH = 10.0
KEEP = 64


class GitMarks(QObject):
    changed = Signal()

    def __init__(self, bridge, config, parent=None) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._config = config
        self._known: dict[str, tuple[float, dict]] = {}
        self._out: dict[str, int] = {}

    def enabled(self) -> bool:
        return bool(self._config.get("git.badges"))

    def ask(self, folder: str) -> None:
        """The folder is on screen; make sure its marks are known and fresh."""
        if not folder or not self.enabled() or self._bridge is None:
            return
        if paths.volume_key(folder) != paths.LOCAL_VOLUME_KEY:
            return
        key = folder.lower()
        if key in self._out:
            return
        known = self._known.get(key)
        if known is not None and time.monotonic() - known[0] < REFRESH:
            return
        self._out[key] = self._bridge.submit(
            Op.GIT, folder, timeout=float(self._config.get("timeout.git")),
            on_reply=lambda reply, k=key: self._on_reply(k, reply))

    def _on_reply(self, key: str, reply: Reply) -> None:
        self._out.pop(key, None)
        payload = reply.payload if reply.status is Status.OK \
            and isinstance(reply.payload, dict) else {"marks": {}}
        before = self._known.get(key)
        self._known[key] = (time.monotonic(), payload)
        while len(self._known) > KEEP:
            self._known.pop(next(iter(self._known)))
        if before is None or before[1] != payload:
            self.changed.emit()

    def mark(self, folder: str, name: str) -> str | None:
        known = self._known.get((folder or "").lower())
        if known is None or not self.enabled():
            return None
        payload = known[1]
        if payload.get("untracked"):
            return "?"
        return payload.get("marks", {}).get(name.lower())

    def branch(self, folder: str) -> str:
        known = self._known.get((folder or "").lower())
        if known is None or not self.enabled():
            return ""
        payload = known[1]
        text = payload.get("branch", "")
        if text and payload.get("ahead"):
            text += f" +{payload['ahead']}"
        if text and payload.get("behind"):
            text += f" -{payload['behind']}"
        return text

    def forget(self) -> None:
        self._known.clear()
        self.changed.emit()

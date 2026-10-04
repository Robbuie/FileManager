"""The F3 browser's questions about a Logix export, asked of a worker (0.55).

Three questions -- what programs and routines are in it, what is in one
routine, and where a piece of text appears -- each an `Op.LOGIX` request to
the export's own volume. One of each kind outstanding at a time, the older one
cancelled at the worker when a newer one is asked, for the preview's reason:
somebody clicking down a list of routines wants the one they stopped on.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, Signal

from app.io.protocol import Op, Reply, Status


class LogixBrowse(QObject):
    outlineReady = Signal(str, object)     # path, {"controller", "containers"}
    routineReady = Signal(str, object)     # path, {"program", "routine", "rungs", ...}
    hitsReady = Signal(str, object)        # path, {"text", "hits", "more"}
    failed = Signal(str, str)              # path, why

    def __init__(self, bridge: Any, config: Any, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._config = config
        #: kind -> request id, for the one outstanding request of each kind.
        self._open: dict[str, int] = {}

    def outline(self, path: str) -> None:
        self._ask("outline", path, {}, self.outlineReady)

    def routine(self, path: str, program: str, routine: str) -> None:
        self._ask("routine", path, {"program": program, "routine": routine},
                  self.routineReady)

    def search(self, path: str, text: str) -> None:
        self._ask("search", path, {"text": text}, self.hitsReady)

    def cancel(self) -> None:
        for request in self._open.values():
            self._bridge.cancel(request)
            self._bridge.forget(request)
        self._open.clear()

    def _ask(self, what: str, path: str, args: dict, signal) -> None:
        previous = self._open.pop(what, None)
        if previous is not None:
            self._bridge.cancel(previous)
            self._bridge.forget(previous)
        holder: dict[str, int] = {}

        def handle(reply: Reply) -> None:
            if self._open.get(what) != holder.get("id"):
                return          # an answer to a question since replaced
            self._open.pop(what, None)
            if reply.status is Status.CANCELLED:
                return
            if reply.status is Status.OK and isinstance(reply.payload, dict):
                signal.emit(path, reply.payload)
                return
            why = reply.message or {
                Status.TIMEOUT: "no answer in time",
                Status.GONE: "not reachable",
                Status.DENIED: "no permission to read this",
            }.get(reply.status, "could not be read")
            self.failed.emit(path, why)

        holder["id"] = self._bridge.submit(
            Op.LOGIX, path, timeout=float(self._config.get("timeout.logix")),
            on_reply=handle, args={"what": what, **args})
        self._open[what] = holder["id"]

"""The one place a worker reply becomes something the UI may touch.

The pool answers on its own reader threads. Qt objects may only be touched on
the thread that made them, so every reply crosses here and nowhere else: the
bridge emits a signal from the reader thread, Qt queues it, and the handler
runs on the UI thread.

That queue is also the reason a late reply is harmless. A tab that navigates
away drops its request id, and when the reply for it finally arrives the
handler for that id is gone.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from PySide6.QtCore import QObject, Signal

from app.io.protocol import Op, Reply, Status

Handler = Callable[[Reply], None]

_SETTLED = {Status.OK, Status.TIMEOUT, Status.CANCELLED,
            Status.DENIED, Status.GONE, Status.ERROR}


class Bridge(QObject):
    """Submits work to the pool and delivers the answers on the UI thread."""

    _arrived = Signal(object)

    def __init__(self, pool: Any, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pool = pool
        self._handlers: dict[int, Handler] = {}
        # Auto-connection across threads means queued, which is the entire
        # point: the emit happens on a reader thread, `_deliver` on this one.
        self._arrived.connect(self._deliver)

    def submit(
        self,
        op: Op,
        path: str,
        *,
        timeout: float,
        on_reply: Handler,
        args: Mapping[str, Any] | None = None,
    ) -> int:
        request_id = self._pool.submit(
            op, path, timeout=timeout, args=args, handler=self._arrived.emit,
        )
        # Registering after submitting is safe, and only because of the queue:
        # a reply that arrives in the meantime is sitting in this thread's
        # event queue and cannot be delivered until this call returns.
        self._handlers[request_id] = on_reply
        return request_id

    def cancel(self, request_id: int | None) -> None:
        if request_id is None:
            return
        self._pool.cancel(request_id)

    def forget(self, request_id: int | None) -> None:
        """Stop caring about a request without cancelling it.

        Used when a pane navigates away: the work may as well finish, but its
        answer is no longer anybody's business.
        """
        if request_id is not None:
            self._handlers.pop(request_id, None)

    def retry(self, path: str) -> None:
        self._pool.retry(path)

    def _deliver(self, reply: Reply) -> None:
        handler = (self._handlers.pop(reply.id, None)
                   if reply.status in _SETTLED else self._handlers.get(reply.id))
        if handler is not None:
            handler(reply)

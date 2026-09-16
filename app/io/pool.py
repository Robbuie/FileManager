"""Spawns, kills and restarts worker processes, one per volume.

The pool is where a hung share stops being everyone's problem. It owns three
things that only make sense together:

  * **Placement.** Workers are keyed on resolved server name, so `S:\\` and
    `\\\\server\\share\\` land on one process instead of two that hang
    independently. Every local disk shares one worker. Each volume has a
    second worker, its side lane, for the slow decorations of a listing
    (`SIDE_OPS`), so they never stand in front of the listing itself.
  * **The watchdog.** A worker cannot enforce its own deadline against a call
    that never returns, so the parent times requests instead. A request whose
    deadline passes is not a late request, it is evidence the process is stuck,
    and the process is killed.
  * **Settling what was outstanding.** Whatever restarts a worker also has to
    answer every request that was in flight on it. A reply that never comes is
    how a tab waits forever, so the pool fails them itself: TIMEOUT for the one
    that expired, GONE for the rest. They are re-issued by the caller, never
    resumed — the worker kept no state to resume from.

Deadlines measure time without progress. Each streamed batch pushes the
deadline out, so a 50,000-row listing that keeps delivering never trips it
while a share that goes quiet trips it within the timeout.

Reply handlers run on the pool's reader threads, not the caller's. Nothing in
`ui` may be touched from one; `core` marshals onto the UI thread with a Qt
signal, which is the only place that crossing happens.
"""

from __future__ import annotations

import itertools
import multiprocessing as mp
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from app.io import menu, paths, worker
from app.io.protocol import MENU_HOST, Op, Reply, Request, Status

ReplyHandler = Callable[[Reply], None]

#: How often the watchdog looks for expired requests. Fine enough that a hung
#: share is noticed promptly, coarse enough to be free.
WATCHDOG_INTERVAL = 0.25

#: Grace between asking a worker to stop and killing it outright.
KILL_GRACE = 1.0

#: How long a reader thread waits on its queue before checking whether the
#: worker it belongs to is still meant to exist.
READER_POLL = 0.2

#: A volume that needs restarting this often in this window is not recovering,
#: and restarting it forever would be a spin. It is marked unreachable and left
#: alone until something explicitly retries it — which is the "reconnecting,
#: with a retry" state the tab shows.
RESTART_LIMIT = 3
RESTART_WINDOW = 60.0

#: The requests that go to the shell host instead of to a volume's worker.
#: Placement lives here rather than in the caller for the same reason every
#: other placement decision does: the callers ask for a menu on a path, and
#: which process is the right one to load somebody else's DLL in is not their
#: question.
#:
#: The host is one process for every volume, not one per volume. A menu is
#: something a person opens one of at a time, and an extension wedged on a
#: share takes the menu down with it either way -- but only the menu, and only
#: until the watchdog kills it.
HOST_OPS = frozenset({Op.MENU, Op.MENU_INVOKE, Op.MENU_RELEASE})

#: The requests that decorate a listing rather than produce one, and so go to a
#: second worker for the same volume instead of queueing in front of the next
#: listing.
#:
#: A worker answers one request at a time, and every one of these can be slow
#: on a share for reasons that have nothing to do with the folder being asked
#: for: an overlay is a full `SHGetFileInfo` per row on screen, which on a
#: Hyper-V or Remote Desktop redirected drive (`\\tsclient\C`) is a round trip
#: through the redirector per file; a thumbnail or a preview reads the file;
#: a folder size walks a tree for up to two minutes. In one queue, the listing
#: of the folder somebody just opened waited behind the badges of the folder
#: they left -- and since a deadline runs from submission, a listing that
#: waited long enough was timed out, its worker killed, and after three of
#: those the volume was marked unreachable. That is "slow, and sometimes fails
#: to load", on a drive Explorer lists instantly.
#:
#: The side lane restarts and is marked unreachable on its own account, so a
#: shell extension that wedges on a share costs the badges and not the folder.
SIDE_OPS = frozenset({Op.OVERLAY, Op.FILE_ICON, Op.THUMBNAIL, Op.PREVIEW, Op.DIR_SIZE})

#: Appended to a volume's key to name its side lane.
SIDE_SUFFIX = " +side"


def lane_key(op: Op, path: str) -> str:
    """Which worker a request goes to: the shell host, a volume's main lane,
    or that volume's side lane."""
    if op in HOST_OPS:
        return MENU_HOST
    key = paths.volume_key(path)
    return key + SIDE_SUFFIX if op in SIDE_OPS else key


@dataclass
class _Pending:
    request: Request
    handler: ReplyHandler
    deadline: float
    cancelled: bool = False


@dataclass
class _Worker:
    key: str
    inbox: Any
    outbox: Any
    control: Any
    process: Any
    pending: dict[int, _Pending] = field(default_factory=dict)
    reader: threading.Thread | None = None
    alive: bool = True


class WorkerPool:
    """The parent-side half of the io layer.

    Not thread safe by accident: it is used from the UI thread and from its own
    reader threads at once, so every mutation of the worker table happens under
    one lock and every handler is called outside it.
    """

    def __init__(self, *, context: Any = None) -> None:
        # Spawn rather than fork: it is the only start method on Windows, and
        # using it everywhere means the test machine exercises what ships.
        self._ctx = context or mp.get_context("spawn")
        self._lock = threading.RLock()
        self._workers: dict[str, _Worker] = {}
        self._restarts: dict[str, list[float]] = {}
        self._ids = itertools.count(1)
        self._stopping = False
        self._watchdog = threading.Thread(
            target=self._watch, name="fm-io-watchdog", daemon=True
        )
        self._watchdog.start()

    # ----------------------------------------------------------------- calls

    def submit(
        self,
        op: Op,
        path: str,
        *,
        timeout: float,
        handler: ReplyHandler,
        args: Mapping[str, Any] | None = None,
    ) -> int:
        """Queue a request and return its id. Never blocks on the filesystem.

        A volume already marked unreachable is answered immediately rather than
        queued, so a caller cannot pile requests onto a process that is not
        coming back.
        """
        resolved = paths.resolve(path)
        key = lane_key(op, resolved)
        request = Request(
            id=next(self._ids), op=op, path=resolved,
            timeout=timeout, args=dict(args or {}),
        )
        with self._lock:
            if self._stopping:
                handler(Reply(request.id, Status.GONE, message="pool is shutting down"))
                return request.id
            if self._unreachable(key):
                handler(Reply(request.id, Status.GONE,
                              message=f"{key} marked unreachable; retry to try again"))
                return request.id
            target = self._ensure(key)
            target.pending[request.id] = _Pending(
                request=request, handler=handler,
                deadline=time.monotonic() + timeout,
            )
            target.inbox.put(request)
        return request.id

    def cancel(self, request_id: int) -> None:
        """Withdraw a request. The reply still arrives, as CANCELLED.

        Settling on the reply rather than on the call keeps one request to one
        reply, which is what lets a late reply be dropped without a special
        case anywhere else.
        """
        with self._lock:
            for target in self._workers.values():
                pending = target.pending.get(request_id)
                if pending is None:
                    continue
                pending.cancelled = True
                pending.deadline = time.monotonic() + pending.request.timeout
                try:
                    target.control.put(("cancel", request_id))
                except (ValueError, OSError):
                    pass
                return

    def kill(self, path: str) -> bool:
        """Kill the worker serving a path and fail what was outstanding on it.

        The manual form of what the watchdog does, and the thing a "reconnect"
        button calls. Returns False when there was no worker to kill.
        """
        key = paths.volume_key(path)
        with self._lock:
            targets = [t for t in (self._workers.get(key),
                                   self._workers.get(key + SIDE_SUFFIX)) if t is not None]
        for target in targets:
            self._restart(target, expired=set())
        return bool(targets)

    def retry(self, path: str) -> None:
        """Clear the unreachable mark on a volume so requests are accepted again."""
        key = paths.volume_key(path)
        with self._lock:
            self._restarts.pop(key, None)
            self._restarts.pop(key + SIDE_SUFFIX, None)

    def retry_host(self) -> None:
        """The same, for the shell host.

        Called before every menu rather than offered as a button. A volume
        that keeps dying is worth leaving alone until somebody says otherwise,
        because the requests behind it are a folder somebody is waiting for. A
        shell host that keeps dying costs one process per right-click and
        nothing else, and the alternative -- a menu that stops appearing until
        the application is restarted -- is the worse failure by some way.
        """
        with self._lock:
            self._restarts.pop(MENU_HOST, None)

    def status(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {
                key: {
                    "pid": getattr(target.process, "pid", None),
                    "alive": bool(target.process and target.process.is_alive()),
                    "pending": len(target.pending),
                    "restarts": len(self._restarts.get(key, ())),
                }
                for key, target in self._workers.items()
            }

    def shutdown(self, *, timeout: float = 5.0) -> None:
        with self._lock:
            self._stopping = True
            targets = list(self._workers.values())
            self._workers.clear()
        for target in targets:
            self._stop(target, timeout=timeout)

    # -------------------------------------------------------------- internals

    def _unreachable(self, key: str) -> bool:
        recent = self._recent_restarts(key)
        return len(recent) >= RESTART_LIMIT

    def _recent_restarts(self, key: str) -> list[float]:
        cutoff = time.monotonic() - RESTART_WINDOW
        recent = [t for t in self._restarts.get(key, ()) if t >= cutoff]
        if recent:
            self._restarts[key] = recent
        else:
            self._restarts.pop(key, None)
        return recent

    def _ensure(self, key: str) -> _Worker:
        """Get the worker for a volume, spawning it if there is not one.

        Spawning is the only thing done eagerly, and it costs nothing: a fresh
        process that has not been asked for anything touches no volume.
        """
        target = self._workers.get(key)
        if target is not None and target.alive and target.process.is_alive():
            return target
        if target is not None:
            self._stop(target, timeout=KILL_GRACE)
        target = self._spawn(key)
        self._workers[key] = target
        return target

    def _spawn(self, key: str) -> _Worker:
        inbox = self._ctx.Queue()
        outbox = self._ctx.Queue()
        control = self._ctx.Queue()
        entry = menu.run if key == MENU_HOST else worker.run
        process = self._ctx.Process(
            target=entry, args=(inbox, outbox, control),
            name=f"fm-io {key.strip() or key}", daemon=True,
        )
        process.start()
        target = _Worker(key=key, inbox=inbox, outbox=outbox,
                         control=control, process=process)
        target.reader = threading.Thread(
            target=self._read, args=(target,),
            name=f"fm-io-reader {key}", daemon=True,
        )
        target.reader.start()
        return target

    def _read(self, target: _Worker) -> None:
        """Drain one worker's replies for as long as it lives.

        Polled rather than blocked on the queue, so that killing the worker
        ends this thread rather than stranding it on a `get` that will never
        return. A restart that leaked a thread each time would be a slow leak
        in exactly the situation the pool exists to survive.
        """
        while target.alive:
            try:
                reply = target.outbox.get(timeout=READER_POLL)
            except queue.Empty:
                continue
            except (EOFError, OSError, ValueError):
                return
            if reply is None:
                return
            self._dispatch(target, reply)

    def _dispatch(self, target: _Worker, reply: Reply) -> None:
        with self._lock:
            pending = target.pending.get(reply.id)
            if pending is None:
                return  # already settled: a late batch after a restart or cancel
            if reply.status is Status.PARTIAL:
                pending.deadline = time.monotonic() + pending.request.timeout
            else:
                target.pending.pop(reply.id, None)
        pending.handler(reply)

    def _watch(self) -> None:
        while not self._stopping:
            time.sleep(WATCHDOG_INTERVAL)
            now = time.monotonic()
            casualties: list[tuple[_Worker, set[int]]] = []
            with self._lock:
                for target in list(self._workers.values()):
                    if not target.alive:
                        continue
                    expired = {
                        p.request.id for p in target.pending.values()
                        if p.deadline <= now
                    }
                    died = target.process is not None and not target.process.is_alive()
                    if expired or (died and target.pending):
                        casualties.append((target, expired))
            for target, expired in casualties:
                self._restart(target, expired=expired)

    def _restart(self, target: _Worker, *, expired: set[int]) -> None:
        """Kill a worker, answer everything it owed, and let the next request
        spawn a fresh one.

        Restarting lazily rather than here keeps the rule that nothing touches a
        volume until something asks for it: a volume nobody is looking at any
        more does not get a new process.
        """
        with self._lock:
            outstanding = target.pending
            target.pending = {}
            target.alive = False
            if self._workers.get(target.key) is target:
                del self._workers[target.key]
            self._restarts.setdefault(target.key, []).append(time.monotonic())
            unreachable = self._unreachable(target.key)

        self._stop(target, timeout=KILL_GRACE)

        note = (
            f"{target.key} stopped responding and was restarted; re-issue the request"
            if not unreachable else
            f"{target.key} is not recovering and is marked unreachable"
        )
        for request_id, pending in outstanding.items():
            status = Status.TIMEOUT if request_id in expired else Status.GONE
            pending.handler(Reply(request_id, status, message=note))

    def _stop(self, target: _Worker, *, timeout: float) -> None:
        """End a worker and let go of its queues.

        Asked first, insisted on second: a worker between requests exits on the
        sentinel and takes its buffered replies with it, and one blocked inside
        a syscall is terminated, which is the whole reason it is a process.
        """
        target.alive = False
        process = target.process
        if process is not None and process.is_alive():
            try:
                target.inbox.put(None)
            except (ValueError, OSError):
                pass
            process.join(timeout)
            if process.is_alive():
                process.terminate()
                process.join(KILL_GRACE)
            if process.is_alive():
                process.kill()
                process.join(KILL_GRACE)
        if target.reader is not None:
            target.reader.join(READER_POLL * 4)
        # `cancel_join_thread` matters here: without it the parent blocks at
        # exit trying to flush a queue whose reader has been killed.
        for q in (target.inbox, target.outbox, target.control):
            try:
                q.cancel_join_thread()
                q.close()
            except (ValueError, OSError, AssertionError):
                pass

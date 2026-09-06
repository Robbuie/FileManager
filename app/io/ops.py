"""The copy/move queue, in a process of its own.

Separate from the listing workers so a stalled transfer cannot take the window
down, and so a transfer is never competing with the listing of the folder it is
writing into.

The hard part is not the copying. It is the queue around it, and this is where
most hobby file managers fall over, so it was designed before the copy loop was
written:

  * pause and resume;
  * per-file and total progress, on a byte count established up front;
  * conflict rules -- skip, overwrite, newer only, auto-rename -- decided per
    item, with an answer that can be applied to the rest of the queue;
  * locked-file retry with a bounded backoff, not an indefinite one;
  * timestamps and attributes preserved on the destination;
  * a confirmed destination before a single byte moves. The application never
    picks a target on its own and never reports where something went after the
    fact.

Three decisions are worth stating because the alternatives all look reasonable
until they are tried.

**One job at a time.** Two transfers to the same disk are slower than the same
two run in order, and two transfers to the same *folder* can produce a name
collision that neither of them was asked about. The queue is a queue.

**Scan before copying.** A byte total that grows while the bar moves is not a
progress bar. The walk costs one pass over the tree and buys a total that means
something, and it is where an unreadable source is found -- before anything has
been written rather than half way through.

**A move within a volume is a rename.** It is attempted first, per source, and
only falls back to copy-then-delete when the rename is refused because the
destination is elsewhere. Moving 40GB between two folders on one disk has to be
instant, because it is.

The process is killable at any point. What that costs is bounded on purpose: a
file is copied to its final name, so a kill mid-file leaves a short file that
the next run treats as a conflict, and a move deletes its source only after the
copy of that item has been verified by size. Nothing is deleted that was not
first written somewhere else.
"""

from __future__ import annotations

import errno
import multiprocessing as mp
import os
import queue
import shutil
import signal
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from app.io.protocol import (
    CHUNK,
    PROGRESS_INTERVAL,
    Answer,
    Conflict,
    Event,
    Job,
    Progress,
    Transfer,
)

#: Waits between attempts on a file something else has open. Bounded, and
#: short: a file locked by an application is usually locked for as long as that
#: application is running, and retrying for a minute only delays telling the
#: user something they need to act on.
RETRY_DELAYS = (0.5, 1.5, 3.0)

#: Windows sharing violation. A file that is open elsewhere, which is the case
#: worth retrying -- as opposed to access denied, which is a permission and
#: will be denied just as firmly in three seconds.
_SHARING_VIOLATION = 32


@dataclass
class Item:
    """One thing to copy: a file, or a directory that has to exist first."""

    source: str
    target: str
    size: int = 0
    is_dir: bool = False


@dataclass
class Totals:
    copied: int = 0
    skipped: int = 0
    failed: int = 0
    bytes: int = 0
    cancelled: bool = False


class _Cancelled(Exception):
    """This job was withdrawn. The next one still runs."""


class _Stopped(Exception):
    """The process is going away. Nothing else runs."""


# --------------------------------------------------------------------------
# The process.
# --------------------------------------------------------------------------


def run(inbox: Any, outbox: Any) -> None:
    """Process entry point."""
    try:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    except (ValueError, OSError):
        pass
    Runner(inbox, outbox).loop()


class Runner:
    """The queue, and the copy loop under it."""

    def __init__(self, inbox: Any, outbox: Any) -> None:
        self.inbox = inbox
        self.outbox = outbox
        self.jobs: list[Job] = []
        self.cancelled: set[int] = set()
        self.answers: dict[int, Answer] = {}
        self.rules: dict[int, Conflict] = {}
        self.paused = False
        self.stopping = False
        self._last_tick = 0.0

    # ------------------------------------------------------------- the loop

    def loop(self) -> None:
        while not self.stopping:
            if not self.jobs:
                try:
                    message = self.inbox.get()
                except (EOFError, OSError):  # the parent went away
                    return
                if message is None:
                    return
                self._control(message)
                continue

            job = self.jobs.pop(0)
            if job.id in self.cancelled:
                self.cancelled.discard(job.id)
                self._emit(job.id, Progress.DONE, {"cancelled": True})
                continue
            try:
                self._run(job)
            except _Stopped:
                return
            except _Cancelled:
                self.cancelled.discard(job.id)
                self._emit(job.id, Progress.DONE, {"cancelled": True})
            except Exception as exc:  # noqa: BLE001 - a job must always end
                self._emit(job.id, Progress.DONE, {"failed": 1},
                           message=_describe(exc))
            finally:
                self.answers.pop(job.id, None)
                self.rules.pop(job.id, None)

    def _control(self, message: Any) -> None:
        kind, payload = message
        if kind == "enqueue":
            self.jobs.append(payload)
        elif kind == "cancel":
            self.cancelled.add(payload)
        elif kind == "answer":
            self.answers[payload.job] = payload
        elif kind == "pause":
            if not self.paused:
                self.paused = True
                self._emit(0, Progress.PAUSED)
        elif kind == "resume":
            if self.paused:
                self.paused = False
                self._emit(0, Progress.RESUMED)
        elif kind == "stop":
            self.stopping = True

    def _pump(self, timeout: float | None = None) -> None:
        """Take control messages. Without a timeout, only what is already waiting.

        This is what makes a pause or a cancel land between chunks rather than
        at the end of a file: the loop calls it often, and it never blocks
        unless it is asked to.
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            try:
                if deadline is None:
                    message = self.inbox.get_nowait()
                else:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return
                    message = self.inbox.get(timeout=remaining)
            except queue.Empty:
                return
            except (EOFError, OSError):
                self.stopping = True
                return
            if message is None:
                self.stopping = True
                return
            self._control(message)
            if deadline is not None:
                return

    def _checkpoint(self, job: Job) -> None:
        """Between chunks: stop, cancel, or wait out a pause."""
        self._pump()
        if self.stopping:
            raise _Stopped()
        if job.id in self.cancelled:
            raise _Cancelled()
        while self.paused:
            self._pump(timeout=0.2)
            if self.stopping:
                raise _Stopped()
            if job.id in self.cancelled:
                raise _Cancelled()

    def _emit(self, job: int, kind: Progress, payload: dict | None = None,
              message: str = "") -> None:
        try:
            self.outbox.put(Event(job=job, kind=kind, payload=payload or {},
                                  message=message))
        except (OSError, ValueError):  # the parent closed the queue
            self.stopping = True

    # ------------------------------------------------------------- one job

    def _run(self, job: Job) -> None:
        self._emit(job.id, Progress.STARTED, {
            "kind": job.kind.value, "destination": job.destination,
            "sources": len(job.sources),
        })
        totals = Totals()
        sources = list(job.sources)

        # A cancel is caught here rather than by the loop, so that the job can
        # still say what it managed before it was stopped. "Cancelled" on its
        # own tells the user nothing about what is now in the destination.
        try:
            if job.kind is Transfer.MOVE:
                sources = self._rename_what_can_be_renamed(job, sources, totals)

            if sources:
                self._emit(job.id, Progress.SCANNING)
                items, unreadable = self._scan(job, sources)
                totals.failed += len(unreadable)
                for source, problem in unreadable:
                    self._emit(job.id, Progress.FAILED_ITEM,
                               {"name": os.path.basename(source)}, message=problem)
                total_bytes = sum(item.size for item in items if not item.is_dir)
                files = sum(1 for item in items if not item.is_dir)
                self._emit(job.id, Progress.SCANNED,
                           {"files": files, "bytes": total_bytes})
                self._transfer(job, items, totals, total_bytes)
                if job.kind is Transfer.MOVE:
                    self._prune(job, items, totals)
        except _Cancelled:
            totals.cancelled = True
            self.cancelled.discard(job.id)

        self._emit(job.id, Progress.DONE, {
            "copied": totals.copied, "skipped": totals.skipped,
            "failed": totals.failed, "bytes": totals.bytes,
            "cancelled": totals.cancelled,
        })

    def _rename_what_can_be_renamed(self, job: Job, sources: list[str],
                                    totals: Totals) -> list[str]:
        """Move by rename where the destination allows it, before scanning.

        Before, not after, because the scan is the expensive part and a move
        within one volume does not need it. What comes back is the sources that
        have to be copied instead.
        """
        remaining: list[str] = []
        for source in sources:
            self._checkpoint(job)
            target = os.path.join(job.destination, os.path.basename(source))
            if os.path.exists(target):
                remaining.append(source)   # a conflict is decided in the copy path
                continue
            try:
                os.rename(source, target)
            except OSError:
                # Cross-volume, in-use, or anything else: the copy path will
                # attempt it properly and report what actually went wrong.
                remaining.append(source)
                continue
            totals.copied += 1
            self._emit(job.id, Progress.COPYING, {
                "name": os.path.basename(source), "instant": True,
                "done": totals.bytes, "total": totals.bytes,
            })
        return remaining

    def _scan(self, job: Job, sources: Iterable[str]):
        """Walk the sources into a flat list of items, directories first.

        Directories come before their contents so that creating them is just
        another item rather than a special case inside the copy loop.
        """
        items: list[Item] = []
        unreadable: list[tuple[str, str]] = []
        for source in sources:
            self._checkpoint(job)
            target = os.path.join(job.destination, os.path.basename(source))
            try:
                if os.path.isdir(source) and not os.path.islink(source):
                    items.append(Item(source, target, is_dir=True))
                    self._walk(job, source, target, items, unreadable)
                else:
                    items.append(Item(source, target,
                                      size=os.path.getsize(source)))
            except OSError as exc:
                unreadable.append((source, _describe(exc)))
        return items, unreadable

    def _walk(self, job: Job, source: str, target: str, items: list[Item],
              unreadable: list[tuple[str, str]]) -> None:
        try:
            scanner = os.scandir(source)
        except OSError as exc:
            unreadable.append((source, _describe(exc)))
            return
        with scanner:
            for entry in scanner:
                self._checkpoint(job)
                child_target = os.path.join(target, entry.name)
                try:
                    if entry.is_dir(follow_symlinks=False):
                        items.append(Item(entry.path, child_target, is_dir=True))
                        self._walk(job, entry.path, child_target, items, unreadable)
                    else:
                        items.append(Item(entry.path, child_target,
                                          size=entry.stat(follow_symlinks=False).st_size))
                except OSError as exc:
                    unreadable.append((entry.path, _describe(exc)))

    def _transfer(self, job: Job, items: list[Item], totals: Totals,
                  total_bytes: int) -> None:
        for item in items:
            self._checkpoint(job)
            if item.is_dir:
                try:
                    os.makedirs(item.target, exist_ok=True)
                except OSError as exc:
                    totals.failed += 1
                    self._emit(job.id, Progress.FAILED_ITEM,
                               {"name": os.path.basename(item.source)},
                               message=_describe(exc))
                continue

            target = item.target
            if os.path.exists(target):
                action = self._decide(job, item)
                if action is Conflict.SKIP:
                    totals.skipped += 1
                    continue
                if action is Conflict.RENAME:
                    target = _unique(target)

            try:
                self._copy_file(job, item, target, totals, total_bytes)
            except _Cancelled:
                raise
            except OSError as exc:
                totals.failed += 1
                self._emit(job.id, Progress.FAILED_ITEM,
                           {"name": os.path.basename(item.source)},
                           message=_describe(exc))
                continue
            totals.copied += 1
            if job.kind is Transfer.MOVE:
                self._remove_source(job, item, totals, target)

    def _copy_file(self, job: Job, item: Item, target: str, totals: Totals,
                   total_bytes: int) -> None:
        """Copy one file, in chunks, checking for pause and cancel between them.

        Not `shutil.copyfile`: that would copy a 4GB file with no way to stop
        and nothing to report while it ran.
        """
        last_error: OSError | None = None
        # Where the byte count stood before the first attempt. A retry rewrites
        # the file from the start, so the count has to go back with it or a
        # locked file that succeeds on the third try reports three times its
        # size and the total stops meaning anything.
        started_at = totals.bytes
        for attempt, delay in enumerate((0.0,) + RETRY_DELAYS):
            if delay:
                self._wait(job, delay)
            totals.bytes = started_at
            try:
                self._stream(job, item, target, totals, total_bytes)
                return
            except _Cancelled:
                raise
            except OSError as exc:
                last_error = exc
                if not _worth_retrying(exc) or attempt == len(RETRY_DELAYS):
                    raise
        if last_error is not None:  # pragma: no cover - the loop always raises
            raise last_error

    def _stream(self, job: Job, item: Item, target: str, totals: Totals,
                total_bytes: int) -> None:
        """Write beside the target, then rename onto it.

        Nothing half-written ever wears the real name. That matters twice: a
        cancel or a kill leaves no truncated file for someone to open next
        week and wonder about, and an overwrite either happens or does not --
        without this, cancelling half way through overwriting a file destroys
        the original and leaves a fragment in its place.

        The rename is within one folder, so it is a metadata operation even on
        a share, and it is the last thing that happens.
        """
        name = os.path.basename(item.source)
        done_before = totals.bytes
        partial = _partial_name(target)
        try:
            with open(item.source, "rb") as source, open(partial, "wb") as sink:
                while True:
                    self._checkpoint(job)
                    chunk = source.read(CHUNK)
                    if not chunk:
                        break
                    sink.write(chunk)
                    totals.bytes += len(chunk)
                    self._tick(job, name, totals.bytes - done_before, item.size,
                               totals.bytes, total_bytes)
            shutil.copystat(item.source, partial)
            os.replace(partial, target)
        except BaseException:
            _discard(partial)
            raise
        self._tick(job, name, item.size, item.size, totals.bytes, total_bytes,
                   force=True)

    def _remove_source(self, job: Job, item: Item, totals: Totals,
                       written: str) -> None:
        """Delete a moved file, and only once its copy is the right size.

        The check is cheap and the failure it guards against is not: a move
        that deleted the original after a truncated copy is the one bug in a
        file manager that cannot be apologised for.
        """
        try:
            if os.path.getsize(written) != item.size:
                raise OSError(f"{os.path.basename(written)} is not the "
                              f"size it should be; the original was kept")
            os.remove(item.source)
        except OSError as exc:
            totals.failed += 1
            self._emit(job.id, Progress.FAILED_ITEM,
                       {"name": os.path.basename(item.source)},
                       message=_describe(exc))

    def _prune(self, job: Job, items: list[Item], totals: Totals) -> None:
        """After a move, remove the source folders, deepest first.

        `os.rmdir` rather than a recursive delete, and that is the safety in
        it: a folder that still holds something -- a file that was skipped,
        one that failed -- refuses to go, which is exactly right.
        """
        for item in sorted((i for i in items if i.is_dir),
                           key=lambda i: len(i.source), reverse=True):
            try:
                os.rmdir(item.source)
            except OSError:
                pass  # not empty, and that is information rather than a fault

    # ------------------------------------------------------------ conflicts

    def _decide(self, job: Job, item: Item) -> Conflict:
        """What to do about a target that already exists.

        The rule for the job can be set once and applied to everything after
        it; until then every collision is a question, and the job waits.
        """
        rule = self.rules.get(job.id, job.conflict)
        if rule is Conflict.ASK:
            rule = self._ask(job, item)
        if rule is Conflict.NEWER:
            try:
                newer = os.path.getmtime(item.source) > os.path.getmtime(item.target)
            except OSError:
                newer = True
            return Conflict.OVERWRITE if newer else Conflict.SKIP
        return rule

    def _ask(self, job: Job, item: Item) -> Conflict:
        self._emit(job.id, Progress.CONFLICT, {
            "name": os.path.basename(item.source),
            "source": _describe_file(item.source),
            "target": _describe_file(item.target),
        })
        while True:
            answer = self.answers.pop(job.id, None)
            if answer is not None:
                if answer.apply_to_all:
                    self.rules[job.id] = answer.action
                return answer.action
            self._checkpoint(job)
            self._pump(timeout=0.2)

    # -------------------------------------------------------------- plumbing

    def _wait(self, job: Job, seconds: float) -> None:
        """Sleep, but stay answerable while doing it."""
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            self._checkpoint(job)
            time.sleep(min(0.1, max(0.0, until - time.monotonic())))

    def _tick(self, job: Job, name: str, item_done: int, item_total: int,
              done: int, total: int, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_tick < PROGRESS_INTERVAL:
            return
        self._last_tick = now
        self._emit(job.id, Progress.COPYING, {
            "name": name, "item_done": item_done, "item_total": item_total,
            "done": done, "total": total,
        })


# --------------------------------------------------------------------------
# The parent side.
# --------------------------------------------------------------------------


class Transfers:
    """Owns the ops process and delivers its events to one handler.

    The handler runs on this object's reader thread, exactly like the pool's
    reply handlers, and for the same reason nothing in `ui` may be touched from
    it. `core` marshals onto the UI thread.

    The process is started on the first job rather than at launch. Someone who
    never copies anything never pays for a second process.
    """

    def __init__(self, on_event: Callable[[Event], None], *, context: Any = None) -> None:
        self._ctx = context or mp.get_context("spawn")
        self._on_event = on_event
        self._lock = threading.RLock()
        self._process: Any = None
        self._inbox: Any = None
        self._outbox: Any = None
        self._reader: threading.Thread | None = None
        self._stopping = False
        self._ids = iter(range(1, 1 << 30))

    # ------------------------------------------------------------- commands

    def submit(self, kind: Transfer, sources: Iterable[str], destination: str, *,
               conflict: Conflict = Conflict.ASK) -> int:
        job = Job(id=next(self._ids), kind=kind, sources=tuple(sources),
                  destination=destination, conflict=conflict)
        with self._lock:
            self._ensure()
            self._inbox.put(("enqueue", job))
        return job.id

    def cancel(self, job_id: int) -> None:
        self._send(("cancel", job_id))

    def pause(self) -> None:
        self._send(("pause", None))

    def resume(self) -> None:
        self._send(("resume", None))

    def answer(self, job_id: int, action: Conflict, *, apply_to_all: bool = False) -> None:
        self._send(("answer", Answer(job=job_id, action=action,
                                     apply_to_all=apply_to_all)))

    @property
    def running(self) -> bool:
        with self._lock:
            return self._process is not None and self._process.is_alive()

    def shutdown(self, timeout: float = 2.0) -> None:
        """Ask the process to stop, then insist.

        Whatever was in flight is abandoned rather than finished: a transfer
        that outlives its window needs a process that is not this one's child,
        and until that exists the honest thing is for the window to say what is
        still running before it closes.
        """
        with self._lock:
            self._stopping = True
            process, inbox = self._process, self._inbox
            self._process = self._inbox = None
        if process is None:
            return
        try:
            inbox.put(("stop", None))
        except (OSError, ValueError):
            pass
        process.join(timeout)
        if process.is_alive():
            process.terminate()
            process.join(1.0)

    # -------------------------------------------------------------- internals

    def _send(self, message: tuple) -> None:
        with self._lock:
            if self._inbox is None:
                return
            try:
                self._inbox.put(message)
            except (OSError, ValueError):
                pass

    def _ensure(self) -> None:
        if self._process is not None and self._process.is_alive():
            return
        self._stopping = False
        self._inbox = self._ctx.Queue()
        self._outbox = self._ctx.Queue()
        self._process = self._ctx.Process(
            target=run, args=(self._inbox, self._outbox),
            name="fm-ops", daemon=True,
        )
        self._process.start()
        self._reader = threading.Thread(target=self._read, args=(self._outbox,),
                                        name="fm-ops-reader", daemon=True)
        self._reader.start()

    def _read(self, outbox: Any) -> None:
        while True:
            try:
                event = outbox.get(timeout=0.2)
            except queue.Empty:
                with self._lock:
                    if self._stopping or self._outbox is not outbox:
                        return
                continue
            except (EOFError, OSError, ValueError):
                return
            try:
                self._on_event(event)
            except Exception:  # noqa: BLE001 - a bad handler must not end the reader
                pass


# --------------------------------------------------------------------------
# Helpers.
# --------------------------------------------------------------------------


def _unique(target: str) -> str:
    """`report.pdf` -> `report (2).pdf`, the way Windows names a second copy.

    Counts up rather than using a timestamp or a uuid: the point of keeping
    both files is that a person is going to look at them.
    """
    folder, name = os.path.split(target)
    stem, dot, suffix = name.rpartition(".")
    if not dot or not stem:
        stem, suffix = name, ""
    index = 2
    while True:
        candidate = f"{stem} ({index})" + (f".{suffix}" if suffix else "")
        full = os.path.join(folder, candidate)
        if not os.path.exists(full):
            return full
        index += 1


#: Suffix for a file still being written. Beside the target rather than in a
#: temp folder, because the rename onto the target has to stay within one
#: volume to be instant -- and on a share, a temp folder is a second network
#: location that may not exist.
PARTIAL_SUFFIX = ".fm-part"


def _partial_name(target: str) -> str:
    candidate = target + PARTIAL_SUFFIX
    index = 2
    while os.path.exists(candidate):
        candidate = f"{target}{PARTIAL_SUFFIX}{index}"
        index += 1
    return candidate


def _discard(path: str) -> None:
    """Remove a partial write. A failure here is not worth reporting: the file
    it could not remove is one nothing refers to.
    """
    try:
        os.remove(path)
    except OSError:
        pass


def _worth_retrying(exc: OSError) -> bool:
    """A file somebody else has open is worth waiting for. A permission is not."""
    if getattr(exc, "winerror", None) == _SHARING_VIOLATION:
        return True
    return exc.errno in (errno.EACCES, errno.EBUSY) and os.name == "nt"


def _describe_file(path: str) -> dict:
    """Enough about a file to choose between two of them."""
    try:
        stat = os.stat(path)
    except OSError:
        return {"path": path, "size": None, "mtime": None}
    return {"path": path, "size": stat.st_size, "mtime": stat.st_mtime}


def _describe(exc: BaseException) -> str:
    winerror = getattr(exc, "winerror", None)
    detail = f" (winerror {winerror})" if winerror else ""
    return f"{type(exc).__name__}: {exc}{detail}"

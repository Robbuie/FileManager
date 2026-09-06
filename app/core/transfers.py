"""The transfer queue as the window sees it.

The same job `app/core/bridge.py` does for workers: events arrive on the ops
process's reader thread, cross to the UI thread through a queued signal, and
nothing in `ui` ever touches anything that came off that thread directly.

What this adds on top is memory. The ops process reports what is happening
right now; a status bar and a queue dialog need to know what has happened so
far -- which job, how far through, what it is called, what has already failed.
That state lives here, in one place, so the status bar and the dialog cannot
disagree about it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from PySide6.QtCore import QObject, Signal

from app.io import ops
from app.io.protocol import Conflict, Event, Progress, Transfer


#: Longest file name shown in a one-line readout. Past this the middle goes,
#: because the start says what it is and the end says what kind.
NAME_LIMIT = 34


def shorten(name: str, limit: int = NAME_LIMIT) -> str:
    if len(name) <= limit:
        return name
    head = (limit - 3) // 2
    return f"{name[:head]}...{name[-(limit - 3 - head):]}"


@dataclass
class JobState:
    """One transfer, as much as is known about it."""

    id: int
    kind: Transfer
    destination: str
    state: str = "queued"       # queued, scanning, running, waiting, done
    files: int = 0
    total: int = 0              # bytes to move, once the scan has finished
    done: int = 0               # bytes moved
    current: str = ""           # the file being written
    copied: int = 0
    skipped: int = 0
    failed: int = 0
    cancelled: bool = False
    problems: list[str] = field(default_factory=list)

    @property
    def percent(self) -> int:
        if self.total <= 0:
            return 0
        return min(100, int(self.done * 100 / self.total))

    @property
    def brief(self) -> str:
        """The same line with the file name shortened rather than the line.

        Eliding the whole string is what takes the numbers off the end -- and
        the numbers are the half a person actually reads while a copy runs. A
        long file name is the part that can afford to lose its middle.
        """
        return self._label(shorten(self.current))

    @property
    def label(self) -> str:
        return self._label(self.current)

    def _label(self, current: str) -> str:
        verb = "Copying" if self.kind is Transfer.COPY else "Moving"
        if self.state == "scanning":
            return f"{verb}: counting what is there"
        if self.state == "waiting":
            return f"{verb} {current} — waiting for an answer"
        if self.state == "done":
            return "Finished"
        return f"{verb} {current}" if current else verb


class TransferQueue(QObject):
    """Owns the ops process and holds what it has said."""

    _arrived = Signal(object)

    changed = Signal()                  # any state moved; redraw the readouts
    conflict = Signal(int, dict)        # job id, what collided
    finished = Signal(object)           # JobState, when a job ends

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.jobs: dict[int, JobState] = {}
        self.order: list[int] = []
        self.paused = False
        self._transfers = ops.Transfers(self._arrived.emit)
        # Auto-connection across threads means queued, which is the point: the
        # emit happens on the ops reader thread and `_deliver` on this one.
        self._arrived.connect(self._deliver)

    # -------------------------------------------------------------- commands

    def copy(self, sources: Iterable[str], destination: str) -> int:
        return self._start(Transfer.COPY, sources, destination)

    def move(self, sources: Iterable[str], destination: str) -> int:
        return self._start(Transfer.MOVE, sources, destination)

    def pause(self) -> None:
        self.paused = True
        self._transfers.pause()
        self.changed.emit()

    def resume(self) -> None:
        self.paused = False
        self._transfers.resume()
        self.changed.emit()

    def cancel(self, job_id: int) -> None:
        self._transfers.cancel(job_id)

    def answer(self, job_id: int, action: Conflict, *, apply_to_all: bool = False) -> None:
        job = self.jobs.get(job_id)
        if job is not None and job.state == "waiting":
            job.state = "running"
        self._transfers.answer(job_id, action, apply_to_all=apply_to_all)
        self.changed.emit()

    def shutdown(self) -> None:
        self._transfers.shutdown()

    # ----------------------------------------------------------------- state

    @property
    def active(self) -> list[JobState]:
        return [self.jobs[i] for i in self.order if self.jobs[i].state != "done"]

    @property
    def busy(self) -> bool:
        return bool(self.active)

    def current(self) -> JobState | None:
        """The job a one-line readout should be about."""
        running = self.active
        return running[0] if running else None

    def totals(self) -> tuple[int, int]:
        """Bytes done and bytes to do, across everything still running."""
        running = self.active
        return (sum(job.done for job in running), sum(job.total for job in running))

    def forget_finished(self) -> None:
        for job_id in [i for i in self.order if self.jobs[i].state == "done"]:
            self.order.remove(job_id)
            self.jobs.pop(job_id, None)
        self.changed.emit()

    # -------------------------------------------------------------- internals

    def _start(self, kind: Transfer, sources: Iterable[str], destination: str) -> int:
        sources = tuple(sources)
        job_id = self._transfers.submit(kind, sources, destination)
        self.jobs[job_id] = JobState(id=job_id, kind=kind, destination=destination,
                                     files=len(sources))
        self.order.append(job_id)
        self.changed.emit()
        return job_id

    def _deliver(self, event: Event) -> None:
        if event.kind is Progress.PAUSED or event.kind is Progress.RESUMED:
            self.paused = event.kind is Progress.PAUSED
            self.changed.emit()
            return

        job = self.jobs.get(event.job)
        if job is None:
            return

        if event.kind is Progress.STARTED:
            job.state = "running"
        elif event.kind is Progress.SCANNING:
            job.state = "scanning"
        elif event.kind is Progress.SCANNED:
            job.files = int(event.payload.get("files", 0))
            job.total = int(event.payload.get("bytes", 0))
            job.state = "running"
        elif event.kind is Progress.COPYING:
            job.state = "running"
            job.current = str(event.payload.get("name", ""))
            job.done = int(event.payload.get("done", job.done))
            if event.payload.get("total"):
                job.total = max(job.total, int(event.payload["total"]))
        elif event.kind is Progress.CONFLICT:
            job.state = "waiting"
            self.changed.emit()
            self.conflict.emit(job.id, dict(event.payload))
            return
        elif event.kind is Progress.FAILED_ITEM:
            job.failed += 1
            name = event.payload.get("name", "")
            job.problems.append(f"{name}: {event.message}" if name else event.message)
        elif event.kind is Progress.DONE:
            job.state = "done"
            job.copied = int(event.payload.get("copied", 0))
            job.skipped = int(event.payload.get("skipped", 0))
            job.failed = max(job.failed, int(event.payload.get("failed", 0)))
            job.cancelled = bool(event.payload.get("cancelled"))
            job.current = ""
            self.changed.emit()
            self.finished.emit(job)
            return

        self.changed.emit()

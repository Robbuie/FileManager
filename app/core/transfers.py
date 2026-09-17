"""The job queue as the window sees it.

The same job `app/core/bridge.py` does for workers: events arrive on the ops
process's reader thread, cross to the UI thread through a queued signal, and
nothing in `ui` ever touches anything that came off that thread directly.

What this adds on top is memory. The ops process reports what is happening
right now; a status bar and a queue panel need to know what has happened so
far -- which job, how far through, what it is called, what has already failed.
That state lives here, in one place, so the status bar and the panel cannot
disagree about it.

Since 0.14 it also holds the *order*, and the order is the reason this file is
not simply a mirror. The ops process owns the real queue -- it is the thing that
decides what runs next -- but a reorder or a hold has to show on screen the
instant it is clicked rather than a round trip later, and the two must not then
drift. So `order` is kept here and moved here, the same command is sent to the
process, and every event that names a position is applied on arrival. The
process is still the authority: if the two ever disagree, the event wins.
"""

from __future__ import annotations

import ntpath
import os.path
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

from PySide6.QtCore import QObject, Signal

from app.core.listing import format_size
from app.io import ops
from app.io.protocol import Conflict, Event, JobKind, Progress


#: Longest file name shown in a one-line readout. Past this the middle goes,
#: because the start says what it is and the end says what kind.
NAME_LIMIT = 34


def shorten(name: str, limit: int = NAME_LIMIT) -> str:
    if len(name) <= limit:
        return name
    head = (limit - 3) // 2
    return f"{name[:head]}...{name[-(limit - 3 - head):]}"


#: What each kind is called while it runs, and what it is called in a list.
#: One table rather than a chain of conditionals in four places, because every
#: one of those places was getting the same four cases slightly differently.
VERBS: dict[JobKind, tuple[str, str]] = {
    JobKind.COPY: ("Copying", "Copy"),
    JobKind.MOVE: ("Moving", "Move"),
    JobKind.RECYCLE: ("Recycling", "Recycle"),
    JobKind.ERASE: ("Erasing", "Erase"),
}


@dataclass
class JobState:
    """One job, as much as is known about it."""

    id: int
    kind: JobKind
    destination: str
    sources: tuple[str, ...] = ()
    state: str = "queued"       # queued, scanning, running, waiting, done
    files: int = 0
    total: int = 0              # bytes to move, or items to remove
    done: int = 0               # bytes moved, or items removed
    current: str = ""           # the file being written or removed
    copied: int = 0
    skipped: int = 0
    failed: int = 0
    cancelled: bool = False
    held: bool = False
    #: Why the job was turned away before anything moved -- today, the
    #: destination not having room for it. Kept as its own field rather than
    #: as one more line in `problems` because it is the *whole* outcome: the
    #: status bar says this instead of "0 copied, 0 skipped", which is what a
    #: refusal would otherwise read as.
    refused: str = ""
    #: Whether pause, hold and cancel can reach this job *now*. A recycle is one
    #: shell call, so once it has started nothing can interrupt it, and a panel
    #: that offered the buttons anyway would be describing a queue that does not
    #: exist. False only while such a step is actually running.
    interruptible: bool = True
    problems: list[str] = field(default_factory=list)
    #: Names Windows refused rather than failed at. Kept apart from `problems`
    #: because only these are worth offering to retry as administrator, and an
    #: offer made about a file that has simply gone would be a consent prompt
    #: that could not have helped.
    denied: list[str] = field(default_factory=list)
    #: 0.29: the file being written, as far as it has got, for the row that is
    #: filling up in the pane. And the names already written, so a finished
    #: row reads as finished rather than empty.
    item_done: int = 0
    item_total: int = 0
    finished_names: set[str] = field(default_factory=set)
    #: `(monotonic seconds, bytes done)`, the last few seconds of them, for the
    #: speed; and one speed per second for the pill's sparkline.
    samples: list[tuple[float, int]] = field(default_factory=list)
    speeds: list[float] = field(default_factory=list)
    spark_at: float = 0.0

    @property
    def speed(self) -> float:
        """Bytes a second over the last few seconds, or 0 when not known."""
        return speed(self.samples)

    @property
    def remaining_seconds(self) -> float | None:
        rate = self.speed
        if rate <= 0 or self.total <= 0 or self.counts_items:
            return None
        return max(0.0, (self.total - self.done) / rate)

    @property
    def percent(self) -> int:
        if self.total <= 0:
            return 0
        return min(100, int(self.done * 100 / self.total))

    @property
    def counts_items(self) -> bool:
        """Whether `done` and `total` are items rather than bytes.

        A delete's cost is the number of files, not their size: 40,000 tiny
        files take far longer than one big one, and a bar drawn from bytes
        would sit still and then jump.
        """
        return self.kind.removes

    @property
    def folders(self) -> set[str]:
        """The folders this job changes, for whoever has to re-list them.

        The destination, and the folder each source came out of. A copy leaves
        its sources alone, but working that out per kind here would put the
        knowledge in two places -- and a pane showing a folder that did not
        change is re-listed for nothing, which costs one listing and no
        correctness.
        """
        touched = {os.path.dirname(source) for source in self.sources}
        if self.destination:
            touched.add(self.destination)
        return {folder for folder in touched if folder}

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
        verb = VERBS[self.kind][0]
        if self.state == "done":
            return "Finished"
        if self.held:
            return f"{verb}: held"
        if self.state == "scanning":
            return f"{verb}: counting what is there"
        if self.state == "waiting":
            return f"{verb} {current} — waiting for an answer"
        if self.state == "queued":
            return f"{verb}: waiting its turn"
        return f"{verb} {current}" if current else verb


#: How far back the speed looks. Long enough to smooth over one slow file on a
#: share, short enough that a copy that has stalled stops claiming a speed.
SPEED_WINDOW = 3.0
#: Sparkline length: one speed per second, this many seconds.
SPARK_POINTS = 60


def speed(samples: list[tuple[float, int]], now: float | None = None,
          window: float = SPEED_WINDOW) -> float:
    """Bytes per second across the samples inside the window. Pure."""
    if len(samples) < 2:
        return 0.0
    end_time, end_bytes = samples[-1]
    if now is not None and now - end_time > window:
        return 0.0
    start_time, start_bytes = samples[0]
    for when, done in samples:
        if end_time - when <= window:
            start_time, start_bytes = when, done
            break
    elapsed = end_time - start_time
    if elapsed <= 0:
        return 0.0
    return max(0.0, (end_bytes - start_bytes) / elapsed)


def format_eta(seconds: float | None) -> str:
    if seconds is None:
        return ""
    seconds = int(round(seconds))
    if seconds < 60:
        return f"0:{seconds:02d} left"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}:{seconds:02d} left"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d} left"


def _same_folder(a: str, b: str) -> bool:
    return (a or "").rstrip("\\/").lower() == (b or "").rstrip("\\/").lower()


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

    def copy(self, sources: Iterable[str], destination: str, *,
             conflict: Conflict = Conflict.ASK) -> int:
        return self._start(JobKind.COPY, sources, destination, conflict=conflict)

    def move(self, sources: Iterable[str], destination: str, *,
             conflict: Conflict = Conflict.ASK) -> int:
        return self._start(JobKind.MOVE, sources, destination, conflict=conflict)

    def duplicate(self, source: str, folder: str, name: str) -> int:
        """A copy of one item beside itself, under a new name.

        A copy job like any other -- the queue, the progress, the cancel, the
        write-beside-and-rename -- with one difference the engine enforces: it
        will not land on a name that is already taken. Merging into an existing
        folder is exactly wrong for a folder-per-day backup.
        """
        return self._start(JobKind.COPY, [source], folder, rename=name)

    def recycle(self, sources: Iterable[str]) -> int:
        """To the Recycle Bin. No destination: the shell knows where that is."""
        return self._start(JobKind.RECYCLE, sources, "")

    def erase(self, sources: Iterable[str]) -> int:
        """Permanently, item by item."""
        return self._start(JobKind.ERASE, sources, "")

    def hold(self, job_id: int) -> None:
        """Keep a job where it is in the queue but do not let it run.

        Optimistic, like `move_job`: the flag goes up here and the command goes
        to the process, because a button that only responded once a message had
        crossed a process boundary and come back would feel broken on a busy
        queue. The HELD event that follows sets the same flag again.
        """
        job = self.jobs.get(job_id)
        if job is None or job.state == "done":
            return
        job.held = True
        self._transfers.hold(job_id)
        self.changed.emit()

    def release(self, job_id: int) -> None:
        job = self.jobs.get(job_id)
        if job is None:
            return
        job.held = False
        self._transfers.release(job_id)
        self.changed.emit()

    def move_job(self, job_id: int, delta: int) -> None:
        """Move a waiting job up or down the queue.

        Refused for the job that is running, and for a finished one. The
        running job has already left the process's own list, so a reorder
        there would silently do nothing and the two lists would disagree from
        then on -- which is exactly the drift this class exists to avoid.
        """
        job = self.jobs.get(job_id)
        if job is None or job.state != "queued":
            return
        self._reorder_locally(job_id, delta)
        self._transfers.reorder(job_id, delta)
        self.changed.emit()

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
        """The job a one-line readout should be about.

        The one that is actually running, not merely the first that has not
        finished: with holds in the queue those are no longer the same job, and
        a status bar that named a held one while another was copying would be
        pointing at the wrong thing.
        """
        running = [job for job in self.active if job.state not in ("queued",)]
        if running:
            return running[0]
        waiting = self.active
        return waiting[0] if waiting else None

    def totals(self) -> tuple[int, int]:
        """Bytes done and bytes to do, across the transfers still running.

        Transfers only. A delete counts items, and adding items to bytes gives
        a number that is not a quantity of anything.
        """
        running = [job for job in self.active if not job.counts_items]
        return (sum(job.done for job in running), sum(job.total for job in running))

    def _sample(self, job: JobState, done: int, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        job.samples.append((now, done))
        while len(job.samples) > 2 and now - job.samples[0][0] > SPEED_WINDOW * 2:
            job.samples.pop(0)
        if now - job.spark_at >= 1.0:
            job.spark_at = now
            job.speeds.append(job.speed)
            del job.speeds[:-SPARK_POINTS]

    def row_progress(self, folder: str, name: str) -> float | None:
        """How far a row has got, for a row a running transfer is writing.

        A row is one of the job's own top-level items: in the folder it came
        from, or in the destination. A file that is being written fills by its
        own bytes; one already written is full; a folder -- whose inside is
        what is being written -- fills by the whole job. None for any other
        row, which is almost every row.
        """
        wanted = name.lower()
        for job in self.active:
            if job.state != "running" or job.counts_items:
                continue
            tops = {ntpath.basename(source.rstrip("\\")).lower(): source
                    for source in job.sources}
            source = tops.get(wanted)
            if source is None:
                continue
            here = _same_folder(folder, job.destination) or \
                _same_folder(folder, ntpath.dirname(source.rstrip("\\")))
            if not here:
                continue
            if job.current.lower() == wanted and job.item_total:
                return min(1.0, job.item_done / job.item_total)
            if wanted in job.finished_names:
                return 1.0
            if job.current and job.current.lower() not in tops and job.total:
                # Something inside a folder is being written: the folder rows
                # fill with the job.
                return min(1.0, job.done / job.total)
            return 0.0
        return None

    def forget_finished(self) -> None:
        for job_id in [i for i in self.order if self.jobs[i].state == "done"]:
            self.order.remove(job_id)
            self.jobs.pop(job_id, None)
        self.changed.emit()

    # -------------------------------------------------------------- internals

    def _place(self, job_id: int, position: int) -> None:
        """Put an id at the process's idea of its position among the waiting.

        The process's positions count only what is still in its own list, so
        they are offsets into the waiting jobs rather than into `order`, which
        also holds the running one and everything already finished. Translating
        rather than trusting the number outright is what keeps a finished job in
        the list from pushing a waiting one to the wrong place.
        """
        if job_id not in self.order:
            return
        waiting = [i for i in self.order
                   if self.jobs[i].state == "queued" and i != job_id]
        position = max(0, min(len(waiting), position))
        self.order.remove(job_id)
        if position < len(waiting):
            self.order.insert(self.order.index(waiting[position]), job_id)
        else:
            self.order.append(job_id)

    def _reorder_locally(self, job_id: int, delta: int) -> None:
        """Move an id within `order`, clamped, and only among the waiting.

        The clamp is against the whole list rather than against the waiting
        part of it, and that is on purpose: `move_job` has already refused
        anything that is not waiting, and the running job is at the front, so
        an "up" from the first waiting job lands back where it started rather
        than in front of the thing writing files.
        """
        if job_id not in self.order:
            return
        index = self.order.index(job_id)
        first_waiting = min(
            (i for i, j in enumerate(self.order) if self.jobs[j].state == "queued"),
            default=index,
        )
        target = max(first_waiting, min(len(self.order) - 1, index + delta))
        if target == index:
            return
        self.order.insert(target, self.order.pop(index))

    def _start(self, kind: JobKind, sources: Iterable[str], destination: str, *,
               conflict: Conflict = Conflict.ASK, rename: str = "") -> int:
        """Start a job. `conflict` is the rule the process applies without
        asking; `ASK` is the default and the only one that stops.

        Carried here rather than left to the process's own default because of
        one case: a copy pasted into the folder it came from collides with
        every name, and asking about each of them is a dialog the user has
        already answered by pressing Ctrl+V in that folder.
        """
        sources = tuple(sources)
        # `rename` only when there is one, so everything that is not a
        # duplicate is submitted exactly as it was before duplicates existed.
        extra = {"rename": rename} if rename else {}
        job_id = self._transfers.submit(kind, sources, destination,
                                        conflict=conflict, **extra)
        self.jobs[job_id] = JobState(id=job_id, kind=kind, destination=destination,
                                     sources=sources, files=len(sources),
                                     total=len(sources) if kind.removes else 0)
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

        if event.kind is Progress.QUEUED:
            # The process's own position, which is the authority. It only ever
            # differs from this side's after a reorder that crossed one in
            # flight, and applying it here is what stops that becoming
            # permanent.
            job.state = "queued"
            self._place(event.job, int(event.payload.get("position", 0)))
        elif event.kind is Progress.HELD:
            job.held = True
        elif event.kind is Progress.RELEASED:
            job.held = False
        elif event.kind is Progress.STARTED:
            job.state = "running"
            job.held = False
        elif event.kind is Progress.SCANNING:
            job.state = "scanning"
        elif event.kind is Progress.SCANNED:
            job.files = int(event.payload.get("files", 0))
            job.state = "running"
            if job.counts_items:
                job.total = job.files
            else:
                job.total = int(event.payload.get("bytes", 0))
        elif event.kind is Progress.REMOVING:
            job.state = "running"
            job.current = str(event.payload.get("name", ""))
            job.done = int(event.payload.get("done", job.done))
            job.total = max(job.total, int(event.payload.get("total", 0)))
            job.interruptible = bool(event.payload.get("interruptible", True))
        elif event.kind is Progress.COPYING:
            job.state = "running"
            name = str(event.payload.get("name", ""))
            if job.current and name != job.current:
                job.finished_names.add(job.current.lower())
            job.current = name
            job.item_total = int(event.payload.get("item_total", 0) or 0)
            job.item_done = int(event.payload.get("item_done", job.item_total if
                                                  event.payload.get("instant") else 0) or 0)
            if job.item_total and job.item_done >= job.item_total:
                job.finished_names.add(name.lower())
            job.done = int(event.payload.get("done", job.done))
            self._sample(job, job.done)
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
            if event.payload.get("denied"):
                # A recycle refuses as a whole and names nothing, so the job's
                # own sources are what an elevated retry would be about.
                job.denied.extend([name] if name
                                  else [os.path.basename(s) for s in job.sources])
        elif event.kind is Progress.REFUSED:
            # Written here rather than in the ops process because this is the
            # side that knows how the application writes a size, and a sentence
            # composed there would be a second answer to that question.
            needed = int(event.payload.get("needed", 0))
            free = int(event.payload.get("free", 0))
            where = str(event.payload.get("destination", ""))
            job.refused = (f"not enough room in {where}: "
                           f"{format_size(needed)} to write, "
                           f"{format_size(free)} free")
            job.problems.append(job.refused)
        elif event.kind is Progress.DONE:
            job.state = "done"
            job.held = False
            job.interruptible = True
            job.copied = int(event.payload.get("copied", 0))
            job.skipped = int(event.payload.get("skipped", 0))
            job.failed = max(job.failed, int(event.payload.get("failed", 0)))
            job.cancelled = bool(event.payload.get("cancelled"))
            job.current = ""
            self.changed.emit()
            self.finished.emit(job)
            return

        self.changed.emit()

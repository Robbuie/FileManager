"""Checks on what 0.14 added to the queue: order, holds, and deletes as jobs.

Three things are worth testing here and the rest is presentation.

**A delete is now a destructive operation running under a progress bar**, which
is the change with something to lose. So the erase tests are about what survives
a cancel and what is reported when Windows refuses -- not about the count coming
out right.

**The order is held in two places** -- the ops process's own list and the
window's mirror of it -- and the whole reason `core/transfers.py` keeps a copy
is that a reorder has to show before the round trip finishes. Two lists that can
disagree are worth a test that makes them.

**A held job is not a paused queue.** Holding the third thing in the queue must
leave the first two running, and the runner's `_next` is where that either works
or quietly becomes a global pause with extra steps.

The runner tests drive `Runner` in-process with a scripted inbox rather than
starting the real process, for `test_ops.py`'s reason: an event that has to
arrive between two particular items is a certainty in-process and a race
everywhere else.
"""

from __future__ import annotations

import os
import queue

import pytest

from app.core.transfers import JobState, TransferQueue
from app.io.ops import Runner
from app.io.protocol import Conflict, Event, Job, JobKind, Progress


class Inbox:
    """A control queue holding exactly what a test wants the runner to see."""

    def __init__(self, messages=()):
        self._messages = list(messages)

    def get_nowait(self):
        if not self._messages:
            raise queue.Empty
        return self._messages.pop(0)

    def get(self, timeout=None):
        if not self._messages:
            # Nothing more is coming, and a runner waiting for the next command
            # would block the test forever. Stopping is what the real process
            # does when the parent goes away.
            return None
        return self._messages.pop(0)


def job(job_id, kind, sources, destination=""):
    return Job(id=job_id, kind=kind, sources=tuple(str(s) for s in sources),
               destination=str(destination), conflict=Conflict.SKIP)


def drain(runner) -> list[Event]:
    events = []
    while True:
        try:
            events.append(runner.outbox.get_nowait())
        except queue.Empty:
            return events


def kinds(events, kind):
    return [event for event in events if event.kind is kind]


# --------------------------------------------------------------------- order


def test_a_held_job_is_stepped_over_and_the_next_one_runs():
    """The point of a hold: it stops one job, not the queue."""
    runner = Runner(Inbox(), queue.Queue())
    runner.jobs = [job(1, JobKind.COPY, ["a"]), job(2, JobKind.COPY, ["b"])]
    runner.held.add(1)
    assert runner._next().id == 2


def test_releasing_a_held_job_gives_it_its_place_back():
    """Held keeps its position rather than going to the back. Holding
    something is saying "not yet", not "after everything else"."""
    runner = Runner(Inbox(), queue.Queue())
    runner.jobs = [job(1, JobKind.COPY, ["a"]), job(2, JobKind.COPY, ["b"])]
    runner.held.add(1)
    assert runner._next().id == 2
    runner.held.discard(1)
    assert runner._next().id == 1


def test_a_queue_that_is_entirely_held_runs_nothing():
    runner = Runner(Inbox(), queue.Queue())
    runner.jobs = [job(1, JobKind.COPY, ["a"])]
    runner.held.add(1)
    assert runner._next() is None


def test_reorder_moves_a_waiting_job_and_says_where_it_landed():
    runner = Runner(Inbox(), queue.Queue())
    runner.jobs = [job(1, JobKind.COPY, ["a"]), job(2, JobKind.COPY, ["b"]),
                   job(3, JobKind.COPY, ["c"])]
    runner._reorder(3, -2)
    assert [j.id for j in runner.jobs] == [3, 1, 2]
    moved = kinds(drain(runner), Progress.QUEUED)
    assert moved and moved[-1].payload["position"] == 0


def test_reorder_past_either_end_stops_at_it():
    runner = Runner(Inbox(), queue.Queue())
    runner.jobs = [job(1, JobKind.COPY, ["a"]), job(2, JobKind.COPY, ["b"])]
    runner._reorder(1, -5)
    runner._reorder(2, 9)
    assert [j.id for j in runner.jobs] == [1, 2]


# -------------------------------------------------------------------- erase


def test_erase_removes_the_tree_and_the_folders_that_held_it(tmp_path):
    root = tmp_path / "gone"
    (root / "deep").mkdir(parents=True)
    (root / "a.txt").write_text("alpha")
    (root / "deep" / "b.txt").write_text("beta")

    runner = Runner(Inbox(), queue.Queue())
    runner._erase(job(1, JobKind.ERASE, [root]))

    assert not root.exists()
    done = kinds(drain(runner), Progress.DONE)[-1]
    assert done.payload["copied"] == 2
    assert done.payload["failed"] == 0


def test_erase_counts_items_rather_than_bytes(tmp_path):
    """A delete's cost is the number of files. `REMOVING` says so, and a bar
    drawn from bytes would sit still through 40,000 small files."""
    for index in range(3):
        (tmp_path / f"f{index}.txt").write_text("x" * (index + 1))

    runner = Runner(Inbox(), queue.Queue())
    runner._erase(job(1, JobKind.ERASE,
                      [tmp_path / f"f{i}.txt" for i in range(3)]))

    removing = kinds(drain(runner), Progress.REMOVING)
    assert removing, "an erase reported no progress at all"
    assert removing[-1].payload["total"] == 3
    assert removing[-1].payload["done"] == 3


def test_a_cancelled_erase_leaves_everything_it_had_not_reached(tmp_path):
    """The one that matters. There is no undo for an erase, so a cancel has to
    mean the rest of the tree is still there."""
    for index in range(12):
        (tmp_path / f"f{index}.txt").write_text("x")

    # The cancel is waiting in the inbox before the first item, so it lands at
    # the first checkpoint rather than at a moment that depends on timing.
    runner = Runner(Inbox([("cancel", 1)]), queue.Queue())
    runner._erase(job(1, JobKind.ERASE,
                      [tmp_path / f"f{i}.txt" for i in range(12)]))

    survivors = sorted(p.name for p in tmp_path.iterdir())
    assert len(survivors) == 12, "a cancelled erase deleted something anyway"
    done = kinds(drain(runner), Progress.DONE)[-1]
    assert done.payload["cancelled"] is True


def test_a_refused_file_is_reported_as_refused_rather_than_failed(tmp_path, monkeypatch):
    """`denied` is what the window turns into the offer to run as
    administrator. A file that has simply gone must not carry it: a consent
    prompt that could not have helped is worse than no prompt."""
    (tmp_path / "locked.txt").write_text("x")
    (tmp_path / "vanished.txt").write_text("x")

    def refuse(path, *args):
        if str(path).endswith("locked.txt"):
            raise PermissionError(13, "Permission denied")
        raise FileNotFoundError(2, "No such file")

    runner = Runner(Inbox(), queue.Queue())
    monkeypatch.setattr("app.io.ops.os.remove", refuse)
    monkeypatch.setattr("app.io.ops.os.chmod", refuse)
    runner._erase(job(1, JobKind.ERASE,
                      [tmp_path / "locked.txt", tmp_path / "vanished.txt"]))

    failures = {event.payload["name"]: event.payload.get("denied")
                for event in kinds(drain(runner), Progress.FAILED_ITEM)}
    assert failures == {"locked.txt": True, "vanished.txt": False}


def test_erase_walks_without_a_destination(tmp_path):
    """The scan is shared with a copy, and a copy's version builds a target
    path per item. An erase has no destination, so every target is empty --
    and an item whose target was silently the current directory would be a
    delete that could write somewhere."""
    (tmp_path / "a.txt").write_text("x")
    runner = Runner(Inbox(), queue.Queue())
    items, unreadable = runner._scan(job(1, JobKind.ERASE, [tmp_path / "a.txt"]),
                                     [str(tmp_path / "a.txt")], destination="")
    assert not unreadable
    assert [item.target for item in items] == [""]


# ------------------------------------------------------------------ recycle


def test_recycle_goes_through_the_worker_handler(tmp_path, monkeypatch):
    """One implementation of the destructive call, not two. The elevated retry
    runs the worker's handler, so the ordinary path has to as well."""
    from app.io import worker

    seen = {}

    def fake(targets):
        seen["targets"] = list(targets)
        return len(targets), "", 0

    monkeypatch.setattr(worker, "recycle", fake)
    runner = Runner(Inbox(), queue.Queue())
    runner._recycle(job(1, JobKind.RECYCLE, [tmp_path / "a", tmp_path / "b"]))

    assert seen["targets"] == [str(tmp_path / "a"), str(tmp_path / "b")]
    assert kinds(drain(runner), Progress.DONE)[-1].payload["copied"] == 2


def test_a_recycle_says_it_cannot_be_interrupted_before_it_starts(monkeypatch):
    """The queue must not offer pause and cancel for a step that has neither.
    The window greys them off this flag."""
    from app.io import worker

    monkeypatch.setattr(worker, "recycle", lambda targets: (len(targets), "", 0))
    runner = Runner(Inbox(), queue.Queue())
    runner._recycle(job(1, JobKind.RECYCLE, ["a"]))

    first = kinds(drain(runner), Progress.REMOVING)[0]
    assert first.payload["interruptible"] is False


def test_a_refused_recycle_is_marked_denied(monkeypatch):
    from app.io import worker

    monkeypatch.setattr(worker, "recycle",
                        lambda targets: (0, "the shell refused it", 5))
    runner = Runner(Inbox(), queue.Queue())
    runner._recycle(job(1, JobKind.RECYCLE, ["a"]))

    failed = kinds(drain(runner), Progress.FAILED_ITEM)[0]
    assert failed.payload["denied"] is True


def test_a_recycle_that_failed_for_another_reason_is_not_denied(monkeypatch):
    from app.io import worker

    monkeypatch.setattr(worker, "recycle",
                        lambda targets: (0, "something else went wrong", 124))
    runner = Runner(Inbox(), queue.Queue())
    runner._recycle(job(1, JobKind.RECYCLE, ["a"]))

    failed = kinds(drain(runner), Progress.FAILED_ITEM)[0]
    assert failed.payload["denied"] is False


# ------------------------------------------------------- the window's mirror


class FakeOps:
    """The parent side of the ops process, recording rather than starting one."""

    def __init__(self, on_event):
        self.on_event = on_event
        self.sent: list[tuple] = []
        self._next = 1

    def submit(self, kind, sources, destination="", *, conflict=None):
        self.sent.append(("submit", kind, tuple(sources), destination))
        self._next += 1
        return self._next - 1

    def cancel(self, job_id): self.sent.append(("cancel", job_id))
    def hold(self, job_id): self.sent.append(("hold", job_id))
    def release(self, job_id): self.sent.append(("release", job_id))
    def reorder(self, job_id, delta): self.sent.append(("reorder", job_id, delta))
    def pause(self): self.sent.append(("pause",))
    def resume(self): self.sent.append(("resume",))
    def answer(self, *a, **k): self.sent.append(("answer",))
    def shutdown(self, *a, **k): self.sent.append(("shutdown",))


@pytest.fixture
def mirror(monkeypatch):
    """A `TransferQueue` with no process behind it.

    Events are delivered straight to `_deliver` rather than through the Qt
    signal, so a test does not need an event loop to see the effect of one.
    """
    monkeypatch.setattr("app.io.ops.Transfers", FakeOps)
    return TransferQueue()


def sent(mirror):
    return mirror._transfers.sent


def test_a_delete_is_submitted_as_a_job(mirror, qapp=None):
    mirror.recycle([r"C:\work\a.txt"])
    mirror.erase([r"C:\work\b.txt"])
    assert [entry[1] for entry in sent(mirror)] == [JobKind.RECYCLE, JobKind.ERASE]


def test_a_delete_starts_with_its_item_count_as_the_total(mirror):
    job_id = mirror.erase([r"C:\work\a", r"C:\work\b"])
    assert mirror.jobs[job_id].total == 2, \
        "a delete with no total draws an empty bar until the scan lands"


def test_holding_shows_before_the_process_answers(mirror):
    """The reason this class keeps its own state at all. A button that only
    responded once a message had crossed a process boundary and come back
    would feel broken on a busy queue."""
    job_id = mirror.copy(["a"], "b")
    mirror.hold(job_id)
    assert mirror.jobs[job_id].held is True
    assert ("hold", job_id) in sent(mirror)


def test_a_reorder_moves_the_local_order_too(mirror):
    first = mirror.copy(["a"], "d")
    second = mirror.copy(["b"], "d")
    mirror.move_job(second, -1)
    assert mirror.order == [second, first]
    assert ("reorder", second, -1) in sent(mirror)


def test_a_running_job_cannot_be_reordered(mirror):
    """It has already left the process's own list, so the command would do
    nothing there while the mirror moved -- which is the drift this class
    exists to prevent."""
    first = mirror.copy(["a"], "d")
    second = mirror.copy(["b"], "d")
    mirror._deliver(Event(job=first, kind=Progress.STARTED))
    mirror.move_job(first, 1)
    assert mirror.order == [first, second]
    assert not any(entry[0] == "reorder" for entry in sent(mirror))


def test_a_waiting_job_cannot_be_moved_in_front_of_the_running_one(mirror):
    first = mirror.copy(["a"], "d")
    second = mirror.copy(["b"], "d")
    mirror._deliver(Event(job=first, kind=Progress.STARTED))
    mirror.move_job(second, -1)
    assert mirror.order == [first, second]


def test_the_position_the_process_reports_wins(mirror):
    """The process is the authority. An event that disagrees with the mirror
    is what stops a crossed reorder becoming permanent."""
    first = mirror.copy(["a"], "d")
    second = mirror.copy(["b"], "d")
    third = mirror.copy(["c"], "d")
    mirror._deliver(Event(job=third, kind=Progress.QUEUED, payload={"position": 0}))
    assert mirror.order == [third, first, second]


def test_the_readout_names_the_job_that_is_running_not_the_one_that_is_held(mirror):
    held = mirror.copy(["a"], "d")
    running = mirror.copy(["b"], "d")
    mirror.hold(held)
    mirror._deliver(Event(job=running, kind=Progress.STARTED))
    assert mirror.current().id == running


def test_totals_leave_deletes_out(mirror):
    """Items and bytes are not the same quantity, and adding them gives a
    number that is not a quantity of anything."""
    copying = mirror.copy(["a"], "d")
    erasing = mirror.erase(["b"])
    mirror._deliver(Event(job=copying, kind=Progress.COPYING,
                          payload={"name": "a", "done": 50, "total": 100}))
    mirror._deliver(Event(job=erasing, kind=Progress.REMOVING,
                          payload={"name": "b", "done": 3, "total": 9}))
    assert mirror.totals() == (50, 100)


def test_a_scanned_delete_measures_itself_in_files(mirror):
    erasing = mirror.erase(["b"])
    mirror._deliver(Event(job=erasing, kind=Progress.SCANNED,
                          payload={"files": 40, "bytes": 12}))
    assert mirror.jobs[erasing].total == 40


def test_a_scanned_copy_measures_itself_in_bytes(mirror):
    copying = mirror.copy(["a"], "d")
    mirror._deliver(Event(job=copying, kind=Progress.SCANNED,
                          payload={"files": 40, "bytes": 12}))
    assert mirror.jobs[copying].total == 12


def test_a_refused_delete_records_what_to_offer_elevation_for(mirror):
    erasing = mirror.erase([os.path.join("C:", "work", "locked.txt")])
    mirror._deliver(Event(job=erasing, kind=Progress.FAILED_ITEM,
                          payload={"name": "locked.txt", "denied": True},
                          message="PermissionError"))
    assert mirror.jobs[erasing].denied == ["locked.txt"]


def test_a_failure_that_is_not_a_refusal_offers_nothing(mirror):
    erasing = mirror.erase(["a.txt"])
    mirror._deliver(Event(job=erasing, kind=Progress.FAILED_ITEM,
                          payload={"name": "a.txt", "denied": False},
                          message="FileNotFoundError"))
    assert mirror.jobs[erasing].denied == []


def test_a_refused_recycle_names_its_own_sources(mirror):
    """A recycle refuses as a whole and names nothing, so what an elevated
    retry would be about is the job's own selection."""
    recycling = mirror.recycle([os.path.join("C:", "work", "a.txt"),
                                os.path.join("C:", "work", "b.txt")])
    mirror._deliver(Event(job=recycling, kind=Progress.FAILED_ITEM,
                          payload={"name": "", "denied": True},
                          message="refused"))
    assert mirror.jobs[recycling].denied == ["a.txt", "b.txt"]


def test_the_folders_to_relist_include_where_a_delete_emptied(mirror):
    erasing = mirror.erase([os.path.join("C:", "work", "a.txt")])
    assert mirror.jobs[erasing].folders == {os.path.join("C:", "work")}


def test_the_folders_to_relist_include_both_ends_of_a_move(mirror):
    moving = mirror.move([os.path.join("C:", "from", "a.txt")],
                         os.path.join("C:", "to"))
    assert mirror.jobs[moving].folders == {os.path.join("C:", "from"),
                                           os.path.join("C:", "to")}


def test_a_finished_job_is_interruptible_again(mirror):
    """So that a recycle which greyed the buttons does not leave them greyed
    once it has gone."""
    recycling = mirror.recycle(["a"])
    mirror._deliver(Event(job=recycling, kind=Progress.REMOVING,
                          payload={"interruptible": False, "total": 1}))
    assert mirror.jobs[recycling].interruptible is False
    mirror._deliver(Event(job=recycling, kind=Progress.DONE, payload={"copied": 1}))
    assert mirror.jobs[recycling].interruptible is True


def test_a_held_job_says_so_in_its_own_line():
    state = JobState(id=1, kind=JobKind.COPY, destination="d", held=True)
    assert "held" in state.label


# ------------------------------------------------------------------ the panel


pytest.importorskip("PySide6")


@pytest.fixture
def panel(mirror):
    """The queue panel, built on a mirror with no process behind it."""
    from PySide6.QtWidgets import QApplication

    from app.ui.transfers import QueueDialog

    app = QApplication.instance() or QApplication([])
    dialog = QueueDialog(mirror)
    dialog.apply_tokens({"txt_0": "#ffffff", "txt_2": "#888888",
                         "bg_4": "#333333", "sel": "#4a91ff"})
    yield dialog, mirror
    dialog.close()
    app.processEvents()


def rows_in(dialog):
    from PySide6.QtCore import Qt as _Qt

    return [dialog._list.item(i).data(_Qt.UserRole)
            for i in range(dialog._list.count())]


def test_the_panel_lists_a_row_per_job(panel):
    dialog, mirror = panel
    first = mirror.copy(["a"], "d")
    second = mirror.erase(["b"])
    assert rows_in(dialog) == [first, second]


def test_a_reordered_row_keeps_its_widget(panel):
    """`takeItem` drops the widget the item was carrying. Without putting it
    back a reorder leaves a blank row, which looks exactly like a crash and is
    not one."""
    dialog, mirror = panel
    first = mirror.copy(["a"], "d")
    second = mirror.copy(["b"], "d")
    mirror.move_job(second, -1)
    assert rows_in(dialog) == [second, first]
    for index in range(dialog._list.count()):
        assert dialog._list.itemWidget(dialog._list.item(index)) is not None


def test_a_reorder_keeps_the_selection_on_the_row_that_moved(panel):
    """Otherwise the button the user just clicked goes dead under their hand
    and the second click does nothing."""
    dialog, mirror = panel
    mirror.copy(["a"], "d")
    second = mirror.copy(["b"], "d")
    dialog._list.item(rows_in(dialog).index(second)).setSelected(True)
    mirror.move_job(second, -1)
    assert dialog._list.item(0).isSelected()


def test_the_panel_does_not_rebuild_its_rows_on_every_event(panel):
    """A rebuild per event drops the selection several times a second while a
    copy runs. The rows are reused; only their contents change."""
    dialog, mirror = panel
    job_id = mirror.copy(["a"], "d")
    before = dialog._list.itemWidget(dialog._list.item(0))
    for done in (10, 20, 30):
        mirror._deliver(Event(job=job_id, kind=Progress.COPYING,
                              payload={"name": "a", "done": done, "total": 100}))
    assert dialog._list.itemWidget(dialog._list.item(0)) is before


def test_a_recycle_the_shell_has_started_offers_neither_hold_nor_cancel(panel):
    """The buttons describe what clicking them would do. A shell recycle is one
    call and answers to nothing, so offering them would be a lie at the one
    moment it matters."""
    dialog, mirror = panel
    job_id = mirror.recycle(["a"])
    mirror._deliver(Event(job=job_id, kind=Progress.REMOVING,
                          payload={"interruptible": False, "total": 1}))
    dialog._list.item(0).setSelected(True)
    assert not dialog._hold.isEnabled()
    assert not dialog._cancel.isEnabled()


def test_a_running_transfer_can_be_held_but_not_reordered(panel):
    dialog, mirror = panel
    job_id = mirror.copy(["a"], "d")
    mirror._deliver(Event(job=job_id, kind=Progress.STARTED))
    dialog._list.item(0).setSelected(True)
    assert dialog._hold.isEnabled()
    assert not dialog._up.isEnabled()


def test_the_hold_button_becomes_release_once_everything_selected_is_held(panel):
    dialog, mirror = panel
    job_id = mirror.copy(["a"], "d")
    dialog._list.item(0).setSelected(True)
    dialog._toggle_hold()
    assert mirror.jobs[job_id].held is True
    assert dialog._hold.text() == "Release"
    dialog._toggle_hold()
    assert mirror.jobs[job_id].held is False


def test_a_finished_job_stays_in_the_list_until_it_is_cleared(panel):
    dialog, mirror = panel
    job_id = mirror.copy(["a"], "d")
    mirror._deliver(Event(job=job_id, kind=Progress.DONE, payload={"copied": 1}))
    assert rows_in(dialog) == [job_id]
    mirror.forget_finished()
    assert rows_in(dialog) == []

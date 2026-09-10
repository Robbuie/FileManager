"""Checks on the transfer engine.

The engine is the one part of this application where a bug destroys something
rather than annoying somebody, so these lean on the cases that would: an
overwrite that is cancelled half way, a move that must not delete its source
until the copy is verified, a conflict answered once and applied to the rest.

They run the real ops process, on POSIX as well as Windows. The mid-file cancel
is driven in-process instead, because a cancel that has to arrive within a
particular chunk is a race everywhere else and a certainty there.
"""

from __future__ import annotations

import os
import queue
import time

import pytest

from app.io.ops import Runner, _partial_name, _unique
from app.io.ops import Transfers
from app.io.protocol import Conflict, Event, Progress, JobKind


def run_job(kind, sources, destination, *, conflict=Conflict.SKIP,
            answer=None, timeout=30.0):
    """Run one transfer to completion and return every event it produced."""
    events: "queue.Queue[Event]" = queue.Queue()
    transfers = Transfers(events.put)
    collected: list[Event] = []
    try:
        job = transfers.submit(kind, sources, destination, conflict=conflict)
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            assert remaining > 0, "the job never finished"
            event = events.get(timeout=remaining)
            collected.append(event)
            if event.kind is Progress.CONFLICT and answer is not None:
                transfers.answer(job, answer[0], apply_to_all=answer[1])
            if event.kind is Progress.DONE:
                return collected
    finally:
        transfers.shutdown()


def final(events: list[Event]) -> Event:
    return events[-1]


def tree(root):
    """Every file under a folder, as relative path -> contents."""
    out = {}
    for folder, _dirs, files in os.walk(root):
        for name in files:
            full = os.path.join(folder, name)
            out[os.path.relpath(full, root).replace("\\", "/")] = \
                open(full, "rb").read()
    return out


@pytest.fixture
def pair(tmp_path):
    source = tmp_path / "src"
    destination = tmp_path / "dst"
    (source / "deep").mkdir(parents=True)
    destination.mkdir()
    (source / "a.txt").write_text("alpha")
    (source / "deep" / "b.txt").write_text("beta")
    (source / "deep" / "big.bin").write_bytes(b"x" * 250_000)
    return source, destination


# --------------------------------------------------------------------------


def test_a_tree_arrives_whole(pair):
    source, destination = pair
    events = run_job(JobKind.COPY, [str(source)], str(destination))

    assert final(events).kind is Progress.DONE
    assert final(events).payload["failed"] == 0
    assert tree(destination / "src") == tree(source)


def test_the_total_is_known_before_anything_is_copied(pair):
    source, destination = pair
    events = run_job(JobKind.COPY, [str(source)], str(destination))

    kinds = [event.kind for event in events]
    scanned = kinds.index(Progress.SCANNED)
    assert Progress.COPYING not in kinds[:scanned], "copying began before the scan"
    assert events[scanned].payload == {"files": 3, "bytes": 250_009}


def test_skip_leaves_what_was_there(pair):
    source, destination = pair
    (destination / "a.txt").write_text("mine")
    events = run_job(JobKind.COPY, [str(source / "a.txt")], str(destination),
                     conflict=Conflict.SKIP)

    assert final(events).payload["skipped"] == 1
    assert (destination / "a.txt").read_text() == "mine"


def test_overwrite_replaces_it(pair):
    source, destination = pair
    (destination / "a.txt").write_text("mine")
    run_job(JobKind.COPY, [str(source / "a.txt")], str(destination),
            conflict=Conflict.OVERWRITE)

    assert (destination / "a.txt").read_text() == "alpha"


def test_newer_only_replaces_the_older_one(pair):
    source, destination = pair
    (destination / "a.txt").write_text("mine")
    old = time.time() - 3600
    os.utime(destination / "a.txt", (old, old))
    run_job(JobKind.COPY, [str(source / "a.txt")], str(destination),
            conflict=Conflict.NEWER)
    assert (destination / "a.txt").read_text() == "alpha"

    # And the other way round: a destination that is newer stays.
    (destination / "a.txt").write_text("mine again")
    run_job(JobKind.COPY, [str(source / "a.txt")], str(destination),
            conflict=Conflict.NEWER)
    assert (destination / "a.txt").read_text() == "mine again"


def test_rename_keeps_both(pair):
    source, destination = pair
    (destination / "a.txt").write_text("mine")
    run_job(JobKind.COPY, [str(source / "a.txt")], str(destination),
            conflict=Conflict.RENAME)

    assert (destination / "a.txt").read_text() == "mine"
    assert (destination / "a (2).txt").read_text() == "alpha"


def test_an_answer_can_be_applied_to_the_rest(pair):
    """One question for a folder of collisions, not one question per file."""
    source, destination = pair
    for name in ("a.txt", "b.txt", "c.txt"):
        (source / name).write_text("new")
        (destination / name).write_text("old")
    events = run_job(JobKind.COPY,
                     [str(source / name) for name in ("a.txt", "b.txt", "c.txt")],
                     str(destination), conflict=Conflict.ASK,
                     answer=(Conflict.OVERWRITE, True))

    asked = [event for event in events if event.kind is Progress.CONFLICT]
    assert len(asked) == 1, "the same question was asked more than once"
    assert final(events).payload["copied"] == 3
    assert (destination / "c.txt").read_text() == "new"


def test_a_move_within_a_volume_is_a_rename(pair):
    source, destination = pair
    events = run_job(JobKind.MOVE, [str(source)], str(destination))

    assert final(events).payload["failed"] == 0
    assert not source.exists(), "the source survived a move"
    assert (destination / "src" / "deep" / "b.txt").read_text() == "beta"


def test_a_move_that_cannot_rename_copies_then_removes(pair):
    """The slow path, forced by a destination that already has the name.

    Worth its own test because it is the path where a move can lose a file:
    it deletes the source, and it must only do that once the copy is there
    and the right size.
    """
    source, destination = pair
    (destination / "a.txt").write_text("old")
    events = run_job(JobKind.MOVE, [str(source / "a.txt")], str(destination),
                     conflict=Conflict.OVERWRITE)

    assert final(events).payload["copied"] == 1
    assert (destination / "a.txt").read_text() == "alpha"
    assert not (source / "a.txt").exists()


def test_a_source_that_cannot_be_read_fails_that_item_only(pair):
    source, destination = pair
    events = run_job(JobKind.COPY,
                     [str(source / "a.txt"), str(source / "nothing-here.txt")],
                     str(destination))

    assert final(events).kind is Progress.DONE
    assert final(events).payload["failed"] == 1
    assert final(events).payload["copied"] == 1
    assert (destination / "a.txt").exists()


def test_every_job_ends_even_when_the_destination_is_gone(tmp_path):
    events = run_job(JobKind.COPY, [str(tmp_path / "absent")],
                     str(tmp_path / "also-absent"))
    assert final(events).kind is Progress.DONE


# --------------------------------------------------------------------------
# In-process, where a cancel can be made to land inside a file.
# --------------------------------------------------------------------------


class Inbox:
    """A control queue holding exactly what a test wants the runner to see."""

    def __init__(self, messages=()):
        self._messages = list(messages)

    def get_nowait(self):
        if not self._messages:
            raise queue.Empty
        return self._messages.pop(0)

    def get(self, timeout=None):
        return self.get_nowait()


def test_a_cancel_inside_a_file_leaves_nothing_half_written(tmp_path):
    source = tmp_path / "big.bin"
    source.write_bytes(b"y" * (4 * 1024 * 1024))
    target = tmp_path / "target.bin"
    target.write_text("the original")

    runner = Runner(Inbox([("cancel", 7)]), queue.Queue())
    from app.io.ops import Item, Totals, _Cancelled
    from app.io.protocol import Job

    job = Job(id=7, kind=JobKind.COPY, sources=(str(source),),
              destination=str(tmp_path), conflict=Conflict.OVERWRITE)
    item = Item(str(source), str(target), size=source.stat().st_size)

    with pytest.raises(_Cancelled):
        runner._stream(job, item, str(target), Totals(), item.size)

    assert target.read_text() == "the original", "an overwrite destroyed the original"
    assert not os.path.exists(_partial_name(str(target)) ), "a partial file was left behind"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["big.bin", "target.bin"]


def test_the_second_copy_of_a_name_is_numbered(tmp_path):
    (tmp_path / "report.pdf").write_text("one")
    first = _unique(str(tmp_path / "report.pdf"))
    assert os.path.basename(first) == "report (2).pdf"

    open(first, "w").close()
    assert os.path.basename(_unique(str(tmp_path / "report.pdf"))) == "report (3).pdf"

    (tmp_path / "README").write_text("x")
    assert os.path.basename(_unique(str(tmp_path / "README"))) == "README (2)"

"""Retry what failed: the engine says where each failure was going, and the
queue puts exactly those back, each into its own folder.

The case worth the test is the deep one. A file that failed three folders down
a copied tree has to land three folders down on the retry, and a retry that
dropped it into the top of the destination would look like it worked.
"""

from __future__ import annotations

import os
import queue

import pytest

from app.core.transfers import TransferQueue
from app.io import ops
from app.io.ops import Runner
from app.io.protocol import Conflict, Event, Job, JobKind, Progress
from tests.test_queue import Inbox, drain, kinds


def copy_job(job_id, sources, destination, into=()):
    return Job(id=job_id, kind=JobKind.COPY, sources=tuple(map(str, sources)),
               destination=str(destination), conflict=Conflict.SKIP,
               into=tuple(map(str, into)))


@pytest.fixture
def source_tree(tmp_path):
    root = tmp_path / "job"
    (root / "a" / "b").mkdir(parents=True)
    (root / "top.txt").write_text("top")
    (root / "a" / "b" / "deep.txt").write_text("deep")
    (tmp_path / "out").mkdir()
    return root


def test_a_failure_says_what_it_was_and_where_it_was_going(tmp_path, source_tree,
                                                            monkeypatch):
    real = Runner._copy_file

    def flaky(self, job, item, target, totals, total_bytes):
        if item.source.endswith("deep.txt"):
            raise PermissionError(13, "locked")
        return real(self, job, item, target, totals, total_bytes)

    monkeypatch.setattr(Runner, "_copy_file", flaky)
    runner = Runner(Inbox(), queue.Queue())
    runner._move_or_copy(copy_job(1, [source_tree], tmp_path / "out"))
    failed = kinds(drain(runner), Progress.FAILED_ITEM)
    assert len(failed) == 1
    assert failed[0].payload["source"] == str(source_tree / "a" / "b" / "deep.txt")
    assert failed[0].payload["into"] == str(tmp_path / "out" / "job" / "a" / "b")


def test_a_job_with_a_folder_per_source_puts_each_where_it_says(tmp_path, source_tree):
    out = tmp_path / "out"
    (out / "job" / "a" / "b").mkdir(parents=True)
    runner = Runner(Inbox(), queue.Queue())
    deep, top = source_tree / "a" / "b" / "deep.txt", source_tree / "top.txt"
    runner._move_or_copy(copy_job(2, [deep, top], out / "job" / "a" / "b",
                                  into=[out / "job" / "a" / "b", out / "job"]))
    assert (out / "job" / "a" / "b" / "deep.txt").read_text() == "deep"
    assert (out / "job" / "top.txt").read_text() == "top"
    assert not (out / "job" / "a" / "b" / "top.txt").exists()


def test_a_folder_that_could_not_be_read_is_retried_into_its_parent(tmp_path,
                                                                    source_tree,
                                                                    monkeypatch):
    real = os.scandir

    def refusing(path):
        if str(path).endswith(os.path.join("a", "b")):
            raise PermissionError(13, "no")
        return real(path)

    monkeypatch.setattr(ops.os, "scandir", refusing)
    runner = Runner(Inbox(), queue.Queue())
    runner._move_or_copy(copy_job(3, [source_tree], tmp_path / "out"))
    failed = kinds(drain(runner), Progress.FAILED_ITEM)
    assert [e.payload["into"] for e in failed] == [str(tmp_path / "out" / "job" / "a")]


# ------------------------------------------------------------------- the queue


class FakeOps:
    def __init__(self, on_event):
        self.sent = []
        self._next = 1

    def submit(self, kind, sources, destination="", **extra):
        self.sent.append((kind, tuple(sources), destination, extra))
        self._next += 1
        return self._next - 1

    def shutdown(self, *a, **k):
        pass


@pytest.fixture
def mirror(monkeypatch):
    monkeypatch.setattr("app.io.ops.Transfers", FakeOps)
    return TransferQueue()


def finish(mirror, job_id, failures=(), **done):
    for source, into in failures:
        mirror._deliver(Event(job=job_id, kind=Progress.FAILED_ITEM,
                              payload={"name": os.path.basename(source),
                                       "source": source, "into": into},
                              message="locked"))
    mirror._deliver(Event(job=job_id, kind=Progress.DONE,
                          payload={"failed": len(failures), **done}))


def test_retry_sends_only_the_failures_each_to_its_folder(mirror):
    job_id = mirror.copy([r"S:\jobs\x"], r"D:\out")
    finish(mirror, job_id, [(r"S:\jobs\x\a\1.dwg", r"D:\out\x\a"),
                            (r"S:\jobs\x\2.dwg", r"D:\out\x")])
    assert mirror.jobs[job_id].retryable
    mirror.retry(job_id)
    kind, sources, destination, extra = mirror._transfers.sent[-1]
    assert kind is JobKind.COPY
    assert sources == (r"S:\jobs\x\a\1.dwg", r"S:\jobs\x\2.dwg")
    assert extra["into"] == (r"D:\out\x\a", r"D:\out\x")


def test_failures_sharing_a_folder_are_an_ordinary_job(mirror):
    job_id = mirror.copy([r"S:\a", r"S:\b"], r"D:\out")
    finish(mirror, job_id, [(r"S:\a", r"D:\out"), (r"S:\b", r"D:\out")])
    mirror.retry(job_id)
    _kind, _sources, destination, extra = mirror._transfers.sent[-1]
    assert destination == r"D:\out" and "into" not in extra


def test_a_refusal_for_room_retries_the_whole_job(mirror):
    job_id = mirror.copy([r"S:\big"], r"D:\out")
    mirror._deliver(Event(job=job_id, kind=Progress.REFUSED,
                          payload={"destination": r"D:\out", "needed": 10, "free": 1}))
    mirror._deliver(Event(job=job_id, kind=Progress.DONE, payload={"failed": 1}))
    mirror.retry(job_id)
    assert mirror._transfers.sent[-1][1:3] == ((r"S:\big",), r"D:\out")


def test_a_failed_recycle_retries_its_sources(mirror):
    job_id = mirror.recycle([r"S:\a.txt", r"S:\b.txt"])
    mirror._deliver(Event(job=job_id, kind=Progress.FAILED_ITEM,
                          payload={"name": ""}, message="the shell refused"))
    mirror._deliver(Event(job=job_id, kind=Progress.DONE, payload={"failed": 2}))
    mirror.retry(job_id)
    assert mirror._transfers.sent[-1][:3] == (JobKind.RECYCLE, (r"S:\a.txt", r"S:\b.txt"), "")


def test_nothing_to_retry_after_success_or_a_cancel(mirror):
    clean = mirror.copy([r"S:\a"], r"D:\out")
    finish(mirror, clean)
    cancelled = mirror.copy([r"S:\b"], r"D:\out")
    finish(mirror, cancelled, [(r"S:\b", r"D:\out")], cancelled=True)
    before = len(mirror._transfers.sent)
    assert mirror.retry(clean) is None and mirror.retry(cancelled) is None
    assert len(mirror._transfers.sent) == before

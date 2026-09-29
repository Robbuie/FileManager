"""Checks on naming the program that holds a file (0.37).

The Restart Manager itself only exists on Windows; what can be tested
anywhere is the round trip the message makes -- written into a failure by the
engine, read back out by the queue -- and that nothing is asked for a failure
that cannot be a lock.
"""

from __future__ import annotations

from app.io import holders


class Locked(OSError):
    """An OSError with a `winerror`, which only Windows builds one with."""

    def __init__(self, winerror, filename):
        super().__init__(13, "in use", filename)
        self._code = winerror

    @property
    def winerror(self):  # noqa: D401 - shadows the base class's member
        return self._code


def test_the_mark_round_trips():
    text = "OSError: in use (winerror 32)" + holders._MARK.format(
        name="Logix Designer", pid=4412)
    assert holders.parse(text) == (4412, "Logix Designer")


def test_a_message_without_a_mark_parses_to_nothing():
    assert holders.parse("FileNotFoundError: gone") is None
    assert holders.parse("") is None


def test_only_lock_failures_are_asked_about(monkeypatch):
    asked = []
    monkeypatch.setattr(holders, "holding", lambda path: asked.append(path) or [(7, "X")])
    missing = Locked(2, "C:\\a.txt")
    assert holders.describe(missing) == ""
    locked = Locked(32, "C:\\a.txt")
    assert holders.describe(locked) == " -- open in X (PID 7)"
    assert asked == ["C:\\a.txt"]


def test_nobody_holding_it_adds_nothing(monkeypatch):
    monkeypatch.setattr(holders, "holding", lambda path: [])
    assert holders.describe(Locked(32, "C:\\a.txt")) == ""


def test_the_queue_collects_the_holder(monkeypatch):
    import pytest

    pytest.importorskip("PySide6")
    from tests.test_retry import FakeOps
    from app.core.transfers import TransferQueue
    from app.io.protocol import Event, Progress

    monkeypatch.setattr("app.io.ops.Transfers", FakeOps)
    queue = TransferQueue()
    job_id = queue.copy(["C:\\a.txt"], "D:\\out")
    queue._deliver(Event(job=job_id, kind=Progress.FAILED_ITEM,
                         payload={"name": "a.txt"},
                         message="OSError: in use (winerror 32) -- open in Excel (PID 9)"))
    assert queue.jobs[job_id].holders == [(9, "Excel")]

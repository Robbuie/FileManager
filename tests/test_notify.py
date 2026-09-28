"""When a finished job is worth interrupting somebody for."""

from __future__ import annotations

from app.core.transfers import JobState, worth_notifying
from app.io.protocol import JobKind


def ran(seconds, **fields):
    return JobState(id=1, kind=JobKind.COPY, destination="D:\\", state="done",
                    started_at=100.0, ended_at=100.0 + seconds, **fields)


def test_a_long_job_behind_another_window_notifies():
    assert worth_notifying(ran(45), window_active=False, threshold=20)


def test_a_job_being_watched_does_not():
    assert not worth_notifying(ran(45), window_active=True, threshold=20)


def test_a_short_job_does_not():
    assert not worth_notifying(ran(5), window_active=False, threshold=20)


def test_a_cancel_does_not():
    assert not worth_notifying(ran(45, cancelled=True), window_active=False,
                               threshold=20)


def test_zero_turns_it_off():
    assert not worth_notifying(ran(4500), window_active=False, threshold=0)


def test_a_job_that_never_started_does_not():
    job = ran(45)
    job.started_at = 0.0
    assert not worth_notifying(job, window_active=False, threshold=20)

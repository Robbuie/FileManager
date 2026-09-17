"""0.29: rows that fill as they are copied, the speed, and the transfer pill."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from app.core import transfers as core  # noqa: E402
from app.core.transfers import JobState, TransferQueue, format_eta, speed  # noqa: E402
from app.io.protocol import Event, JobKind, Progress  # noqa: E402
from app.theme import sheet  # noqa: E402
from app.ui.pill import TransferPill, readout  # noqa: E402


def test_speed_is_measured_over_the_last_few_seconds_only():
    samples = [(0.0, 0), (1.0, 10), (5.0, 50), (6.0, 150), (7.0, 250)]
    assert speed(samples) == pytest.approx(200 / 2.0)
    assert speed(samples, now=20.0) == 0.0
    assert speed([(1.0, 5)]) == 0.0


def test_time_left_reads_like_a_clock():
    assert format_eta(12) == "0:12 left"
    assert format_eta(125) == "2:05 left"
    assert format_eta(3725) == "1:02:05 left"
    assert format_eta(None) == ""


@pytest.fixture
def queue(monkeypatch):
    made = TransferQueue.__new__(TransferQueue)
    QWidget.__init__  # noqa: B018 - Qt is imported
    from PySide6.QtCore import QObject

    QObject.__init__(made)
    made.jobs = {}
    made.order = []
    made.paused = False
    return made


def _job(queue, job_id=1, sources=("C:\\Jobs\\big.apa", "C:\\Jobs\\Logix"),
         destination="D:\\Archive"):
    job = JobState(job_id, JobKind.COPY, destination, tuple(sources), state="running",
                   total=1000)
    queue.jobs[job_id] = job
    queue.order.append(job_id)
    return job


def _copying(queue, name, item_done, item_total, done):
    queue._deliver(Event(1, Progress.COPYING, {  # noqa: SLF001
        "name": name, "item_done": item_done, "item_total": item_total,
        "done": done, "total": 1000}))


def test_a_file_being_written_fills_by_its_own_bytes_in_both_panes(queue):
    _job(queue)
    _copying(queue, "big.apa", 30, 100, 30)
    assert queue.row_progress("C:\\Jobs", "big.apa") == pytest.approx(0.3)
    assert queue.row_progress("D:\\Archive\\", "BIG.APA") == pytest.approx(0.3)
    assert queue.row_progress("E:\\Other", "big.apa") is None
    assert queue.row_progress("C:\\Jobs", "notes.txt") is None


def test_a_written_file_is_full_and_a_folder_fills_with_the_job(queue):
    _job(queue)
    _copying(queue, "big.apa", 100, 100, 100)
    _copying(queue, "Main.ACD", 10, 400, 500)   # inside the Logix folder
    assert queue.row_progress("C:\\Jobs", "big.apa") == 1.0
    assert queue.row_progress("C:\\Jobs", "Logix") == pytest.approx(0.5)


def test_the_pill_says_how_far_how_fast_and_what_is_queued(queue):
    job = _job(queue)
    job.done = 630
    job.current = "big.apa"
    job.samples = [(0.0, 0), (1.0, 630)]
    _job(queue, job_id=2).state = "queued"
    first, second, fraction = readout(queue)
    assert "big.apa" in first
    assert second.startswith("63%")
    assert "/s" in second and "left" in second and "1 queued" in second
    assert fraction == pytest.approx(0.63)


def test_the_pill_appears_while_something_runs_and_leaves_after(queue):
    parent = QWidget()
    parent.resize(900, 600)
    parent.show()
    pill = TransferPill(queue, parent)
    pill.apply_tokens(sheet.tokens())
    pill.set_motion(False)
    job = _job(queue)
    pill.refresh()
    QApplication.processEvents()
    assert pill.isVisible()
    assert pill.geometry().bottom() < parent.height()
    job.state = "done"
    pill.refresh()
    QApplication.processEvents()
    assert not pill.isVisible()
    parent.hide()

"""The hang recorder: silent while the loop turns, a stack dump when it stops."""

from __future__ import annotations

import os
import time

from app.core.hangs import HangRecorder, default_path


def test_it_lives_beside_the_settings():
    assert default_path(os.path.join("a", "b", "settings.json")) == \
        os.path.join("a", "b", "hangs.log")


def test_a_turning_loop_writes_nothing_but_the_header(tmp_path):
    recorder = HangRecorder(str(tmp_path / "hangs.log"), version="t", stall=0.4)
    recorder.start()
    try:
        for _ in range(4):
            time.sleep(0.1)
            recorder.beat()
    finally:
        recorder.stop()
    text = (tmp_path / "hangs.log").read_text(encoding="utf-8")
    assert "started" in text
    assert "Timeout" not in text


def test_a_stalled_loop_leaves_every_threads_stack(tmp_path):
    """The point of the whole module: the stuck thread is named, and so is the
    line it is stuck on."""
    recorder = HangRecorder(str(tmp_path / "hangs.log"), version="t", stall=0.2)
    recorder.start()
    try:
        time.sleep(0.5)          # the event loop, not turning
        recorder.beat()          # and turning again
    finally:
        recorder.stop()
    text = (tmp_path / "hangs.log").read_text(encoding="utf-8")
    assert "Timeout" in text
    assert "test_a_stalled_loop_leaves_every_threads_stack" in text
    assert "answered again after" in text


def test_a_folder_that_cannot_be_written_is_not_a_crash(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    recorder = HangRecorder(str(blocker / "hangs.log"))
    recorder.start()
    assert not recorder.active
    recorder.stop()

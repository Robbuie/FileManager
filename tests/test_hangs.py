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


def test_a_dump_asked_for_by_hand_is_written(tmp_path):
    """The case the stall dump cannot catch: a window whose loop is turning
    but which answers nobody, because something invisible holds the input.
    Somebody drops the trigger file beside the log and gets the stacks.
    """
    recorder = HangRecorder(str(tmp_path / "hangs.log"), version="t", stall=60.0)
    recorder._watch_seconds = 0.05
    recorder.start()
    try:
        (tmp_path / "dump.now").write_text("")
        for _ in range(40):
            time.sleep(0.05)
            if "asked for by hand" in (tmp_path / "hangs.log").read_text(encoding="utf-8"):
                break
    finally:
        recorder.stop()
    text = (tmp_path / "hangs.log").read_text(encoding="utf-8")
    assert "asked for by hand" in text
    assert "Thread" in text or "File " in text
    # One file is one dump, so the trigger is taken away as it is read.
    assert not (tmp_path / "dump.now").exists()


def test_the_window_census_is_empty_rather_than_wrong_off_windows():
    from app.core import hangs

    assert hangs.windows() == []


def test_a_dump_with_no_windows_says_so_rather_than_nothing(tmp_path):
    recorder = HangRecorder(str(tmp_path / "hangs.log"), version="t")
    recorder.start()
    try:
        recorder.dump("because a test asked")
    finally:
        recorder.stop()
    text = (tmp_path / "hangs.log").read_text(encoding="utf-8")
    assert "because a test asked" in text
    assert "no top-level windows found" in text


def test_a_loop_that_turns_but_is_slow_is_written_down(tmp_path):
    """The freeze that produced no record: every repaint finished, so nothing
    stalled, and the window was unusable anyway."""
    recorder = HangRecorder(str(tmp_path / "hangs.log"), version="t", stall=5.0)
    recorder._late = 0.1
    recorder.start()
    try:
        time.sleep(0.3)
        recorder.beat()
    finally:
        recorder.stop()
    text = (tmp_path / "hangs.log").read_text(encoding="utf-8")
    assert "slow: one turn of the event loop took" in text
    assert "Timeout" not in text          # nothing was stuck, so no stacks

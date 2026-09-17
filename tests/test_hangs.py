"""The hang recorder: silent while the loop turns, a stack dump when it stops."""

from __future__ import annotations

import os
import time

from app.core import hangs
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


def test_the_log_is_bounded_within_a_run_and_not_only_across_launches(tmp_path):
    """The ceiling was checked in `start` alone until 0.29.12.

    Which bounded the log across launches and not within one -- and the run
    that needs bounding is the long one. A window left alone while Windows
    still reports it as not responding dumps every `HUNG_REPEAT_SECONDS`, so
    an afternoon of that is hundreds of megabytes of stacks nobody reads past
    the first.
    """
    path = tmp_path / "hangs.log"
    recorder = HangRecorder(str(path), version="t", stall=60.0)
    recorder.start()
    try:
        recorder._file.write("x" * (hangs.MAX_BYTES + 1))
        recorder._file.flush()
        assert path.stat().st_size > hangs.MAX_BYTES
        recorder.dump("after the log got long")
    finally:
        recorder.stop()

    text = path.read_text(encoding="utf-8")
    assert path.stat().st_size < hangs.MAX_BYTES
    assert "was started again" in text
    assert "after the log got long" in text, "the dump that triggered it was lost"
    assert "xxxx" not in text


def test_rotating_keeps_the_descriptor_faulthandler_was_armed_with(tmp_path):
    """Truncated in place rather than reopened, and not as a preference.

    `faulthandler.dump_traceback_later` keeps the file *descriptor*. Closing it
    frees the number, the next thing to open a file gets it, and a stall a
    moment later writes a traceback into whatever that turned out to be.
    """
    path = tmp_path / "hangs.log"
    recorder = HangRecorder(str(path), version="t", stall=60.0)
    recorder.start()
    try:
        before = recorder._file.fileno()
        recorder._file.write("x" * (hangs.MAX_BYTES + 1))
        recorder._rotate()
        assert recorder._file.fileno() == before
        assert not recorder._file.closed
    finally:
        recorder.stop()


def test_a_log_under_the_ceiling_is_left_alone(tmp_path):
    path = tmp_path / "hangs.log"
    recorder = HangRecorder(str(path), version="t", stall=60.0)
    recorder.start()
    try:
        recorder.dump("first")
        recorder.dump("second")
    finally:
        recorder.stop()
    text = path.read_text(encoding="utf-8")
    assert "first" in text and "second" in text
    assert "was started again" not in text

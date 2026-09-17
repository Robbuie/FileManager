"""End-to-end checks on the worker and the pool.

These are the only part of the application that can be verified without a
person watching a window, so they cover the behaviour the design turns on
rather than the shape of the code: that a listing streams, that a cancel is
answered, and above all that a stuck worker settles the requests outstanding
against it instead of leaving them waiting.

They run on POSIX as well as Windows. The hung-share case is reproduced with
SIGSTOP, which is the closest local analogue of a process blocked in an SMB
call: still there, still holding its work, never answering.
"""

from __future__ import annotations

import os
import queue
import signal
import sys
import time

import pytest

from app.io.pool import WorkerPool
from app.io.protocol import Op, Reply, Status

SETTLED = {Status.OK, Status.TIMEOUT, Status.CANCELLED,
           Status.DENIED, Status.GONE, Status.ERROR}


class Sink:
    """Collects one request's replies the way a caller would."""

    def __init__(self) -> None:
        self.replies: queue.Queue[Reply] = queue.Queue()
        self.batches: list[Reply] = []

    def __call__(self, reply: Reply) -> None:
        self.replies.put(reply)

    def settle(self, timeout: float = 15.0) -> Reply:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError("no settled reply arrived; the request hung")
            reply = self.replies.get(timeout=remaining)
            if reply.status is Status.PARTIAL:
                self.batches.append(reply)
                continue
            return reply

    @property
    def rows(self) -> int:
        return sum(len(b.payload or []) for b in self.batches)


@pytest.fixture
def pool():
    instance = WorkerPool()
    try:
        yield instance
    finally:
        instance.shutdown()


def populate(directory, count: int) -> None:
    for index in range(count):
        (directory / f"file-{index:06d}.txt").write_text("x")


def test_a_listing_streams_rather_than_arriving_whole(pool, tmp_path):
    populate(tmp_path, 2500)
    sink = Sink()
    pool.submit(Op.LIST, str(tmp_path), timeout=20, handler=sink)
    final = sink.settle()

    assert final.status is Status.OK
    assert len(sink.batches) >= 2, "the listing was not delivered in batches"
    assert sink.rows + len(final.payload or []) == 2500


def test_an_empty_directory_settles_ok(pool, tmp_path):
    sink = Sink()
    pool.submit(Op.LIST, str(tmp_path), timeout=10, handler=sink)
    final = sink.settle()
    assert final.status is Status.OK
    assert final.payload == []


def test_a_missing_path_is_gone_not_an_error(pool, tmp_path):
    sink = Sink()
    pool.submit(Op.LIST, str(tmp_path / "not-here"), timeout=10, handler=sink)
    assert sink.settle().status is Status.GONE


def test_a_cancel_is_answered(pool, tmp_path):
    populate(tmp_path, 12000)
    sink = Sink()
    request_id = pool.submit(Op.LIST, str(tmp_path), timeout=20, handler=sink)

    first = sink.replies.get(timeout=15)
    assert first.status is Status.PARTIAL
    sink.batches.append(first)
    pool.cancel(request_id)

    final = sink.settle()
    assert final.status is Status.CANCELLED


def test_the_pool_answers_what_a_killed_worker_owed(pool, tmp_path):
    populate(tmp_path, 4000)
    sink = Sink()
    pool.submit(Op.LIST, str(tmp_path), timeout=30, handler=sink)
    pool.kill(str(tmp_path))

    final = sink.settle(timeout=20)
    # Either the listing beat the kill or the pool failed it. What must not
    # happen is silence: a request with no reply is a tab waiting forever.
    assert final.status in SETTLED


def test_the_pool_recovers_after_a_kill(pool, tmp_path):
    populate(tmp_path, 10)
    pool.kill(str(tmp_path))  # nothing there yet; spawns nothing
    sink = Sink()
    pool.submit(Op.LIST, str(tmp_path), timeout=10, handler=sink)
    assert sink.settle().status is Status.OK

    pool.kill(str(tmp_path))
    again = Sink()
    pool.submit(Op.LIST, str(tmp_path), timeout=10, handler=again)
    assert again.settle().status is Status.OK


def test_ping_reaches_the_worker(pool, tmp_path):
    sink = Sink()
    pool.submit(Op.PING, str(tmp_path), timeout=10, handler=sink)
    reply = sink.settle()
    assert reply.status is Status.OK
    assert reply.payload["pid"] != os.getpid(), "the io ran in the calling process"


def test_dir_size_walks_recursively(pool, tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.bin").write_bytes(b"0" * 100)
    (tmp_path / "sub" / "b.bin").write_bytes(b"0" * 250)
    sink = Sink()
    pool.submit(Op.DIR_SIZE, str(tmp_path), timeout=20, handler=sink)
    reply = sink.settle()
    assert reply.status is Status.OK
    assert reply.payload == {"bytes": 350, "files": 2, "folders": 1}


@pytest.mark.skipif(sys.platform == "win32", reason="SIGSTOP stands in for a hung SMB call")
def test_a_stuck_worker_times_out_and_is_replaced(pool, tmp_path):
    """The case the whole architecture exists for.

    A worker that is present and not answering must produce a TIMEOUT within
    the deadline, not a wait, and the volume must be usable again straight
    after.
    """
    warmup = Sink()
    pool.submit(Op.PING, str(tmp_path), timeout=10, handler=warmup)
    pid = warmup.settle().payload["pid"]

    os.kill(pid, signal.SIGSTOP)
    try:
        stuck = Sink()
        started = time.monotonic()
        pool.submit(Op.LIST, str(tmp_path), timeout=0.5, handler=stuck)
        final = stuck.settle(timeout=10)
        elapsed = time.monotonic() - started
    finally:
        try:
            os.kill(pid, signal.SIGCONT)
        except ProcessLookupError:
            pass  # already killed by the pool, which is the expected outcome

    assert final.status is Status.TIMEOUT
    assert elapsed < 5, f"took {elapsed:.1f}s to give up on a stuck worker"

    populate(tmp_path, 5)
    after = Sink()
    pool.submit(Op.LIST, str(tmp_path), timeout=10, handler=after)
    reply = after.settle()
    assert reply.status is Status.OK
    assert len(reply.payload) == 5


@pytest.mark.skipif(sys.platform == "win32", reason="signal delivery differs; the handler is the same")
def test_a_worker_survives_the_console_interrupt(pool, tmp_path):
    """Ctrl-C reaches the whole process group, workers included.

    A worker that dies on it leaves the parent holding requests that will never
    be answered, which is the one thing the io layer must never do.
    """
    warmup = Sink()
    pool.submit(Op.PING, str(tmp_path), timeout=10, handler=warmup)
    pid = warmup.settle().payload["pid"]

    os.kill(pid, signal.SIGINT)
    time.sleep(0.3)

    after = Sink()
    pool.submit(Op.PING, str(tmp_path), timeout=10, handler=after)
    reply = after.settle()
    assert reply.status is Status.OK
    assert reply.payload["pid"] == pid, "the worker died on SIGINT and was replaced"


def test_a_wedged_worker_settles_everything_outstanding(pool, tmp_path):
    """The architecture's acceptance test, without needing a share to yank.

    `Op.STALL` imitates the call that never returns: it ignores its deadline
    and its control queue, so the only thing that can end it is the pool. Both
    the wedged request and the one queued behind it must settle.
    """
    warmup = Sink()
    pool.submit(Op.PING, str(tmp_path), timeout=10, handler=warmup)
    before = warmup.settle().payload["pid"]

    wedged = Sink()
    started = time.monotonic()
    pool.submit(Op.STALL, str(tmp_path), timeout=0.5, handler=wedged)
    time.sleep(0.25)
    behind = Sink()
    pool.submit(Op.LIST, str(tmp_path), timeout=0.5, handler=behind)

    assert wedged.settle(timeout=15).status is Status.TIMEOUT
    assert behind.settle(timeout=15).status in SETTLED
    assert time.monotonic() - started < 10

    recovery = Sink()
    pool.submit(Op.PING, str(tmp_path), timeout=10, handler=recovery)
    reply = recovery.settle()
    assert reply.status is Status.OK
    assert reply.payload["pid"] != before, "the wedged process was reused"


def test_free_space_answers_for_a_real_path(pool, tmp_path):
    sink = Sink()
    pool.submit(Op.FREE_SPACE, str(tmp_path), timeout=10, handler=sink)
    reply = sink.settle()

    assert reply.status is Status.OK
    assert reply.payload["total"] > 0
    assert 0 <= reply.payload["free"] <= reply.payload["total"]


def test_free_space_on_a_missing_path_fails_rather_than_hangs(pool, tmp_path):
    sink = Sink()
    pool.submit(Op.FREE_SPACE, str(tmp_path / "nowhere"), timeout=10, handler=sink)
    reply = sink.settle()

    assert reply.status in SETTLED
    assert reply.status is not Status.OK


def test_drives_answers_without_touching_a_volume(pool, tmp_path):
    """Off Windows the list is empty, which is the point: it still answers.

    A picker that hangs when a mapped server is down is the failure this op
    exists to avoid, and an empty answer is a picker with nothing in it rather
    than a pane waiting on a reply.
    """
    sink = Sink()
    pool.submit(Op.DRIVES, "", timeout=10, handler=sink)
    reply = sink.settle(timeout=10)

    assert reply.status is Status.OK
    assert isinstance(reply.payload, list)
    for drive in reply.payload:
        # 0.27 added `ejectable`. Off Windows the list is empty, which is why
        # this only failed on the release build's Windows runner.
        assert set(drive) == {"letter", "type", "unc", "ejectable"}
        assert isinstance(drive["ejectable"], bool)


@pytest.mark.skipif(hasattr(os, "startfile"), reason="Windows opens the file for real")
def test_open_reports_rather_than_pretends_where_the_shell_is_absent(pool, tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("x")
    sink = Sink()
    pool.submit(Op.OPEN, str(target), timeout=10, handler=sink)
    reply = sink.settle()

    assert reply.status is Status.ERROR
    assert "Windows" in reply.message


def test_mkdir_makes_one_folder_and_refuses_a_second(pool, tmp_path):
    target = tmp_path / "made"
    sink = Sink()
    pool.submit(Op.MKDIR, str(target), timeout=10, handler=sink)
    assert sink.settle().status is Status.OK
    assert target.is_dir()

    again = Sink()
    pool.submit(Op.MKDIR, str(target), timeout=10, handler=again)
    reply = again.settle()
    assert reply.status is not Status.OK, "an existing folder was silently accepted"


def test_mkdir_does_not_build_the_folders_a_typo_implies(pool, tmp_path):
    sink = Sink()
    pool.submit(Op.MKDIR, str(tmp_path / "one" / "two"), timeout=10, handler=sink)

    assert sink.settle().status is not Status.OK
    assert not (tmp_path / "one").exists()


def test_rename_will_not_overwrite(pool, tmp_path):
    (tmp_path / "keep.txt").write_text("keep")
    (tmp_path / "other.txt").write_text("other")
    sink = Sink()
    pool.submit(Op.RENAME, str(tmp_path / "other.txt"), timeout=10,
                args={"name": "keep.txt"}, handler=sink)
    reply = sink.settle()

    assert reply.status is not Status.OK
    assert (tmp_path / "keep.txt").read_text() == "keep", "the target was overwritten"
    assert (tmp_path / "other.txt").exists()


def test_rename_refuses_a_name_that_is_a_path(pool, tmp_path):
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "sub").mkdir()
    sink = Sink()
    pool.submit(Op.RENAME, str(tmp_path / "a.txt"), timeout=10,
                args={"name": "sub/b.txt"}, handler=sink)

    assert sink.settle().status is Status.ERROR
    assert (tmp_path / "a.txt").exists(), "a rename moved a file"


def test_rename_renames(pool, tmp_path):
    (tmp_path / "a.txt").write_text("a")
    sink = Sink()
    pool.submit(Op.RENAME, str(tmp_path / "a.txt"), timeout=10,
                args={"name": "b.txt"}, handler=sink)
    reply = sink.settle()

    assert reply.status is Status.OK
    assert reply.payload["name"] == "b.txt"
    assert (tmp_path / "b.txt").exists() and not (tmp_path / "a.txt").exists()


def test_a_permanent_delete_removes_files_and_folders(pool, tmp_path):
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "tree").mkdir()
    (tmp_path / "tree" / "inner.txt").write_text("inner")
    sink = Sink()
    pool.submit(Op.DELETE, str(tmp_path), timeout=30,
                args={"names": ["a.txt", "tree"], "permanent": True}, handler=sink)
    reply = sink.settle()

    assert reply.status is Status.OK
    assert reply.payload["deleted"] == 2
    assert not (tmp_path / "a.txt").exists() and not (tmp_path / "tree").exists()


def test_a_delete_of_nothing_is_refused_rather_than_reported_as_success(pool, tmp_path):
    sink = Sink()
    pool.submit(Op.DELETE, str(tmp_path), timeout=10,
                args={"names": [], "permanent": True}, handler=sink)

    assert sink.settle().status is Status.ERROR


@pytest.mark.skipif(sys.platform == "win32", reason="pywin32 recycles for real here")
def test_a_recycle_without_the_shell_deletes_nothing(pool, tmp_path):
    """The failure that matters: no Recycle Bin must not mean deleting anyway."""
    (tmp_path / "a.txt").write_text("a")
    sink = Sink()
    pool.submit(Op.DELETE, str(tmp_path), timeout=10,
                args={"names": ["a.txt"], "permanent": False}, handler=sink)
    reply = sink.settle()

    assert reply.status is Status.ERROR
    assert (tmp_path / "a.txt").exists(), "a file was deleted with no way back"


def test_decorations_go_to_a_side_lane_and_listings_do_not():
    """The placement rule behind a listing never waiting on a badge."""
    from app.io import paths
    from app.io.pool import SIDE_OPS, SIDE_SUFFIX, lane_key

    share = r"\\tsclient\C\Users"
    main = paths.volume_key(share)
    for op in (Op.LIST, Op.FOLDERS, Op.STAT, Op.MKDIR, Op.RENAME, Op.FREE_SPACE):
        assert lane_key(op, share) == main
    for op in SIDE_OPS:
        assert lane_key(op, share) == main + SIDE_SUFFIX
    assert {Op.OVERLAY, Op.FILE_ICON, Op.THUMBNAIL, Op.PREVIEW, Op.DIR_SIZE} <= SIDE_OPS


@pytest.mark.skipif(sys.platform == "win32", reason="SIGSTOP stands in for a hung shell call")
def test_a_wedged_side_lane_does_not_hold_up_a_listing(pool, tmp_path):
    """The Hyper-V report: a folder on `\\\\tsclient\\C` slow or failing to load
    while Explorer lists it at once, because the listing queued behind the
    previous folder's overlays on the same worker."""
    from app.io.pool import SIDE_SUFFIX

    populate(tmp_path, 20)
    warmup = Sink()
    pool.submit(Op.DIR_SIZE, str(tmp_path), timeout=10, handler=warmup)
    assert warmup.settle().status is Status.OK
    side = next(state["pid"] for key, state in pool.status().items()
                if key.endswith(SIDE_SUFFIX))

    os.kill(side, signal.SIGSTOP)
    try:
        wedged = Sink()
        pool.submit(Op.DIR_SIZE, str(tmp_path), timeout=30, handler=wedged)
        listing = Sink()
        started = time.monotonic()
        pool.submit(Op.LIST, str(tmp_path), timeout=5, handler=listing)
        reply = listing.settle(timeout=10)
        elapsed = time.monotonic() - started
    finally:
        os.kill(side, signal.SIGCONT)

    assert reply.status is Status.OK
    assert len(reply.payload) == 20
    assert elapsed < 3, f"the listing waited {elapsed:.1f}s behind the side lane"
    assert wedged.settle(timeout=10).status in SETTLED


def test_kill_and_retry_reach_both_lanes(pool, tmp_path):
    for op in (Op.PING, Op.DIR_SIZE):
        sink = Sink()
        pool.submit(op, str(tmp_path), timeout=10, handler=sink)
        sink.settle()
    assert len(pool.status()) == 2
    assert pool.kill(str(tmp_path)) is True
    assert pool.status() == {}

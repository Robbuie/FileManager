"""Checks on 0.58.1: a queued delete that left files behind and said nothing.

What was seen: fifteen .L5K files marked and recycled, some of them still there
afterwards; two .ACD files, one deleted and one not, and the second attempt
fine. The job said "the shell refused the delete (code 32)" and named nothing.

Two faults made that, and the tests are about both:

* **The application's own reads stood in the way.** Python's `open` on Windows
  leaves FILE_SHARE_DELETE out, so a preview halfway through the file under the
  cursor was a sharing violation for the shell -- code 32 -- and the shell stops
  the whole list there. `paths.open_shared` is the fix; off Windows it is plain
  `open`, so what can be checked here is that every reader goes through it and
  that it refuses to be a writer.
* **The recycle reported what the shell said, not what happened.** It now
  looks: what is still there is named, retried once after a pause, and offered
  to Retry by itself.
"""

from __future__ import annotations

import queue

import pytest

from app.io import ops, paths, worker
from app.io.ops import Runner
from app.io.protocol import Conflict, Job, JobKind, Progress


def job(sources):
    return Job(id=1, kind=JobKind.RECYCLE, sources=tuple(str(s) for s in sources),
               destination="", conflict=Conflict.SKIP)


def events(runner, kind):
    found = []
    while True:
        try:
            event = runner.outbox.get_nowait()
        except queue.Empty:
            return found
        if event.kind is kind:
            found.append(event)


def done(runner):
    return events(runner, Progress.DONE)[-1].payload


class Shell:
    """A `worker.recycle` that removes what it is told to and refuses the rest,
    the way `SHFileOperation` stops at a file somebody has open."""

    def __init__(self, *rounds):
        self.rounds = list(rounds)
        self.calls: list[list[str]] = []

    def __call__(self, targets):
        self.calls.append(list(targets))
        removes = self.rounds.pop(0) if self.rounds else set()
        gone = 0
        for target in targets:
            if any(target.endswith(name) for name in removes):
                import os
                os.remove(target)
                gone += 1
        if gone == len(targets):
            return gone, "", 0
        return 0, "the shell refused the delete (code 32)", 32


@pytest.fixture
def files(tmp_path, monkeypatch):
    monkeypatch.setattr(ops, "RECYCLE_SETTLE", 0)
    made = []
    for name in ("a.L5K", "b.L5K", "c.L5K"):
        target = tmp_path / name
        target.write_text("x")
        made.append(target)
    return made


# -------------------------------------------------------------- the recycle


def test_a_recycle_stopped_part_way_names_what_it_left(files, monkeypatch):
    """The shell took a.L5K and stopped. The job says one went and names the
    two that did not -- not "0 deleted", which is what it used to say."""
    shell = Shell({"a.L5K"}, set())
    monkeypatch.setattr(worker, "recycle", shell)
    runner = Runner(None, queue.Queue())
    runner._recycle(job(files))

    outbox = []
    while True:
        try:
            outbox.append(runner.outbox.get_nowait())
        except queue.Empty:
            break
    failed = [e for e in outbox if e.kind is Progress.FAILED_ITEM]
    finished = [e for e in outbox if e.kind is Progress.DONE][-1].payload
    assert [e.payload["name"] for e in failed] == ["b.L5K", "c.L5K"]
    assert all("code 32" in e.message for e in failed)
    assert finished["copied"] == 1 and finished["failed"] == 2


def test_only_what_is_left_is_tried_again(files, monkeypatch):
    shell = Shell({"a.L5K"}, set())
    monkeypatch.setattr(worker, "recycle", shell)
    Runner(None, queue.Queue())._recycle(job(files))
    assert shell.calls[1] == [str(files[1]), str(files[2])]


def test_a_file_let_go_of_in_the_pause_is_deleted_by_the_second_try(files, monkeypatch):
    """The case actually seen: something was reading b.L5K, and a moment later
    was not. The second attempt takes it and the job is clean."""
    shell = Shell({"a.L5K"}, {"b.L5K", "c.L5K"})
    monkeypatch.setattr(worker, "recycle", shell)
    runner = Runner(None, queue.Queue())
    runner._recycle(job(files))
    assert not any(f.exists() for f in files)
    finished = done(runner)
    assert finished["copied"] == 3 and finished["failed"] == 0


def test_a_clean_recycle_is_not_tried_twice(files, monkeypatch):
    shell = Shell({"a.L5K", "b.L5K", "c.L5K"})
    monkeypatch.setattr(worker, "recycle", shell)
    Runner(None, queue.Queue())._recycle(job(files))
    assert len(shell.calls) == 1


def test_a_shell_that_said_yes_but_left_the_file_is_not_believed(files, monkeypatch):
    monkeypatch.setattr(worker, "recycle", lambda targets: (len(targets), "", 0))
    runner = Runner(None, queue.Queue())
    runner._recycle(job(files[:1]))
    failed = events(runner, Progress.FAILED_ITEM)
    assert failed and failed[0].message.startswith("still there after the delete")


def test_what_is_left_is_offered_to_retry_by_itself(files, monkeypatch):
    """Per file, with its path, so Retry runs those two and not all fifteen."""
    monkeypatch.setattr(worker, "recycle", Shell({"a.L5K"}, set()))
    runner = Runner(None, queue.Queue())
    runner._recycle(job(files))
    failed = events(runner, Progress.FAILED_ITEM)
    assert [e.payload["source"] for e in failed] == [str(files[1]), str(files[2])]


def test_a_path_that_cannot_be_asked_about_counts_as_still_there(monkeypatch):
    """A share that stopped answering between the delete and the look must not
    turn into "deleted"."""
    def unreachable(_path):
        raise PermissionError(13, "the network name is no longer available")

    monkeypatch.setattr(ops.os, "lstat", unreachable)
    assert ops._still_there([r"\\dc01\jobs\a.L5K"]) == [r"\\dc01\jobs\a.L5K"]


def test_without_pywin32_nothing_is_looked_at_or_retried(files, monkeypatch):
    calls = []

    def absent(targets):
        calls.append(targets)
        return 0, worker.RECYCLE_NEEDS_PYWIN32, 0

    monkeypatch.setattr(worker, "recycle", absent)
    runner = Runner(None, queue.Queue())
    runner._recycle(job(files))
    assert len(calls) == 1
    assert done(runner)["failed"] == 3


# ---------------------------------------------------------- open_shared


def test_open_shared_reads(tmp_path):
    target = tmp_path / "a.L5K"
    target.write_bytes(b"IE_VER := 2.25;\r\n")
    with paths.open_shared(str(target), "rb") as handle:
        assert handle.read() == b"IE_VER := 2.25;\r\n"
    with paths.open_shared(str(target), "r", encoding="utf-8", errors="replace") as handle:
        assert handle.read().startswith("IE_VER")


@pytest.mark.parametrize("mode", ["wb", "ab", "r+b", "xb", "w"])
def test_open_shared_will_not_write(tmp_path, mode):
    with pytest.raises(ValueError):
        paths.open_shared(str(tmp_path / "a"), mode)


def test_open_shared_says_when_the_file_is_not_there(tmp_path):
    with pytest.raises(FileNotFoundError):
        paths.open_shared(str(tmp_path / "missing.ACD"), "rb")


def test_no_reader_in_the_io_layer_opens_a_file_the_old_way():
    """The fix is only as good as its coverage: one preview path left on plain
    `open` is the same fault on one file type. Copies and archive extraction
    are left alone on purpose -- those write, or are the source of a copy that
    is meant to finish."""
    import pathlib
    import re

    root = pathlib.Path(paths.__file__).parent
    allowed = {"ops.py", "history.py", "archive.py", "paths.py"}
    pattern = re.compile(r"(?<![\w.])open\(paths\.api\(|Image\.open\(paths\.api\(")
    offenders = [f"{path.name}:{number}"
                 for path in sorted(root.glob("*.py")) if path.name not in allowed
                 for number, line in enumerate(path.read_text("utf-8").splitlines(), 1)
                 if pattern.search(line)]
    assert offenders == []


def test_an_archive_index_does_not_hold_the_archive(tmp_path):
    import zipfile

    from app.io import archive

    target = tmp_path / "jobs.zip"
    with zipfile.ZipFile(target, "w") as handle:
        handle.writestr("a.L5K", "x")
    index = archive._read_index(str(target), deadline=float("inf"))
    assert index is not None
    target.unlink()   # on Windows this is the line that failed while open


# ------------------------------------------------------- the preview lets go


pytest.importorskip("PySide6.QtCore")


class FakeBridge:
    def __init__(self):
        self.sent, self.cancelled, self.forgotten = [], [], []

    def submit(self, op, path, *, timeout, on_reply, args=None):
        self.sent.append(path)
        return len(self.sent)

    def cancel(self, request_id):
        self.cancelled.append(request_id)

    def forget(self, request_id):
        self.forgotten.append(request_id)


@pytest.fixture
def previews():
    from app.core.config import Config
    from app.core.previews import Previews

    bridge = FakeBridge()
    return Previews(bridge, Config({})), bridge


def test_a_delete_withdraws_the_preview_of_a_file_it_removes(previews):
    provider, bridge = previews
    provider.ask(r"C:\Jobs\Line3.ACD", mtime=1.0, size=10, delay_ms=0)
    provider.withdraw((r"C:\Jobs\Line3.ACD", r"C:\Jobs\Line4.ACD"))
    assert bridge.cancelled == [1]
    assert provider.pending is None


def test_a_preview_still_waiting_on_its_timer_is_withdrawn_too(previews):
    provider, bridge = previews
    provider.ask(r"C:\Jobs\Line3.ACD", mtime=1.0, size=10)
    provider.withdraw((r"c:\jobs\line3.acd",))
    provider.fire_now()
    assert bridge.sent == []


def test_a_delete_elsewhere_leaves_the_preview_alone(previews):
    provider, bridge = previews
    provider.ask(r"C:\Jobs\Line3.ACD", mtime=1.0, size=10, delay_ms=0)
    provider.withdraw((r"D:\Other\Line3.ACD",))
    assert bridge.cancelled == []
    assert provider.pending == r"C:\Jobs\Line3.ACD"


def test_the_queue_says_what_a_delete_is_about_to_remove(monkeypatch):
    from app.core.transfers import TransferQueue

    class FakeOps:
        def __init__(self, on_event):
            self.n = 0

        def submit(self, *args, **kwargs):
            self.n += 1
            return self.n

    monkeypatch.setattr("app.io.ops.Transfers", FakeOps)
    mirror = TransferQueue()
    heard = []
    mirror.removing.connect(heard.append)
    mirror.recycle([r"C:\Jobs\a.L5K"])
    mirror.erase([r"C:\Jobs\b.L5K"])
    assert heard == [(r"C:\Jobs\a.L5K",), (r"C:\Jobs\b.L5K",)]

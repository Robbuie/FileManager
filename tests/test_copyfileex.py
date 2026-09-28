"""The copy path that only runs on Windows, and the destination listing.

`CopyFileEx` cannot be called here, so a stand-in with the same contract is:
it calls the progress routine between chunks, stops and removes what it wrote
when the routine answers PROGRESS_CANCEL, and refuses an existing destination
under fail-if-exists. What is being tested is everything this application does
around the call -- carrying a cancel out of the routine, never discarding a
partial that is not ours, and handing back an error the retry rule can read.
Whether Windows keeps to that contract on a real share is for the harness.
"""

from __future__ import annotations

import os
import types

import pytest

from app.io import ops
from app.io.ops import Item, Runner, Totals, _Cancelled, _Destination
from app.io.protocol import Conflict, Job, JobKind
from tests.test_ops import Inbox


class FakeError(Exception):
    def __init__(self, winerror, funcname, strerror):
        super().__init__(winerror, funcname, strerror)
        self.winerror, self.funcname, self.strerror = winerror, funcname, strerror


def fake_win32(calls, *, cancel_at=None, fail_with=None):
    def copy_file_ex(source, dest, routine, data, cancel, flags):
        calls.append((source, dest, flags))
        if flags & 0x1 and os.path.exists(dest):
            raise FakeError(80, "CopyFileEx", "The file exists.")
        payload = open(source, "rb").read()
        with open(dest, "wb") as sink:
            for start in range(0, len(payload), 1024):
                sink.write(payload[start:start + 1024])
                if fail_with is not None and start:
                    raise FakeError(fail_with, "CopyFileEx", "failed")
                if routine(len(payload), min(start + 1024, len(payload)), 0, 0, 1, 0, None, None, data) == 1:
                    sink.close()
                    os.remove(dest)
                    raise FakeError(1235, "CopyFileEx", "The request was aborted.")

    win32file = types.SimpleNamespace(CopyFileEx=copy_file_ex,
                                      PROGRESS_CONTINUE=0, PROGRESS_CANCEL=1)
    return win32file, types.SimpleNamespace(error=FakeError)


def job(tmp_path):
    return Job(id=7, kind=JobKind.COPY, sources=(), destination=str(tmp_path),
               conflict=Conflict.OVERWRITE)


@pytest.fixture
def native(monkeypatch):
    def install(**kwargs):
        calls = []
        win32file, pywintypes = fake_win32(calls, **kwargs)
        monkeypatch.setattr(ops, "win32file", win32file)
        monkeypatch.setattr(ops, "pywintypes", pywintypes)
        return calls
    return install


def test_the_native_copy_writes_beside_and_renames(tmp_path, native):
    calls = native()
    source = tmp_path / "a.bin"
    source.write_bytes(b"x" * 5000)
    target = tmp_path / "out" / "a.bin"
    target.parent.mkdir()
    totals = Totals()
    Runner(Inbox(), __import__("queue").Queue())._stream(
        job(tmp_path), Item(str(source), str(target), size=5000), str(target),
        totals, 5000)
    assert target.read_bytes() == source.read_bytes()
    assert calls[0][1].endswith(ops.PARTIAL_SUFFIX)
    assert totals.bytes == 5000
    assert sorted(os.listdir(target.parent)) == ["a.bin"]


def test_a_stale_partial_is_stepped_round_not_removed(tmp_path, native):
    calls = native()
    source = tmp_path / "a.bin"
    source.write_bytes(b"x" * 3000)
    target = tmp_path / "t.bin"
    stale = tmp_path / ("t.bin" + ops.PARTIAL_SUFFIX)
    stale.write_text("somebody's")
    Runner(Inbox(), __import__("queue").Queue())._stream(
        job(tmp_path), Item(str(source), str(target), size=3000), str(target),
        Totals(), 3000)
    assert target.read_bytes() == source.read_bytes()
    assert stale.read_text() == "somebody's", "a partial that was not ours went"
    assert len(calls) == 2


def test_a_cancel_in_the_progress_routine_is_carried_out(tmp_path, native):
    native()
    source = tmp_path / "big.bin"
    source.write_bytes(b"y" * 8000)
    target = tmp_path / "target.bin"
    target.write_text("the original")
    runner = Runner(Inbox([("cancel", 7)]), __import__("queue").Queue())
    with pytest.raises(_Cancelled):
        runner._stream(job(tmp_path), Item(str(source), str(target), size=8000),
                       str(target), Totals(), 8000)
    assert target.read_text() == "the original"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["big.bin", "target.bin"]


def test_a_windows_error_comes_back_as_an_oserror_with_its_code(tmp_path, native):
    native(fail_with=32)
    source = tmp_path / "locked.bin"
    source.write_bytes(b"z" * 4000)
    target = tmp_path / "t.bin"
    with pytest.raises(OSError) as caught:
        Runner(Inbox(), __import__("queue").Queue())._copy_native(
            job(tmp_path), Item(str(source), str(target), size=4000), str(target),
            lambda _done: None)
    # OSError only keeps a Win32 code on Windows; elsewhere the fourth
    # argument is dropped, which is why this half is conditional.
    if os.name == "nt":
        assert caught.value.winerror == 32
    assert sorted(p.name for p in tmp_path.iterdir()) == ["locked.bin"]


def test_a_large_file_skips_the_cache(tmp_path, native, monkeypatch):
    calls = native()
    monkeypatch.setattr(ops, "UNBUFFERED_FROM", 2000)
    source = tmp_path / "a.bin"
    source.write_bytes(b"x" * 3000)
    target = tmp_path / "t.bin"
    Runner(Inbox(), __import__("queue").Queue())._copy_native(
        job(tmp_path), Item(str(source), str(target), size=3000), str(target),
        lambda _done: None)
    assert calls[0][2] & 0x1000


def items_into(folder, names):
    return [Item(os.path.join("src", n), os.path.join(folder, n)) for n in names]


def test_a_busy_folder_is_listed_once(tmp_path, monkeypatch):
    (tmp_path / "Report.PDF").write_text("")
    names = [f"f{i}.txt" for i in range(ops.LIST_DESTINATION_FROM)] + ["report.pdf"]
    present = _Destination(items_into(str(tmp_path), names))
    monkeypatch.setattr(os.path, "exists", lambda _p: pytest.fail("asked per file"))
    assert present.has(str(tmp_path / "report.pdf")), "case was not folded"
    assert not present.has(str(tmp_path / "f1.txt"))
    present.add(str(tmp_path / "F1.TXT"))
    assert present.has(str(tmp_path / "f1.txt")), "a file this job wrote was forgotten"


def test_a_quiet_folder_and_a_tilde_are_asked_by_name(tmp_path):
    (tmp_path / "a.txt").write_text("")
    quiet = _Destination(items_into(str(tmp_path), ["a.txt"]))
    assert quiet.has(str(tmp_path / "a.txt"))
    busy = _Destination(items_into(
        str(tmp_path), [f"f{i}" for i in range(ops.LIST_DESTINATION_FROM)] + ["LONGNA~1.TXT"]))
    asked = []
    real = os.path.exists
    os.path.exists = lambda p: asked.append(p) or real(p)
    try:
        busy.has(str(tmp_path / "LONGNA~1.TXT"))
    finally:
        os.path.exists = real
    assert asked, "a name with a tilde was answered from the listing"

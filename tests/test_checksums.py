"""Checksums (0.43): the worker, the pane's request and the dialog's verdicts."""

from __future__ import annotations

import hashlib
import queue
import zipfile

from app.io import worker
from app.io.protocol import Op, Request, Status


def ask(path, **args):
    outbox: queue.Queue = queue.Queue()
    worker._handle(Request(1, Op.HASH, str(path), 5.0, args), outbox, queue.Queue(), set())  # noqa: SLF001
    replies = []
    while not outbox.empty():
        replies.append(outbox.get())
    return replies[-1]


def test_each_algorithm_and_a_missing_file(tmp_path):
    (tmp_path / "a.bin").write_bytes(b"abc" * 1000)
    for algorithm in ("sha256", "sha1", "md5"):
        reply = ask(tmp_path, names=["a.bin", "gone.bin"], algorithm=algorithm)
        assert reply.status is Status.OK
        assert reply.payload["sums"]["a.bin"] == hashlib.new(algorithm, b"abc" * 1000).hexdigest()
        assert "gone.bin" in reply.payload["failed"]


def test_an_unknown_algorithm_and_an_escaping_name_are_refused(tmp_path):
    assert ask(tmp_path, names=["a"], algorithm="crc9").status is Status.ERROR
    reply = ask(tmp_path, names=["..\\..\\secret"], algorithm="md5")
    assert reply.payload["failed"] == {"..\\..\\secret": "not a name in this folder"}


def test_a_member_of_an_archive(tmp_path):
    path = tmp_path / "x.zip"
    with zipfile.ZipFile(path, "w") as handle:
        handle.writestr("in/a.txt", "hello")
    reply = ask(f"{path}\\in", names=["a.txt"], algorithm="sha256")
    assert reply.payload["sums"]["a.txt"] == hashlib.sha256(b"hello").hexdigest()


def test_verdicts():
    from app.ui.checksums import lines, verdict

    sums = {"a": "ABC1", "b": "abc1"}
    assert verdict(sums, "") == ("not identical", set())
    assert verdict({"a": "x", "b": "x"}, "") == ("all identical", set())
    assert verdict(sums, " abc1 ") == ("matches a, b", {"a", "b"})
    assert verdict(sums, "zzz")[0] == "no file here has that checksum"
    assert lines({"a.txt": "ff"}) == "ff *a.txt"


def test_the_pane_asks_the_folders_worker(tmp_path):
    from app.core.config import Config
    from app.core.pane import Pane
    from tests.test_columns import FakeBridge

    bridge = FakeBridge()
    pane = Pane(bridge, Config({}, path=str(tmp_path / "c.json")), "left")
    pane.navigate(str(tmp_path))
    pane.checksums(["a.bin"], "md5", lambda payload, message: None)
    assert bridge.sent[-1]["op"] is Op.HASH
    assert bridge.sent[-1]["args"] == {"names": ["a.bin"], "algorithm": "md5"}


def test_the_dialog_shows_what_comes_back():
    from app.ui.checksums import ChecksumDialog

    asked = []
    dialog = ChecksumDialog(None, names=["a", "b"], algorithm="sha1",
                            ask=lambda algorithm, done: asked.append((algorithm, done)) or 7)
    assert asked[-1][0] == "sha1" and dialog.request_id == 7
    asked[-1][1]({"algorithm": "sha1", "sums": {"a": "11", "b": "11"}, "failed": {}}, "")
    assert dialog._table.item(0, 1).text() == "11"  # noqa: SLF001
    assert dialog._verdict.text() == "all identical"  # noqa: SLF001
    dialog._algorithm.setCurrentIndex(dialog._algorithm.findData("md5"))  # noqa: SLF001
    assert asked[-1][0] == "md5"

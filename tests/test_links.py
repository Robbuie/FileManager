"""Links (0.45): making them, reading where they point, the dialog."""

from __future__ import annotations

import os
import queue
import zipfile

import pytest

from app.io import worker
from app.io.protocol import Entry, Op, Reply, Request, Status


def ask(op, path, **args):
    outbox: queue.Queue = queue.Queue()
    worker._handle(Request(1, op, str(path), 5.0, args), outbox, queue.Queue(), set())  # noqa: SLF001
    return outbox.get()


def test_symbolic_and_hard_links_and_where_they_point(tmp_path):
    (tmp_path / "target").mkdir()
    (tmp_path / "f.txt").write_text("x")
    assert ask(Op.LINK, tmp_path, name="to-target", target=str(tmp_path / "target"),
               kind="symbolic").status is Status.OK
    assert os.path.islink(tmp_path / "to-target")
    assert ask(Op.LINK_TARGET, tmp_path / "to-target").payload == {
        "target": str(tmp_path / "target")}
    assert ask(Op.LINK, tmp_path, name="twin.txt", target=str(tmp_path / "f.txt"),
               kind="hard").status is Status.OK
    assert os.stat(tmp_path / "twin.txt").st_nlink == 2


def test_refusals(tmp_path):
    (tmp_path / "taken").write_text("mine")
    (tmp_path / "dir").mkdir()
    assert "already exists" in ask(Op.LINK, tmp_path, name="taken", target=str(tmp_path),
                                   kind="symbolic").message
    assert "not a usable name" in ask(Op.LINK, tmp_path, name="..\\x", target=str(tmp_path),
                                      kind="symbolic").message
    assert "hard link is to a file" in ask(Op.LINK, tmp_path, name="h",
                                           target=str(tmp_path / "dir"), kind="hard").message
    assert (tmp_path / "taken").read_text() == "mine"
    archive = tmp_path / "a.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("x", "x")
    assert "read-only" in ask(Op.LINK, archive, name="l", target=str(tmp_path),
                              kind="symbolic").message


def test_the_dialog_needs_a_name_and_a_target():
    from app.ui.links import LinkDialog, default_kind

    assert default_kind(True) == "junction" and default_kind(False) == "hard"
    dialog = LinkDialog(None, folder="D:\\out", target="C:\\Jobs", target_is_dir=True,
                        name="Jobs")
    assert dialog._ok.isEnabled()  # noqa: SLF001
    assert dialog.answer() == ("Jobs", "C:\\Jobs", "junction")
    dialog._name.setText("a:b")  # noqa: SLF001
    assert not dialog._ok.isEnabled()  # noqa: SLF001


def test_following_a_link_asks_its_own_worker(tmp_path):
    from app.core.config import Config
    from app.core.pane import Pane
    from tests.test_columns import FakeBridge

    bridge = FakeBridge()
    pane = Pane(bridge, Config({}, path=str(tmp_path / "c.json")), "left")
    pane.navigate(str(tmp_path))
    tab = pane.current
    pane._on_reply(tab, Reply(tab.request_id, Status.OK,  # noqa: SLF001
                              payload=[Entry("link", True, 0, 1.0, 0, is_link=True)]))
    pane.follow_link(tab.model.row_of("link"))
    assert bridge.sent[-1]["op"] is Op.LINK_TARGET
    assert bridge.sent[-1]["path"].endswith("link")
    pane.make_link(str(tmp_path), "l", "C:\\x", "junction")
    assert bridge.sent[-1] == {"op": Op.LINK, "path": str(tmp_path),
                               "args": {"name": "l", "target": "C:\\x", "kind": "junction"}}

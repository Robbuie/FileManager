"""Search and the duplicate finder (0.42)."""

from __future__ import annotations

import io
import queue
import time
import zipfile

import pytest

from app.io import search, worker
from app.io.protocol import Entry, Op, Request, Status


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "a" / "x.L5K").write_text("CONTROLLER Pump_101 (ProcessorType := 1)")
    (tmp_path / "a" / "b" / "y.L5K").write_text("nothing here")
    (tmp_path / "a" / "b" / "z.txt").write_text("has Pump_101 here", encoding="utf-16")
    (tmp_path / "a" / "dup1.bin").write_bytes(b"q" * 100_000)
    (tmp_path / "a" / "b" / "dup2.bin").write_bytes(b"q" * 100_000)
    (tmp_path / "a" / "near.bin").write_bytes(b"q" * 99_999 + b"r")
    (tmp_path / "a" / "small1.txt").write_text("same")
    (tmp_path / "a" / "b" / "small2.txt").write_text("same")
    (tmp_path / "a" / "empty1").write_bytes(b"")
    (tmp_path / "a" / "b" / "empty2").write_bytes(b"")
    return tmp_path


def walk(path, **args):
    outbox: queue.Queue = queue.Queue()
    worker._handle(Request(1, Op.WALK, str(path), 5.0, args), outbox, queue.Queue(), set())  # noqa: SLF001
    replies = []
    while not outbox.empty():
        replies.append(outbox.get())
    return replies


def names(replies):
    return sorted(entry.name for reply in replies for entry in (reply.payload or []))


# ------------------------------------------------------------------ the rules

def test_patterns_split_and_a_bare_word_is_part_of_a_name():
    assert search.patterns("*.L5K; *.acd, pump") == ("*.l5k", "*.acd", "*pump*")
    assert search.patterns("  ") == ()


def test_the_cheap_filters():
    spec = search.Spec(names=("*.l5k",), after=100.0, min_size=10, max_size=20)
    assert search.cheap_matches(spec, "A.L5K", False, 15, 150.0)
    assert not search.cheap_matches(spec, "A.L5K", False, 15, 50.0)
    assert not search.cheap_matches(spec, "A.L5K", False, 25, 150.0)
    assert not search.cheap_matches(spec, "A.txt", False, 15, 150.0)
    assert not search.cheap_matches(spec, "A.L5K", True, 0, 150.0)


def test_text_across_a_block_boundary_is_found(monkeypatch):
    monkeypatch.setattr(search, "BLOCK", 8)
    needle = search.Needle(search.Spec(text="Pump_101"))
    assert needle.found_in(io.BytesIO(b"xxxxxxPump_101yyy"))
    assert not needle.found_in(io.BytesIO(b"xxxxxxPump_10 1yyy"))


def test_case_and_utf16_and_regex():
    folded = search.Needle(search.Spec(text="pump_101"))
    assert folded.found_in(io.BytesIO("PUMP_101".encode("utf-16-le")))
    exact = search.Needle(search.Spec(text="pump_101", case=True))
    assert not exact.found_in(io.BytesIO(b"PUMP_101"))
    pattern = search.Needle(search.Spec(text=r"Pump_\d{3}\b", regex=True))
    assert pattern.found_in(io.BytesIO(b"x Pump_202 y"))


def test_a_bad_pattern_is_refused_by_the_worker(tree):
    reply = walk(tree, search={"text": "(", "regex": True})[-1]
    assert reply.status is Status.ERROR and "does not work" in reply.message


# ----------------------------------------------------------------- the worker

def test_names(tree):
    assert names(walk(tree, search={"names": "*.l5k"})) == ["a\\b\\y.L5K", "a\\x.L5K"]


def test_contents_in_either_encoding(tree):
    assert names(walk(tree, search={"text": "pump_101"})) == ["a\\b\\z.txt", "a\\x.L5K"]
    assert names(walk(tree, search={"text": "pump_101", "case": True})) == []


def test_folders_by_name_only_when_asked(tree):
    assert "a\\b" not in names(walk(tree, search={"names": "b"}))
    assert "a\\b" in names(walk(tree, search={"names": "b", "folders": True}))


def test_no_search_is_the_plain_walk(tree):
    assert len(names(walk(tree))) == 10


def test_inside_an_archive_by_name_and_contents(tmp_path):
    path = tmp_path / "export.zip"
    with zipfile.ZipFile(path, "w") as handle:
        handle.writestr("HMI/a.txt", "Pump_101")
        handle.writestr("b.txt", "nothing")
    assert names(walk(path, search={"text": "Pump_101"})) == ["HMI\\a.txt"]
    assert names(walk(path, search={"names": "b.*"})) == ["b.txt"]


def test_duplicates_are_compared_by_content_and_empty_files_are_left_out(tree):
    replies = walk(tree, duplicates=True)
    assert names(replies) == ["a\\b\\dup2.bin", "a\\b\\small2.txt", "a\\dup1.bin",
                              "a\\small1.txt"]
    assert replies[-1].message.startswith("duplicates groups=2 wasted=100004")


def test_duplicates_honour_the_name_filter(tree):
    assert names(walk(tree, duplicates=True, search={"names": "*.txt"})) == [
        "a\\b\\small2.txt", "a\\small1.txt"]


# ------------------------------------------------------------------- the pane

def make_pane(tmp_path):
    from app.core.config import Config
    from app.core.pane import Pane
    from tests.test_columns import FakeBridge

    bridge = FakeBridge()
    return Pane(bridge, Config({}, path=str(tmp_path / "c.json")), "left"), bridge


def test_a_search_opens_a_tab_that_walks_with_the_filter(tmp_path):
    from app.core.listing import Column
    from app.io.protocol import Reply

    pane, bridge = make_pane(tmp_path)
    pane.navigate(str(tmp_path))
    assert pane.search(str(tmp_path), {"names": "*.L5K"})
    tab = pane.current
    assert len(pane.tabs) == 2 and tab.flat and tab.label == "Search: *.L5K"
    assert bridge.sent[-1]["op"] is Op.WALK
    assert bridge.sent[-1]["args"]["search"] == {"names": "*.L5K"}
    pane._on_reply(tab, Reply(tab.request_id, Status.PARTIAL,  # noqa: SLF001
                              payload=[Entry("a\\x.L5K", False, 5, 1.0, 0)]))
    assert tab.status_text.startswith("searching, 1 found")
    pane.navigate(str(tmp_path / "elsewhere"))
    assert tab.search is None and not tab.flat

    pane.search(str(tmp_path), {}, duplicates=True)
    tab = pane.current
    assert tab.label.startswith("Duplicates in") and bridge.sent[-1]["args"]["duplicates"]
    assert tab.model.sort_column == Column.SIZE
    pane._on_reply(tab, Reply(tab.request_id, Status.OK, payload=[],  # noqa: SLF001
                              message="duplicates groups=2 wasted=1048576 skipped=0"))
    assert "2 sets of identical files, 1.0 M in extra copies" in tab.status_text


def test_the_dialog_builds_the_spec():
    from app.ui.search import SearchDialog, after_for

    dialog = SearchDialog(None, folder="C:\\Jobs", last={"names": "*.L5K", "when": 7,
                                                        "size": 2})
    now = 1_800_000_000.0
    spec = dialog.spec(now)
    assert spec["names"] == "*.L5K" and spec["after"] == now - 7 * 86400
    assert spec["min_size"] == 1024 * 1024 and "text" in spec
    dialog._duplicates.setChecked(True)
    assert "text" not in dialog.spec(now)
    dialog._duplicates.setChecked(False)
    dialog._regex.setChecked(True)
    dialog._text.setText("(")
    assert not dialog._ok.isEnabled()
    assert after_for(-1, now) <= now and after_for(0) == 0.0

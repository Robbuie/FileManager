"""One-way sync: the plan it shows, and what it hands the queue.

The planner is where a sync could go wrong in a way that costs files, so most
of this is about it -- above all that mirror never removes something the walk
did not look at, and that nothing newer on the target is overwritten.
"""

from __future__ import annotations

import os
import queue

import pytest

pytest.importorskip("PySide6")

from app.core import sync  # noqa: E402
from app.core.config import Config  # noqa: E402
from app.io.protocol import Conflict, Entry, Op, Reply, Request, Status  # noqa: E402
from tests.test_live import FakeBridge  # noqa: E402


def f(name, mtime=100.0, size=10):
    return Entry(name=name, is_dir=False, size=size, mtime=mtime, attributes=0)


def d(name, *, link=False):
    return Entry(name=name, is_dir=True, size=0, mtime=50.0, attributes=0,
                 is_link=link)


def kinds(plan):
    return [(action.kind, action.path) for action in plan.actions]


# ------------------------------------------------------------------ the plan


def test_new_and_newer_are_copied_and_nothing_else_is():
    plan = sync.plan([f("a.dwg", 200), f("b.dwg", 100), f("c.dwg")],
                     [f("a.dwg", 100), f("b.dwg", 100)], mirror=False)
    assert kinds(plan) == [(sync.NEW_FILE, "c.dwg"), (sync.NEWER, "a.dwg")]


def test_a_newer_target_is_left_alone_and_said_so():
    plan = sync.plan([f("a.dwg", 100)], [f("a.dwg", 500)], mirror=True)
    assert kinds(plan) == [(sync.TARGET_NEWER, "a.dwg")]
    assert plan.empty


def test_two_seconds_apart_is_the_same_moment():
    plan = sync.plan([f("a", 101.5)], [f("a", 100.0)], mirror=False)
    assert plan.actions == []


def test_same_time_and_a_different_size_is_left_alone():
    plan = sync.plan([f("a", size=1)], [f("a", size=2)], mirror=False)
    assert kinds(plan) == [(sync.DIFFERENT, "a")]


def test_a_missing_folder_is_one_action_with_its_contents_counted():
    plan = sync.plan([d("new"), d("new\\deep"), f("new\\x", size=5),
                      f("new\\deep\\y", size=7)], [], mirror=False)
    assert kinds(plan) == [(sync.NEW_FOLDER, "new")]
    assert plan.actions[0].files == 2 and plan.actions[0].size == 12


def test_update_never_removes():
    plan = sync.plan([], [f("extra"), d("old"), f("old\\z")], mirror=False)
    assert plan.removals == []
    assert plan.empty


def test_mirror_removes_what_only_the_target_has_folders_whole():
    plan = sync.plan([f("keep")], [f("keep"), f("extra"), d("old"), f("old\\z")],
                     mirror=True)
    assert [(a.kind, a.path) for a in plan.removals] == [
        (sync.EXTRA_FOLDER, "old"), (sync.EXTRA_FILE, "extra")]


def test_names_match_whatever_their_case():
    plan = sync.plan([f("Report.PDF", 100)], [f("report.pdf", 100)], mirror=True)
    assert plan.actions == []


def test_links_are_never_walked_copied_or_removed():
    plan = sync.plan([d("jn", link=True), f("jn\\inside")],
                     [d("tl", link=True)], mirror=True)
    assert set(kinds(plan)) == {(sync.LINK, "jn"), (sync.LINK, "tl")}
    assert plan.empty


def test_a_folder_against_a_file_is_left_alone_with_everything_under_it():
    plan = sync.plan([d("x"), f("x\\a")], [f("x")], mirror=True)
    assert kinds(plan) == [(sync.CLASH, "x")]


def test_the_requests_send_each_item_into_its_own_folder():
    plan = sync.plan([d("a"), f("a\\b.dwg", 300), f("top.txt"), d("new")],
                     [d("a"), f("a\\b.dwg", 100), f("gone.txt")], mirror=True)
    sources, into = sync.copy_request(plan.copies, "S:\\Jobs", "D:\\Backup")
    assert list(zip(sources, into)) == [
        ("S:\\Jobs\\new", "D:\\Backup"),
        ("S:\\Jobs\\top.txt", "D:\\Backup"),
        ("S:\\Jobs\\a\\b.dwg", "D:\\Backup\\a"),
    ]
    assert sync.removal_request(plan.removals, "D:\\Backup") == ["D:\\Backup\\gone.txt"]


def test_one_folder_inside_the_other_is_refused():
    assert sync.refusal("C:\\Jobs", "C:\\Jobs\\backup")
    assert sync.refusal("C:\\Jobs\\backup", "C:\\jobs")
    assert sync.refusal("C:\\Jobs", "C:\\Jobs\\")
    assert not sync.refusal("C:\\Jobs", "C:\\Jobs2")


# ------------------------------------------------------------------ the walk


def test_the_walk_sends_folders_when_asked(tmp_path):
    from app.io import worker

    (tmp_path / "a" / "empty").mkdir(parents=True)
    (tmp_path / "a" / "x.txt").write_text("x")
    outbox: queue.Queue = queue.Queue()
    worker._walk(Request(id=1, op=Op.WALK, path=str(tmp_path), timeout=5.0,  # noqa: SLF001
                         args={"folders": True}), outbox, queue.Queue(), set())
    rows = []
    while not outbox.empty():
        rows.extend(outbox.get().payload or [])
    found = {(row.name.replace("\\", "/"), row.is_dir) for row in rows}
    assert found == {("a", True), ("a/empty", True), ("a/x.txt", False)}


def test_a_walk_that_did_not_see_everything_rules_out_mirror():
    reply = Reply(1, Status.OK, payload=[], message="limit skipped=2")
    assert sync.gaps(reply) == ["stopped at the walk limit",
                                "2 folders could not be read"]
    scan = sync.Scan(left="a", right="b", left_gaps=sync.gaps(reply))
    assert not scan.complete


def test_the_scan_waits_for_both_sides():
    bridge = FakeBridge()
    scanner = sync.SyncScan(bridge, Config({}))
    done = []
    scanner.finished.connect(done.append)
    scanner.start("S:\\Jobs", "D:\\Backup")
    assert [sent["op"] for sent in bridge.sent] == [Op.WALK, Op.WALK]
    bridge.answer(1, [f("a")])
    assert not done
    bridge.answer(2, [f("b")])
    assert done and done[0].left_rows[0].name == "a" and done[0].complete


def test_a_side_that_cannot_be_read_ends_the_scan_with_the_reason():
    bridge = FakeBridge()
    scanner = sync.SyncScan(bridge, Config({}))
    done = []
    scanner.finished.connect(done.append)
    scanner.start("S:\\Jobs", "D:\\Backup")
    bridge.sent[0]["handler"](Reply(1, Status.GONE, message="gone"))
    assert done and "S:\\Jobs" in done[0].error
    assert 2 in bridge.cancelled


# ---------------------------------------------------------------- the dialog


class FakeQueue:
    def __init__(self):
        self.calls = []

    def copy_into(self, sources, destination, into, *, conflict):
        self.calls.append(("copy", list(sources), destination, list(into), conflict))

    def recycle(self, sources):
        self.calls.append(("recycle", list(sources)))


@pytest.fixture
def dialog(qapp=None):
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    from app.ui.sync import SyncDialog

    bridge = FakeBridge()
    scanner = sync.SyncScan(bridge, Config({}))
    work = FakeQueue()
    made = SyncDialog(scanner, work, "S:\\Jobs", "D:\\Backup")
    bridge.answer(1, [f("new.dwg"), f("same", 100)])
    bridge.answer(2, [f("same", 100), f("extra.txt")])
    yield made, work
    made.deleteLater()


def test_the_dialog_queues_a_newer_only_copy(dialog):
    made, work = dialog
    assert made._go.isEnabled()  # noqa: SLF001
    made._submit()  # noqa: SLF001
    assert work.calls == [("copy", ["S:\\Jobs\\new.dwg"], "D:\\Backup",
                           ["D:\\Backup"], Conflict.NEWER)]


def test_mirror_says_how_many_it_removes_and_queues_them(dialog):
    made, work = dialog
    made._mirror.setChecked(True)  # noqa: SLF001
    assert "removing 1" in made._go.text()  # noqa: SLF001
    made._submit()  # noqa: SLF001
    assert work.calls[-1] == ("recycle", ["D:\\Backup\\extra.txt"])


def test_swapping_reads_the_same_walk_the_other_way(dialog):
    made, work = dialog
    made._swap()  # noqa: SLF001
    made._submit()  # noqa: SLF001
    assert work.calls == [("copy", ["D:\\Backup\\extra.txt"], "S:\\Jobs",
                           ["S:\\Jobs"], Conflict.NEWER)]

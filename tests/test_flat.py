"""0.25, flat view: the walk, the model and the pane.

The walk is the real handler against a real folder, with no process between.
The model and the pane are driven with invented rows, because the names a walk
produces are Windows-shaped and the path layer here is too.
"""

from __future__ import annotations

import queue

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402

from app.core.config import Config  # noqa: E402
from app.core.listing import Column, ListingModel  # noqa: E402
from app.io.protocol import Entry, Op, Reply, Request, Status  # noqa: E402
from tests.test_columns import FakeBridge  # noqa: E402


# -------------------------------------------------------------------- the walk

def walk(path, *, limit=50_000):
    from app.io import worker
    outbox: queue.Queue = queue.Queue()
    request = Request(id=7, op=Op.WALK, path=str(path), timeout=5.0,
                      args={"limit": limit})
    worker._walk(request, outbox, queue.Queue(), set())  # noqa: SLF001
    replies = []
    while not outbox.empty():
        replies.append(outbox.get())
    return replies


def tree(root):
    (root / "2026-09-15" / "HMI").mkdir(parents=True)
    (root / "2026-09-16").mkdir()
    (root / "top.txt").write_text("x")
    (root / "2026-09-15" / "main.L5X").write_text("xx")
    (root / "2026-09-15" / "HMI" / "screen.mer").write_text("xxx")
    (root / "2026-09-16" / "main.L5X").write_text("xxxx")
    (root / "empty").mkdir()


def test_the_walk_finds_every_file_and_no_folders(tmp_path):
    tree(tmp_path)
    replies = walk(tmp_path)
    assert replies[-1].status is Status.OK
    names = sorted(e.name for r in replies for e in (r.payload or []))
    assert names == ["2026-09-15\\HMI\\screen.mer", "2026-09-15\\main.L5X",
                     "2026-09-16\\main.L5X", "top.txt"]
    assert all(not e.is_dir for r in replies for e in (r.payload or []))


def test_the_walk_stops_at_the_limit_and_says_so(tmp_path):
    tree(tmp_path)
    replies = walk(tmp_path, limit=2)
    files = [e for r in replies for e in (r.payload or [])]
    assert len(files) == 2
    assert replies[-1].status is Status.OK
    assert "limit" in replies[-1].message.split()


def test_a_walk_of_a_missing_folder_fails_like_a_listing(tmp_path):
    replies = walk(tmp_path / "nope")
    assert len(replies) == 1
    assert replies[0].status is not Status.OK


def test_a_cancelled_walk_says_so(tmp_path):
    from app.io import worker
    tree(tmp_path)
    outbox: queue.Queue = queue.Queue()
    request = Request(id=9, op=Op.WALK, path=str(tmp_path), timeout=5.0)
    worker._walk(request, outbox, queue.Queue(), {9})  # noqa: SLF001
    assert outbox.get().status is Status.CANCELLED


# ------------------------------------------------------------------- the model

def row(name, size=10, mtime=100.0):
    return Entry(name=name, is_dir=False, size=size, mtime=mtime, attributes=0)


def flat_model(grouped=False):
    model = ListingModel()
    model.set_folder("S:\\Jobs\\PLC")
    model.set_flat(True, grouped=grouped)
    model.begin(has_parent=False)
    model.add([row("2026-09-16\\main.L5X", mtime=300), row("top.txt", mtime=50),
               row("2026-09-15\\HMI\\screen.mer", mtime=200),
               row("2026-09-15\\main.L5X", mtime=100)])
    model.finish()
    return model


def cell(model, r, column):
    return model.data(model.index(r, int(column)))


def test_a_flat_row_shows_its_own_name_and_where_it_is():
    model = flat_model()
    names = [(cell(model, r, Column.NAME), cell(model, r, Column.EXT),
              cell(model, r, Column.LOCATION)) for r in range(model.rowCount())]
    assert ("screen", "mer", "2026-09-15\\HMI") in names
    assert ("top", "txt", "") in names


def test_a_dot_in_a_folder_name_is_not_an_extension():
    model = ListingModel()
    model.set_flat(True)
    assert model.split(row("v1.2\\notes")) == ("notes", "")


def test_the_filter_matches_the_file_name_not_the_folders_above_it():
    model = flat_model()
    model.set_filter("2026")
    assert model.rowCount() == 0
    model.set_filter("L5X")
    assert model.rowCount() == 2


def test_grouped_rows_are_together_and_the_first_of_each_has_a_heading():
    model = flat_model(grouped=True)
    model.sort(int(Column.MODIFIED), Qt.DescendingOrder)
    places = [cell(model, r, Column.LOCATION) for r in range(model.rowCount())]
    headings = [model.group_heading(r) for r in range(model.rowCount())]
    assert [h for h in headings if h] == [("2026-09-16", 1), ("2026-09-15", 1),
                                         ("2026-09-15\\HMI", 1), ("", 1)]


def test_the_column_layout_has_no_headings():
    model = flat_model()
    assert all(model.group_heading(r) is None for r in range(model.rowCount()))


def test_the_summary_counts_files_and_folders():
    assert flat_model().summary().startswith("4 files in 4 folders")


# -------------------------------------------------------------------- the pane

def make_pane(tmp_path, **values):
    from app.core.pane import Pane
    bridge = FakeBridge()
    config = Config({"left.path": "S:\\Jobs\\PLC", **values}, str(tmp_path / "c.json"))
    return Pane(bridge, config, "left"), bridge


def test_flat_view_walks_rather_than_lists(tmp_path):
    pane, bridge = make_pane(tmp_path)
    pane.set_flat(True)
    assert bridge.sent[-1]["op"] is Op.WALK
    assert bridge.sent[-1]["args"]["limit"] == 50000
    assert pane.current.model.flat


def test_going_to_another_folder_ends_flat_view(tmp_path):
    pane, bridge = make_pane(tmp_path)
    pane.set_flat(True)
    pane.navigate("S:\\Jobs")
    assert not pane.current.flat
    assert bridge.sent[-1]["op"] is Op.LIST


def test_a_flat_view_is_not_live_checked(tmp_path):
    pane, _ = make_pane(tmp_path)
    pane.set_flat(True)
    pane.current.listed = True
    pane.current.request_id = None
    assert pane.check(now=1e12) is False


def test_the_final_reply_notes_the_limit_and_unreadable_folders(tmp_path):
    pane, _ = make_pane(tmp_path)
    pane.set_flat(True)
    tab = pane.current
    pane._on_reply(tab, Reply(tab.request_id, Status.OK,  # noqa: SLF001
                              payload=[row("a\\b.txt")], message="limit skipped=3"))
    assert "limit" in tab.status_text
    assert "3 folders could not be read" in tab.status_text


def test_stopping_a_walk_keeps_what_it_found(tmp_path):
    pane, _ = make_pane(tmp_path)
    pane.set_flat(True)
    tab = pane.current
    pane._on_reply(tab, Reply(tab.request_id, Status.PARTIAL,  # noqa: SLF001
                              payload=[row("a\\b.txt"), row("c.txt")]))
    assert pane.stop_walk()
    assert tab.model.rowCount() == 2
    assert "stopped" in tab.status_text


def test_the_layout_setting_regroups_open_flat_tabs(tmp_path):
    pane, _ = make_pane(tmp_path)
    pane.set_flat(True)
    pane.set_flat_layout("groups")
    assert pane.current.model.grouped
    assert pane._config.get("flat.layout") == "groups"  # noqa: SLF001
    pane.set_flat_layout("column")
    assert not pane.current.model.grouped


# ----------------------------------------------------------------- the widget

def test_location_is_a_column_only_in_the_column_layout(tmp_path):
    from PySide6.QtWidgets import QApplication
    from tests.test_look import make
    widget = make(tmp_path)
    view = widget._view
    assert view.isColumnHidden(int(Column.LOCATION))
    widget._pane.set_flat(True)
    QApplication.processEvents()
    assert not view.isColumnHidden(int(Column.LOCATION))
    assert view.horizontalHeader().visualIndex(int(Column.LOCATION)) == 1
    widget._pane.set_flat_layout("groups")
    assert view.isColumnHidden(int(Column.LOCATION))


def test_grouped_rows_that_start_a_folder_are_taller(tmp_path):
    from PySide6.QtWidgets import QApplication
    from app.ui.rows import GROUP_HEAD
    from tests.test_look import make
    widget = make(tmp_path)
    pane = widget._pane
    pane.set_flat_layout("groups")
    pane.set_flat(True)
    tab = pane.current
    pane._on_reply(tab, Reply(tab.request_id, Status.OK,  # noqa: SLF001
                              payload=[row("a\\1.txt"), row("a\\2.txt"), row("b\\3.txt")]))
    QApplication.processEvents()
    heights = [widget._view.rowHeight(r) for r in range(3)]
    assert heights[0] == heights[2] == heights[1] + GROUP_HEAD


def test_the_tab_says_it_is_flat(tmp_path):
    from tests.test_look import make
    widget = make(tmp_path)
    widget._pane.set_flat(True)
    assert "(flat)" in widget._tabs.tabText(widget._tabs.currentIndex())


def test_rename_is_refused_in_flat_view(tmp_path):
    from tests.test_look import make
    widget = make(tmp_path)
    widget._pane.set_flat(True)
    widget.rename_current()
    assert "outside flat view" in widget._pane.current.status_text


def test_ctrl_shift_b_is_reserved_now_that_ctrl_b_is_flat_view():
    from app.core.commands import RESERVED
    assert "Ctrl+B" in RESERVED and "Ctrl+Shift+B" in RESERVED


def test_a_location_goes_to_that_folder_and_ends_flat_view(tmp_path):
    pane, bridge = make_pane(tmp_path)
    pane.set_flat(True)
    tab = pane.current
    pane._on_reply(tab, Reply(tab.request_id, Status.OK,  # noqa: SLF001
                              payload=[row("2026-09-15\\HMI\\screen.mer")]))
    pane.go_to_location(0)
    assert not tab.flat
    assert tab.path == "S:\\Jobs\\PLC\\2026-09-15\\HMI"
    assert tab.reveal_name == "screen.mer"
    assert bridge.sent[-1]["op"] is Op.LIST

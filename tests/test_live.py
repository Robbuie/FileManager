"""Live folders, and dragging rows out of the window.

Reported from use: a file saved by another program did not appear until the
folder was refreshed by hand, and nothing could be dragged into an email.

The live half turns on one thing the rest depends on, so it is checked
hardest: a refresh **reconciles** a listing into the model rather than
resetting it. A check every few seconds that reset the model would throw away
the marks, the cursor and the scroll position while somebody was working in
the folder, which is why the setting sat at zero for twenty releases.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QItemSelectionModel, Qt  # noqa: E402
from PySide6.QtWidgets import QAbstractItemView, QApplication, QTableView  # noqa: E402

from app.core.config import Config  # noqa: E402
from app.core.listing import ListingModel  # noqa: E402
from app.core.pane import CHECK_FAILURE_WAIT, IDLE, Pane  # noqa: E402
from app.io.protocol import Entry, Op, Reply, Status  # noqa: E402


def row(name, *, is_dir=False, size=1, mtime=100.0):
    return Entry(name=name, is_dir=is_dir, size=size, mtime=mtime, attributes=0)


class FakeBridge:
    def __init__(self):
        self.sent = []
        self.cancelled = []

    def submit(self, op, path, *, timeout, on_reply, args=None):
        self.sent.append({"op": op, "path": path, "handler": on_reply})
        return len(self.sent)

    def cancel(self, request_id):
        self.cancelled.append(request_id)

    def forget(self, request_id):
        pass

    def retry(self, path):
        pass

    def listings(self):
        return [i + 1 for i, sent in enumerate(self.sent) if sent["op"] is Op.LIST]

    def answer(self, request_id, rows=(), status=Status.OK):
        self.sent[request_id - 1]["handler"](
            Reply(request_id, status, payload=list(rows)))


# ------------------------------------------------------------------ the model


def listed(names, *, parent=True):
    model = ListingModel()
    model.set_folder("C:\\Jobs")
    model.begin(has_parent=parent)
    model.add([row(name) for name in names])
    model.finish()
    return model


@pytest.fixture
def view():
    made = QTableView()
    made.setSelectionBehavior(QAbstractItemView.SelectRows)
    made.setSelectionMode(QAbstractItemView.ExtendedSelection)
    yield made
    made.deleteLater()


def mark(view, model, *names):
    picker = view.selectionModel()
    for name in names:
        picker.select(model.index(model.row_of(name), 0),
                      QItemSelectionModel.Select | QItemSelectionModel.Rows)


def marked(view, model):
    return sorted(model.entry(i.row()).name for i in view.selectionModel().selectedRows())


def test_a_reconcile_keeps_the_marks_and_the_cursor(view):
    model = listed(["a", "c", "e"])
    view.setModel(model)
    mark(view, model, "a", "e")
    view.selectionModel().setCurrentIndex(model.index(model.row_of("c"), 0),
                                          QItemSelectionModel.NoUpdate)

    assert model.reconcile([row(n) for n in ["a", "b", "c", "d", "e"]]) is True

    assert model.rowCount() == 6          # `..` and five
    assert marked(view, model) == ["a", "e"]
    assert model.entry(view.currentIndex().row()).name == "c"


def test_a_row_that_went_takes_its_mark_with_it(view):
    model = listed(["a", "b", "c"])
    view.setModel(model)
    mark(view, model, "a", "b")

    model.reconcile([row("a"), row("c")])

    assert marked(view, model) == ["a"]
    assert model.row_of("b") == -1


def test_an_unchanged_listing_changes_nothing():
    model = listed(["a", "b"])
    seen = []
    model.layoutChanged.connect(lambda: seen.append("layout"))
    model.modelReset.connect(lambda: seen.append("reset"))
    assert model.reconcile([row("b"), row("a")]) is False
    assert seen == []


def test_a_file_saved_again_counts_as_a_change():
    model = listed(["a"])
    assert model.reconcile([row("a", size=99)]) is True
    assert model.entry(model.row_of("a")).size == 99


def test_the_filter_and_the_sort_survive_a_reconcile():
    model = listed(["plan.dwg", "notes.txt"])
    model.set_filter("*.dwg")
    model.reconcile([row("plan.dwg"), row("notes.txt"), row("site.dwg")])
    shown = [model.entry(r).name for r in range(model.rowCount()) if model.entry(r)]
    assert shown == ["plan.dwg", "site.dwg"]
    assert set(model.names()) == {"plan.dwg", "notes.txt", "site.dwg"}


def test_a_reconcile_never_resets_the_model():
    """A reset is what throws the scroll position away."""
    model = listed(["a"])
    resets = []
    model.modelReset.connect(lambda: resets.append(True))
    model.reconcile([row("a"), row("b")])
    assert resets == []


# ------------------------------------------------------------------- the pane


@pytest.fixture
def pane():
    bridge = FakeBridge()
    config = Config({"left.path": "C:\\Jobs"})
    core = Pane(bridge, config, "left")
    core.refresh()
    bridge.answer(bridge.listings()[-1], [row("a"), row("b")])
    return core, bridge, config


def test_a_check_waits_until_it_is_due(pane):
    core, bridge, _ = pane
    before = len(bridge.sent)
    assert core.check(now=0.0) is False
    assert len(bridge.sent) == before
    assert core.check(now=core.current.check_after + 0.01) is True
    assert bridge.sent[-1]["op"] is Op.LIST


def test_a_check_that_finds_a_new_file_shows_it(pane):
    core, bridge, _ = pane
    core.check(now=1e12)
    bridge.answer(bridge.listings()[-1], [row("a"), row("b"), row("new.txt")])
    assert core.current.model.row_of("new.txt") > 0
    assert core.current.status_state == IDLE
    assert "3" in core.current.status_text


def test_a_check_that_finds_nothing_says_nothing(pane):
    core, bridge, _ = pane
    core.current.status_text = "copied 3 item(s)"
    core.check(now=1e12)
    bridge.answer(bridge.listings()[-1], [row("b"), row("a")])
    assert core.current.status_text == "copied 3 item(s)"


def test_a_check_that_fails_keeps_the_rows_and_backs_off(pane):
    core, bridge, _ = pane
    core.current.status_text = "2 files"
    core.check(now=1e12)
    bridge.answer(bridge.listings()[-1], status=Status.TIMEOUT)
    assert core.current.model.row_of("a") > 0
    assert core.current.status_text == "2 files"
    import time
    assert core.current.check_after >= time.monotonic() + CHECK_FAILURE_WAIT - 1


def test_a_check_never_stands_in_front_of_a_listing_somebody_asked_for(pane):
    core, bridge, _ = pane
    core.navigate("C:\\Other")
    before = len(bridge.sent)
    assert core.check(now=1e12) is False
    assert len(bridge.sent) == before


def test_navigating_cancels_a_check_in_flight(pane):
    core, bridge, _ = pane
    core.check(now=1e12)
    check = bridge.listings()[-1]
    assert core.busy is False
    core.navigate("C:\\Other")
    assert check in bridge.cancelled


def test_a_refresh_of_the_same_folder_is_reconciled(pane):
    core, bridge, _ = pane
    resets = []
    core.current.model.modelReset.connect(lambda: resets.append(True))
    core.refresh()
    assert core.busy is True
    bridge.answer(bridge.listings()[-1], [row("a"), row("b"), row("c")])
    assert resets == []
    assert core.current.model.row_of("c") > 0


def test_a_new_folder_still_streams_into_an_empty_model(pane):
    core, bridge, _ = pane
    core.navigate("C:\\Other")
    assert core.current.model.row_of("a") == -1


def test_a_slow_folder_is_checked_less_often(pane):
    core, _, config = pane
    import time
    core._schedule(core.current, 4.0, ok=True)
    assert core.current.check_after - time.monotonic() > 30


def test_zero_turns_checks_off(pane):
    core, bridge, config = pane
    config.set("refresh.local_seconds", 0)
    before = len(bridge.sent)
    assert core.check(now=1e12) is False
    assert len(bridge.sent) == before


def test_a_minimised_window_checks_nothing(pane):
    core, bridge, _ = pane
    core.set_live(False)
    assert core.check(now=1e12) is False
    core.set_live(True)
    assert core.check(now=1e12) is True


def test_a_share_is_checked_on_its_own_setting(pane):
    core, _, config = pane
    config.set("refresh.local_seconds", 1.0)
    config.set("refresh.network_seconds", 9.0)
    core.current.path = "\\\\tsclient\\C\\PLC"
    assert core._check_interval(core.current) == 9.0


# ------------------------------------------------------------------- dragging


def test_rows_drag_out_as_file_urls_and_the_parent_row_does_not():
    model = listed(["plan.dwg", "notes.txt"])
    assert not model.flags(model.index(0, 0)) & Qt.ItemIsDragEnabled
    first = model.index(model.row_of("plan.dwg"), 0)
    assert model.flags(first) & Qt.ItemIsDragEnabled

    # A row selection hands over every column of each row; one file each.
    data = model.mimeData([first, model.index(first.row(), 2)])
    assert len(data.urls()) == 1
    assert data.urls()[0].isLocalFile()
    assert data.urls()[0].toLocalFile().endswith("plan.dwg")


def test_a_drag_is_only_ever_a_copy():
    assert listed(["a"]).supportedDragActions() == Qt.CopyAction


def pane_widget(tmp_path):
    from PySide6.QtCore import QObject, Signal

    from app.core.pane import Pane as CorePane
    from app.theme import sheet
    from app.ui.pane import PaneWidget

    class Volumes(QObject):
        changed = Signal()
        drives = []

        def letter_for(self, path):
            return None

        def refresh(self, *, rescan=False):
            pass

    config = Config({"left.path": "C:\\Jobs"}, str(tmp_path / "config.json"))
    return PaneWidget(CorePane(FakeBridge(), config, "left"), Volumes(),
                      sheet.metrics("normal"), None)


def test_a_change_while_the_rename_dialog_is_open_renames_the_right_file(
        tmp_path, monkeypatch):
    """The folder is live now, so a dialog is no longer a pause: rows can move
    under it. The rename has to find its file again by name."""
    from app.ui import dialogs

    widget = pane_widget(tmp_path)
    core = widget._pane
    model = core.current.model
    model.begin(has_parent=False)
    model.add([row("m.txt"), row("z.txt")])
    model.finish()
    widget._view.setCurrentIndex(model.index(model.row_of("m.txt"), 0))

    def ask_name(parent, **kwargs):
        model.reconcile([row("a.txt"), row("b.txt"), row("m.txt"), row("z.txt")])
        return "renamed.txt"

    renamed = []
    monkeypatch.setattr(dialogs, "ask_name", ask_name)
    monkeypatch.setattr(core, "rename", lambda r, name: renamed.append(model.entry(r).name))
    widget.rename_current()
    assert renamed == ["m.txt"]
    widget.deleteLater()


def test_both_views_drag_out_and_neither_accepts_a_drop(tmp_path):
    widget = pane_widget(tmp_path)
    for view in (widget._view, widget._grid):
        assert view.dragEnabled()
        assert view.dragDropMode() == QAbstractItemView.DragOnly
    widget.deleteLater()

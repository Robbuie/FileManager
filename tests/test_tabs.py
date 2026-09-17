"""Checks on a pane's tabs: the things that go wrong silently.

Tabs are cheap to get almost right. The failures worth catching are the ones
that leave the window looking correct while it acts on the wrong thing:

  * a drag in the strip reorders the widget and not the pane, so afterwards
    every index -- the one a click selects, the one a close button reports --
    names a different tab than the one under it;
  * a closed tab whose listing is still in flight, which holds a volume's
    worker for a folder nobody is looking at;
  * a locked tab that quietly navigates anyway, which is the whole point of it
    gone;
  * a session that comes back with the wrong tab in front, or does not come
    back at all because one stored entry was malformed.
"""

from __future__ import annotations

import pytest

from app.core.config import Config
from app.core.pane import MAX_HISTORY, MAX_TABS, Pane
from app.io.protocol import Entry, Op, Reply, Status


class FakeBridge:
    """Records what was asked, and what was withdrawn."""

    def __init__(self):
        self.sent = []
        self.cancelled = []
        self.forgotten = []

    def submit(self, op, path, *, timeout, on_reply, args=None):
        self.sent.append({"op": op, "path": path, "args": dict(args or {}),
                          "handler": on_reply})
        return len(self.sent)

    def cancel(self, request_id):
        self.cancelled.append(request_id)

    def forget(self, request_id):
        self.forgotten.append(request_id)

    def retry(self, path):
        pass

    def listings(self):
        return [entry["path"] for entry in self.sent if entry["op"] is Op.LIST]

    def answer(self, request_id, rows=()):
        """Settle one request, the way a worker eventually does."""
        entry = self.sent[request_id - 1]
        entry["handler"](Reply(request_id, Status.OK, payload=list(rows)))


def row(name, is_dir=True):
    return Entry(name=name, is_dir=is_dir, size=0, mtime=0.0, attributes=0)


@pytest.fixture
def pane():
    bridge = FakeBridge()
    config = Config({"left.path": "C:\\Jobs"})
    return Pane(bridge, config, "left"), bridge, config


# ------------------------------------------------------------------ ordering

def test_a_drag_in_the_strip_reorders_the_pane_too(pane):
    """The failure this catches is a silent one: without `move_tab` the widget
    and the pane disagree, and the disagreement only shows up as the wrong tab
    closing some time later.
    """
    core, _, _ = pane
    core.open_tab("C:\\A")
    core.open_tab("C:\\B")
    assert [tab.path for tab in core.tabs] == ["C:\\Jobs", "C:\\A", "C:\\B"]

    core.move_tab(2, 0)
    assert [tab.path for tab in core.tabs] == ["C:\\B", "C:\\Jobs", "C:\\A"]


def test_a_drag_keeps_the_same_tab_in_front(pane):
    core, _, _ = pane
    core.open_tab("C:\\A")
    core.open_tab("C:\\B")
    core.select_tab(1)                     # C:\A
    core.move_tab(0, 2)                    # C:\Jobs to the end
    assert core.current.path == "C:\\A"
    assert core.index == 0


def test_a_move_that_goes_nowhere_changes_nothing(pane):
    core, _, _ = pane
    core.open_tab("C:\\A")
    core.move_tab(1, 1)
    core.move_tab(0, 9)
    assert [tab.path for tab in core.tabs] == ["C:\\Jobs", "C:\\A"]


# -------------------------------------------------------------------- closing

def test_closing_a_tab_withdraws_its_listing(pane):
    """A 50,000-row listing over SMB holds that volume's worker. Closing the
    tab that asked for it has to take it back, not merely stop caring.
    """
    core, bridge, _ = pane
    core.open_tab("C:\\Slow")
    in_flight = core.tabs[1].request_id
    assert in_flight is not None

    core.close_tab(1)
    assert in_flight in bridge.cancelled


def test_the_last_tab_does_not_close(pane):
    core, _, _ = pane
    core.close_tab(0)
    assert len(core.tabs) == 1


def test_close_others_keeps_the_locked_ones(pane):
    """The command most likely to be reached for by accident, and the tab
    somebody pinned to a job folder is the one they would least like to lose.
    """
    core, _, _ = pane
    core.open_tab("C:\\Keep")
    core.open_tab("C:\\Go")
    core.set_locked(1, True)
    core.close_others(0)
    assert [tab.path for tab in core.tabs] == ["C:\\Jobs", "C:\\Keep"]


def test_close_to_the_right_leaves_everything_left_of_it(pane):
    core, _, _ = pane
    for path in ("C:\\A", "C:\\B", "C:\\C"):
        core.open_tab(path)
    core.close_to_right(1)
    assert [tab.path for tab in core.tabs] == ["C:\\Jobs", "C:\\A"]


def test_a_locked_tab_refuses_to_close(pane):
    core, _, _ = pane
    core.open_tab("C:\\Pinned")
    core.set_locked(1, True)
    core.close_tab(1)
    assert len(core.tabs) == 2


# --------------------------------------------------------------------- locking

def test_a_locked_tab_opens_a_new_one_rather_than_moving(pane):
    core, _, _ = pane
    core.set_locked(0, True)
    core.navigate("C:\\Elsewhere")
    assert core.tabs[0].path == "C:\\Jobs"
    assert core.tabs[1].path == "C:\\Elsewhere"
    assert core.current.path == "C:\\Elsewhere"


def test_the_tab_a_locked_one_opens_is_not_itself_locked(pane):
    """Otherwise the second navigation opens a third tab, and so on: a lock
    that propagates is a tab per double click for the rest of the session.
    """
    core, _, _ = pane
    core.set_locked(0, True)
    core.navigate("C:\\One")
    core.navigate("C:\\Two")
    assert len(core.tabs) == 2
    assert core.tabs[1].path == "C:\\Two"


def test_a_locked_tab_still_refreshes(pane):
    """Refresh is the same path with the same folder, which is not a move."""
    core, bridge, _ = pane
    core.set_locked(0, True)
    before = len(core.tabs)
    core.refresh()
    assert len(core.tabs) == before
    assert bridge.listings()[-1] == "C:\\Jobs"


def test_a_locked_tab_offers_no_history(pane):
    core, _, _ = pane
    core.navigate("C:\\Down")
    assert core.current.can_go_back
    core.set_locked(0, True)
    assert not core.current.can_go_back


def test_history_stops_growing(pane):
    """Unbounded until 0.29.12, across up to `MAX_TABS` tabs.

    Not much memory on its own -- a path is a short string -- but it is the
    other list here that only ever grew, and 0.27 put a dropdown in front of
    it, so it is also a menu nobody can read.
    """
    core, _, _ = pane
    for index in range(MAX_HISTORY + 60):
        core.navigate(f"C:\\Folder{index}")

    tab = core.current
    assert len(tab.history) == MAX_HISTORY
    assert tab.position == len(tab.history) - 1
    assert tab.history[-1] == tab.path


def test_trimming_history_leaves_back_pointing_where_it_looks(pane):
    """`position` is an index into the list, so trimming the front without
    moving it would send Alt+Left to a folder somebody was never in.
    """
    core, _, _ = pane
    for index in range(MAX_HISTORY + 10):
        core.navigate(f"C:\\Folder{index}")

    tab = core.current
    expected = tab.history[tab.position - 1]
    core.go_back()
    assert core.current.path == expected


# ------------------------------------------------------------------ opening

def test_a_background_tab_lists_without_taking_the_pane_with_it(pane):
    """A middle click opens a folder behind. The rows are asked for anyway --
    a background tab that is empty until it is looked at makes switching to it
    feel slower than opening it did.
    """
    core, bridge, _ = pane
    core.open_tab("C:\\Behind", background=True)
    assert core.current.path == "C:\\Jobs"
    assert core.index == 0
    assert "C:\\Behind" in bridge.listings()


def test_a_duplicate_lands_on_the_same_folder(pane):
    core, _, _ = pane
    core.navigate("C:\\Jobs\\2026")
    core.duplicate_tab()
    assert [tab.path for tab in core.tabs] == ["C:\\Jobs\\2026", "C:\\Jobs\\2026"]


def test_tabs_stop_at_the_cap(pane):
    core, _, _ = pane
    for index in range(MAX_TABS + 10):
        core.open_tab(f"C:\\{index}")
    assert len(core.tabs) == MAX_TABS


def test_cycling_wraps_in_both_directions(pane):
    core, _, _ = pane
    core.open_tab("C:\\A")
    core.open_tab("C:\\B")
    core.select_tab(2)
    core.cycle_tab(1)
    assert core.index == 0
    core.cycle_tab(-1)
    assert core.index == 2


def test_cycling_one_tab_is_not_a_special_case(pane):
    core, _, _ = pane
    core.cycle_tab(1)
    assert core.index == 0


# ------------------------------------------------------------------- session

def test_the_tabs_come_back_where_they_were():
    bridge = FakeBridge()
    config = Config({"left.path": "C:\\Jobs"})
    first = Pane(bridge, config, "left")
    first.open_tab("C:\\A")
    first.open_tab("C:\\B")
    first.set_locked(1, True)
    first.select_tab(1)

    config.set("left.tabs", first.session())
    config.set("left.tab", first.index)

    second = Pane(FakeBridge(), config, "left")
    assert [tab.path for tab in second.tabs] == ["C:\\Jobs", "C:\\A", "C:\\B"]
    assert second.index == 1
    assert second.tabs[1].locked


def test_a_malformed_stored_tab_is_dropped_not_repaired():
    config = Config({"left.path": "C:\\Jobs",
                     "left.tabs": [{"path": "C:\\Good"}, {"path": ""}, 7,
                                   {"locked": True}, "C:\\Older"]})
    core = Pane(FakeBridge(), config, "left")
    assert [tab.path for tab in core.tabs] == ["C:\\Good", "C:\\Older"]


def test_an_empty_session_falls_back_to_the_stored_path():
    config = Config({"left.path": "C:\\Jobs", "left.tabs": []})
    core = Pane(FakeBridge(), config, "left")
    assert [tab.path for tab in core.tabs] == ["C:\\Jobs"]


def test_a_stored_index_past_the_end_lands_on_a_real_tab():
    config = Config({"left.path": "C:\\Jobs",
                     "left.tabs": [{"path": "C:\\A"}], "left.tab": 9})
    core = Pane(FakeBridge(), config, "left")
    assert core.index == 0


# ---------------------------------------------------- the assumption underneath
#
# The pane is connected to both of `QTabBar`'s ordering signals, and which one
# arrives first decides whether the fix-up works. `tabMoved` first means
# `move_tab` sees the order as it was and the `currentChanged` that follows is
# already satisfied; the other way round, `select_tab` would move the pane to
# whatever sat at the target index in the *old* order and `move_tab` would then
# preserve the wrong tab. Reversed, both features look fine in isolation and
# the pane quietly shows the wrong folder after a drag -- so the order is
# asserted rather than assumed.


def test_qt_still_reports_a_moved_tab_before_it_reports_the_selection():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QTabBar

    assert QApplication.instance() is not None
    bar = QTabBar()
    for name in ("A", "B", "C"):
        bar.addTab(name)
    bar.setCurrentIndex(0)

    order = []
    bar.currentChanged.connect(lambda index: order.append("currentChanged"))
    bar.tabMoved.connect(lambda source, target: order.append("tabMoved"))
    bar.moveTab(0, 2)

    assert order == ["tabMoved", "currentChanged"]
    assert bar.currentIndex() == 2


def test_the_pane_agrees_with_the_bar_after_a_drag():
    """The two signals played back in the order Qt sends them, against a real
    pane -- which is the whole of what a drag does to it.
    """
    pytest.importorskip("PySide6")
    core = Pane(FakeBridge(), Config({"left.path": "C:\\Jobs"}), "left")
    core.open_tab("C:\\A")
    core.open_tab("C:\\B")
    core.select_tab(0)                      # C:\Jobs is in front

    core.move_tab(0, 2)                     # tabMoved, then
    core.select_tab(2)                      # currentChanged with the bar's index

    assert [tab.path for tab in core.tabs] == ["C:\\A", "C:\\B", "C:\\Jobs"]
    assert core.index == 2
    assert core.current.path == "C:\\Jobs"


# ------------------------------------------------------- the gestures, on a bar
#
# Two mouse gestures reach the tab strip through an event filter rather than
# through a signal, which means nothing about them is visible in the widget's
# connections and a rename of the filter would take them out silently.


class FakeVolumes:
    """Enough of `core.Volumes` for a pane widget to draw: a list and a signal."""

    def __init__(self):
        from PySide6.QtCore import QObject, Signal

        class Emitter(QObject):
            changed = Signal()

        self._emitter = Emitter()
        self.changed = self._emitter.changed
        self.drives = []

    def letter_for(self, path):
        return None


def widget_for(core):
    from app.theme import sheet
    from app.ui.pane import PaneWidget

    return PaneWidget(core, FakeVolumes(), sheet.metrics("normal"))


def double_click(bar, point):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    where = QPointF(point)
    event = QMouseEvent(QEvent.MouseButtonDblClick, where, where,
                        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(bar, event)


def test_double_clicking_the_empty_strip_opens_a_tab(pane):
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QPoint

    core, _, _ = pane
    widget = widget_for(core)
    widget.resize(700, 400)
    strip = widget._tabs
    beyond = strip.tabRect(strip.count() - 1).right() + 40

    double_click(strip, QPoint(beyond, strip.height() // 2))
    assert len(core.tabs) == 2
    assert core.tabs[1].path == core.tabs[0].path


def test_double_clicking_a_tab_itself_does_not(pane):
    """Double-clicking a tab is how a lot of people expect to rename one.
    Opening a tab there would be a surprise on the gesture most likely to be
    made by accident.
    """
    pytest.importorskip("PySide6")

    core, _, _ = pane
    widget = widget_for(core)
    widget.resize(700, 400)
    strip = widget._tabs

    double_click(strip, strip.tabRect(0).center())
    assert len(core.tabs) == 1

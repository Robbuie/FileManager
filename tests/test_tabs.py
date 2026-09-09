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
from app.core.pane import MAX_TABS, Pane
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

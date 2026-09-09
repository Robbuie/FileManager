"""Checks on which pane the window thinks is the active one.

Nearly every command in the window begins with "the pane that has focus":
Ctrl+D saves its folder, F5 copies out of it, Ctrl+L edits its path, the
accent border says which one it is. So getting that wrong is not one bug, it
is every one of those at once -- and it is quiet, because Tab still works and
Tab is what a session testing by keyboard uses.

Which is exactly how it got out: `PaneWidget.focusInEvent` never fires. A
`QFrame` does not take focus itself, its listing or its path bar does, so the
only thing that ever moved the active pane was the Tab key doing it
explicitly. Clicking into the other pane changed nothing.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QObject, Qt, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from app.core.capacity import Capacity  # noqa: E402
from app.core.config import Config  # noqa: E402
from app.core.favorites import Favorites  # noqa: E402
from app.core.pane import Pane  # noqa: E402
from app.core.transfers import TransferQueue  # noqa: E402
from app.ui import dialogs  # noqa: E402
from app.ui.window import MainWindow  # noqa: E402

LEFT = "C:\\Jobs"
RIGHT = "D:\\Archive"


class FakeBridge:
    def __init__(self):
        self.sent = []

    def submit(self, op, path, *, timeout, on_reply, args=None):
        self.sent.append((op, path))
        return len(self.sent)

    def cancel(self, request_id):
        pass

    def forget(self, request_id):
        pass

    def retry(self, path):
        pass


class FakeVolumes(QObject):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.drives = []

    def letter_for(self, path):
        return None

    def refresh(self, *, rescan=False):
        pass


@pytest.fixture
def window(tmp_path):
    """A real window over fake io. Nothing here touches a filesystem."""
    config = Config({"left.path": LEFT, "right.path": RIGHT},
                    str(tmp_path / "config.json"))
    bridge = FakeBridge()
    made = MainWindow(config, Pane(bridge, config, "left"),
                      Pane(bridge, config, "right"), FakeVolumes(),
                      TransferQueue(), None, Favorites(config),
                      Capacity(bridge, config))
    made.resize(1200, 600)
    made.show()
    QApplication.processEvents()
    yield made
    made.hide()


def test_clicking_into_the_other_panes_listing_makes_it_the_active_one(window):
    """The bug, in the words it was reported in: save-to-favorites only ever
    saw the left pane."""
    assert window._current_pane().current.path == LEFT
    window._widgets[1]._view.setFocus(Qt.MouseFocusReason)
    QApplication.processEvents()
    assert window._current_pane().current.path == RIGHT


def test_the_path_bar_counts_as_being_in_that_pane(window):
    """The field is behind the breadcrumb now, so this goes through the same
    door Ctrl+L does. It is still the same question: a pane whose path is
    being edited is the pane the window should think is active."""
    window._widgets[1].focus_path()
    QApplication.processEvents()
    assert window._current_pane().current.path == RIGHT


def test_clicking_a_crumb_in_the_other_pane_makes_it_the_active_one(window):
    """A borderless control takes no focus, and the window works out the
    active pane from where the focus went -- so every one of them has to say
    it was used, or it walks a pane while the keyboard still talks to the
    other. Same failure as the reported bug, different first cause."""
    widget = window._widgets[1]
    widget._crumbs.navigate.emit("D:\\Archive\\2025")
    QApplication.processEvents()
    assert window._current_pane() is window._panes[1]


def test_the_nav_buttons_claim_their_pane_too(window):
    """Up in the pane that is not active must not walk the one that is."""
    window._widgets[1]._up.click()
    QApplication.processEvents()
    assert window._current_pane() is window._panes[1]


def test_the_tab_strip_counts_too(window):
    window._widgets[1]._tabs.setFocus(Qt.MouseFocusReason)
    QApplication.processEvents()
    assert window._current_pane().current.path == RIGHT


def test_the_border_follows_it(window):
    """The accent on the border is the only thing on screen that says where
    the next keystroke lands, so it has to agree."""
    window._widgets[1]._view.setFocus(Qt.MouseFocusReason)
    QApplication.processEvents()
    assert window._widgets[1].property("active") == "true"
    assert window._widgets[0].property("active") == "false"


def test_focus_leaving_for_something_that_is_not_a_pane_changes_nothing(window):
    """A dialog, the menu bar, the transfer queue. Somebody who opens one and
    closes it again expects to be where they were."""
    window._widgets[1]._view.setFocus(Qt.MouseFocusReason)
    QApplication.processEvents()
    stray = QWidget()
    stray.setFocusPolicy(Qt.StrongFocus)
    stray.show()
    stray.setFocus(Qt.OtherFocusReason)
    QApplication.processEvents()
    assert window._current_pane().current.path == RIGHT
    stray.hide()


def test_tab_still_switches_panes(window):
    window._switch_pane()
    QApplication.processEvents()
    assert window._current_pane().current.path == RIGHT


def test_ctrl_d_saves_the_folder_of_the_pane_with_focus(window, monkeypatch):
    """The whole bug, end to end, through the command that reported it."""
    monkeypatch.setattr(dialogs, "ask_name",
                        lambda *args, **kwargs: "Archive")
    window._widgets[1]._view.setFocus(Qt.MouseFocusReason)
    QApplication.processEvents()
    window._add_favorite()
    assert [(e.name, e.path) for e in window._favorites.entries] == \
        [("Archive", RIGHT)]


def test_a_favorite_opens_in_the_pane_with_focus(window):
    window._favorites.add("Jobs", LEFT)
    window._widgets[1]._view.setFocus(Qt.MouseFocusReason)
    QApplication.processEvents()
    window._go_to_favorite(LEFT)
    assert window._panes[1].current.path == LEFT
    assert window._panes[0].current.path == LEFT   # it was already there


# --------------------------------------------------------------------- rail


def test_the_rail_sends_places_to_the_pane_that_has_the_keyboard(window):
    """The rail is one widget for two panes, so where a click lands is the
    whole question -- and it is answered by the pane that is already active
    rather than by anything the rail knows."""
    window._widgets[1]._view.setFocus(Qt.MouseFocusReason)
    QApplication.processEvents()
    window._rail.chosen.emit("E:\\Elsewhere", False)
    assert window._panes[1].current.path == "E:\\Elsewhere"
    assert window._panes[0].current.path == LEFT


def test_clicking_in_the_rail_does_not_change_which_pane_is_active(window):
    """Nothing in the rail takes focus, and that is the point: a rail that
    took the keyboard would leave the *next* click going wherever the last one
    left things. Same failure as 0.10's and 0.11's, from a third direction."""
    window._widgets[1]._view.setFocus(Qt.MouseFocusReason)
    QApplication.processEvents()
    rows = window._rail._elidable
    if rows:
        rows[0][0].click()
        QApplication.processEvents()
    assert window._current_pane() is window._panes[1]


def test_a_middle_click_in_the_rail_opens_a_tab_in_that_same_pane(window):
    window._widgets[1]._view.setFocus(Qt.MouseFocusReason)
    QApplication.processEvents()
    before = len(window._panes[1].tabs)
    window._rail.chosen.emit("E:\\Elsewhere", True)
    assert len(window._panes[1].tabs) == before + 1
    assert len(window._panes[0].tabs) == 1


def test_the_rail_marks_the_folder_the_active_pane_is_on(window):
    window._favorites.add("Archive", RIGHT)
    window._widgets[1]._view.setFocus(Qt.MouseFocusReason)
    QApplication.processEvents()
    marked = [button.property("target")
              for button, _label in window._rail._elidable
              if button.property("state") == "current"]
    assert marked == [RIGHT]


def test_ctrl_b_hides_the_rail_and_remembers_that_it_is_hidden(window):
    window._toggle_rail(False)
    assert not window._rail.isVisible()
    assert window._config.get("rail.shown") is False
    window._toggle_rail(True)
    assert window._rail.isVisible()


def test_the_rail_does_not_put_a_floor_under_the_window(window):
    """The favorites bar's bug, at window scale: a rail full of long names
    must not stop the window being made narrow."""
    for index in range(8):
        window._favorites.add(f"A rather long saved folder name {index}",
                              f"C:\\Jobs\\{index}")
    QApplication.processEvents()
    window.resize(820, 600)
    QApplication.processEvents()
    assert window.width() == 820

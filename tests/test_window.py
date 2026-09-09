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
                      TransferQueue(), None, Favorites(config))
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

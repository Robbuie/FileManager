"""The Tools menu end of 0.17: the keys, the request, and the two panes marked.

These build a real window, which is expensive and is the point. 0.16 shipped a
grid that looked finished in every render and answered no keys, because the
commands went through shared state and kept working while the *keys* went
through a filter that named one widget. An external command is nothing but a
key, so the only test that covers it is one that presses it -- in both views,
which is the half that was missing last time.

What is deliberately not here: whether the program starts. That is
`worker.locate` and a real machine, and it is the harness's job.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QObject, Qt, Signal  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.core import commands as core_commands  # noqa: E402
from app.core.config import Config  # noqa: E402
from app.io.protocol import Entry, Op, Reply, Status  # noqa: E402


class FakeBridge:
    def __init__(self):
        self.sent = []

    def submit(self, op, path, *, timeout, on_reply, args=None):
        self.sent.append({"op": op, "path": path, "args": dict(args or {}),
                          "timeout": timeout, "handler": on_reply})
        return len(self.sent)

    def cancel(self, request_id):
        pass

    def forget(self, request_id):
        pass

    def answer(self, index, payload, status=Status.OK):
        self.sent[index]["handler"](Reply(index + 1, status, payload=payload))


def entry(name, *, mtime=100.0, size=2048, is_dir=False):
    return Entry(name=name, is_dir=is_dir, size=size, mtime=mtime, attributes=0)


def fill(pane, rows):
    """`pane` is a `Pane`; `pane.current` is the tab in front."""
    model = pane.current.model
    model.begin(has_parent=False)
    model.add(list(rows))
    model.finish()


@pytest.fixture
def window(tmp_path):
    from app.core.capacity import Capacity
    from app.core.commands import Commands
    from app.core.favorites import Favorites
    from app.core.pane import Pane
    from app.core.transfers import TransferQueue
    from app.ui.window import MainWindow

    class Volumes(QObject):
        changed = Signal()
        drives = []

        def letter_for(self, path):
            return None

        def refresh(self, *, rescan=False):
            pass

    config = Config({"left.path": "C:\\Jobs", "right.path": "D:\\Archive"},
                    str(tmp_path / "config.json"))
    bridge = FakeBridge()
    made = MainWindow(
        config,
        Pane(bridge, config, "left"),
        Pane(bridge, config, "right"),
        Volumes(), TransferQueue(), None, Favorites(config),
        Capacity(bridge, config), Commands(bridge, config))
    made.resize(1200, 600)
    made.show()
    QApplication.processEvents()
    made.bridge = bridge
    yield made
    made.hide()


def press(widget, key, modifiers=Qt.NoModifier, text=""):
    """Send a key the way a keyboard does: to the view, through the filter.

    Not to the pane. `QAbstractItemView` answers several of these itself, and a
    test that posts straight to the pane proves the handler works while saying
    nothing about whether the key ever reaches it -- which is exactly the shape
    of the bug 0.16 shipped.
    """
    event = QKeyEvent(QKeyEvent.KeyPress, key, modifiers, text)
    QApplication.sendEvent(widget, event)


# --------------------------------------------------------------- the keys

def test_f9_in_the_listing_asks_for_the_terminal(window) -> None:
    widget = window._widgets[0]
    seen = []
    widget.commandRequested.connect(seen.append)
    press(widget._view, Qt.Key_F9)
    assert seen == ["terminal"]


def test_f9_in_the_grid_asks_for_the_same_thing(window) -> None:
    """The 0.16 lesson, applied before it can be repeated: a second view
    inherits the commands and not the keys unless somebody checks.
    """
    widget = window._widgets[0]
    widget.set_view_mode("grid")
    QApplication.processEvents()
    seen = []
    widget.commandRequested.connect(seen.append)
    press(widget._grid, Qt.Key_F9)
    assert seen == ["terminal"]


def test_a_modified_function_key_beats_the_bare_one(window) -> None:
    """Ctrl+F2 is the compare tool and F2 is still rename, which only works
    because the table is matched before the built-in function keys.
    """
    widget = window._widgets[0]
    seen = []
    widget.commandRequested.connect(seen.append)
    press(widget._view, Qt.Key_F2, Qt.ControlModifier)
    assert seen == ["compare"]


def test_a_command_key_typed_into_the_path_bar_is_not_a_command(window) -> None:
    """The guard every function key in this application sits behind."""
    widget = window._widgets[0]
    widget.focus_path()
    QApplication.processEvents()
    seen = []
    widget.commandRequested.connect(seen.append)
    press(widget._path, Qt.Key_F9)
    assert seen == []


def test_a_pane_with_no_table_answers_its_own_keys_only(window) -> None:
    """What a preview render and a window built without a table get: the key
    does nothing, rather than raising on a pane that has no map.
    """
    widget = window._widgets[0]
    widget.set_command_keys({})
    seen = []
    widget.commandRequested.connect(seen.append)
    press(widget._view, Qt.Key_F9)
    assert seen == []


def test_the_built_in_function_keys_still_work_with_a_table_loaded(window) -> None:
    """The order in `keyPressEvent` is the whole of this: the table is matched
    first, so an F5 that is not in the table has to fall through to the copy.
    """
    widget = window._widgets[0]
    asked = []
    widget.transferRequested.connect(asked.append)
    press(widget._view, Qt.Key_F5)
    assert asked == ["copy"]


# ------------------------------------------------------------- the request

def test_running_a_command_sends_it_to_the_folder_the_pane_is_on(window) -> None:
    fill(window._panes[0], [entry("one.txt")])
    window._on_command("terminal")
    sent = window.bridge.sent[-1]
    assert sent["op"] is Op.RUN
    assert sent["args"]["program"] == "powershell.exe"
    assert sent["args"]["working"] == "C:\\Jobs"


def test_the_two_pane_compare_gets_both_folders(window) -> None:
    window._on_command("compare")
    sent = window.bridge.sent[-1]
    assert sent["args"]["arguments"] == ["C:\\Jobs", "D:\\Archive"]


def test_a_refused_command_sends_nothing_and_says_why(window) -> None:
    before = len(window.bridge.sent)
    window._on_command("edit")           # %F, and nothing is under the cursor
    assert len(window.bridge.sent) == before
    assert window.statusBar().currentMessage() != ""


def test_a_command_that_could_not_start_reaches_the_status_line(window) -> None:
    window._on_command("terminal")
    window.bridge.answer(len(window.bridge.sent) - 1, None, status=Status.ERROR)
    assert window.statusBar().currentMessage() != ""


def test_the_key_map_follows_an_edit(window) -> None:
    """Both panes, immediately. A table saved but not handed down is a key
    that works after a restart and not before it, which reads as a bug.
    """
    table = [core_commands.renamed(command, shortcut="Ctrl+F10")
             if command.id == "terminal" else command
             for command in window._commands.commands]
    window._commands.replace(table)
    for widget in window._widgets:
        assert widget._command_keys.get("Ctrl+F10") == "terminal"
        assert "F9" not in widget._command_keys


# ------------------------------------------------------------- the compare

def test_comparing_the_panes_marks_both_sides(window) -> None:
    fill(window._panes[0], [
        entry("only-here.txt"), entry("newer.txt", mtime=500.0),
        entry("same.txt")])
    fill(window._panes[1], [
        entry("newer.txt"), entry("same.txt"), entry("only-there.txt")])
    window._compare_panes()
    QApplication.processEvents()
    assert set(window._widgets[0].selected_names()) == {"only-here.txt", "newer.txt"}
    assert set(window._widgets[1].selected_names()) == {"only-there.txt"}


def test_comparing_replaces_whatever_was_marked_before(window) -> None:
    """Otherwise the second press marks the union of two answers, and the
    selection stops meaning "what would have to be copied".
    """
    fill(window._panes[0], [entry("a.txt"), entry("b.txt")])
    fill(window._panes[1], [entry("a.txt"), entry("b.txt")])
    window._widgets[0].select_all()
    window._compare_panes()
    QApplication.processEvents()
    assert window._widgets[0].selected_names() == []


def test_the_answer_lands_in_the_status_line(window) -> None:
    fill(window._panes[0], [entry("a.txt")])
    fill(window._panes[1], [])
    window._compare_panes()
    assert "only here" in window.statusBar().currentMessage()


def test_comparing_two_empty_panes_says_so_rather_than_nothing(window) -> None:
    window._compare_panes()
    assert "No differences" in window.statusBar().currentMessage()


# ----------------------------------------------------------------- the menu

def test_every_shown_command_is_on_the_menu(window) -> None:
    labels = [action.text() for action in window._tools_menu.actions()]
    for command in window._commands.visible():
        assert any(command.name in label for label in labels)


def test_the_menu_shows_a_key_without_claiming_it(window) -> None:
    """Every one of them is the pane's. A window shortcut on F4 would take the
    key from the path bar, which is the rule the function keys already follow.
    """
    for action in window._tools_menu.actions():
        if "PowerShell" in action.text():
            assert action.shortcut().isEmpty()
            assert "F9" in action.text()
            return
    raise AssertionError("the terminal command is not on the menu")


def test_the_menu_is_rebuilt_without_leaving_actions_behind(window) -> None:
    """The favourites menu's rule: a shortcut action remade on every rebuild
    stays alive on the window, and Qt then honours none of the copies.
    """
    before = window._compare_action
    window._commands.replace(window._commands.commands)
    assert window._compare_action is before
    claiming = [action for action in window.actions()
                if action.shortcut().toString() == "Ctrl+Shift+F2"]
    assert len(claiming) <= 1


# ----------------------------------------------------- the mouse side buttons
#
# Reported from the window on 15 September: the thumb buttons did nothing. They
# were handled nowhere -- the history behind Alt+Left has always worked, and no
# widget in Qt answers `BackButton` by itself, so the events were simply
# dropped. These press the real buttons at the real widgets for the reason
# `press` above exists.

def click(widget, button, kind=None):
    """A mouse button at a widget, pressed and released where it started."""
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QMouseEvent

    where = QPointF(widget.rect().center())
    for which in ([kind] if kind else [QEvent.MouseButtonPress,
                                       QEvent.MouseButtonRelease]):
        QApplication.sendEvent(widget, QMouseEvent(
            which, where, widget.mapToGlobal(where), button, button,
            Qt.NoModifier))


def walked(window, pane_index=0):
    """Put two folders in a tab's history, so there is something to go back to."""
    pane = window._panes[pane_index]
    pane.navigate("C:\\Jobs\\24-118")
    QApplication.processEvents()
    return pane


def test_the_back_button_walks_the_tabs_own_history(window) -> None:
    pane = walked(window)
    assert pane.current.path == "C:\\Jobs\\24-118"
    click(window._widgets[0]._view.viewport(), Qt.BackButton)
    assert pane.current.path == "C:\\Jobs"


def test_the_forward_button_comes_back(window) -> None:
    pane = walked(window)
    click(window._widgets[0]._view.viewport(), Qt.BackButton)
    click(window._widgets[0]._view.viewport(), Qt.ForwardButton)
    assert pane.current.path == "C:\\Jobs\\24-118"


def test_the_side_buttons_work_in_the_grid_too(window) -> None:
    """0.16's lesson again: a second view answers the commands and not the
    gestures unless somebody presses them there.
    """
    pane = walked(window)
    widget = window._widgets[0]
    widget.set_view_mode("grid")
    QApplication.processEvents()
    click(widget._grid.viewport(), Qt.BackButton)
    assert pane.current.path == "C:\\Jobs"


def test_a_side_button_over_the_inactive_pane_walks_that_one(window) -> None:
    """And makes it active. A side button does not move the focus the way a
    click on a row does, so without the claim the window would keep sending
    every keystroke to the pane that used to have it.
    """
    right = walked(window, 1)
    window._set_active(0)
    click(window._widgets[1]._view.viewport(), Qt.BackButton)
    assert right.current.path == "D:\\Archive"
    assert window._active == 1


def test_a_side_press_does_not_clear_the_selection(window) -> None:
    """The reason the press is swallowed rather than only the release. Left to
    itself the view reads an unknown button as a click on a row.
    """
    from PySide6.QtCore import QEvent

    fill(window._panes[0], [entry("a.txt"), entry("b.txt")])
    widget = window._widgets[0]
    widget.select_all()
    marked = set(widget.selected_names())
    assert marked
    click(widget._view.viewport(), Qt.BackButton, kind=QEvent.MouseButtonPress)
    assert set(widget.selected_names()) == marked


def test_a_locked_tab_refuses_the_side_buttons(window) -> None:
    """`Tab.can_go_back` already decides this; what is checked here is that the
    mouse goes through it rather than around it.
    """
    pane = walked(window)
    pane.set_locked(pane.index, True)
    click(window._widgets[0]._view.viewport(), Qt.BackButton)
    assert pane.current.path == "C:\\Jobs\\24-118"


def test_the_other_mouse_buttons_are_left_alone(window) -> None:
    pane = walked(window)
    click(window._widgets[0]._view.viewport(), Qt.RightButton)
    assert pane.current.path == "C:\\Jobs\\24-118"

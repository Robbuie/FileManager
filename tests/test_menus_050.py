"""0.50: the application menu and the right-click menu, shorter, and the glass
looks.

The rule the menus were cleaned up under is that nothing goes away: every
command is still on a menu or in Ctrl+K, and every key still works. So these
tests check that, not only the new shape.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from app.core import options  # noqa: E402
from app.core.config import DEFAULTS  # noqa: E402
from app.theme import qss  # noqa: E402
from tests.test_options import window as _shown_window  # noqa: E402


@pytest.fixture
def window(tmp_path):
    """test_options' window, deleted afterwards rather than only hidden.

    Every window a test leaves behind is one more set of widgets the next
    `setStyleSheet` repolishes, and the theme tests later in the run were
    taking minutes each by the time they got there.
    """
    from PySide6.QtWidgets import QApplication

    made = _shown_window.__wrapped__(tmp_path)
    shown = next(made)
    yield shown
    try:
        next(made)
    except StopIteration:
        pass
    shown.deleteLater()
    QApplication.processEvents()


def _titles(menu_bar):
    return [action.text().replace("&", "") for action in menu_bar.actions()
            if not action.isSeparator()]


def test_six_menus_and_options(window) -> None:  # noqa: F811
    assert _titles(window.menuBar()) == ["File", "Edit", "Go", "View", "Tools",
                                         "Help", "Options..."]


def _submenu(window, title):
    for action in window.menuBar().actions():
        if action.menu() is not None and action.text().replace("&", "") == title:
            return action.menu()
    raise AssertionError(title)


def test_tabs_favorites_and_workspaces_are_under_go(window) -> None:  # noqa: F811
    go = _submenu(window, "Go")
    names = [a.text().replace("&", "") for a in go.actions() if a.menu() is not None]
    assert names == ["Tabs", "Favorites", "Workspaces"]


def test_the_view_menu_is_short(window) -> None:  # noqa: F811
    view = _submenu(window, "View")
    entries = [a for a in view.actions() if not a.isSeparator()]
    assert len(entries) < 20


def test_the_switches_are_still_found_by_the_palette(window) -> None:  # noqa: F811
    labels = {item.label for item in window.palette_sources() if item.kind == "command"}
    # The ones a test window enables -- overlays and the shell's menu are
    # greyed without their providers, and the palette leaves greyed ones out.
    for wanted in ("Animations", "Folder header", "Windows thumbnail handlers",
                   "Type badges instead of icons"):
        assert wanted in labels
    assert "Options" in labels


def test_every_shortcut_still_reaches_the_window(window) -> None:  # noqa: F811
    """A key on an action that is not on a shown menu and not on the window is
    a key that does nothing. Collected from every menu, submenus included."""
    owned = {action for action in window.actions()}
    missing = []

    def walk(menu):
        for action in menu.actions():
            if action.menu() is not None:
                walk(action.menu())
            elif not action.shortcut().isEmpty():
                reachable = action in owned or any(
                    w is window.menuBar() or getattr(w, "isVisibleTo", None)
                    for w in action.associatedObjects())
                if not reachable:
                    missing.append(action.text())

    walk(window.menuBar())
    assert not missing


def test_split_and_join_moved_to_file_more(window) -> None:  # noqa: F811
    more = window._file_more
    texts = [a.text() for a in more.actions()]
    assert "Split file..." in texts and "Join files..." in texts
    tools = [a.text() for a in window._tools_menu.actions()]
    assert "Split file..." not in tools


# --------------------------------------------------------------- glass

def test_the_glass_setting() -> None:
    assert DEFAULTS["window.glass"] == "mica"
    row = next(o for o in options.OPTIONS if o.key == "window.glass")
    assert {value for value, _ in row.choices} == {"mica", "acrylic", "frosted", "tinted"}
    assert row.restart
    assert DEFAULTS["menu.shell_inline"] is False
    assert not options.check()


def test_frosted_panes_let_the_desktop_through_and_nothing_else_does() -> None:
    assert qss.build("dark", backdrop="frosted")["pane_bg"].startswith("rgba(")
    assert qss.build("dark", backdrop="frosted")["backdrop"] == "transparent"
    assert qss.build("dark", backdrop="glass")["pane_bg"] == qss.build("dark")["bg_2"]
    assert qss.build("dark")["pane_bg"] == qss.build("dark")["bg_2"]

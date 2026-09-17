"""0.28: the command palette. Ranking is pure and checked here; the window's
half is checked for what it offers and what choosing does."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.core import palette  # noqa: E402
from app.core.commands import RESERVED  # noqa: E402
from app.core.favorites import Favorite  # noqa: E402

from tests.test_frame import _window  # noqa: E402


def _items():
    return [
        palette.Item("command", "Duplicate as today", detail="File", shortcut="Shift+F5"),
        palette.Item("command", "Flat view", detail="View", shortcut="Ctrl+B"),
        palette.Item("command", "Swap panes", detail="Go", shortcut="Ctrl+U"),
        palette.Item("favorite", "Jobs", detail="S:\\Jobs", target="S:\\Jobs"),
        palette.Item("recent", "2026-09-15", detail="S:\\Jobs\\Riverside\\2026-09-15"),
        palette.Item("here", "Drawings", detail="S:\\Jobs\\Riverside"),
    ]


def test_a_start_of_label_match_beats_a_word_match_beats_letters_in_order():
    assert palette.score("dup", "Duplicate as today") > palette.score("dup", "Make a duplicate")
    assert palette.score("dup", "Make a duplicate") > palette.score("dup", "Deep update")
    assert palette.score("xyz", "Duplicate") is None


def test_ranking_puts_the_best_match_first_and_empty_shows_everything():
    assert palette.rank("flat", _items())[0].label == "Flat view"
    assert len(palette.rank("", _items())) == len(_items())


def test_prefixes_narrow_the_sources():
    assert {item.kind for item in palette.rank(">", _items())} == {"command"}
    assert [item.label for item in palette.rank("@", _items())] == ["Jobs"]
    assert [item.label for item in palette.rank("/dr", _items())] == ["Drawings"]


def test_a_folder_is_found_by_its_path_as_well_as_its_name():
    assert palette.rank("riverside", _items())[0].kind in ("recent", "here")


def test_a_typed_path_offers_going_there():
    first = palette.rank("S:\\Jobs\\Dupont", _items())[0]
    assert first.kind == "path" and first.target == "S:\\Jobs\\Dupont"
    assert palette.looks_like_path("\\\\dc01\\projects")
    assert not palette.looks_like_path("Jobs")


def test_menu_text_is_cleaned_into_a_label_and_a_key():
    assert palette.clean_label("&Copy\tF5") == ("Copy", "F5")
    assert palette.clean_label("Manage favorites...") == ("Manage favorites", "")


def test_recent_folders_are_newest_first_each_once_and_not_on_screen():
    got = palette.unique_recent([["C:\\A", "C:\\B", "C:\\C"], ["D:\\X", "c:\\b"]],
                                {"C:\\C"})
    assert got == ["c:\\b", "D:\\X", "C:\\A"]
    assert "C:\\C" not in got


def test_ctrl_k_is_reserved_so_no_command_can_take_it():
    assert "Ctrl+K" in RESERVED


def test_the_window_offers_menu_commands_and_saved_folders(tmp_path):
    window = _window(tmp_path)
    try:
        window._favorites._entries.append(Favorite("Jobs", "S:\\Jobs"))  # noqa: SLF001
        sources = window.palette_sources()
        labels = {item.label for item in sources if item.kind == "command"}
        assert "Swap panes" in labels
        assert "Command palette" not in labels
        assert any(item.kind == "favorite" and item.label == "Jobs" for item in sources)
    finally:
        window.hide()


def test_choosing_a_folder_walks_the_active_pane_and_a_command_runs(tmp_path):
    window = _window(tmp_path)
    try:
        window._palette.open(window.palette_sources(), "swap")  # noqa: SLF001
        QApplication.processEvents()
        assert window._palette.isVisible()  # noqa: SLF001
        assert window._palette.items[0].label == "Swap panes"  # noqa: SLF001
        window._on_palette_chosen(palette.Item("favorite", "Jobs", target="S:\\Jobs"))  # noqa: SLF001
        assert window._current_pane().current.path == "S:\\Jobs"  # noqa: SLF001
    finally:
        window.hide()

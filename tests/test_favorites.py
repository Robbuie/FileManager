"""Checks on the saved locations.

Small enough to look obviously correct, which is why the tests are here: the
things that go wrong with a bookmark list are not in the reading and writing
but at the edges -- the same folder added twice under two names, a stored file
somebody hand-edited, an order that resets itself.
"""

from __future__ import annotations

import json
import os

import pytest

from app.core.config import Config
from app.core.favorites import MAX_FAVORITES, Favorites


@pytest.fixture
def favorites(tmp_path):
    return Favorites(Config({}, str(tmp_path / "config.json")))


def test_a_folder_added_twice_is_one_entry_renamed(favorites):
    """A list with one folder under two names is a list nobody trusts to be
    the whole list."""
    favorites.add("Jobs", "C:\\Jobs")
    favorites.add("Current work", "C:\\Jobs")
    assert [e.name for e in favorites.entries] == ["Current work"]


def test_the_same_folder_spelled_differently_is_still_the_same_folder(favorites):
    favorites.add("Jobs", "C:\\Jobs")
    favorites.add("Again", "c:/jobs\\")
    assert len(favorites) == 1


def test_a_path_is_stored_in_one_spelling(favorites):
    favorites.add("Jobs", "c:/jobs/2026//")
    assert favorites.entries[0].path == "C:\\jobs\\2026"


def test_an_empty_name_is_refused(favorites):
    assert not favorites.add("   ", "C:\\Jobs")
    assert len(favorites) == 0


def test_the_list_stops_at_the_cap(favorites):
    for index in range(MAX_FAVORITES + 5):
        favorites.add(f"Entry {index}", f"C:\\{index}")
    assert len(favorites) == MAX_FAVORITES


def test_moving_an_entry_keeps_the_rest_in_order(favorites):
    for name in ("A", "B", "C"):
        favorites.add(name, f"C:\\{name}")
    assert favorites.move(2, -1) == 1
    assert [e.name for e in favorites.entries] == ["A", "C", "B"]


def test_moving_past_the_end_stops_at_it(favorites):
    for name in ("A", "B"):
        favorites.add(name, f"C:\\{name}")
    assert favorites.move(1, 5) == 1
    assert favorites.move(0, -5) == 0
    assert [e.name for e in favorites.entries] == ["A", "B"]


def test_a_change_reaches_the_settings_file_without_waiting_for_the_window(tmp_path):
    """The one thing here that is not like the rest of the settings, and the
    reason it is that way: a favourite is something the user deliberately
    made, and losing one to a crash is a different loss from a window size.
    """
    path = str(tmp_path / "config.json")
    favorites = Favorites(Config({}, path))
    favorites.add("Jobs", "C:\\Jobs")
    assert os.path.exists(path)
    with open(path, encoding="utf-8") as handle:
        stored = json.load(handle)
    assert stored["favorites"] == [{"name": "Jobs", "path": "C:\\Jobs"}]


def test_they_come_back_in_order(tmp_path):
    path = str(tmp_path / "config.json")
    first = Favorites(Config({}, path))
    for name in ("A", "B", "C"):
        first.add(name, f"C:\\{name}")
    first.move(0, 2)
    first.commit_order()

    second = Favorites(Config.load(path))
    assert [e.name for e in second.entries] == ["B", "C", "A"]


def test_a_malformed_stored_entry_is_dropped_not_repaired():
    config = Config({"favorites": [
        {"name": "Good", "path": "C:\\Good"},
        {"name": "No path"},
        "not a mapping",
        {"path": "C:\\Unnamed"},          # named after its folder instead
        {"name": "  ", "path": "C:\\Blank"},
    ]})
    favorites = Favorites(config)
    assert [(e.name, e.path) for e in favorites.entries] == [
        ("Good", "C:\\Good"),
        ("Unnamed", "C:\\Unnamed"),
        ("Blank", "C:\\Blank"),
    ]


def test_a_stored_list_that_is_not_a_list_is_no_favorites(favorites):
    assert Favorites(Config({"favorites": "C:\\Jobs"})).entries == []


def test_the_suggested_name_is_the_folders_own(favorites):
    assert favorites.suggested_name("C:\\Jobs\\2026\\Drawings") == "Drawings"
    assert favorites.suggested_name("C:\\") == "C:\\"

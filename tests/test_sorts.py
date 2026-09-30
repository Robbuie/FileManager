"""Per-folder sort memory (0.40)."""

from __future__ import annotations

from app.core.config import Config
from app.core.sorts import LIMIT, SortMemory


def memory(**values):
    return SortMemory(Config(values, path="unused.json"))


def test_a_folder_keeps_its_order_without_regard_to_case():
    sorts = memory()
    sorts.put(r"S:\Jobs\2026-09-29", 3, 1)
    assert sorts.get(r"s:\jobs\2026-09-29") == (3, 1)
    assert sorts.get(r"S:\Jobs\2026-09-29" + "\\") == (3, 1)
    assert sorts.get(r"S:\Jobs") is None


def test_switched_off_it_neither_remembers_nor_answers():
    sorts = memory(**{"listing.remember_sort": False})
    sorts.put(r"C:\a", 1, 0)
    assert sorts.get(r"C:\a") is None
    assert not sorts.has(r"C:\a")


def test_the_oldest_is_dropped_past_the_limit_and_a_resort_counts_as_new():
    sorts = memory()
    for n in range(LIMIT):
        sorts.put(rf"C:\f{n}", 0, 0)
    sorts.put(r"C:\f0", 3, 1)          # touched again: now the newest
    sorts.put(r"C:\extra", 0, 0)       # one over: the stalest goes
    assert sorts.get(r"C:\f0") == (3, 1)
    assert sorts.get(r"C:\f1") is None
    assert sorts.get(r"C:\extra") == (0, 0)


def test_forget_says_whether_there_was_anything():
    sorts = memory()
    sorts.put(r"C:\a", 1, 0)
    assert sorts.forget(r"C:\A")
    assert not sorts.forget(r"C:\A")


def test_junk_in_the_file_reads_as_nothing():
    sorts = memory(**{"listing.sorts": {r"c:\a": "newest", r"c:\b": [1]}})
    assert sorts.get(r"C:\a") is None and sorts.get(r"C:\b") is None


# ------------------------------------------------------------------ the pane

def test_a_folder_opens_in_its_own_order_and_others_in_the_tabs(tmp_path):
    import pytest
    pytest.importorskip("PySide6")
    from PySide6.QtCore import Qt

    from app.core.listing import Column
    from app.core.pane import Pane
    from tests.test_columns import FakeBridge

    config = Config({}, path=str(tmp_path / "c.json"))
    pane = Pane(FakeBridge(), config, "left")
    days, jobs, other = str(tmp_path / "days"), str(tmp_path / "jobs"), str(tmp_path / "x")

    pane.navigate(days)
    pane.current.model.sort(int(Column.MODIFIED), Qt.DescendingOrder)
    pane.sorted_by_hand(int(Column.MODIFIED), True)

    pane.navigate(jobs)
    pane.current.model.sort(int(Column.NAME), Qt.AscendingOrder)
    pane.sorted_by_hand(int(Column.NAME), False)

    pane.navigate(days)
    assert (pane.current.model.sort_column, pane.current.model.sort_order) == \
        (Column.MODIFIED, Qt.DescendingOrder)
    assert pane.has_own_sort()

    # Never sorted: the order last clicked in the tab, which was by name.
    pane.navigate(other)
    assert pane.current.model.sort_column == Column.NAME
    assert not pane.has_own_sort()

    pane.navigate(days)
    assert pane.forget_sort()
    pane.navigate(other)
    pane.navigate(days)
    assert pane.current.model.sort_column == Column.NAME

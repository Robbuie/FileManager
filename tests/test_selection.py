"""Checks on the part of selecting a group that has to stay cheap.

Selecting 40,000 rows is a real thing to do in this application -- it is built
for folders that size -- and Qt's selection model is where a command like that
either feels instant or redraws for seconds. What decides it is whether the
rows go out as a handful of contiguous ranges or as one range each, which is
what `_runs` is for.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from app.core.listing import matches  # noqa: E402
from app.ui.pane import _runs  # noqa: E402


def test_consecutive_rows_become_one_range():
    assert _runs([0, 1, 2, 3]) == [(0, 3)]


def test_a_gap_starts_a_new_range():
    assert _runs([0, 1, 3, 4, 9]) == [(0, 1), (3, 4), (9, 9)]


def test_nothing_selected_is_no_ranges():
    assert _runs([]) == []


def test_every_other_row_is_the_worst_case_and_still_correct():
    rows = list(range(0, 20, 2))
    assert _runs(rows) == [(row, row) for row in rows]


def test_a_full_folder_is_one_range():
    """The case that matters: select-all over 40,000 rows is one range, not
    40,000 of them."""
    assert _runs(list(range(40_000))) == [(0, 39_999)]


# ------------------------------------------------------------ what a pattern is

@pytest.mark.parametrize("name, pattern, expected", [
    ("plan.dwg", "*.dwg", True),
    ("plan.dwg", "*.dxf", False),
    ("plan.dwg", "plan", True),          # no wildcard, so a substring
    ("site-plan.dwg", "plan", True),
    ("PLAN.DWG", "*.dwg", True),         # Windows does not care about case
    ("plan.dwg", "*.dwg;*.dxf", True),
    ("plan.dxf", "*.dwg;*.dxf", True),
    ("plan.txt", "*.dwg;*.dxf", False),
    ("plan.dwg", "pl?n.dwg", True),
    ("plan.dwg", "  ;  ;*.dwg", True),   # empty parts are skipped
    ("plan.dwg", "", False),
    ("plan.dwg", "   ", False),
])
def test_what_a_pattern_means(name, pattern, expected):
    assert matches(name, pattern) is expected

"""The drafting grid's alignment, which is the part that can be wrong quietly.

The grid is drawn in each listing and has to move with the content: Qt scrolls
by moving pixels already on screen and repainting only the strip that came
into view, so a grid computed from the viewport rather than from the content
would tear at every scroll. `lines` is where that is decided.
"""

from __future__ import annotations

from app.ui.drafting import CELL, MAJOR_EVERY, lines


def test_unscrolled_lines_start_at_the_origin_and_the_first_is_major():
    got = lines(0, CELL * 6, 0)
    assert [position for position, _ in got] == [0, 24, 48, 72, 96, 120]
    assert [major for _, major in got] == [True, False, False, False, False, True]


def test_a_repainted_strip_gets_the_lines_the_whole_view_would_have_there():
    """The strip a scroll exposes must agree with a full repaint."""
    offset = 37
    whole = lines(0, 400, offset)
    strip = lines(300, 100, offset)
    assert strip == [entry for entry in whole if entry[0] >= 300]


def test_scrolling_by_one_cell_moves_every_line_by_one_cell_and_keeps_its_weight():
    before = dict(lines(0, 480, 0))
    after = dict(lines(0, 480, CELL))
    for position, major in after.items():
        if position + CELL in before:
            assert before[position + CELL] == major


def test_majors_are_every_fifth_line():
    got = lines(0, CELL * MAJOR_EVERY * 3, 0)
    majors = [position for position, major in got if major]
    assert majors == [0, CELL * MAJOR_EVERY, CELL * MAJOR_EVERY * 2]


def test_an_empty_area_has_no_lines():
    assert lines(0, 0, 10) == []

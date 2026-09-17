"""Where a dialog lands.

A modal dialog that cannot be seen is a window that answers nobody: the rest
of the application ignores the keyboard until it is dealt with, which from the
outside is a freeze. The arithmetic is tested rather than the window, for
`winframe.region`'s reason -- the bug is in the arithmetic, and checking it
for real would need two monitors and one of them unplugged.
"""

from __future__ import annotations

from PySide6.QtCore import QRect

from app.ui.dialogs import fit

SCREEN = QRect(0, 0, 1920, 1080)


def test_a_dialog_already_on_the_screen_is_left_alone():
    box = QRect(700, 400, 420, 200)
    assert fit(box, SCREEN) == box


def test_one_off_the_right_edge_comes_back():
    assert fit(QRect(1800, 400, 420, 200), SCREEN) == QRect(1500, 400, 420, 200)


def test_one_below_the_bottom_comes_back():
    assert fit(QRect(700, 1000, 420, 200), SCREEN) == QRect(700, 880, 420, 200)


def test_one_on_a_monitor_that_is_no_longer_there_comes_back():
    """The case this exists for: the window was on a second screen at
    -1920, and the dialog is centred on a window nobody can see."""
    assert fit(QRect(-1700, 300, 420, 200), SCREEN) == QRect(0, 300, 420, 200)


def test_a_dialog_larger_than_the_screen_keeps_its_top_left_on_it():
    """Moved, never resized -- and the corner that stays is the one with the
    title and the question on it."""
    placed = fit(QRect(-50, -50, 2400, 1400), SCREEN)
    assert placed.topLeft() == SCREEN.topLeft()
    assert placed.size() == QRect(0, 0, 2400, 1400).size()


def test_the_screen_is_where_it_says_it_is():
    """A second monitor's available area does not start at zero, and a dialog
    clamped to the primary screen's numbers would land on the wrong one."""
    area = QRect(1920, 0, 1920, 1080)
    assert fit(QRect(3800, 200, 420, 200), area) == QRect(3420, 200, 420, 200)

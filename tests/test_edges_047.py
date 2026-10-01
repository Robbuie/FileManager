"""0.47: where a column ends, and a size bar that stays off the digits.

Reported on 30 September: nothing marked where one column ended, so changing a
width meant hunting along the header for the cursor to change; and the size
bar, two pixels under the figure, ran into the digits on some themes. The
fixes are a divider in the header, a grab zone a few pixels either side of each
edge, a line down the listing at the edge being worked on, and the bar moved
behind the figure.

The grab zone is the part worth testing hardest. It works by moving a mouse
event onto the edge before Qt sees it, so the failure it can have is a quiet
one: a drag that jumps by the distance the press was moved, or a click in the
middle of a heading taken as a drag and no longer sorting.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent, QStandardItemModel  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QHeaderView, QTableView  # noqa: E402

from app.core import options  # noqa: E402
from app.core.config import DEFAULTS  # noqa: E402
from app.theme import qss  # noqa: E402
from app.theme.tokens import THEMES  # noqa: E402
from app.ui.pane import GRAB_ZONE, SortHeader, nearest_edge  # noqa: E402
from app.ui.rows import BAR_HEIGHT, parse_colour, size_bar_rect  # noqa: E402


# ------------------------------------------------------------------ tokens

@pytest.mark.parametrize("theme", sorted(THEMES))
def test_every_theme_has_the_new_tokens(theme) -> None:
    tokens = qss.build(theme)
    for key in ("rule", "rule_soft", "band", "size_fill", "size_fill_dir"):
        assert parse_colour(tokens[key]).isValid(), key


@pytest.mark.parametrize("theme", sorted(THEMES))
def test_the_divider_stands_off_the_pane_but_under_the_text(theme) -> None:
    """Visible against the pane, and never as strong as the muted text --
    the divider separates headings, it is not one."""
    tokens = qss.build(theme)

    def luma(name):
        c = parse_colour(tokens[name])
        return 0.2126 * c.red() + 0.7152 * c.green() + 0.0722 * c.blue()

    pane, rule, text = luma("bg_2"), luma("rule"), luma("txt_2")
    assert abs(rule - pane) > 12
    assert abs(rule - pane) < abs(text - pane)


def test_the_settings_have_defaults_and_rows() -> None:
    assert DEFAULTS["listing.column_edges"] == "header"
    assert DEFAULTS["listing.size_bar"] == "behind"
    keys = {row.key for row in options.OPTIONS}
    assert {"listing.column_edges", "listing.size_bar"} <= keys
    assert not options.check()


# ---------------------------------------------------------------- the bar

def test_behind_fills_the_row_and_is_right_aligned() -> None:
    cell = QRectF(100, 40, 120, 22)
    bar = size_bar_rect("behind", cell, 0.5)
    assert bar.right() == pytest.approx(cell.right() - 3)
    assert bar.height() >= cell.height() - 8
    assert bar.top() > cell.top() and bar.bottom() < cell.bottom()


def test_behind_draws_no_sliver_for_a_small_file() -> None:
    """A few pixels behind the last digit looked like a cursor, not a bar."""
    assert size_bar_rect("behind", QRectF(0, 0, 120, 22), 0.03) is None
    assert size_bar_rect("behind", QRectF(0, 0, 120, 22), 0.2).width() >= 8


def test_under_is_the_old_line() -> None:
    bar = size_bar_rect("under", QRectF(0, 0, 120, 22), 1.0)
    assert bar.height() == BAR_HEIGHT


@pytest.mark.parametrize("mode", ["off", "behind", "under"])
def test_nothing_for_no_share(mode) -> None:
    assert size_bar_rect(mode, QRectF(0, 0, 120, 22), 0.0) is None


def test_off_draws_nothing() -> None:
    assert size_bar_rect("off", QRectF(0, 0, 120, 22), 0.7) is None


# ------------------------------------------------------------- the edges

def test_nearest_edge_picks_the_closest_within_the_zone() -> None:
    edges = [(0, 200), (2, 280), (3, 340)]
    assert nearest_edge(edges, 203) == (0, 200)
    assert nearest_edge(edges, 200 - GRAB_ZONE) == (0, 200)
    assert nearest_edge(edges, 200 + GRAB_ZONE + 1) is None
    assert nearest_edge(edges, 240) is None


def send(header, kind, x, y, button=Qt.NoButton, buttons=Qt.NoButton) -> None:
    """A mouse event straight to the header, not through QTest.

    QTest moves the real cursor and lets the platform decide which window is
    under it, so with another test's window still about the hover tests saw
    enter and leave events nobody made. Sent directly, the header sees exactly
    the gesture described.
    """
    local = QPointF(x, y)
    globe = QPointF(header.viewport().mapToGlobal(QPoint(int(x), int(y))))
    QApplication.sendEvent(header.viewport(),
                           QMouseEvent(kind, local, globe, button, buttons,
                                       Qt.NoModifier))


@pytest.fixture
def table():
    model = QStandardItemModel(5, 4)
    model.setHorizontalHeaderLabels(["Name", "Ext", "Size", "Modified"])
    view = QTableView()
    header = SortHeader(view)
    header.setSectionsClickable(True)
    view.setHorizontalHeader(header)
    view.setModel(model)
    for column in range(4):
        header.setSectionResizeMode(column, QHeaderView.Interactive)
        header.resizeSection(column, 100)
    view.resize(600, 300)
    view.show()
    QApplication.processEvents()
    yield view, header
    view.close()


def test_a_drag_started_beside_the_edge_does_not_jump(table) -> None:
    """Pressed five pixels right of the edge and moved twenty: the column is
    twenty wider, not twenty-five. The shift onto the edge is carried through
    the drag, or the column would jump on the first move."""
    _view, header = table
    edge = header.sectionViewportPosition(0) + header.sectionSize(0)
    y = header.height() // 2
    start = QPoint(edge + 5, y)
    QTest.mouseMove(header.viewport(), start)
    QTest.mousePress(header.viewport(), Qt.LeftButton, Qt.NoModifier, start)
    QTest.mouseMove(header.viewport(), QPoint(edge + 25, y))
    QTest.mouseRelease(header.viewport(), Qt.LeftButton, Qt.NoModifier,
                       QPoint(edge + 25, y))
    assert header.sectionSize(0) == 120


def test_hovering_beside_an_edge_shows_the_resize_cursor(table) -> None:
    _view, header = table
    edge = header.sectionViewportPosition(1) + header.sectionSize(1)
    send(header, QEvent.MouseMove, edge - 5, header.height() // 2)
    assert header.cursor().shape() == Qt.SplitHCursor


def test_a_click_in_the_middle_of_a_heading_still_sorts(table) -> None:
    view, header = table
    clicked = []
    header.sectionClicked.connect(clicked.append)
    middle = header.sectionViewportPosition(2) + header.sectionSize(2) // 2
    QTest.mouseClick(header.viewport(), Qt.LeftButton, Qt.NoModifier,
                     QPoint(middle, header.height() // 2))
    assert clicked == [2]
    assert header.sectionSize(2) == 100


def test_a_double_click_beside_an_edge_asks_for_a_fit(table) -> None:
    _view, header = table
    asked = []
    header.sectionHandleDoubleClicked.connect(asked.append)
    edge = header.sectionViewportPosition(0) + header.sectionSize(0)
    QTest.mouseDClick(header.viewport(), Qt.LeftButton, Qt.NoModifier,
                      QPoint(edge + 4, header.height() // 2))
    assert asked == [0]


def test_the_guide_follows_the_edge_and_says_when_it_is_a_drag(table) -> None:
    _view, header = table
    seen = []
    header.guideMoved.connect(lambda x, c, d: seen.append((x, c, d)))
    edge = header.sectionViewportPosition(0) + header.sectionSize(0)
    y = header.height() // 2
    send(header, QEvent.MouseMove, edge + 3, y)
    assert seen[-1] == (edge, 0, False)
    send(header, QEvent.MouseButtonPress, edge + 3, y, Qt.LeftButton, Qt.LeftButton)
    send(header, QEvent.MouseMove, edge + 13, y, Qt.NoButton, Qt.LeftButton)
    assert seen[-1] == (edge + 10, 0, True)
    send(header, QEvent.MouseButtonRelease, edge + 13, y, Qt.LeftButton, Qt.NoButton)
    send(header, QEvent.MouseMove, edge + 60, y)
    assert seen[-1] == (-1, -1, False)

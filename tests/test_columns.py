"""The listing's columns: dragged, remembered, fitted and hidden.

Reported from the window on 15 September as "there is no good way to change the
column sizes on the fly", and it was two faults rather than one. The name was a
`Stretch` section, which Qt gives no usable drag handle, so the one column
anybody wants wider could not be touched at all. And the other four could be
dragged and were thrown away on the next tab switch, because `_sync_current`
laid the columns out again from the hardcoded numbers.

Both are checked here, because both are invisible in a diff: a stretched
section looks like a deliberate layout choice, and a call that resets widths
looks like tidiness.

The fit tests matter for a different reason. `ResizeToContents` measures every
row in the model, which on a folder of 50,000 files is the cost this whole
application is built to avoid -- so the double click on a divider is taken over
and answers from the rows on screen. A test that let it measure everything
would pass just as happily, which is why the bound is asserted directly.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QObject, Qt, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication, QHeaderView  # noqa: E402

from app.core.config import Config  # noqa: E402
from app.core.listing import HEADERS, Column  # noqa: E402
from app.io.protocol import Entry, Reply, Status  # noqa: E402
from app.ui.pane import DEFAULT_WIDTHS, MIN_COLUMN  # noqa: E402


class FakeBridge:
    def __init__(self):
        self.sent = []

    def submit(self, op, path, *, timeout, on_reply, args=None):
        self.sent.append({"op": op, "path": path, "args": dict(args or {})})
        return len(self.sent)

    def cancel(self, request_id):
        pass

    def forget(self, request_id):
        pass


def entry(name, *, is_dir=False):
    return Entry(name=name, is_dir=is_dir, size=2048, mtime=100.0, attributes=0)


@pytest.fixture
def pane(tmp_path):
    """One pane widget, shown, over fake io."""
    from app.core.pane import Pane
    from app.ui.pane import PaneWidget
    from app.theme import sheet

    class Volumes(QObject):
        changed = Signal()
        drives = []

        def letter_for(self, path):
            return None

        def refresh(self, *, rescan=False):
            pass

    # Every column on screen, which is what these tests were written against.
    # Since 0.24 Ext starts hidden; `test_look.py` covers that default.
    config = Config({"left.path": "C:\\Jobs", "left.columns_hidden": []},
                    str(tmp_path / "config.json"))
    model = Pane(FakeBridge(), config, "left")
    widget = PaneWidget(model, Volumes(), sheet.metrics("normal"), None)
    widget.resize(900, 500)
    widget.show()
    QApplication.processEvents()
    widget.model = model
    widget.config = config
    yield widget
    widget.hide()


def fill(widget, names):
    model = widget.model.current.model
    model.begin(has_parent=False)
    model.add([entry(name) for name in names])
    model.finish()
    QApplication.processEvents()


def header(widget) -> QHeaderView:
    return widget._view.horizontalHeader()


# ------------------------------------------------------------------ dragging

def test_every_column_can_be_dragged_including_the_name(pane) -> None:
    """The whole complaint. `Stretch` gives a section no drag handle, so the
    one column people want wider was the one that could not be touched.
    """
    for column in range(len(HEADERS)):
        assert header(pane).sectionResizeMode(column) == QHeaderView.Interactive


def test_a_drag_is_remembered(pane) -> None:
    header(pane).resizeSection(int(Column.NAME), 500)
    assert pane.model.columns[int(Column.NAME)] == 500


def test_a_drag_survives_a_tab_switch(pane) -> None:
    """The second fault. Widths belong to the view, not the model, and laying
    them out again on every `setModel` is what threw away every drag.
    """
    header(pane).resizeSection(int(Column.SIZE), 200)
    pane.model.open_tab("C:\\Other")
    QApplication.processEvents()
    pane.model.select_tab(0)
    QApplication.processEvents()
    assert header(pane).sectionSize(int(Column.SIZE)) == 200


def test_stored_widths_come_back_on_a_new_widget(tmp_path) -> None:
    """A width that only lasts while the window is open is not remembered."""
    from app.core.pane import Pane
    from app.ui.pane import PaneWidget
    from app.theme import sheet

    class Volumes(QObject):
        changed = Signal()
        drives = []

        def letter_for(self, path):
            return None

        def refresh(self, *, rescan=False):
            pass

    config = Config({"left.columns": [410, 60, 100, 50, 150]},
                    str(tmp_path / "config.json"))
    widget = PaneWidget(Pane(FakeBridge(), config, "left"), Volumes(),
                        sheet.metrics("normal"), None)
    widget.show()
    QApplication.processEvents()
    assert widget._view.horizontalHeader().sectionSize(int(Column.NAME)) == 410
    widget.hide()


def test_the_name_takes_the_slack_until_something_is_dragged(pane) -> None:
    """What a pane nobody has touched looks like, which has to be what it
    looked like before any of this existed.
    """
    others = sum(header(pane).sectionSize(column)
                 for column in range(1, len(HEADERS)))
    assert header(pane).sectionSize(int(Column.NAME)) > others // 2
    assert pane.model.columns == []


def test_once_dragged_the_name_stops_following_the_pane(pane) -> None:
    """A column that re-widened itself on every resize is one that will not
    stay where it is put, which is the complaint in another form.
    """
    header(pane).resizeSection(int(Column.NAME), 300)
    pane.resize(1400, 500)
    QApplication.processEvents()
    assert header(pane).sectionSize(int(Column.NAME)) == 300


# --------------------------------------------------------------- fitting

def test_fitting_measures_the_rows_on_screen(pane) -> None:
    fill(pane, ["a.txt", "a-very-considerably-longer-name-than-the-others.txt"])
    narrow = 60
    header(pane).resizeSection(int(Column.NAME), narrow)
    pane._fit_column(int(Column.NAME))
    assert header(pane).sectionSize(int(Column.NAME)) > narrow


def test_fitting_never_measures_more_than_a_screenful(pane) -> None:
    """The bound that makes this affordable. `ResizeToContents` would measure
    all fifty thousand; this asks the view which rows are on screen, so a
    name far below the fold does not widen the column.
    """
    rows = [f"{n:04d}.txt" for n in range(4000)]
    rows[3500] = "z" * 300 + ".txt"
    fill(pane, sorted(rows))
    pane._fit_column(int(Column.NAME))
    assert header(pane).sectionSize(int(Column.NAME)) < 600


def test_fitting_an_empty_folder_does_not_collapse_the_column(pane) -> None:
    """The header's own text is the floor. A column fitted to nothing would
    otherwise become a column that cannot be found again.
    """
    pane.fit_columns()
    for column in range(len(HEADERS)):
        if pane._view.isColumnHidden(column):
            continue        # Location, outside flat view
        assert header(pane).sectionSize(column) >= MIN_COLUMN


def test_fitting_is_remembered_like_a_drag(pane) -> None:
    fill(pane, ["one.txt"])
    pane.fit_columns()
    assert pane.model.columns != []


# ---------------------------------------------------------------- resetting

def test_reset_puts_the_shipped_widths_back(pane) -> None:
    header(pane).resizeSection(int(Column.EXT), 250)
    pane.reset_columns()
    assert header(pane).sectionSize(int(Column.EXT)) == DEFAULT_WIDTHS[Column.EXT]


def test_reset_gives_the_name_the_slack_again(pane) -> None:
    header(pane).resizeSection(int(Column.NAME), 100)
    pane.reset_columns()
    assert pane.model.columns == []
    assert header(pane).sectionSize(int(Column.NAME)) > 100


# ------------------------------------------------------------------ hiding

def test_a_column_can_be_hidden_and_shown(pane) -> None:
    pane._set_column_shown(int(Column.EXT), False)
    assert pane._view.isColumnHidden(int(Column.EXT))
    pane._set_column_shown(int(Column.EXT), True)
    assert not pane._view.isColumnHidden(int(Column.EXT))


def test_the_name_cannot_be_hidden(pane) -> None:
    """A listing with no names is not a listing. Refused rather than guarded
    against in the menu alone, so a hand-edited settings file cannot do it.
    """
    pane._set_column_shown(int(Column.NAME), False)
    assert not pane._view.isColumnHidden(int(Column.NAME))


def test_hiding_survives_a_tab_switch(pane) -> None:
    """`setModel` brings every column back, which is why `_sync_current` has to
    reapply this even though it no longer reapplies the widths.
    """
    pane._set_column_shown(int(Column.AGE), False)
    pane.model.open_tab("C:\\Other")
    QApplication.processEvents()
    assert pane._view.isColumnHidden(int(Column.AGE))


def test_a_hidden_column_is_remembered(pane) -> None:
    pane._set_column_shown(int(Column.MODIFIED), False)
    assert int(Column.MODIFIED) in pane.model.hidden_columns


def test_a_hidden_column_gives_its_room_to_the_name(pane) -> None:
    """Otherwise hiding a column leaves a gap where it was, which looks like
    the column is still there and empty.
    """
    before = header(pane).sectionSize(int(Column.NAME))
    pane._set_column_shown(int(Column.MODIFIED), False)
    assert header(pane).sectionSize(int(Column.NAME)) > before


def test_a_hidden_column_gives_its_room_to_the_name_after_a_drag_too(pane) -> None:
    """The case the first version of this got wrong. With stored widths the
    name is no longer recomputed, so hiding a column would leave a gap exactly
    where it was -- which reads as the column still being there and empty.
    """
    head = header(pane)
    head.resizeSection(int(Column.NAME), 300)
    room = head.sectionSize(int(Column.MODIFIED))
    pane._set_column_shown(int(Column.MODIFIED), False)
    assert head.sectionSize(int(Column.NAME)) == 300 + room
    pane._set_column_shown(int(Column.MODIFIED), True)
    assert head.sectionSize(int(Column.NAME)) == 300


# --------------------------------------------------------------- a narrow pane

def test_a_narrow_pane_squeezes_the_other_columns_not_the_name(pane) -> None:
    """A fault older than this release: the other four are 328 pixels of fixed
    width, so below about 380 of viewport the name collapsed to a column of
    first letters and the table grew a horizontal scrollbar. The name is the
    column being read; it is the last thing that should give way.
    """
    pane.resize(360, 500)
    QApplication.processEvents()
    assert header(pane).sectionSize(int(Column.NAME)) >= 120


def test_a_narrow_pane_does_not_need_a_horizontal_scrollbar(pane) -> None:
    pane.resize(380, 500)
    QApplication.processEvents()
    total = sum(header(pane).sectionSize(column)
                for column in range(len(HEADERS))
                if not pane._view.isColumnHidden(column))
    assert total <= pane._view.viewport().width() + 2


def test_a_wide_pane_still_gets_the_full_widths(pane) -> None:
    """The squeeze is for the narrow case only; nothing about a normal pane
    changes, which is what makes this safe to do automatically.
    """
    pane.resize(1400, 500)
    QApplication.processEvents()
    assert header(pane).sectionSize(int(Column.MODIFIED)) == DEFAULT_WIDTHS[Column.MODIFIED]


def test_a_pane_whose_widths_were_set_is_left_alone_when_narrowed(pane) -> None:
    """Undoing somebody's own widths on a window resize is the complaint this
    release exists to answer, arriving through a different door.
    """
    header(pane).resizeSection(int(Column.MODIFIED), 300)
    pane.resize(360, 500)
    QApplication.processEvents()
    assert header(pane).sectionSize(int(Column.MODIFIED)) == 300


# -------------------------------------------------- squeezed out, not broken

def test_a_very_narrow_pane_hides_columns_rather_than_breaking_them(pane) -> None:
    """0.23. A column squeezed to `MIN_COLUMN` drew "EX", "GE" and "MO" in the
    header and an age chip over the size. Past what it can be read at, a
    column goes away for as long as the pane is that narrow.
    """
    from app.ui.pane import READABLE
    pane.resize(260, 500)
    QApplication.processEvents()
    for column, least in READABLE.items():
        if not pane._view.isColumnHidden(int(column)):
            assert header(pane).sectionSize(int(column)) >= least
    assert pane._view.isColumnHidden(int(Column.AGE))


def test_the_age_column_goes_before_the_size(pane) -> None:
    pane.resize(260, 500)
    QApplication.processEvents()
    if not pane._view.isColumnHidden(int(Column.AGE)):
        assert not pane._view.isColumnHidden(int(Column.SIZE))
    if pane._view.isColumnHidden(int(Column.SIZE)):
        assert pane._view.isColumnHidden(int(Column.AGE))


def test_a_squeezed_out_column_comes_back_when_there_is_room(pane) -> None:
    pane.resize(260, 500)
    QApplication.processEvents()
    pane.resize(1400, 500)
    QApplication.processEvents()
    for column in (Column.AGE, Column.EXT, Column.MODIFIED, Column.SIZE):
        assert not pane._view.isColumnHidden(int(column))


def test_squeezing_out_is_not_stored_as_hidden(pane) -> None:
    pane.resize(260, 500)
    QApplication.processEvents()
    assert pane._pane.hidden_columns == [] or int(Column.AGE) not in pane._pane.hidden_columns


# ------------------------------------------------------------ the idle pane

def test_the_idle_pane_is_faded_and_the_live_one_is_not(pane) -> None:
    """0.23. The two-pixel accent bar was the only way to tell which pane a
    key would land in. The idle pane's rows are now drawn faded.
    """
    from PySide6.QtGui import QImage, QPainter
    from app.ui.rows import IDLE_OPACITY
    fill(pane, ["a.txt"])
    seen = []

    class Spy(QPainter):
        def setOpacity(self, value):  # noqa: N802
            seen.append(value)
            super().setOpacity(value)

    delegate = pane._rows
    index = pane._view.model().index(0, int(Column.NAME))
    from PySide6.QtWidgets import QStyleOptionViewItem
    option = QStyleOptionViewItem()
    option.rect = pane._view.visualRect(index)
    image = QImage(400, 40, QImage.Format_ARGB32)
    for live, expect in ((True, False), (False, True)):
        seen.clear()
        pane.set_active(live)
        painter = Spy(image)
        delegate.paint(painter, option, index)
        painter.end()
        assert (IDLE_OPACITY in seen) is expect

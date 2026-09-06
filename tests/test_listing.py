"""Checks on the item model.

Qt is needed for these, so they skip where it is absent rather than failing:
the io layer's tests are the ones that have to run everywhere.
"""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402

from app.core.listing import Column, ListingModel, format_size, split_name  # noqa: E402
from app.io.protocol import Entry  # noqa: E402


def entry(name, is_dir=False, size=0, mtime=0.0):
    return Entry(name=name, is_dir=is_dir, size=size, mtime=mtime, attributes=0)


@pytest.fixture
def model():
    return ListingModel()


def test_it_sorts_ascending_by_name_by_default(model):
    model.begin(has_parent=False)
    model.add([entry("xorg", True), entry("apt", True), entry("Mozilla", True)])
    model.finish()
    names = [model.data(model.index(row, Column.NAME)) for row in range(model.rowCount())]
    assert names == ["apt", "Mozilla", "xorg"]


def test_folders_come_first_whatever_the_column(model):
    model.begin(has_parent=False)
    model.add([entry("a.txt", size=10), entry("zeta", True), entry("b.txt", size=5)])
    for column in (Column.NAME, Column.SIZE, Column.MODIFIED):
        model.sort(int(column), Qt.AscendingOrder)
        assert model.data(model.index(0, Column.NAME)) == "zeta"


def test_the_parent_row_is_not_one_of_the_entries(model):
    model.begin(has_parent=True)
    model.add([entry("one", True), entry("two", True)])
    model.finish()
    assert model.rowCount() == 3
    assert model.is_parent_row(0)
    assert model.entry(0) is None
    assert "2 folders" in model.summary()


def test_a_folder_shows_no_size_rather_than_zero(model):
    model.begin(has_parent=False)
    model.add([entry("folder", True)])
    model.finish()
    assert model.data(model.index(0, Column.SIZE)) == "<DIR>"


def test_extension_is_split_off_the_name():
    assert split_name(entry("report.final.pdf")) == ("report.final", "pdf")
    assert split_name(entry(".gitignore")) == (".gitignore", "")
    assert split_name(entry("My.Folder", is_dir=True)) == ("My.Folder", "")


def test_sizes_read_as_sizes():
    assert format_size(0) == "0 B"
    assert format_size(1023) == "1023 B"
    assert format_size(1536) == "1.5 K"
    assert format_size(5 * 1024 ** 3) == "5.0 G"


def test_the_filter_hides_rows_without_losing_them(model):
    model.begin(has_parent=False)
    model.add([entry("report.pdf"), entry("notes.txt"), entry("report.docx")])
    model.finish()

    model.set_filter("report")
    assert model.rowCount() == 2
    assert "2 shown" in model.summary()

    model.set_filter("")
    assert model.rowCount() == 3, "clearing the filter must not re-list the folder"


def test_the_filter_takes_a_glob_when_one_is_typed(model):
    model.begin(has_parent=False)
    model.add([entry("a.txt"), entry("b.log"), entry("c.txt")])
    model.finish()

    model.set_filter("*.txt")
    names = [model.data(model.index(row, Column.NAME)) for row in range(model.rowCount())]
    assert names == ["a", "c"]


def test_rows_arriving_under_a_filter_are_filtered_too(model):
    model.begin(has_parent=False)
    model.add([entry("keep-1")])
    model.set_filter("keep")
    model.add([entry("keep-2"), entry("drop-1")])

    assert model.rowCount() == 2
    model.set_filter("")
    assert model.rowCount() == 3


def test_a_batch_arriving_unfiltered_is_added_once(model):
    model.begin(has_parent=False)
    model.add([entry("one"), entry("two")])
    model.add([entry("three")])
    assert model.rowCount() == 3


def test_navigating_drops_the_filter(model):
    model.begin(has_parent=False)
    model.add([entry("a.txt")])
    model.set_filter("nothing-matches")
    assert model.rowCount() == 0

    model.begin(has_parent=False)
    model.add([entry("a.txt")])
    assert model.filter_text == ""
    assert model.rowCount() == 1


def test_the_selection_totals_skip_the_parent_row(model):
    model.begin(has_parent=True)
    model.add([entry("folder", is_dir=True), entry("a.txt", size=100),
               entry("b.txt", size=50)])
    model.finish()

    assert model.selection([0]) == (0, 0, 0)
    assert model.selection([0, 1, 2, 3]) == (1, 2, 150)


def test_a_row_can_be_found_by_name_whatever_its_case(model):
    model.begin(has_parent=True)
    model.add([entry("Zebra", is_dir=True), entry("notes.txt")])
    model.finish()

    assert model.row_of("zebra") == 1
    assert model.row_of("NOTES.TXT") == 2
    assert model.row_of("absent") == -1

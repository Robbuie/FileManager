"""Checks on the item model.

Qt is needed for these, so they skip where it is absent rather than failing:
the io layer's tests are the ones that have to run everywhere.
"""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402

from app.core.listing import (  # noqa: E402
    AGE_STEPS,
    Column,
    ListingModel,
    age_step,
    format_age,
    format_size,
    split_name,
)
from app.theme.tokens import AGE_ALPHA  # noqa: E402
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
    assert model.data(model.index(0, Column.SIZE)) == ""


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


# --------------------------------------------------------------- quick search
#
# The jump that happens when somebody types into the listing. Qt's own
# `keyboardSearch` is not used, so the behaviour has to be stated here: what a
# prefix beats, where a second keystroke carries on from, and what wrapping
# means.


def filled(model, names, has_parent=False):
    model.begin(has_parent=has_parent)
    model.add([entry(name) for name in names])
    model.finish()
    return model


def test_a_prefix_beats_a_name_that_merely_contains_it(model):
    """Two passes rather than one ranked pass, so a folder called `drawings`
    is never passed over for `old-drawings` that happens to sort above it.
    """
    filled(model, ["old-drawings", "drawings"])
    assert model.data(model.index(model.find("draw"), Column.NAME)) == "drawings"


def test_it_falls_back_to_a_name_that_contains_what_was_typed(model):
    filled(model, ["site-plan.dwg", "notes.txt"])
    assert model.data(model.index(model.find("plan"), Column.NAME)) == "site-plan"


def test_it_does_not_care_about_case(model):
    filled(model, ["Drawings"])
    assert model.find("dRaW") == 0


def test_it_carries_on_from_where_the_cursor_is(model):
    """What makes a second keystroke narrow the answer instead of restarting
    the walk."""
    filled(model, ["a1", "a2", "a3"])
    assert model.find("a", start=1) == 1
    assert model.find("a", start=2) == 2


def test_it_wraps(model):
    filled(model, ["a1", "b1"])
    assert model.find("a", start=1) == 0


def test_it_wraps_backwards_too(model):
    """`start` is where the walk begins, not a row it skips -- stepping past
    the current match is the caller's job, and `_step_search` does it by
    passing the row it wants to start at."""
    filled(model, ["a1", "b1", "a2"])          # sorts to a1, a2, b1
    assert model.find("a", start=2, forward=False) == 1
    assert model.find("a", start=0, forward=False) == 0


def test_nothing_matching_is_minus_one_rather_than_a_row(model):
    filled(model, ["a1", "b1"])
    assert model.find("zz") == -1


def test_the_parent_row_is_never_the_answer(model):
    """`..` is not an entry, so it cannot be typed to and cannot be landed on
    -- and the row numbers it shifts everything by still have to come back
    right."""
    filled(model, ["a.txt", "other"], has_parent=True)
    assert model.find(".") == 1


def test_an_empty_search_matches_nothing(model):
    filled(model, ["a1"])
    assert model.find("") == -1


def test_a_filtered_out_row_cannot_be_found(model):
    """The search is over what is on screen. A filter that hides a name and a
    search that jumps to it would be two features disagreeing about what the
    folder contains."""
    filled(model, ["keep-me", "hide-me"])
    model.set_filter("keep")
    assert model.find("hide") == -1


# ---------------------------------------------------------- selecting a group
#
# The rows a selection command acts on. Rows rather than names, with the parent
# row's offset already in them, because that is what the widget hands to Qt --
# and `..` is never among them, because a selection holding it would offer it
# to the next operation.


def test_a_pattern_with_a_star_matches_the_whole_name(model):
    filled(model, ["plan.dwg", "plan.dxf", "notes.txt"])
    rows = model.rows_matching("*.dwg")
    assert [model.entry(row).name for row in rows] == ["plan.dwg"]


def test_a_pattern_without_a_wildcard_matches_part_of_a_name(model):
    filled(model, ["site-plan.dwg", "notes.txt"])
    assert [model.entry(row).name for row in model.rows_matching("plan")] == \
        ["site-plan.dwg"]


def test_several_patterns_at_once(model):
    """What makes `*.dwg;*.dxf` one answer to "select the drawings"."""
    filled(model, ["a.dwg", "b.dxf", "c.txt"])
    names = [model.entry(row).name for row in model.rows_matching("*.dwg;*.dxf")]
    assert names == ["a.dwg", "b.dxf"]


def test_the_parent_row_is_never_selected(model):
    filled(model, ["one", "two"], has_parent=True)
    assert model.rows_matching("*") == [1, 2]
    assert model.all_rows() == [1, 2]


def test_a_pattern_can_leave_the_folders_out(model):
    model.begin(has_parent=False)
    model.add([entry("build", is_dir=True), entry("build.log")])
    model.finish()
    rows = model.rows_matching("build*", files_only=True)
    assert [model.entry(row).name for row in rows] == ["build.log"]


def test_the_same_kind_is_files_of_one_extension(model):
    filled(model, ["a.dwg", "b.dwg", "c.txt"])
    assert [model.entry(row).name for row in model.rows_with_extension("dwg")] == \
        ["a.dwg", "b.dwg"]


def test_a_folder_is_never_the_same_kind_however_many_dots_are_in_it(model):
    """The rule the Ext column already follows, stated where a command uses it."""
    model.begin(has_parent=False)
    model.add([entry("release.v2.dwg", is_dir=True), entry("plan.dwg")])
    model.finish()
    names = [model.entry(row).name for row in model.rows_with_extension("dwg")]
    assert names == ["plan.dwg"]


def test_a_filtered_out_row_is_not_selected(model):
    """A selection command acts on the listing, and the listing is what the
    filter lets through."""
    filled(model, ["keep.dwg", "hide.dwg"])
    model.set_filter("keep")
    assert [model.entry(row).name for row in model.rows_matching("*.dwg")] == \
        ["keep.dwg"]


def test_folder_names_are_what_is_on_screen(model):
    model.begin(has_parent=True)
    model.add([entry("kept", is_dir=True), entry("gone", is_dir=True),
               entry("a.txt")])
    model.finish()
    model.set_filter("kept")
    assert model.folder_names() == ["kept"]


def test_an_entry_is_findable_by_name_whatever_its_case(model):
    filled(model, ["Drawings"])
    assert model.entry_named("drawings").name == "Drawings"
    assert model.entry_named("missing") is None


# ------------------------------------------------------------------- the age

NOW = 1_700_000_000.0
HOUR = 3600.0
DAY = HOUR * 24


@pytest.mark.parametrize("ago, shown", [
    (30, "now"),
    (HOUR / 4, "15m"),
    (HOUR * 5, "5h"),
    (DAY * 1.5, "1d"),
    (DAY * 9, "9d"),
    (DAY * 60, "2M"),
    (DAY * 800, "2y"),
])
def test_an_age_reads_in_three_characters(ago, shown):
    assert format_age(NOW - ago, NOW) == shown


def test_a_file_dated_in_the_future_is_a_clock_not_a_negative_age():
    """A share whose clock is ahead is a real thing and reporting it as
    `-3h` would be a bug report. The Modified column still says what the
    share claims."""
    assert format_age(NOW + DAY, NOW) == "now"
    assert age_step(NOW + DAY, NOW) == "fresh"


def test_no_mtime_gets_no_age_and_no_chip():
    assert format_age(0.0, NOW) == ""
    assert age_step(0.0, NOW) is None


@pytest.mark.parametrize("ago, step", [
    (HOUR, "fresh"),
    (DAY * 3, "recent"),
    (DAY * 20, "month"),
    (DAY * 40, None),
    (DAY * 900, None),
])
def test_the_chip_has_three_steps_and_then_stops(ago, step):
    assert age_step(NOW - ago, NOW) == step


def test_every_step_the_model_produces_has_a_tint_to_draw_it_with():
    """The rule about time lives here and the rule about colour lives in
    `app.theme.tokens`, so that neither layer imports the other. This is what
    holds them together: a step with no tint draws no chip and raises
    nothing, which is the kind of thing that ships."""
    assert {name for _, name in AGE_STEPS} == set(AGE_ALPHA)


def test_the_age_column_sorts_newest_first_when_it_sorts_up(model):
    """Ascending age is the smallest age, which is the largest mtime. Sorting
    it like the date would put the oldest thing in the folder at the top of a
    column headed "how long ago"."""
    model.begin(has_parent=False)
    model.add([entry("old", mtime=NOW - DAY * 30),
               entry("new", mtime=NOW - HOUR),
               entry("middle", mtime=NOW - DAY * 2)])
    model.sort(int(Column.AGE), Qt.AscendingOrder)
    names = [model.data(model.index(row, Column.NAME))
             for row in range(model.rowCount())]
    assert names == ["new", "middle", "old"]


# ------------------------------------------------------------ the size bars

def test_the_size_bar_scales_against_the_largest_file(model):
    model.begin(has_parent=False)
    model.add([entry("big", size=1000), entry("small", size=250)])
    model.finish()
    shares = {model.data(model.index(row, Column.NAME)):
              model.data(model.index(row, Column.SIZE), ListingModel.SizeShareRole)
              for row in range(model.rowCount())}
    assert shares == {"big": 1.0, "small": 0.25}


def test_a_folder_gets_no_bar_even_once_it_has_been_measured(model):
    """A folder total can be orders of magnitude past anything in the folder.
    On the same scale every real file becomes no bar at all, so the number is
    kept and the comparison -- which is what would be the lie -- is not."""
    class Sizes:
        def known(self, folder, name):
            return "4.0 G"

    model.set_sizes(Sizes())
    model.set_folder("C:\\Jobs")
    model.begin(has_parent=False)
    model.add([entry("archive", is_dir=True), entry("note", size=90)])
    model.finish()
    rows = {model.data(model.index(row, Column.NAME)): row
            for row in range(model.rowCount())}
    assert model.data(model.index(rows["archive"], Column.SIZE)) == "4.0 G"
    assert model.data(model.index(rows["archive"], Column.SIZE),
                      ListingModel.SizeShareRole) is None
    assert model.data(model.index(rows["note"], Column.SIZE),
                      ListingModel.SizeShareRole) == 1.0


def test_the_scale_follows_the_filter(model):
    """The bar compares this file with what is on screen. Hiding the largest
    file and leaving every other bar at the width it had reads as nothing
    having changed."""
    model.begin(has_parent=False)
    model.add([entry("huge", size=1000), entry("keep", size=100)])
    model.finish()
    assert model.size_scale == 1000
    model.set_filter("keep")
    assert model.size_scale == 100


def test_a_listing_of_nothing_but_folders_has_no_scale_and_asks_for_no_bars(model):
    model.begin(has_parent=False)
    model.add([entry("one", is_dir=True), entry("two", is_dir=True)])
    model.finish()
    assert model.size_scale == 0
    assert model.data(model.index(0, Column.SIZE),
                      ListingModel.SizeShareRole) is None

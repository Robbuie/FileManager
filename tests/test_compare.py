"""Comparing the two panes, which is arithmetic over rows already in memory.

Every case here is built from `Entry` values directly, because that is exactly
what the feature has to work with: a listing carries name, size and mtime
because `os.scandir` hands them over with the name, and this module is not
allowed to want anything else. A test that had to make real files would be
testing something the feature does not do.

The two rules worth asserting are the ones a reasonable implementation gets
wrong: names fold to one case because Windows does, and timestamps two seconds
apart are the same moment because FAT and SMB round them. Both produce
differences that are not differences, and a comparison that cries wolf is one
people stop pressing.
"""

from __future__ import annotations

import pytest

from app.core.compare import (
    CLASH,
    DIFFERENT,
    FOLDER,
    MARKED,
    NEWER,
    OLDER,
    ONLY,
    SAME,
    compare,
    marks,
    summary,
)
from app.io.protocol import Entry

NOW = 1_700_000_000.0


def file(name: str, size: int = 10, mtime: float = NOW) -> Entry:
    return Entry(name=name, is_dir=False, size=size, mtime=mtime, attributes=0)


def folder(name: str) -> Entry:
    return Entry(name=name, is_dir=True, size=0, mtime=NOW, attributes=0)


# ------------------------------------------------------------------ verdicts

def test_the_same_file_on_both_sides_is_the_same() -> None:
    result = compare([file("a.txt")], [file("a.txt")])
    assert result.left == {"a.txt": SAME}
    assert result.right == {"a.txt": SAME}
    assert result.identical


def test_a_file_on_one_side_only() -> None:
    result = compare([file("a.txt")], [])
    assert result.left == {"a.txt": ONLY}
    assert result.right == {}
    assert not result.identical


def test_the_later_one_is_newer_and_the_other_is_older() -> None:
    """Both sides are recorded rather than one and an inverse, because the
    status line wants to say how many of each and they are different files.
    """
    result = compare([file("a.txt", mtime=NOW + 60)], [file("a.txt")])
    assert result.left == {"a.txt": NEWER}
    assert result.right == {"a.txt": OLDER}


def test_the_same_age_and_a_different_size_is_its_own_answer() -> None:
    """One of them is wrong and neither timestamp says which, which is worth a
    word of its own rather than being called newer.
    """
    result = compare([file("a.txt", size=10)], [file("a.txt", size=99)])
    assert result.left == {"a.txt": DIFFERENT}
    assert result.right == {"a.txt": DIFFERENT}


def test_a_folder_and_a_file_of_one_name_is_a_clash() -> None:
    result = compare([folder("thing")], [file("thing")])
    assert result.left == {"thing": CLASH}


def test_a_folder_on_both_sides_is_paired_and_not_looked_into() -> None:
    """Walking two trees over SMB is the queue's kind of work, not a
    keystroke's. What this says is that the folder is on both sides, which is
    the part that needs no walk.
    """
    result = compare([folder("jobs")], [folder("jobs")])
    assert result.left == {"jobs": FOLDER}
    assert result.identical


def test_a_folder_missing_on_one_side_is_still_reported() -> None:
    result = compare([folder("jobs")], [])
    assert result.left == {"jobs": ONLY}


# ------------------------------------------------------- the two forgiveness

def test_names_fold_to_one_case_because_windows_does() -> None:
    """Two files whose names differ only in case cannot be in one folder, so
    folding is safe here -- and not folding would report a folder as different
    from its own copy because something wrote `README.TXT`.
    """
    result = compare([file("README.txt")], [file("readme.TXT")])
    assert list(result.left.values()) == [SAME]


def test_timestamps_within_two_seconds_are_the_same_moment() -> None:
    """FAT keeps mtime to two seconds and a copy to a stick or a share lands on
    either side of the rounding. An exact comparison calls half of those files
    newer, every time.
    """
    assert compare([file("a", mtime=NOW + 1.5)], [file("a")]).left == {"a": SAME}
    assert compare([file("a", mtime=NOW + 3)], [file("a")]).left == {"a": NEWER}


def test_the_tolerance_can_be_widened() -> None:
    """A share that rounds harder than FAT does is a setting, not a rebuild."""
    result = compare([file("a", mtime=NOW + 3)], [file("a")], tolerance=5)
    assert result.left == {"a": SAME}


# --------------------------------------------------------------- the marking

def test_what_gets_marked_is_what_this_side_would_have_to_copy() -> None:
    """The useful end of the feature: after it runs, F5 copies exactly the
    rows that would make the other side match.
    """
    left = [file("only.txt"), file("newer.txt", mtime=NOW + 60),
            file("older.txt"), file("same.txt")]
    right = [file("newer.txt"), file("older.txt", mtime=NOW + 60),
             file("same.txt"), file("theirs.txt")]
    result = compare(left, right)
    assert marks(result.left) == ["only.txt", "newer.txt"]
    assert marks(result.right) == ["older.txt", "theirs.txt"]


def test_the_older_copy_is_deliberately_not_marked() -> None:
    """It is the same file. The copy that would fix it starts from the other
    pane, where it is marked as newer.
    """
    assert OLDER not in MARKED


def test_marks_keep_the_order_the_listing_holds() -> None:
    rows = [file("c"), file("a"), file("b")]
    result = compare(rows, [])
    assert marks(result.left) == ["c", "a", "b"]


# --------------------------------------------------------------- the summary

def test_the_summary_says_nothing_about_folders_it_did_not_look_into() -> None:
    """Reporting a paired folder as "the same" would be a claim this module
    has not earned.
    """
    line = summary(compare([folder("jobs")], [folder("jobs")]))
    assert "No differences" in line


def test_the_summary_counts_both_sides() -> None:
    result = compare([file("a"), file("b", mtime=NOW + 60)], [file("b")])
    line = summary(result)
    assert "1 newer here" in line
    assert "1 only here" in line


@pytest.mark.parametrize("size", [0, 1, 5_000])
def test_an_empty_folder_compares_without_complaint(size: int) -> None:
    rows = [file(f"f{n}") for n in range(size)]
    result = compare(rows, [])
    assert len(result.left) == size
    assert result.right == {}

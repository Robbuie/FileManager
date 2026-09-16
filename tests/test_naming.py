"""Checks on the name a duplicate is offered.

The habit this serves is a folder per day of PLC work: yesterday's is
duplicated and the copy named for today. The date has to come back in the form
the folder already wrote it, and nothing that merely looks like a date may be
rewritten.
"""

from __future__ import annotations

import datetime

import pytest

from app.core.naming import duplicate_name, with_date

TODAY = datetime.date(2026, 9, 16)


@pytest.mark.parametrize("name, expected", [
    ("2026-09-15", "2026-09-16"),
    ("Line 3 2026-09-15", "Line 3 2026-09-16"),
    ("2026_09_15 backup", "2026_09_16 backup"),
    ("2026.9.15", "2026.09.16"),
    ("20260915", "20260916"),
    ("PLC_20260915_rev", "PLC_20260916_rev"),
    ("09-15-2026", "09-16-2026"),
    ("9.15.2026 Press", "09.16.2026 Press"),
    ("260915", "260916"),
])
def test_the_date_comes_back_in_the_form_it_was_written(name, expected):
    assert duplicate_name(name, [name], TODAY) == expected


def test_a_date_from_long_ago_is_replaced_all_the_same():
    assert duplicate_name("2025-12-31", [], TODAY) == "2026-09-16"


@pytest.mark.parametrize("name", ["Pump 12345678", "Rev 99999999", "Cell 1234"])
def test_digits_that_are_not_a_date_are_left_alone(name):
    assert with_date(name, TODAY) is None
    assert duplicate_name(name, [name], TODAY) == f"{name} - Copy"


def test_when_today_already_exists_it_falls_back_to_a_copy_name():
    taken = ["2026-09-15", "2026-09-16"]
    assert duplicate_name("2026-09-15", taken, TODAY) == "2026-09-15 - Copy"


def test_copies_are_numbered_and_compared_as_windows_does():
    taken = ["Job", "job - copy", "JOB - Copy (2)"]
    assert duplicate_name("Job", taken, TODAY) == "Job - Copy (3)"


def test_a_file_keeps_its_extension_at_the_end():
    assert duplicate_name("plan.dwg", ["plan.dwg"], TODAY, is_dir=False) == "plan - Copy.dwg"
    assert duplicate_name("plc.2026.ACD", [], TODAY, is_dir=False) == "plc.2026 - Copy.ACD"


def test_a_folder_with_a_dot_is_not_given_an_extension():
    assert duplicate_name("v1.2", ["v1.2"], TODAY) == "v1.2 - Copy"


def test_a_dated_file_keeps_its_extension():
    assert duplicate_name("Line3_20260915.ACD", [], TODAY, is_dir=False) == "Line3_20260916.ACD"

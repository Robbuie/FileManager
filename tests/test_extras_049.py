"""0.49: the listing extras from the 30 September mockups.

Filtering by a column, the stripe for a share or a live folder, date chips in
day-folder names, the selection pill, placeholder rows and the taskbar
button. The rules behind them are plain functions and are tested here as such;
the widgets get the checks that need a widget.
"""

from __future__ import annotations

import datetime
import time
from types import SimpleNamespace

import pytest

from app.core import colfilter, location, naming, options
from app.core.config import DEFAULTS


# ------------------------------------------------------------ column filter

def test_a_plain_filter_is_still_a_name() -> None:
    spec = colfilter.parse("Line3")
    assert spec.name == "Line3" and not spec.by_column


def test_terms_and_a_name_together() -> None:
    spec = colfilter.parse("main ext:acd,.L5X size:>10mb modified:week")
    assert spec.name == "main"
    assert spec.exts == {"acd", "l5x"}
    assert spec.sizes == ((">", 10 * 1024 ** 2),)
    assert spec.modified == "week"


def test_a_term_still_being_typed_filters_nothing() -> None:
    spec = colfilter.parse("size:")
    assert not spec.by_column and not spec.problems


def test_a_term_that_does_not_read_is_reported() -> None:
    spec = colfilter.parse("size:huge modified:someday")
    assert len(spec.problems) == 2 and not spec.by_column


@pytest.mark.parametrize("text,size,expected", [
    ("size:>1k", 2048, True), ("size:>1k", 1024, False), ("size:<=1k", 1024, True),
    ("size:2mb", 2 * 1024 ** 2, True), ("size:=0", 0, True),
])
def test_sizes(text, size, expected) -> None:
    spec = colfilter.parse(text)
    assert colfilter.passes(spec, is_dir=False, ext="", size=size, mtime=0) is expected


def test_ext_and_size_leave_folders_out() -> None:
    for text in ("ext:dwg", "size:>0"):
        assert not colfilter.passes(colfilter.parse(text), is_dir=True, ext="",
                                    size=0, mtime=0)


def test_modified_window() -> None:
    now = time.time()
    spec = colfilter.parse("modified:today")
    assert colfilter.passes(spec, is_dir=False, ext="", size=1, mtime=now, now=now)
    assert not colfilter.passes(spec, is_dir=False, ext="", size=1,
                                mtime=now - 3 * 86400, now=now)


def test_kind() -> None:
    folders = colfilter.parse("kind:folder")
    assert colfilter.passes(folders, is_dir=True, ext="", size=0, mtime=0)
    assert not colfilter.passes(folders, is_dir=False, ext="x", size=0, mtime=0)


def test_the_model_filters_by_column() -> None:
    pytest.importorskip("PySide6")
    from app.core.listing import ListingModel
    from app.io.protocol import Entry

    model = ListingModel()
    model.begin(has_parent=False)
    model.add([Entry("a.dwg", False, 5 * 1024 ** 2, 1.0, 0),
               Entry("b.dwg", False, 10, 1.0, 0),
               Entry("c.pdf", False, 9 * 1024 ** 2, 1.0, 0),
               Entry("Drawings", True, 0, 1.0, 0)])
    model.set_filter("ext:dwg size:>1mb")
    assert [model.entry(r).name for r in range(model.rowCount())] == ["a.dwg"]
    model.set_filter("size:nonsense")
    assert model.filter_problems


# ------------------------------------------------------------------ location

def test_server_local_and_live() -> None:
    assert location.kind_of("C:\\Jobs", "C:\\Jobs", []) == location.LOCAL
    assert location.kind_of("S:\\Jobs", "\\\\fs01\\data\\Jobs", []) == location.SERVER
    assert location.kind_of("P:\\Line3\\Run", "P:\\Line3\\Run", ["p:\\line3"]) == location.LIVE


def test_live_is_by_whole_segments() -> None:
    assert location.live_root("P:\\Line30", ["P:\\Line3"]) is None
    assert location.live_root("P:\\Line3", ["P:\\Line3"]) == "P:\\Line3"


def test_live_marked_through_a_letter_holds_for_the_unc() -> None:
    assert location.kind_of("\\\\fs01\\p\\Line3", "\\\\fs01\\p\\Line3",
                            ["\\\\fs01\\p\\Line3"]) == location.LIVE


def test_toggling_takes_off_the_mark_that_covers() -> None:
    marked = location.toggled("P:\\Line3", [])
    assert marked == ["P:\\Line3"]
    assert location.toggled("P:\\Line3\\Run", marked) == []


# -------------------------------------------------------------- date chips

@pytest.mark.parametrize("name,day", [
    ("Line3_2026-09-30", datetime.date(2026, 9, 30)),
    ("09.26.2026 backup", datetime.date(2026, 9, 26)),
    ("20260925", datetime.date(2026, 9, 25)),
])
def test_date_span(name, day) -> None:
    start, end, found = naming.date_span(name)
    assert found == day
    assert name[start:end].replace("-", "").replace(".", "") != ""


def test_no_date_no_span() -> None:
    assert naming.date_span("Archive") is None


# ------------------------------------------------------------------- taskbar

def queue_with(job, paused=False):
    return SimpleNamespace(current=lambda: job, paused=paused)


def job(**fields):
    base = dict(percent=40, state="running", held=False, failed=0, total=100)
    base.update(fields)
    return SimpleNamespace(**base)


def test_taskbar_states() -> None:
    from app.ui import taskbar

    assert taskbar.state(queue_with(None)) == (taskbar.NOPROGRESS, 0.0)
    assert taskbar.state(queue_with(None), failed_since=True)[0] == taskbar.ERROR
    assert taskbar.state(queue_with(job())) == (taskbar.NORMAL, 0.4)
    assert taskbar.state(queue_with(job(), paused=True))[0] == taskbar.PAUSED
    assert taskbar.state(queue_with(job(state="waiting")))[0] == taskbar.PAUSED
    assert taskbar.state(queue_with(job(failed=1)))[0] == taskbar.ERROR
    assert taskbar.state(queue_with(job(state="scanning", total=0)))[0] \
        == taskbar.INDETERMINATE


def test_taskbar_off_windows_is_silent() -> None:
    import sys

    from app.ui import taskbar

    if sys.platform != "win32":
        bar = taskbar.Taskbar()
        bar.show(1234, taskbar.NORMAL, 0.5)
        assert bar.problem


# ----------------------------------------------------------------- settings

def test_settings_and_rows() -> None:
    for key in ("listing.stripes", "listing.date_chips", "listing.location_stripe",
                "places.live", "listing.selection_pill", "listing.placeholders",
                "transfers.taskbar"):
        assert key in DEFAULTS
    keys = {row.key for row in options.OPTIONS}
    assert {"listing.stripes", "listing.date_chips", "listing.location_stripe",
            "listing.selection_pill", "listing.placeholders",
            "transfers.taskbar"} <= keys
    assert DEFAULTS["listing.stripes"] is False
    assert not options.check()

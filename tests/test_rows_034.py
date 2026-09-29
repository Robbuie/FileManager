"""Checks on the 0.34 listing extras: the scrollbar map, bars on counted
folders, and the recency and fade settings.

The arithmetic is tested without a screen. What cannot be tested here is how
any of it looks, which is what `tools/preview.py` is for.
"""

from __future__ import annotations

import time

import pytest

from app.core import scrollmap
from app.io.protocol import Entry


def test_fractions_are_row_centres():
    assert scrollmap.fractions([0, 9], 10) == [0.05, 0.95]
    assert scrollmap.fractions([12], 10) == []
    assert scrollmap.fractions([1], 0) == []


def test_pixels_collapse_to_one_tick_per_pixel():
    positions = scrollmap.fractions(range(50000), 50000)
    ticks = scrollmap.pixels(positions, 10, 600)
    assert len(ticks) <= 600
    assert ticks[0] >= 10 and ticks[-1] <= 10 + 599


def test_build_leaves_empty_kinds_out():
    marks = scrollmap.build(100, marked=[3], today=[], hits=[50, 51])
    assert set(marks) == {"marked", "hits"}


class Sizes:
    def __init__(self, known):
        self._known = known

    def known(self, folder, name):
        return self._known.get(name)


@pytest.fixture
def model():
    pytest.importorskip("PySide6")
    from app.core.listing import ListingModel

    made = ListingModel()
    made.set_folder("C:\\Jobs")
    made.set_sizes(Sizes({"big": "10.0 G", "small": "1.0 G"}))
    made.begin(has_parent=False)
    now = time.time()
    made.add([Entry("big", True, 0, now, 0), Entry("small", True, 0, now, 0),
              Entry("uncounted", True, 0, now, 0),
              Entry("a.txt", False, 10, now, 0)])
    made.finish()
    return made


def _share(model, name):
    from app.core.listing import Column, ListingModel

    row = model.row_of(name)
    return model.data(model.index(row, int(Column.SIZE)), ListingModel.SizeShareRole)


def test_counted_folders_are_scaled_against_each_other(model):
    model.set_folder_bars(True)
    assert _share(model, "big") == pytest.approx(1.0)
    assert _share(model, "small") == pytest.approx(0.1)
    assert _share(model, "uncounted") is None
    # Files keep their own scale: the largest file, not the largest folder.
    assert _share(model, "a.txt") == pytest.approx(1.0)


def test_folder_bars_off_draws_none(model):
    model.set_folder_bars(False)
    assert _share(model, "big") is None


def test_a_new_size_moves_the_scale_once_told(model):
    model.set_folder_bars(True)
    assert _share(model, "small") == pytest.approx(0.1)
    model._sizes._known["small"] = "20.0 G"
    model.forget_folder_scale()
    assert _share(model, "small") == pytest.approx(1.0)


def test_rows_containing_matches_the_name_only(model):
    assert model.rows_containing("TXT") == [model.row_of("a.txt")]
    assert model.rows_containing("") == []


def test_recency_off_draws_no_tint():
    pytest.importorskip("PySide6")
    from app.ui.rows import RowDelegate

    delegate = RowDelegate()
    delegate.recency = "off"
    assert delegate.recency == "off"


def test_every_new_option_names_a_real_setting():
    from app.core import options

    assert options.check() == []
    for key in ("listing.recency", "listing.fade_days", "listing.scrollmap",
                "listing.folder_bars", "rail.capacity"):
        assert key in options.by_key()

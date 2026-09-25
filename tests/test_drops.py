"""Dragging rows between panes: what is carried, where it lands, and what is
refused before the button is let go (0.29.13)."""

from __future__ import annotations

import pytest

from app.core import drops


def test_the_paths_survive_the_trip() -> None:
    sent = ["C:\\Jobs\\a.dwg", "\\\\srv\\share\\b"]
    assert drops.decode(drops.encode(sent)) == sent


@pytest.mark.parametrize("data", [None, b"", b"not json", b"[1, 2]",
                                  b'{"sources": "C:\\\\x"}', b'{"other": []}'])
def test_anything_else_is_not_a_drag_of_ours(data) -> None:
    assert drops.decode(data) == []


def test_ctrl_moves_and_nothing_else_does() -> None:
    assert drops.is_move(True) is True
    assert drops.is_move(False) is False


def test_another_folder_takes_the_drop() -> None:
    assert drops.refusal(["C:\\Jobs\\a.dwg"], "D:\\Archive") == ""
    assert drops.refusal(["C:\\Jobs\\a.dwg"], "C:\\Jobs\\Sub") == ""


def test_the_folder_they_came_from_is_refused() -> None:
    assert drops.refusal(["C:\\Jobs\\a.dwg", "C:\\Jobs\\b.dwg"], "c:\\jobs\\") != ""


def test_a_folder_cannot_be_dropped_into_itself_or_below() -> None:
    assert drops.refusal(["C:\\Jobs\\Sub"], "C:\\Jobs\\Sub") != ""
    assert drops.refusal(["C:\\Jobs\\Sub"], "C:\\Jobs\\Sub\\Deeper") != ""
    # A sibling whose name merely starts the same way is somewhere else.
    assert drops.refusal(["C:\\Jobs\\Sub"], "C:\\Jobs\\Sub2") == ""


def test_the_listing_carries_the_format_and_the_parent_row_is_not_in_it() -> None:
    from app.core.listing import ListingModel
    from app.io.protocol import Entry

    model = ListingModel()
    model.set_folder("C:\\Jobs")
    model.begin(has_parent=True)
    model.add([Entry(name="a.dwg", is_dir=False, size=1, mtime=0.0, attributes=0),
               Entry(name="Sub", is_dir=True, size=0, mtime=0.0, attributes=0)])
    model.finish()
    indexes = [model.index(row, 0) for row in range(model.rowCount())]
    data = model.mimeData(indexes)
    carried = drops.decode(bytes(data.data(drops.DRAG_FORMAT).data()))
    assert sorted(carried) == ["C:\\Jobs\\Sub", "C:\\Jobs\\a.dwg"]

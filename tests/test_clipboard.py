"""The clipboard: the format Explorer expects, and the paste that must not run.

The format tests are the ones that matter most, because the failure they catch
is silent. A cut written without `Preferred DropEffect` still pastes -- it just
pastes as a copy, and the user finds out when the original is still there. So
the bytes are asserted, not the behaviour around them.

The refusal tests are the other half: a paste that must not happen is the only
part of this feature that could lose work, and it is pure string comparison, so
it can be checked exhaustively without a clipboard or a queue anywhere near it.
"""

from __future__ import annotations

import pytest

from app.core.clipboard import (
    DROP_EFFECT,
    DROPEFFECT_COPY,
    DROPEFFECT_MOVE,
    Clipboard,
    effect_bytes,
    read_effect,
    refusal,
)
from app.core.listing import ListingModel
from app.io.protocol import Entry

pytest.importorskip("PySide6")

from PySide6.QtCore import QMimeData, QUrl  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402


# ------------------------------------------------------------- the drop effect

def test_effect_bytes_are_what_windows_reads() -> None:
    """One little-endian unsigned word, four bytes, nothing else."""
    assert effect_bytes(DROPEFFECT_COPY) == b"\x01\x00\x00\x00"
    assert effect_bytes(DROPEFFECT_MOVE) == b"\x02\x00\x00\x00"


@pytest.mark.parametrize("raw, expected", [
    (b"\x02\x00\x00\x00", DROPEFFECT_MOVE),
    (b"\x01\x00\x00\x00", DROPEFFECT_COPY),
    (b"\x02\x00\x00\x00extra", DROPEFFECT_MOVE),   # more than four bytes
    (b"", 0),
    (b"\x02", 0),                                  # truncated is not a cut
    (None, 0),
])
def test_read_effect(raw, expected) -> None:
    assert read_effect(raw) == expected


def test_a_program_that_sets_both_bits_is_read_as_a_cut() -> None:
    """They are flags, not an enumeration, and some programs set more than one.

    Windows treats the move bit as the stronger of the two, so a word of 3 is
    a cut. Reading it as "equals 2" would turn that into a copy and leave the
    original behind.
    """
    assert read_effect(effect_bytes(DROPEFFECT_COPY | DROPEFFECT_MOVE)) & DROPEFFECT_MOVE


# ------------------------------------------------------------------ the paste

def test_nothing_on_the_clipboard_is_refused() -> None:
    assert refusal([], "C:\\somewhere", cut=False) == "nothing on the clipboard"


def test_a_folder_cannot_be_pasted_into_itself() -> None:
    why = refusal(["C:\\Jobs"], "C:\\Jobs", cut=False)
    assert "itself" in why


def test_a_folder_cannot_be_pasted_into_its_own_subtree() -> None:
    """The one that could fill a disk: the copy keeps finding what it wrote."""
    why = refusal(["C:\\Jobs"], "C:\\Jobs\\2026\\March", cut=False)
    assert "inside it" in why


def test_a_sibling_with_a_shared_prefix_is_not_a_subtree() -> None:
    """`C:\\Jobs2` starts with `C:\\Jobs` and is a different folder.

    A prefix test without the separator refuses a paste that is perfectly
    legal, which is the kind of bug that gets worked around rather than fixed.
    """
    assert refusal(["C:\\Jobs"], "C:\\Jobs2", cut=False) == ""


def test_a_cut_pasted_where_it_came_from_is_refused() -> None:
    why = refusal(["C:\\Jobs\\one.txt", "C:\\Jobs\\two.txt"], "C:\\Jobs", cut=True)
    assert why == "already in this folder"


def test_a_copy_pasted_where_it_came_from_is_allowed() -> None:
    """That is how a duplicate is made; the caller asks the queue to rename."""
    assert refusal(["C:\\Jobs\\one.txt"], "C:\\Jobs", cut=False) == ""


def test_a_cut_from_two_folders_into_one_of_them_is_allowed() -> None:
    """Only a cut where *everything* is already here is nothing happening."""
    assert refusal(["C:\\Jobs\\one.txt", "D:\\other\\two.txt"],
                   "C:\\Jobs", cut=True) == ""


def test_the_comparison_is_made_on_the_resolved_form() -> None:
    """`S:\\Jobs` and `\\\\server\\jobs` are one folder.

    A check that compared the displayed spelling would let a folder be pasted
    into itself through a drive letter, which is the subtree case arriving by
    the back door.
    """
    mapping = {"S:": "\\\\server\\jobs"}
    why = refusal(["S:\\Drawings"], "\\\\server\\jobs\\Drawings\\old",
                  cut=False, mapping=mapping)
    assert "inside it" in why


def test_case_does_not_decide_a_refusal() -> None:
    assert refusal(["C:\\JOBS"], "c:\\jobs\\inner", cut=False) != ""


# -------------------------------------------------------------- the clipboard

@pytest.fixture
def board():
    """The system clipboard, emptied before and after."""
    handle = QGuiApplication.clipboard()
    if handle is None:
        pytest.skip("no clipboard on this platform")
    handle.clear()
    yield handle
    handle.clear()


def test_copy_writes_urls_and_a_copy_effect(board) -> None:
    clipboard = Clipboard()
    assert clipboard.copy(["C:\\Jobs\\one.txt", "C:\\Jobs\\two.txt"])
    data = board.mimeData()
    assert data.hasUrls()
    assert bytes(data.data(DROP_EFFECT)) == effect_bytes(DROPEFFECT_COPY)


def test_cut_writes_the_move_effect(board) -> None:
    clipboard = Clipboard()
    clipboard.cut(["C:\\Jobs\\one.txt"])
    data = board.mimeData()
    assert bytes(data.data(DROP_EFFECT)) == effect_bytes(DROPEFFECT_MOVE)


def test_the_files_are_the_payload_not_a_text_copy(board) -> None:
    """Qt synthesizes a text form from the URLs; nothing here adds one.

    The distinction matters at the other end of a paste: an application that
    puts its own text on alongside the files can have a program pick up the
    paths as a string when it wanted the files. What is asserted is that the
    URLs are what was written and that any text is Qt deriving it from them.
    """
    clipboard = Clipboard()
    clipboard.copy(["C:\\Jobs\\one.txt"])
    data = board.mimeData()
    assert data.hasUrls()
    assert not data.text() or "one.txt" in data.text()


def test_a_round_trip_gives_the_paths_back(board) -> None:
    clipboard = Clipboard()
    clipboard.copy(["C:\\Jobs\\one.txt", "C:\\Jobs\\two.txt"])
    found, cut = clipboard.contents()
    assert [item.lower() for item in found] == \
        ["c:\\jobs\\one.txt", "c:\\jobs\\two.txt"]
    assert cut is False


def test_a_cut_round_trips_as_a_cut(board) -> None:
    clipboard = Clipboard()
    clipboard.cut(["C:\\Jobs\\one.txt"])
    _, cut = clipboard.contents()
    assert cut is True


def test_a_clipboard_written_the_way_explorer_writes_one_is_read(board) -> None:
    """The case this feature exists for: the copy was made in another program.

    Built here the way Explorer builds it -- `CF_HDROP` as URLs, and four raw
    bytes -- rather than by calling this module, so the test would still fail
    if both halves of ours changed together.
    """
    data = QMimeData()
    data.setUrls([QUrl.fromLocalFile("C:\\Jobs\\from-explorer.txt")])
    data.setData(DROP_EFFECT, b"\x02\x00\x00\x00")
    board.setMimeData(data)

    clipboard = Clipboard()
    found, cut = clipboard.contents()
    assert [item.lower() for item in found] == ["c:\\jobs\\from-explorer.txt"]
    assert cut is True


def test_files_without_a_drop_effect_are_a_copy(board) -> None:
    data = QMimeData()
    data.setUrls([QUrl.fromLocalFile("C:\\Jobs\\one.txt")])
    board.setMimeData(data)
    _, cut = Clipboard().contents()
    assert cut is False


def test_text_on_the_clipboard_is_not_files(board) -> None:
    board.setText("C:\\Jobs\\one.txt")
    clipboard = Clipboard()
    assert clipboard.has_files() is False
    assert clipboard.contents() == ([], False)


# --------------------------------------------------------------- the cut mark

def test_cut_names_are_the_names_in_that_folder(board) -> None:
    clipboard = Clipboard()
    clipboard.cut(["C:\\Jobs\\one.txt", "C:\\Jobs\\two.txt",
                   "C:\\Elsewhere\\three.txt"])
    assert clipboard.cut_names("C:\\Jobs") == {"one.txt", "two.txt"}
    assert clipboard.cut_names("C:\\Elsewhere") == {"three.txt"}
    assert clipboard.cut_names("C:\\Nothing") == frozenset()


def test_a_copy_marks_nothing(board) -> None:
    clipboard = Clipboard()
    clipboard.copy(["C:\\Jobs\\one.txt"])
    assert clipboard.cut_names("C:\\Jobs") == frozenset()


def test_the_folder_answer_is_worked_out_once(board, monkeypatch) -> None:
    """The mark is asked for once per row painted, so it has to be a lookup.

    Counted rather than timed: at 50,000 rows the difference between one
    resolution and 50,000 is the difference between a listing and a hang, and
    a cache that quietly stopped working would show up as nothing but slowness.
    """
    clipboard = Clipboard()
    clipboard.cut(["C:\\Jobs\\one.txt"])

    from app.core import clipboard as module
    calls = []
    real = module.paths.resolve
    monkeypatch.setattr(module.paths, "resolve",
                        lambda *a, **k: (calls.append(a), real(*a, **k))[1])
    clipboard.cut_names("C:\\Jobs")
    first = len(calls)
    for _ in range(200):
        clipboard.cut_names("C:\\Jobs")
    assert len(calls) == first


def test_the_clipboard_is_the_authority_for_the_mark(board) -> None:
    """A copy made anywhere clears a cut mark this application put up.

    Recomputed from the clipboard rather than remembered, which is also what
    lets a cut made in Explorer grey the rows here.
    """
    clipboard = Clipboard()
    clipboard.cut(["C:\\Jobs\\one.txt"])
    assert clipboard.cut_names("C:\\Jobs")

    data = QMimeData()
    data.setUrls([QUrl.fromLocalFile("C:\\Jobs\\two.txt")])
    data.setData(DROP_EFFECT, effect_bytes(DROPEFFECT_COPY))
    board.setMimeData(data)
    clipboard._on_data_changed()  # noqa: SLF001 - dataChanged, without the loop

    assert clipboard.cut_names("C:\\Jobs") == frozenset()


def test_clear_takes_the_mark_down(board) -> None:
    clipboard = Clipboard()
    clipboard.cut(["C:\\Jobs\\one.txt"])
    clipboard.clear()
    assert clipboard.cut_names("C:\\Jobs") == frozenset()
    assert clipboard.has_files() is False


# ------------------------------------------------------------------ the model

def test_the_model_asks_the_clipboard_for_the_cut_role(board) -> None:
    clipboard = Clipboard()
    clipboard.cut(["C:\\Jobs\\one.txt"])

    model = ListingModel()
    model.set_cut(clipboard)
    model.set_folder("C:\\Jobs")
    model.add([Entry(name="one.txt", is_dir=False, size=1, mtime=0.0, attributes=0),
               Entry(name="two.txt", is_dir=False, size=1, mtime=0.0, attributes=0)])

    marks = {model.data(model.index(row, 0), ListingModel.EntryRole).name:
             model.data(model.index(row, 0), ListingModel.CutRole)
             for row in range(model.rowCount())
             if model.data(model.index(row, 0), ListingModel.EntryRole) is not None}
    assert marks == {"one.txt": True, "two.txt": False}


def test_a_model_without_a_clipboard_marks_nothing() -> None:
    model = ListingModel()
    model.set_folder("C:\\Jobs")
    model.add([Entry(name="one.txt", is_dir=False, size=1, mtime=0.0, attributes=0)])
    row = next(r for r in range(model.rowCount())
               if model.data(model.index(r, 0), ListingModel.EntryRole) is not None)
    assert model.data(model.index(row, 0), ListingModel.CutRole) is False


# ------------------------------------------------------------- what the pane
# ------------------------------------------------------------- does with it

class _NoBridge:
    """A pane can be built without a worker behind it; paste never asks one."""


class _Recording:
    """The queue, as far as a paste can tell."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def copy(self, sources, destination, **kwargs) -> int:
        self.calls.append(("copy", list(sources), destination, kwargs))
        return 1

    def move(self, sources, destination, **kwargs) -> int:
        self.calls.append(("move", list(sources), destination, kwargs))
        return 1


def _pane(clipboard, queue, folder: str):
    import os

    from app.core.config import Config
    from app.core.pane import Pane

    config = Config({}, path=os.devnull)
    pane = Pane(_NoBridge(), config, "left", None, None, None, None, None,
                transfers=queue, clipboard=clipboard)
    pane.current.path = folder
    return pane


def test_a_refused_paste_says_so_on_the_pane_own_status_line(board) -> None:
    """The pane answers where it answers everything else.

    This was reported in the window's status bar first, which is the far
    corner of the window from the pane that was right-clicked and is gone six
    seconds later. It read as a key that had done nothing, which is exactly
    what a refusal is not.
    """
    from app.core.pane import BAD

    clipboard = Clipboard()
    clipboard.cut(["C:\\Jobs"])
    queue = _Recording()
    pane = _pane(clipboard, queue, "C:\\Jobs")

    assert pane.paste() == "Jobs cannot be pasted into itself"
    assert pane.current.status_text == "Jobs cannot be pasted into itself"
    assert pane.current.status_state == BAD
    assert queue.calls == []


def test_a_paste_into_the_folder_the_copy_came_from_renames(board) -> None:
    """How a duplicate is made. Asking about every name the user collided with
    on purpose is a dialog they have already answered.
    """
    from app.io.protocol import Conflict

    clipboard = Clipboard()
    clipboard.copy(["C:\\Jobs\\one.txt"])
    queue = _Recording()
    pane = _pane(clipboard, queue, "C:\\Jobs")

    assert pane.paste() == ""
    kind, sources, destination, kwargs = queue.calls[0]
    assert kind == "copy"
    assert kwargs["conflict"] is Conflict.RENAME


def test_a_cut_pasted_elsewhere_moves_and_spends_the_clipboard(board) -> None:
    """Explorer empties the clipboard after a cut is pasted, and so does this.

    A cut left on the clipboard would offer to move the same files again, out
    of a folder they are no longer in.
    """
    from app.io.protocol import Conflict

    clipboard = Clipboard()
    clipboard.cut(["C:\\Jobs\\one.txt"])
    queue = _Recording()
    pane = _pane(clipboard, queue, "D:\\Landing")

    assert pane.paste() == ""
    kind, sources, destination, kwargs = queue.calls[0]
    assert kind == "move"
    assert destination == "D:\\Landing"
    assert kwargs["conflict"] is Conflict.ASK
    assert clipboard.has_files() is False

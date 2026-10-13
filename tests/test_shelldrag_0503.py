"""0.50.3: a file dragged into Outlook arrived as its name.

Qt's drag offers a dragged file as a link as well as a file, and Outlook's
editor takes the link. Drags out of a listing now go to the shell, which builds
the same data object Explorer drags. The shell call needs Windows; what is
tested here is everything around it -- the folder the files share, and that a
drop on one of this application's own panes still recognises its own drag
while the shell is running it, and nothing else.
"""

from __future__ import annotations

from app.core import drops
from app.ui import shelldrag


def test_common_folder() -> None:
    assert shelldrag.common_folder([r"C:\Jobs\a.dwg", r"C:\Jobs\b.pdf"]) == r"C:\Jobs"
    assert shelldrag.common_folder([r"C:\Jobs\a.dwg", r"C:\jobs\B.pdf"]) == r"C:\Jobs"
    assert shelldrag.common_folder([r"C:\Jobs\a.dwg", r"C:\Other\b.pdf"]) == ""
    assert shelldrag.common_folder([r"C:\a.dwg"]) == "C:\\"
    assert shelldrag.common_folder([r"\\fs01\data\Line3\a.acd"]) == r"\\fs01\data\Line3"


def test_own_format_is_still_read_first() -> None:
    own = drops.encode([r"C:\Jobs\a.dwg"])
    assert drops.sources_from(own, []) == [r"C:\Jobs\a.dwg"]


def test_a_shell_drag_of_ours_is_recognised_by_its_files() -> None:
    drops.outgoing = [r"C:\Jobs\a.dwg", r"C:\Jobs\b.pdf"]
    try:
        assert drops.sources_from(None, ["C:/Jobs/b.pdf", "C:/jobs/A.dwg"]) == \
            [r"C:\Jobs\a.dwg", r"C:\Jobs\b.pdf"]
    finally:
        drops.outgoing = None


def test_a_drag_from_explorer_is_still_refused() -> None:
    assert drops.sources_from(None, ["C:/Jobs/a.dwg"]) == []
    drops.outgoing = [r"C:\Jobs\a.dwg"]
    try:
        # Different files from what this window is dragging: somebody else's.
        assert drops.sources_from(None, ["C:/Jobs/other.dwg"]) == []
        assert drops.sources_from(None, ["C:/Jobs/a.dwg", "C:/Jobs/extra.dwg"]) == []
    finally:
        drops.outgoing = None


def test_off_windows_the_shell_drag_steps_aside() -> None:
    import sys

    if sys.platform != "win32":
        assert shelldrag.drag(0, [r"C:\a.txt"]) == (False, "not Windows")


def test_the_setting() -> None:
    from app.core import options
    from app.core.config import DEFAULTS

    assert DEFAULTS["listing.drag_out"] == shelldrag.FILE_LIST
    row = next(row for row in options.OPTIONS if row.key == "listing.drag_out")
    assert tuple(value for value, _label in row.choices) == shelldrag.DRAG_OUT
    assert "listing.shell_drag" not in DEFAULTS
    assert not options.check()


def test_the_file_list_is_what_windows_reads() -> None:
    """0.56.2: `CF_HDROP` built by hand -- a 20-byte `DROPFILES` saying the
    names start straight after it and are wide, then each path ended by a
    null and the list by one more."""
    import struct

    raw = shelldrag.dropfiles([r"C:\Jobs\a.dwg", r"\\fs01\data\b.pdf"])
    offset, x, y, nc, wide = struct.unpack("<IiiII", raw[:20])
    assert (offset, x, y, nc, wide) == (20, 0, 0, 0, 1)
    names = raw[20:].decode("utf-16-le")
    assert names == "C:\\Jobs\\a.dwg\0\\\\fs01\\data\\b.pdf\0\0"


def test_off_windows_the_file_list_drag_steps_aside() -> None:
    import sys

    if sys.platform != "win32":
        assert shelldrag.drag_file_list([r"C:\a.txt"]) == (False, "not Windows")


def test_a_drag_is_not_started_inside_the_one_running() -> None:
    """0.56.3: the view's mouseMoveEvent can call startDrag again while the
    drag runs. That second call is swallowed rather than nested."""
    from app.ui import shelldrag

    class View:
        drag_out = shelldrag.FILE_LIST

        def model(self):  # pragma: no cover - must not be reached
            raise AssertionError("a second drag was started")

    shelldrag._running = True
    try:
        assert shelldrag.start_from_view(View()) is True
    finally:
        shelldrag._running = False

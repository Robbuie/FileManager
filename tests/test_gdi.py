"""`io/gdi.py` off Windows: it must load, say it is unavailable, and not raise."""

from __future__ import annotations

import sys

import pytest

from app.io import gdi


@pytest.mark.skipif(sys.platform == "win32", reason="the Windows answer is a real draw")
def test_off_windows_it_says_so_rather_than_raising():
    assert not gdi.available()
    problems: list[str] = []
    assert gdi.draw_icon(1, 16, 0, problems) is None
    assert problems == ["drawing icons needs Windows"]
    assert gdi.bitmap_pixels(1) is None


def test_the_bitmap_structure_matches_windows_on_64_bit():
    # BITMAP is 32 bytes on x64: four LONGs, two WORDs, then a pointer
    # aligned to eight. A wrong size here is GetObjectW returning 0.
    import ctypes
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        assert ctypes.sizeof(gdi.BITMAP) == 32

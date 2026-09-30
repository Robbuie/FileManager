"""The few GDI calls that turn a Windows icon or bitmap into bytes.

0.39: this replaces pywin32's `win32ui`, which was imported for exactly two
things -- drawing an icon onto a memory surface, and reading the pixels of a
menu bitmap -- and which cost the installer 6.6 MB to do them: `win32ui.pyd`
is a wrapper over MFC, and MFC (`mfc140u.dll`) came along behind it. Both jobs
are a dozen `gdi32` and `user32` calls, and `ctypes` makes those calls
directly.

Every handle is declared as a pointer rather than left to ctypes' default of a
C `int`. On 64-bit Windows a handle is pointer-sized, and a handle truncated to
32 bits usually still works -- which is what makes the default dangerous: the
one that does not is a blank icon on somebody else's machine.

Nothing here raises. A drawing that fails returns None, the caller treats that
as one missing icon, and the reason goes into `problems` when there is a list
to put it in, the way the code this replaced did.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

if sys.platform == "win32":
    try:
        _gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        _user32 = ctypes.WinDLL("user32", use_last_error=True)
    except OSError:  # pragma: no cover - gdi32 is part of Windows itself
        _gdi32 = _user32 = None
else:
    _gdi32 = _user32 = None

HANDLE = ctypes.c_void_p

#: DrawIconEx's flag for "the image and its mask", the ordinary way to draw.
DI_NORMAL = 0x0003


class BITMAP(ctypes.Structure):
    # Fixed widths rather than wintypes.LONG: on Windows the two agree, and
    # spelling them out keeps the layout test meaningful on any machine.
    _fields_ = [
        ("bmType", ctypes.c_int32),
        ("bmWidth", ctypes.c_int32),
        ("bmHeight", ctypes.c_int32),
        ("bmWidthBytes", ctypes.c_int32),
        ("bmPlanes", ctypes.c_uint16),
        ("bmBitsPixel", ctypes.c_uint16),
        ("bmBits", ctypes.c_void_p),
    ]


def _declare() -> None:
    if _gdi32 is None or _user32 is None:
        return
    signatures = (
        (_user32.GetDC, [HANDLE], HANDLE),
        (_user32.ReleaseDC, [HANDLE, HANDLE], ctypes.c_int),
        (_user32.FillRect, [HANDLE, ctypes.POINTER(wintypes.RECT), HANDLE], ctypes.c_int),
        (_user32.DrawIconEx, [HANDLE, ctypes.c_int, ctypes.c_int, HANDLE, ctypes.c_int,
                              ctypes.c_int, wintypes.UINT, HANDLE, wintypes.UINT],
         wintypes.BOOL),
        (_gdi32.CreateCompatibleDC, [HANDLE], HANDLE),
        (_gdi32.CreateCompatibleBitmap, [HANDLE, ctypes.c_int, ctypes.c_int], HANDLE),
        (_gdi32.SelectObject, [HANDLE, HANDLE], HANDLE),
        (_gdi32.CreateSolidBrush, [wintypes.DWORD], HANDLE),
        (_gdi32.DeleteObject, [HANDLE], wintypes.BOOL),
        (_gdi32.DeleteDC, [HANDLE], wintypes.BOOL),
        (_gdi32.GetObjectW, [HANDLE, ctypes.c_int, ctypes.c_void_p], ctypes.c_int),
        (_gdi32.GetBitmapBits, [HANDLE, wintypes.LONG, ctypes.c_void_p], wintypes.LONG),
    )
    for function, arguments, result in signatures:
        function.argtypes = arguments
        function.restype = result


_declare()


def available() -> bool:
    """Whether these calls can be made at all: Windows, in short."""
    return _gdi32 is not None and _user32 is not None


def _bitmap_info(handle: int) -> BITMAP | None:
    info = BITMAP()
    if _gdi32.GetObjectW(handle, ctypes.sizeof(info), ctypes.byref(info)) == 0:
        return None
    return info


def _read_bits(handle: int, count: int) -> bytes | None:
    buffer = ctypes.create_string_buffer(count)
    copied = _gdi32.GetBitmapBits(handle, count, buffer)
    if copied != count:
        return None
    return buffer.raw


def bitmap_pixels(handle: int) -> tuple[bytes, int, int, int] | None:
    """A bitmap's raw bits, with its width, height and bits per pixel.

    What `win32ui.CreateBitmapFromHandle(...).GetBitmapBits(True)` returned,
    plus the three fields of `GetInfo()` the caller looked at. The bitmap is
    read, never taken: it belongs to whoever handed over the handle.
    """
    if not available() or not handle:
        return None
    try:
        info = _bitmap_info(handle)
        if info is None:
            return None
        count = int(info.bmWidthBytes) * abs(int(info.bmHeight))
        if count <= 0:
            return None
        bits = _read_bits(handle, count)
        if bits is None:
            return None
        return bits, int(info.bmWidth), abs(int(info.bmHeight)), int(info.bmBitsPixel)
    except Exception:  # noqa: BLE001 - one bitmap, not worth a traceback
        return None


def draw_icon(hicon: int, size: int, fill: int,
              problems: list[str] | None = None) -> bytes | None:
    """An icon drawn at `size` onto a solid `fill` (a COLORREF), as BGRA bytes.

    The surface is compatible with the screen, so on the 32-bit desktop every
    Windows 10 and 11 machine has it comes back as four bytes a pixel; the
    caller checks the length rather than trusting that. Every object created
    here is released here, the old bitmap is put back into the memory DC
    before either is deleted, and the screen DC is released last.
    """
    if not available():
        if problems is not None:
            problems.append("drawing icons needs Windows")
        return None
    screen = _user32.GetDC(None)
    if not screen:
        if problems is not None:
            problems.append("no screen device context to draw on")
        return None
    memory = bitmap = previous = brush = None
    try:
        memory = _gdi32.CreateCompatibleDC(screen)
        bitmap = _gdi32.CreateCompatibleBitmap(screen, size, size)
        if not memory or not bitmap:
            if problems is not None:
                problems.append("could not create a surface to draw the icon on")
            return None
        previous = _gdi32.SelectObject(memory, bitmap)
        brush = _gdi32.CreateSolidBrush(fill & 0xFFFFFF)
        area = wintypes.RECT(0, 0, size, size)
        if brush:
            _user32.FillRect(memory, ctypes.byref(area), brush)
        if not _user32.DrawIconEx(memory, 0, 0, hicon, size, size, 0, None, DI_NORMAL):
            if problems is not None:
                problems.append(f"DrawIconEx failed ({ctypes.get_last_error()})")
            return None
        # Deselect before reading, so the bits are the bitmap's and not a
        # snapshot of a surface Windows may still be batching work into.
        _gdi32.SelectObject(memory, previous)
        previous = None
        info = _bitmap_info(bitmap)
        if info is None:
            return None
        return _read_bits(bitmap, int(info.bmWidthBytes) * abs(int(info.bmHeight)))
    except Exception as exc:  # noqa: BLE001 - a drawing failure is one missing icon
        if problems is not None:
            problems.append(f"drawing the icon failed: {exc}")
        return None
    finally:
        if previous is not None and memory:
            _gdi32.SelectObject(memory, previous)
        if brush:
            _gdi32.DeleteObject(brush)
        if bitmap:
            _gdi32.DeleteObject(bitmap)
        if memory:
            _gdi32.DeleteDC(memory)
        _user32.ReleaseDC(None, screen)

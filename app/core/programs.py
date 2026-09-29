"""Bringing another program's window to the front (0.37).

What "Switch to it" does after a copy or a delete failed because a file was
open somewhere: the failure names the process (see `app/io/holders.py`), and
this finds that process's main window and asks Windows to show it. A question
about windows, not files, so it is answered here in the window's process.

Windows does not let a program steal the foreground on a whim, and it is right
not to. This is called from a click, which is exactly the case it allows; if
it still refuses, the window's taskbar button flashes instead, which is the
answer Windows chooses and the one to accept.
"""

from __future__ import annotations

import ctypes
import os


def bring_forward(pid: int) -> bool:
    """Show the first visible top-level window belonging to `pid`.

    True if one was found and asked to come forward. False off Windows, for a
    process with no window (a service), and for one that has since exited.
    """
    if os.name != "nt" or pid <= 0:
        return False
    user32 = ctypes.windll.user32
    found: list[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def visit(hwnd, _param):
        owner = ctypes.c_ulong(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd) \
                and not user32.GetWindow(hwnd, 4):     # GW_OWNER: a top level
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(visit, 0)
    if not found:
        return False
    hwnd = found[0]
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)                      # SW_RESTORE
    user32.SetForegroundWindow(hwnd)
    return True

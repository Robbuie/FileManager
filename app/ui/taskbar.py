"""The window's taskbar button as a progress bar while a transfer runs.

0.49. A long copy to a server is the moment somebody switches to another
window, and the pill is then out of sight. Windows will fill the application's
taskbar button for it -- green while running, amber when paused or waiting on
an answer, red when something failed -- through `ITaskbarList3`.

Two halves, kept apart for the reason `fit_popup` is a plain function: `state`
decides what the button should say from the queue, and is tested anywhere;
`Taskbar` says it to Windows, by calling the COM interface through its vtable
with ctypes -- pywin32 does not wrap `ITaskbarList3`, and adding comtypes for
two calls would be a new dependency in the installer. Off Windows, and if the
interface cannot be had, `Taskbar` does nothing and says so once.

Nothing here touches a filesystem.
"""

from __future__ import annotations

import sys

#: TBPF_* values, from shobjidl_core.h.
NOPROGRESS = 0x0
INDETERMINATE = 0x1
NORMAL = 0x2
ERROR = 0x4
PAUSED = 0x8


def state(queue, *, failed_since: bool = False) -> tuple[int, float]:
    """`(TBPF flag, fraction)` for the queue as it is now.

    No job is no progress, unless the last one failed and nobody has looked at
    the window since -- then the button stays red until they do. A job that
    is scanning has no total yet and is drawn indeterminate rather than as an
    empty bar that looks stuck.
    """
    if queue is None:
        return NOPROGRESS, 0.0
    job = queue.current()
    if job is None:
        return (ERROR, 1.0) if failed_since else (NOPROGRESS, 0.0)
    fraction = max(0.0, min(1.0, job.percent / 100.0))
    if job.state == "waiting" or getattr(queue, "paused", False) or job.held:
        return PAUSED, fraction
    if job.failed:
        return ERROR, fraction
    if job.state in ("queued", "scanning") or not job.total:
        return INDETERMINATE, 0.0
    return NORMAL, fraction


class Taskbar:
    """`ITaskbarList3` for one window, or a polite nothing."""

    def __init__(self) -> None:
        self._list = None
        self._shown = (NOPROGRESS, -1)
        self.problem = ""
        if sys.platform != "win32":
            self.problem = "not Windows"
            return
        try:
            self._list = _create()
        except OSError as exc:  # noqa: PERF203 - once, at startup
            self.problem = f"taskbar progress unavailable: {exc}"

    def show(self, hwnd: int, flag: int, fraction: float) -> None:
        if self._list is None or not hwnd:
            return
        step = int(round(fraction * 1000))
        if (flag, step) == self._shown:
            return
        try:
            _call(self._list, _SET_STATE, hwnd, flag)
            if flag in (NORMAL, PAUSED, ERROR):
                _call(self._list, _SET_VALUE, hwnd, step, 1000)
            self._shown = (flag, step)
        except OSError as exc:
            self.problem = f"taskbar progress failed: {exc}"
            self._list = None


# ----------------------------------------------------------------- COM, raw

#: Vtable slots: IUnknown (0-2), ITaskbarList (HrInit 3, AddTab 4, DeleteTab 5,
#: ActivateTab 6, SetActiveAlt 7), ITaskbarList2 (MarkFullscreenWindow 8),
#: ITaskbarList3 (SetProgressValue 9, SetProgressState 10, ...).
_HR_INIT = 3
_SET_VALUE = 9
_SET_STATE = 10

_CLSID_TASKBARLIST = "{56FDF344-FD6D-11d0-958A-006097C9A090}"
_IID_ITASKBARLIST3 = "{EA1AFB91-9E28-4B86-90E9-9E9F8A5EEFAF}"


def _guid(text: str):
    import ctypes

    class GUID(ctypes.Structure):
        _fields_ = [("a", ctypes.c_ulong), ("b", ctypes.c_ushort),
                    ("c", ctypes.c_ushort), ("d", ctypes.c_ubyte * 8)]

    guid = GUID()
    result = ctypes.oledll.ole32.CLSIDFromString(ctypes.c_wchar_p(text),
                                                  ctypes.byref(guid))
    if result:
        raise OSError(f"CLSIDFromString {result:#x}")
    return guid


def _create():
    import ctypes

    ole32 = ctypes.oledll.ole32
    ole32.CoInitialize(None)    # already initialised by Qt: S_FALSE, harmless
    pointer = ctypes.c_void_p()
    ole32.CoCreateInstance(ctypes.byref(_guid(_CLSID_TASKBARLIST)), None, 1,
                           ctypes.byref(_guid(_IID_ITASKBARLIST3)),
                           ctypes.byref(pointer))
    if not pointer.value:
        raise OSError("CoCreateInstance gave no interface")
    _call(pointer, _HR_INIT)
    return pointer


def _call(pointer, slot: int, *args) -> None:
    import ctypes

    vtable = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    if slot == _SET_VALUE:
        prototype = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p,
                                       ctypes.c_ulonglong, ctypes.c_ulonglong)
    elif slot == _SET_STATE:
        prototype = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p,
                                       ctypes.c_int)
    else:
        prototype = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)
    function = prototype(vtable[slot])
    result = function(pointer, *[ctypes.c_void_p(a) if i == 0 and slot != _HR_INIT
                                 else a for i, a in enumerate(args)])
    if result < 0:
        raise OSError(f"ITaskbarList3 slot {slot}: {result & 0xFFFFFFFF:#x}")

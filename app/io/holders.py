"""Which program has a file open, from the Restart Manager (0.37).

"The process cannot access the file because it is being used by another
process" is Windows' least useful sentence: it is true, and it names nobody.
The Restart Manager is the API installers use to find out what to close before
replacing a file, and it answers exactly the question that sentence leaves
open -- so a failure on a locked file can say "open in Logix Designer
(PID 4412)" and the window can offer to bring that program forward.

Asked only after something has already failed, and only for the one file, so
the ordinary path costs nothing. It is a local question: the Restart Manager
knows about processes on this machine, which covers a file on a local disk and
a file on a share that *this* machine has open. A share file held open by
somebody else's computer is not something it can see, and then the message is
simply what it was before.

Runs in whichever process hit the failure -- the ops process or a worker --
never in the window, because it is asked about a path. No dependency: ctypes
and `rstrtmgr.dll`, which every supported Windows has.
"""

from __future__ import annotations

import ctypes
import os
import re
from ctypes import wintypes

#: The failures worth asking about: a sharing violation, a lock violation,
#: and access denied -- which is what deleting a running program's file says.
LOCKED_WINERRORS = frozenset({32, 33, 5})

#: How the answer is written into a failure's message, and read back out of it
#: by `parse` on the window's side. A sentence first, because the message is
#: shown as it is in the queue and the history; the PID in brackets is what
#: makes it parseable.
_MARK = " -- open in {name} (PID {pid})"
_READ = re.compile(r" -- open in (?P<name>.+?) \(PID (?P<pid>\d+)\)")

_CCH_RM_SESSION_KEY = 32
_CCH_RM_MAX_APP_NAME = 255
_CCH_RM_MAX_SVC_NAME = 63
_ERROR_MORE_DATA = 234


class _UniqueProcess(ctypes.Structure):
    _fields_ = [("dwProcessId", wintypes.DWORD),
                ("ProcessStartTime", wintypes.FILETIME)]


class _ProcessInfo(ctypes.Structure):
    _fields_ = [("Process", _UniqueProcess),
                ("strAppName", ctypes.c_wchar * (_CCH_RM_MAX_APP_NAME + 1)),
                ("strServiceShortName", ctypes.c_wchar * (_CCH_RM_MAX_SVC_NAME + 1)),
                ("ApplicationType", ctypes.c_int),
                ("AppStatus", wintypes.ULONG),
                ("TSSessionId", wintypes.DWORD),
                ("bRestartable", wintypes.BOOL)]


def holding(path: str) -> list[tuple[int, str]]:
    """`(pid, program name)` for every process with `path` open. [] when
    nobody is, when it cannot be asked, and everywhere but Windows."""
    if os.name != "nt" or not path:
        return []
    try:
        manager = ctypes.WinDLL("rstrtmgr")
    except OSError:
        return []
    session = wintypes.DWORD(0)
    key = ctypes.create_unicode_buffer(_CCH_RM_SESSION_KEY + 1)
    if manager.RmStartSession(ctypes.byref(session), 0, key) != 0:
        return []
    try:
        files = (wintypes.LPCWSTR * 1)(path)
        if manager.RmRegisterResources(session, 1, files, 0, None, 0, None) != 0:
            return []
        needed = wintypes.UINT(0)
        count = wintypes.UINT(0)
        reasons = wintypes.DWORD(0)
        result = manager.RmGetList(session, ctypes.byref(needed), ctypes.byref(count),
                                   None, ctypes.byref(reasons))
        if result not in (0, _ERROR_MORE_DATA) or needed.value == 0:
            return []
        infos = (_ProcessInfo * needed.value)()
        count = wintypes.UINT(needed.value)
        if manager.RmGetList(session, ctypes.byref(needed), ctypes.byref(count),
                             infos, ctypes.byref(reasons)) != 0:
            return []
        return [(int(infos[i].Process.dwProcessId),
                 infos[i].strAppName or infos[i].strServiceShortName or "a program")
                for i in range(count.value)]
    except (OSError, ValueError):
        return []
    finally:
        manager.RmEndSession(session)


def describe(exc: BaseException) -> str:
    """What to add to a failure's message: who has the file, or "".

    Only for the failures that can be a lock, and only when the exception
    carries the file it was about.
    """
    if getattr(exc, "winerror", None) not in LOCKED_WINERRORS:
        return ""
    path = getattr(exc, "filename", None)
    if not isinstance(path, str) or not path:
        return ""
    found = holding(path)
    if not found:
        return ""
    pid, name = found[0]
    return _MARK.format(name=name, pid=pid)


def parse(message: str) -> tuple[int, str] | None:
    """`(pid, name)` back out of a message `describe` added to, or None."""
    match = _READ.search(message or "")
    if match is None:
        return None
    return int(match.group("pid")), match.group("name")

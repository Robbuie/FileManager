"""A record of where the window was when it stopped answering.

The window freezing is the one defect this application exists to prevent, and
it is also the one that cannot be diagnosed after the fact: by the time
somebody reports it the process has been ended from Task Manager, and what it
was doing went with it. An outside profiler is the usual answer and was tried
first; on the machine it was needed on it would not attach.

So the application watches itself. A timer on the UI thread re-arms
`faulthandler.dump_traceback_later` once a second. While the event loop is
turning, the dump is pushed back before it can happen and costs nothing. When
the loop stops for longer than `STALL_SECONDS`, faulthandler's own C thread --
which needs neither the interpreter lock nor the stuck thread -- writes every
thread's stack to `hangs.log` beside the settings, and again every
`STALL_SECONDS` for as long as the freeze lasts. Every thread matters: a UI
thread waiting on the pool's lock is only half the answer, and the other half
is whichever thread is holding it.

The file is local and under `%APPDATA%`, the same deliberate exception the
settings file is. It is opened once at startup, not written from the event
loop, except for one line when the window recovers from a stall -- which is
the only moment a time for the stall can be known.

0.29.6 adds the case the first version missed. A window can stop answering
*input* while its event loop is still turning -- a dialog or a menu opened at
a position off the edge of the screen holds the keyboard and the mouse, and
everything else in the window ignores them until it is answered. Nothing is
stuck, so no dump is ever written, and it looks exactly like a freeze from the
outside. So a second watcher runs on a thread of its own and writes the same
stacks plus a census of every window this process owns, with positions, when
either of two things happens: Windows itself reports the window as not
responding, or somebody asks by hand -- creating a file called `dump.now`
beside the log, which can be done from Explorer while the window is stuck.

The thread touches no Qt object. Enumerating windows is a Win32 call and the
stacks come from `faulthandler`; neither needs the UI thread, which is the
whole point of not being on it.

Standard library only, on purpose: this has to work in the frozen build on a
machine with nothing else installed.
"""

from __future__ import annotations

import faulthandler
import os
import sys
import threading
import time
from typing import TextIO

from PySide6.QtCore import QObject, QTimer

#: How long the event loop may stop before the stacks are written. Long enough
#: that a slow but finished repaint is not a report, short enough that
#: somebody who gives up and ends the task has still left one behind.
STALL_SECONDS = 5.0

#: How often the timer re-arms the dump.
BEAT_MS = 1000

#: A turn of the event loop slower than this is written down as a line, with
#: no stacks. 0.29.8, and the reason is the freeze that produced no record at
#: all: a window can finish every repaint and still be unusable if each one
#: takes most of a second, which is what a translucent window at full size
#: costs over a remote connection. Nothing stalls, so nothing was dumped, and
#: the log's silence was itself the finding. Well above the frame budget, so
#: an ordinary busy moment is not an entry.
LATE_SECONDS = 0.75

#: Past this size the log is started again rather than appended to. A freeze
#: of several minutes is a few hundred kilobytes of stacks.
MAX_BYTES = 2 * 1024 * 1024

FILE_NAME = "hangs.log"

#: Create this beside the log and the next tick writes a dump. The way to get
#: a stack out of a window that is not stuck but will not answer.
TRIGGER_NAME = "dump.now"

#: How often the watcher thread looks.
WATCH_SECONDS = 1.0

#: While Windows still reports the window as not responding, how long between
#: dumps. Longer than the stall dumps: this one repeats for as long as
#: somebody leaves the window alone.
HUNG_REPEAT_SECONDS = 30.0


class HangRecorder(QObject):
    """Writes every thread's stack when the UI thread stops answering."""

    def __init__(self, path: str, *, version: str = "",
                 stall: float = STALL_SECONDS, beat_ms: int = BEAT_MS,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.path = path
        self._version = version
        self._stall = float(stall)
        self._file: TextIO | None = None
        self._last = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(int(beat_ms))
        self._timer.timeout.connect(self.beat)
        self._trigger = os.path.join(os.path.dirname(path) or ".", TRIGGER_NAME)
        self._watch_seconds = WATCH_SECONDS
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()
        self._hung_at = 0.0
        self._late = LATE_SECONDS
        self._slow = 0

    @property
    def active(self) -> bool:
        return self._file is not None

    def start(self) -> None:
        """Open the log and arm the first dump. Never raises: an application
        that will not start because it could not write a diagnostic is worse
        than one that runs without it."""
        try:
            folder = os.path.dirname(self.path)
            if folder:
                os.makedirs(folder, exist_ok=True)
            mode = "a"
            try:
                if os.path.getsize(self.path) > MAX_BYTES:
                    mode = "w"
            except OSError:
                pass
            self._file = open(self.path, mode, encoding="utf-8")
            self._write(f"\n---- {_now()}  File Manager {self._version} started, "
                        f"pid {os.getpid()}; stacks follow any stall over "
                        f"{self._stall:g} s, and a line any turn of the event "
                        f"loop over {self._late:g} s\n")
        except OSError:
            self._file = None
            return
        self.beat()
        self._timer.start()
        self._thread = threading.Thread(target=self._watch, name="fm-hangs",
                                        daemon=True)
        self._thread.start()

    def beat(self) -> None:
        """Push the dump back. Called by the timer; a late call is a stall."""
        if self._file is None:
            return
        now = time.monotonic()
        gap = now - self._last if self._last else 0.0
        if gap > self._stall:
            self._write(f"---- {_now()}  the window answered again after "
                        f"{gap:.1f} s\n")
        elif gap > self._late:
            # Not a stall and not nothing: the loop is turning, so no stack
            # would say anything, and the window is still too slow to use.
            self._slow += 1
            self._write(f"{_now()}  slow: one turn of the event loop took "
                        f"{gap:.2f} s ({self._slow} so far)\n")
        self._last = now
        try:
            faulthandler.dump_traceback_later(self._stall, repeat=True,
                                              file=self._file, exit=False)
        except Exception:  # noqa: BLE001 - a recorder that fails stays quiet
            self.stop()

    def stop(self) -> None:
        self._stopping.set()
        self._timer.stop()
        try:
            faulthandler.cancel_dump_traceback_later()
        except Exception:  # noqa: BLE001
            pass
        if self._file is not None:
            try:
                self._file.close()
            except OSError:
                pass
        self._file = None

    # ------------------------------------------------- the watcher thread

    def _watch(self) -> None:
        """Look for a window that will not answer, and for a request by hand.

        Deliberately forgiving of its own failures: this runs beside an
        application that is already misbehaving, and a diagnostic that raises
        is one more thing to explain.
        """
        while not self._stopping.wait(self._watch_seconds):
            try:
                if self._asked():
                    self.dump("asked for by hand")
                    continue
                hung = [w for w in windows() if w.get("hung")]
                now = time.monotonic()
                if hung and now - self._hung_at > HUNG_REPEAT_SECONDS:
                    self._hung_at = now
                    self.dump("Windows reports the window as not responding")
                elif not hung:
                    self._hung_at = 0.0
            except Exception:  # noqa: BLE001 - see the docstring
                continue

    def _asked(self) -> bool:
        """Whether somebody left the trigger file. Removed as it is read, so
        one file is one dump."""
        try:
            if not os.path.exists(self._trigger):
                return False
            os.remove(self._trigger)
        except OSError:
            return False
        return True

    def dump(self, reason: str) -> None:
        """Write every thread's stack now, and what windows this process owns.

        The census is the half the stacks cannot give: a modal dialog at
        `-3200, 400` is a window waiting on a monitor that is not there, and
        it looks like a freeze from every other angle.
        """
        if self._file is None:
            return
        self._write(f"\n---- {_now()}  {reason}\n")
        try:
            faulthandler.dump_traceback(file=self._file, all_threads=True)
        except Exception:  # noqa: BLE001
            self._write("the stacks could not be written\n")
        found = windows()
        if not found:
            self._write("no top-level windows found for this process\n")
        for item in found:
            self._write(
                "window {hwnd:#x} {cls} \"{title}\" at {rect} "
                "visible={visible} enabled={enabled} owner={owner:#x}"
                "{hung}\n".format(hung="  NOT RESPONDING" if item["hung"] else "",
                                  **item))
        self._write("----\n")
        if self._file is not None:
            self._file.flush()

    def _write(self, text: str) -> None:
        if self._file is None:
            return
        try:
            self._file.write(text)
            self._file.flush()
        except (OSError, ValueError):
            pass


def windows() -> list[dict]:
    """Every top-level window this process owns, with what it is doing.

    Empty off Windows and empty if the calls fail, which is the same answer a
    process with no windows gives -- the caller says so either way rather than
    printing a reassuring blank.
    """
    if sys.platform != "win32":
        return []
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:  # noqa: BLE001
        return []

    user32 = ctypes.windll.user32
    pid = ctypes.windll.kernel32.GetCurrentProcessId()
    found: list[dict] = []

    def visit(hwnd, _extra):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value != pid:
            return True
        title = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, title, 256)
        name = ctypes.create_unicode_buffer(128)
        user32.GetClassNameW(hwnd, name, 128)
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        found.append({
            "hwnd": int(hwnd),
            "cls": name.value,
            "title": title.value,
            "rect": f"{rect.left},{rect.top} {rect.right - rect.left}"
                    f"x{rect.bottom - rect.top}",
            "visible": bool(user32.IsWindowVisible(hwnd)),
            "enabled": bool(user32.IsWindowEnabled(hwnd)),
            "owner": int(user32.GetWindow(hwnd, 4) or 0),   # GW_OWNER
            "hung": bool(user32.IsHungAppWindow(hwnd)),
        })
        return True

    try:
        proto = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows(proto(visit), 0)
    except Exception:  # noqa: BLE001
        return found
    return found


def default_path(settings_path: str) -> str:
    """`hangs.log` in the folder the settings file lives in."""
    return os.path.join(os.path.dirname(settings_path), FILE_NAME)


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")

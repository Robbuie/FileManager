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

Standard library only, on purpose: this has to work in the frozen build on a
machine with nothing else installed.
"""

from __future__ import annotations

import faulthandler
import os
import time
from typing import TextIO

from PySide6.QtCore import QObject, QTimer

#: How long the event loop may stop before the stacks are written. Long enough
#: that a slow but finished repaint is not a report, short enough that
#: somebody who gives up and ends the task has still left one behind.
STALL_SECONDS = 5.0

#: How often the timer re-arms the dump.
BEAT_MS = 1000

#: Past this size the log is started again rather than appended to. A freeze
#: of several minutes is a few hundred kilobytes of stacks.
MAX_BYTES = 2 * 1024 * 1024

FILE_NAME = "hangs.log"


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
                        f"{self._stall:g} s\n")
        except OSError:
            self._file = None
            return
        self.beat()
        self._timer.start()

    def beat(self) -> None:
        """Push the dump back. Called by the timer; a late call is a stall."""
        if self._file is None:
            return
        now = time.monotonic()
        if self._last and now - self._last > self._stall:
            self._write(f"---- {_now()}  the window answered again after "
                        f"{now - self._last:.1f} s\n")
        self._last = now
        try:
            faulthandler.dump_traceback_later(self._stall, repeat=True,
                                              file=self._file, exit=False)
        except Exception:  # noqa: BLE001 - a recorder that fails stays quiet
            self.stop()

    def stop(self) -> None:
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

    def _write(self, text: str) -> None:
        if self._file is None:
            return
        try:
            self._file.write(text)
            self._file.flush()
        except (OSError, ValueError):
            pass


def default_path(settings_path: str) -> str:
    """`hangs.log` in the folder the settings file lives in."""
    return os.path.join(os.path.dirname(settings_path), FILE_NAME)


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")

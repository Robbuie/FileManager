"""One window: a second start hands its folder to the first and exits (0.41).

Double Commander opens one window however it is started, and "Open in File
Manager" from Explorer's right-click menu is only useful if it does the same
-- a new window per click is a new set of workers per click, each polling the
same folders.

The first process owns a named pipe, `\\\\.\\pipe\\FileManager.<user>`, created
with FILE_FLAG_FIRST_PIPE_INSTANCE so that exactly one process can: a second
one asking for the same name is refused, and that refusal is how it knows it
is second. It then connects, writes one short JSON message -- which folder to
open, if any -- and exits. The first process reads messages on a daemon
thread and hands each to the window through a queued Qt signal; the thread
never touches the window or the filesystem itself.

The pipe refuses remote clients, and the name carries the user's name so two
people signed in to one machine each get their own. Nothing here is required:
without pywin32, or off Windows, `claim` answers "not claimed" with no
listener, `send` answers False, and the application starts as it always did.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Callable

try:
    import pywintypes
    import win32file
    import win32pipe
except Exception:  # noqa: BLE001 - optional: a second window is the fallback
    pywintypes = None
    win32file = None
    win32pipe = None

#: CreateNamedPipe flags pywin32 does not name.
FILE_FLAG_FIRST_PIPE_INSTANCE = 0x00080000
PIPE_REJECT_REMOTE_CLIENTS = 0x00000008
ERROR_ACCESS_DENIED = 5
ERROR_PIPE_BUSY = 231

#: The largest message read. A path is at most 32,767 characters, and UTF-8
#: at worst triples it.
LIMIT = 1 << 17


def pipe_name() -> str:
    user = "".join(ch for ch in os.environ.get("USERNAME", "user") if ch.isalnum()) or "user"
    return rf"\\.\pipe\FileManager.{user}"


def encode(folder: str | None) -> bytes:
    return json.dumps({"open": folder or ""}).encode("utf-8")


def decode(data: bytes) -> str | None:
    """The folder a message asks for, "" for none, or None if it is not one."""
    try:
        message = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(message, dict) or not isinstance(message.get("open", ""), str):
        return None
    return message.get("open", "")


def clean_folder(argument: str) -> str:
    """A folder as Explorer's command line delivers it, made usable.

    `"%1"` on a drive is `"C:\\"`, and the rule Windows programs split a command
    line by reads the backslash before the closing quote as escaping it: the
    argument arrives as `C:"`. So a trailing quote is dropped, and a bare
    drive letter gets its root back -- `C:` alone means "wherever C: was last",
    which is not the drive somebody right-clicked.
    """
    text = (argument or "").strip().strip('"').strip()
    if len(text) == 2 and text[1] == ":" and text[0].isalpha():
        text += "\\"
    return text


def available() -> bool:
    return win32pipe is not None and win32file is not None


def _create(first: bool):
    flags = win32pipe.PIPE_ACCESS_INBOUND | (FILE_FLAG_FIRST_PIPE_INSTANCE if first else 0)
    mode = (win32pipe.PIPE_TYPE_MESSAGE | win32pipe.PIPE_READMODE_MESSAGE
            | win32pipe.PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS)
    return win32pipe.CreateNamedPipe(pipe_name(), flags, mode,
                                     win32pipe.PIPE_UNLIMITED_INSTANCES,
                                     0, LIMIT, 0, None)


class Listener(threading.Thread):
    """Reads what later starts send, for as long as the process runs."""

    def __init__(self, first_handle, deliver: Callable[[str], None]) -> None:
        super().__init__(name="instance-pipe", daemon=True)
        self._handle = first_handle
        self._deliver = deliver

    def run(self) -> None:  # pragma: no cover - needs Windows
        handle = self._handle
        while handle is not None:
            folder = None
            try:
                try:
                    win32pipe.ConnectNamedPipe(handle, None)
                except pywintypes.error as exc:
                    # 535, ERROR_PIPE_CONNECTED: a client got in between the
                    # create and the connect, which is a connection.
                    if exc.winerror != 535:
                        raise
                _hr, data = win32file.ReadFile(handle, LIMIT)
                folder = decode(bytes(data))
            except pywintypes.error:
                pass
            # The next instance of the pipe exists before this one is closed,
            # so a start that arrives in between finds a pipe to wait on
            # rather than none -- which it would read as "no window yet" and
            # open a second one.
            try:
                following = _create(first=False)
            except pywintypes.error:
                following = None
            for close in (win32pipe.DisconnectNamedPipe, win32file.CloseHandle):
                try:
                    close(handle)
                except pywintypes.error:
                    pass
            if folder is not None:
                self._deliver(folder)
            handle = following


def claim() -> tuple[bool, object | None]:
    """Try to be the one window.

    `(True, handle)` for the first process, which passes the handle to
    `Listener`; `(False, None)` when another process already owns the pipe;
    `(True, None)` when this cannot be decided at all, which is treated as
    first -- a second window beats no window.
    """
    if not available():
        return True, None
    try:
        return True, _create(first=True)
    except pywintypes.error as exc:  # pragma: no cover - needs Windows
        if exc.winerror == ERROR_ACCESS_DENIED:
            return False, None
        return True, None


def send(folder: str | None, *, wait_ms: int = 2000) -> bool:  # pragma: no cover
    """Hand `folder` to the window that owns the pipe. False if it could not.

    Allows that process to come to the front before writing, because Windows
    only lets the process the user is interacting with -- this one, just
    started -- give the foreground away.
    """
    if not available():
        return False
    try:
        import ctypes
        ctypes.windll.user32.AllowSetForegroundWindow(-1)      # ASFW_ANY
    except Exception:  # noqa: BLE001 - the window still opens the folder
        pass
    try:
        win32pipe.WaitNamedPipe(pipe_name(), wait_ms)
        handle = win32file.CreateFile(pipe_name(), win32file.GENERIC_WRITE, 0, None,
                                      win32file.OPEN_EXISTING, 0, None)
    except pywintypes.error:
        return False
    try:
        win32file.WriteFile(handle, encode(folder))
        return True
    except pywintypes.error:
        return False
    finally:
        try:
            win32file.CloseHandle(handle)
        except pywintypes.error:
            pass

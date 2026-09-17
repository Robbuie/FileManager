"""The Explorer context menu, asked for and answered on the UI thread's terms.

The window draws its own menu from what comes back, so this is the layer that
turns a right-click into a request and a reply into a list of items somebody
can click. It holds two things worth naming.

**A token.** The ids in a menu mean nothing on their own: they are the shell's
own numbering, reused by the next menu for different commands. An id is only
ever sent back with the token it arrived with, and the host refuses one that
belongs to a menu it has let go of. So a click on a menu that has been
replaced does nothing, rather than running the wrong command.

**One menu at a time.** Building a second menu releases the first, because a
menu that is not on screen is one nobody will invoke. The exception is a
command that is still running: a verb that opened a dialog holds the shell
host until the person answers it, and asking for another menu meanwhile would
queue a request behind that dialog -- which the pool would eventually read as
a wedged process and kill, taking somebody's half-typed commit message with
it. So while a command is open this says so and offers nothing, which is a
menu that is briefly shorter rather than a dialog that vanishes.
"""

from __future__ import annotations

import sys
from typing import Any

from PySide6.QtCore import QObject, Signal

from app.io.protocol import Op, Reply, Status


class ShellMenu(QObject):
    """Builds and invokes the shell's menu for a selection."""

    #: token, list[MenuItem] -- the entries to add to a menu already on screen.
    ready = Signal(int, object)
    #: Why there are no shell entries this time. Not an error dialog: the
    #: menu is open and the application's own verbs are on it.
    unavailable = Signal(str)
    #: A command was run. The verb, for the status line, and the folder to
    #: re-list -- what a shell verb did is not knowable from here, so anything
    #: that ran is treated as something that changed the folder.
    invoked = Signal(str, str)
    problem = Signal(str)

    def __init__(self, bridge: Any, config: Any, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._config = config
        self._token: int | None = None
        self._folder = ""
        self._request: int | None = None
        self._running = False

    @property
    def busy(self) -> bool:
        """Whether a command from the last menu is still open."""
        return self._running

    def request(self, folder: str, names: list[str], *, extended: bool = False) -> None:
        """Ask for the menu for a selection in a folder.

        `names` empty means the folder itself, which is what a right-click on
        empty space in the listing asks about.
        """
        if self._running:
            self.unavailable.emit("a command from the last menu is still open")
            return
        self._forget()
        self._folder = folder
        # Before every menu, not on a button: a shell host that keeps dying
        # costs a process per right-click, and the alternative is a menu that
        # stops appearing until the application is restarted.
        self._bridge.retry_host()
        self._request = self._bridge.submit(
            Op.MENU, folder,
            timeout=float(self._config.get("timeout.menu")),
            on_reply=self._on_built,
            args={"names": list(names), "extended": bool(extended)},
        )

    def invoke(self, token: int, item_id: int) -> None:
        """Run one entry. Everything after this is the shell's business."""
        if self._token is None or token != self._token or self._running:
            return
        folder = self._folder
        self._running = True

        def handle(reply: Reply) -> None:
            self._running = False
            self._token = None
            payload = reply.payload if isinstance(reply.payload, dict) else {}
            if reply.status is Status.OK:
                self.invoked.emit(str(payload.get("verb") or ""), folder)
            elif reply.status is Status.DENIED:
                self.problem.emit("the command was refused")
            elif reply.status is Status.CANCELLED:
                pass
            else:
                self.problem.emit(reply.message or "the command did not run")

        _lend_foreground(self._bridge)
        self._bridge.submit(
            Op.MENU_INVOKE, folder,
            # Long, and deliberately: what this waits for is a person reading
            # a dialog that a shell extension opened. The deadline is here to
            # bound a host that has genuinely wedged, not to bound somebody
            # typing, and a watchdog that fires while that dialog is open
            # closes it from underneath them.
            timeout=float(self._config.get("timeout.menu_invoke")),
            on_reply=handle,
            args={"token": token, "item": int(item_id)},
        )

    def release(self) -> None:
        """Let go of a menu that was closed without anything being chosen.

        Not merely tidiness: the shell objects behind it are a third-party
        DLL's, and some of them hold the folder open while they exist.
        """
        token, self._token = self._token, None
        self._forget()
        if token is None or self._running:
            return
        self._bridge.submit(
            Op.MENU_RELEASE, self._folder,
            timeout=float(self._config.get("timeout.menu")),
            on_reply=lambda reply: None,
            args={"token": token},
        )

    # -------------------------------------------------------------- internals

    def _forget(self) -> None:
        if self._request is not None:
            self._bridge.forget(self._request)
            self._request = None

    def _on_built(self, reply: Reply) -> None:
        if reply.id != self._request:
            return  # an answer to a menu that has already been replaced
        self._request = None
        payload = reply.payload if isinstance(reply.payload, dict) else {}
        if reply.status is not Status.OK:
            self._token = None
            self.unavailable.emit(_explain(reply))
            return
        self._token = int(payload.get("token") or reply.id)
        self.ready.emit(self._token, list(payload.get("items") or ()))


def _lend_foreground(bridge: Any) -> None:
    """Let the shell host put the window a command opens in front of this one.

    Windows only lets the foreground process choose who is in front next, and
    the host is never the foreground process: without this, Properties and
    every other dialog a verb opens appeared behind the application. The grant
    has to come from here, the process that has the foreground at the moment
    somebody clicks. A window-manager call, not a filesystem one.
    """
    if sys.platform != "win32":
        return
    pid_of = getattr(bridge, "host_pid", None)
    pid = pid_of() if callable(pid_of) else None
    if not pid:
        return
    try:
        import ctypes
        ctypes.windll.user32.AllowSetForegroundWindow(int(pid))
    except Exception:  # noqa: BLE001 - a dialog behind is worse, not broken
        pass


def _explain(reply: Reply) -> str:
    """Why the shell entries are missing, in one line for a menu.

    Short on purpose. This goes in a disabled entry at the bottom of a menu
    that is already open, and a sentence there is one nobody reads.
    """
    if reply.status is Status.TIMEOUT:
        return "Explorer commands: the shell did not answer"
    if reply.status is Status.GONE:
        return "Explorer commands: not available here"
    if reply.status is Status.DENIED:
        return "Explorer commands: access denied"
    return f"Explorer commands: {reply.message or 'unavailable'}"

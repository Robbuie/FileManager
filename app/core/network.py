r"""The network locations this session can reach, letters or not.

`Volumes` answers "which drive letters does this session have", which is a
bitmask read and covers everything Windows has given a letter to. This answers
the question that one cannot: **what is this session attached to that has no
letter at all.**

That gap is not academic. A Hyper-V or Remote Desktop redirected drive is a
real, working connection with no letter anywhere -- the host's C: arrives as
`\\tsclient\C` and nothing else -- so a sidebar built from `GetLogicalDrives`
has no row to put it in, and a file manager that only lists letters simply
cannot see it. That is the report this module exists to answer.

Two sources, and they are deliberately different kinds of thing:

  * **What is connected.** Read from the redirector's own table of current
    connections, which is local -- the same table `WNetGetConnection` answers
    from -- so it contacts no server and cannot block on one that has gone.
    Refreshed when asked, never on a timer.
  * **What the user has added.** Not everything can be enumerated: a share
    nobody has connected to yet is in no table, so it has to be typed once and
    is then remembered. These are listed whether or not they are reachable,
    which is the point -- a location that vanishes from the sidebar the moment
    a server reboots is a location you cannot click to get it back.

What this module does **not** do is enumerate the network. Asking what exists
out there is `RESOURCE_GLOBALNET`, a real round trip through the browser
service, and it is how a file manager comes to hang while drawing its sidebar.
What is out there is not this application's question; what this session already
holds, and what its user has named, is.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, Signal

from app.io import paths
from app.io.protocol import Op, Reply, Status

#: The path a NETWORK request carries. The op ignores it; the pool does not --
#: an empty path keys the local worker, which is where a read of the local
#: connection table belongs. `Volumes.LOCAL`'s reasoning, and the same value.
LOCAL = ""


class Location:
    """One row of the network section.

    `saved` is what tells the two kinds apart, and the difference is visible in
    the sidebar: a connection is there because Windows says so and goes when it
    goes; a saved location is there because somebody put it there and stays.
    """

    __slots__ = ("remote", "local", "label", "saved")

    def __init__(self, remote: str, *, local: str = "", label: str = "",
                 saved: bool = False) -> None:
        self.remote = remote
        self.local = local
        self.label = label or _label_for(remote, local)
        self.saved = saved

    @property
    def path(self) -> str:
        """Where clicking it goes. The UNC, always.

        Never the drive letter, even when there is one. The letter is a name
        this session happens to have for it; the UNC is what the thing is, and
        it is what survives the letter's session dying -- which is the whole of
        this application's path handling in one line.
        """
        return self.remote

    def __eq__(self, other: object) -> bool:
        return (isinstance(other, Location)
                and other.remote.lower() == self.remote.lower()
                and other.saved == self.saved)

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"Location({self.remote!r}, local={self.local!r}, saved={self.saved})"


def _label_for(remote: str, local: str) -> str:
    """What to call a location in a list.

    The letter when it has one, because that is what the user types; otherwise
    the share's own name with the server after it, because `C` on its own is
    not a useful thing to read in a sidebar that also lists a drive called C:.
    """
    if local:
        return local.rstrip("\\")
    pieces = paths.split_unc(remote)
    if pieces is None:
        return remote
    server, share, _ = pieces
    return f"{share} on {server}"


def clean(path: str) -> str:
    """A typed location, tidied, or "" if it is not a UNC path at all.

    The one validation there is, and it is a string test rather than a
    filesystem one: whether the share exists is a question for the worker that
    tries to list it, and asking it here would be a blocking call in a dialog.
    """
    text = paths.normalize((path or "").strip())
    if not text:
        return ""
    return text.rstrip("\\") if paths.is_unc(text) else ""


class Network(QObject):
    """Connections this session holds, plus the locations the user has saved."""

    changed = Signal()
    #: A reconnect finished: the path, and why it failed (empty when it worked).
    reconnected = Signal(str, str)

    def __init__(self, bridge: Any, config: Any, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._config = config
        self._request_id: int | None = None
        self._connections: list[Location] = []
        #: Why the list is short, when it is short for a reason. Empty on a
        #: machine that simply has nothing mapped, which is the common case
        #: and is not a problem.
        self.problem = ""

    # ------------------------------------------------------------- the list

    @property
    def saved(self) -> list[Location]:
        stored = self._config.get("network.saved")
        if not isinstance(stored, list):
            return []
        out: list[Location] = []
        for item in stored:
            path = clean(str(item))
            if path:
                out.append(Location(path, saved=True))
        return out

    @property
    def locations(self) -> list[Location]:
        """Everything to draw, connections first, without duplicates.

        A saved location that is also connected is one row, and it is the
        connected one: it carries the letter, and a row that says `S:` when the
        user is looking for `S:` is the row they mean.
        """
        seen = {item.remote.lower() for item in self._connections}
        return self._connections + [item for item in self.saved
                                    if item.remote.lower() not in seen]

    def refresh(self) -> None:
        """Ask what this session is attached to. Local, and probes nothing."""
        if self._request_id is not None:
            self._bridge.forget(self._request_id)
        self._request_id = self._bridge.submit(
            Op.NETWORK, LOCAL,
            timeout=float(self._config.get("timeout.drives")),
            on_reply=self._on_reply,
        )

    def _on_reply(self, reply: Reply) -> None:
        self._request_id = None
        if reply.status is not Status.OK or not isinstance(reply.payload, dict):
            return  # keep the last good list, as `Volumes` does
        self.problem = str(reply.payload.get("problem", ""))
        self._connections = [
            Location(str(item.get("remote", "")),
                     local=str(item.get("local", "")),
                     label=str(item.get("label", "")))
            for item in reply.payload.get("connections", []) if item.get("remote")
        ]
        self.changed.emit()

    # -------------------------------------------------------- saved entries

    def add(self, path: str) -> str:
        """Save a location. Returns the cleaned path, or "" if it was not one."""
        target = clean(path)
        if not target:
            return ""
        stored = [item.remote for item in self.saved]
        if target.lower() not in {item.lower() for item in stored}:
            stored.append(target)
            self._config.set("network.saved", stored)
            self._config.save()
            self.changed.emit()
        return target

    def remove(self, path: str) -> None:
        target = clean(path).lower()
        stored = [item.remote for item in self.saved
                  if item.remote.lower() != target]
        self._config.set("network.saved", stored)
        self._config.save()
        self.changed.emit()

    # ---------------------------------------------------------- reconnecting

    def reconnect(self, path: str) -> None:
        """Attach to a share again, for one that has stopped answering.

        The one call in this module that touches the network, so it is only
        ever sent because somebody asked for it, and it carries a deadline long
        enough to cover a server deciding whether it is awake. Nothing is
        blocked behind it: it goes to that server's own worker, which is the
        worker already stuck on it.
        """
        target = clean(path)
        if not target:
            return
        self._bridge.submit(
            Op.CONNECT, target,
            timeout=float(self._config.get("timeout.connect")),
            on_reply=self._replier(target),
            args={"remember": True},
        )

    def _replier(self, target: str):
        def handle(reply: Reply) -> None:
            why = "" if reply.status is Status.OK else (
                reply.message or "could not reconnect")
            self.reconnected.emit(target, why)
            if not why:
                # It is attached now, so the connection table has changed and
                # the row should stop looking like a saved-but-absent one.
                self.refresh()
        return handle

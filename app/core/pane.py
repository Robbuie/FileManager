"""One pane: its tabs, where each of them is, and how it gets there.

This is the layer that turns a click into a request and a reply into rows. The
widget above it knows nothing about the pool, and the pool below it knows
nothing about tabs.

The rule that makes tabs safe is that a reply is only believed if it answers
the request the tab is currently waiting on. A tab that navigates away, or
navigates twice quickly, will still be handed the older answer eventually --
`io` guarantees a reply for every request, including the abandoned ones -- and
the id check is what makes that a non-event rather than a listing appearing in
the wrong folder.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject, Signal

from app.core.listing import ListingModel, format_size
from app.io import paths
from app.io.protocol import Op, Reply, Status

#: What a status line says while a listing is in flight. Named because the
#: widget styles on it.
BUSY = "busy"
IDLE = "idle"
BAD = "bad"


class Tab:
    """One folder being looked at, with where it has been."""

    def __init__(self, path: str) -> None:
        self.path = paths.normalize(path)
        self.model = ListingModel()
        self.history: list[str] = [self.path]
        self.position = 0
        self.request_id: int | None = None
        self.space_id: int | None = None
        self.status_text = ""
        self.status_state = IDLE
        self.space_text = ""
        self.reveal_name: str | None = None

    @property
    def label(self) -> str:
        return paths.leaf(self.path)

    @property
    def can_go_back(self) -> bool:
        return self.position > 0

    @property
    def can_go_forward(self) -> bool:
        return self.position < len(self.history) - 1


class Pane(QObject):
    """The tabs on one side of the window."""

    tabsChanged = Signal()
    currentChanged = Signal()
    statusChanged = Signal(str, str)   # text, state
    pathChanged = Signal(str)          # display form
    spaceChanged = Signal(str)         # free space on this tab's volume, or ""
    revealRequested = Signal(str)      # put the cursor on this name, once it is there
    folderChanged = Signal(str)        # something in this folder was created or removed

    def __init__(self, bridge, config, side: str, parent=None) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._config = config
        self._side = side
        self.tabs: list[Tab] = [Tab(config.get(f"{side}.path"))]
        self.index = 0

    # ------------------------------------------------------------------ state

    @property
    def current(self) -> Tab:
        return self.tabs[self.index]

    @property
    def show_unc(self) -> bool:
        return bool(self._config.get(f"{self._side}.show_unc"))

    def display(self, path: str | None = None) -> str:
        return paths.display(path or self.current.path, prefer_letter=not self.show_unc)

    def set_show_unc(self, value: bool) -> None:
        self._config.set(f"{self._side}.show_unc", bool(value))
        self.pathChanged.emit(self.display())

    # ------------------------------------------------------------- navigation

    def navigate(self, path: str, *, record: bool = True) -> None:
        tab = self.current
        target = paths.normalize(path)
        self._abandon(tab)

        tab.path = target
        if record and (not tab.history or tab.history[tab.position] != target):
            del tab.history[tab.position + 1:]
            tab.history.append(target)
            tab.position = len(tab.history) - 1

        tab.model.begin(has_parent=paths.parent(target) is not None)
        self._set_status(tab, "listing", BUSY)
        self.pathChanged.emit(self.display(target))
        self.tabsChanged.emit()

        tab.request_id = self._bridge.submit(
            Op.LIST, target,
            timeout=float(self._config.get("timeout.listing")),
            on_reply=self._replier(tab),
        )

    def refresh(self) -> None:
        self.navigate(self.current.path, record=False)

    def go_up(self) -> None:
        above = paths.parent(self.current.path)
        if above:
            self.navigate(above)

    def go_back(self) -> None:
        tab = self.current
        if tab.can_go_back:
            tab.position -= 1
            self.navigate(tab.history[tab.position], record=False)

    def go_forward(self) -> None:
        tab = self.current
        if tab.can_go_forward:
            tab.position += 1
            self.navigate(tab.history[tab.position], record=False)

    def activate(self, row: int) -> None:
        """Open what is at a row: a folder is navigation, a file is not ours yet."""
        tab = self.current
        if tab.model.is_parent_row(row):
            self.go_up()
            return
        entry = tab.model.entry(row)
        if entry is None:
            return
        if entry.is_dir:
            self.navigate(paths.join(tab.path, entry.name))
        else:
            self.open(row)

    def open(self, row: int) -> None:
        """Hand a row to the shell.

        Only ever a file: a folder is navigation, and asking the shell to open
        one would open a second file manager, which is a strange thing for a
        file manager to do.
        """
        tab = self.current
        entry = tab.model.entry(row)
        if entry is None or entry.is_dir:
            return
        target = paths.join(tab.path, entry.name)
        self._set_status(tab, f"opening {entry.name}", BUSY)

        def handle(reply: Reply) -> None:
            if reply.status is Status.OK:
                self._set_status(tab, tab.model.summary(), IDLE)
            else:
                self._set_status(tab, f"{entry.name}: {_explain(reply)}", BAD)

        self._bridge.submit(
            Op.OPEN, target,
            timeout=float(self._config.get("timeout.open")),
            on_reply=handle,
        )

    def paths_for(self, names: list[str]) -> list[str]:
        """Full paths for names in this folder, for handing to a transfer."""
        return [paths.join(self.current.path, name) for name in names]

    def as_path(self, text: str) -> str:
        """Normalise something the user typed into a path this app can use.

        Here rather than in the dialog that collected it: the widget does not
        do path arithmetic, and a destination typed by hand deserves the same
        treatment as one navigated to.
        """
        return paths.normalize(text)

    def row_path(self, row: int) -> str | None:
        """The full path of a row, for the things outside this pane that need it.

        Here rather than in the widget because it is path arithmetic, and the
        widget does not do path arithmetic even when the answer is obvious.
        """
        entry = self.current.model.entry(row)
        if entry is None:
            return None
        return paths.join(self.current.path, entry.name)

    # ------------------------------------------------------- changing things

    def names_for(self, rows) -> list[str]:
        """The names on a set of rows, parent row excluded.

        The caller passes rows because rows are what a selection is. What comes
        back is names, because a name is what an operation takes -- and because
        a row number is only meaningful until the next listing arrives.
        """
        names = []
        for row in sorted(rows):
            entry = self.current.model.entry(row)
            if entry is not None:
                names.append(entry.name)
        return names

    def make_folder(self, name: str) -> None:
        tab = self.current
        self._set_status(tab, f"creating {name}", BUSY)
        self._mutate(tab, Op.MKDIR, paths.join(tab.path, name),
                     timeout=float(self._config.get("timeout.mkdir")),
                     reveal=name, failed=f"could not create {name}")

    def rename(self, row: int, name: str) -> None:
        tab = self.current
        source = self.row_path(row)
        if source is None or not name:
            return
        self._set_status(tab, f"renaming to {name}", BUSY)
        self._mutate(tab, Op.RENAME, source,
                     timeout=float(self._config.get("timeout.rename")),
                     args={"name": name}, reveal=name,
                     failed=f"could not rename to {name}")

    def delete(self, names: list[str], *, permanent: bool = False) -> None:
        """Remove named items from this folder.

        Names, not rows: by the time the reply lands the listing has been
        replaced, and a row number that meant something when the user pressed
        the key would mean something else by now.
        """
        tab = self.current
        if not names:
            return
        kind = "deleting" if not permanent else "deleting permanently"
        self._set_status(tab, f"{kind} {len(names)} item(s)", BUSY)
        self._mutate(tab, Op.DELETE, tab.path,
                     timeout=float(self._config.get("timeout.delete")),
                     args={"names": names, "permanent": permanent},
                     failed="the delete did not finish")

    def _mutate(self, tab: Tab, op: Op, path: str, *, timeout: float,
                args: dict | None = None, reveal: str | None = None,
                failed: str = "the operation failed") -> None:
        """Submit something that changes the folder, then re-list it.

        Re-listed rather than patched into the model: the folder is the truth,
        the reply is only what this application believes it did to it, and the
        two differ whenever anything else on the machine is also writing. A
        listing is cheap next to being subtly wrong about what is on disk.
        """
        def handle(reply: Reply) -> None:
            if reply.status is not Status.OK:
                self._set_status(tab, f"{failed}: {_explain(reply)}", BAD)
                return
            if reveal:
                tab.reveal_name = reveal
            if tab is self.current:
                self.refresh()
            self.folderChanged.emit(tab.path)

        self._bridge.submit(op, path, timeout=timeout, on_reply=handle, args=args)

    def set_filter(self, text: str) -> None:
        """Narrow what the listing shows. Never re-lists the folder."""
        tab = self.current
        tab.model.set_filter(text)
        if tab.request_id is None:
            self._set_status(tab, tab.model.summary(), tab.status_state)

    def retry(self) -> None:
        """What a pane offers after a volume has gone away."""
        self._bridge.retry(self.current.path)
        self.refresh()

    # ------------------------------------------------------------------- tabs

    def open_tab(self, path: str | None = None) -> None:
        self.tabs.append(Tab(path or self.current.path))
        self.index = len(self.tabs) - 1
        self.tabsChanged.emit()
        self.currentChanged.emit()
        self.navigate(self.tabs[self.index].path, record=False)

    def close_tab(self, index: int) -> None:
        if len(self.tabs) <= 1 or not 0 <= index < len(self.tabs):
            return
        self._abandon(self.tabs[index])
        del self.tabs[index]
        self.index = min(self.index if index > self.index else self.index - 1,
                         len(self.tabs) - 1)
        self.index = max(0, self.index)
        self.tabsChanged.emit()
        self.currentChanged.emit()
        self._announce(self.current)

    def select_tab(self, index: int) -> None:
        if not 0 <= index < len(self.tabs) or index == self.index:
            return
        self.index = index
        self.currentChanged.emit()
        self._announce(self.current)

    # -------------------------------------------------------------- internals

    def _replier(self, tab: Tab) -> Callable[[Reply], None]:
        def handle(reply: Reply) -> None:
            self._on_reply(tab, reply)
        return handle

    def _on_reply(self, tab: Tab, reply: Reply) -> None:
        if reply.id != tab.request_id:
            return  # an answer to a question this tab has stopped asking

        if reply.status is Status.PARTIAL:
            tab.model.add(reply.payload or [])
            self._set_status(tab, f"listing, {tab.model.rowCount():,} rows", BUSY)
            return

        tab.request_id = None
        if reply.status is Status.OK:
            tab.model.add(reply.payload or [])
            tab.model.finish()
            self._set_status(tab, tab.model.summary(), IDLE)
            self._request_space(tab)
            if tab.reveal_name and tab is self.current:
                name, tab.reveal_name = tab.reveal_name, None
                self.revealRequested.emit(name)
            return

        tab.model.finish()
        self._set_status(tab, _explain(reply), BAD)

    def _request_space(self, tab: Tab) -> None:
        """How full the volume is, asked for after the listing rather than with it.

        After, because it opens the volume: on a share that is answering slowly
        this would otherwise be one more thing between the user and their rows.
        A failure is a blank readout, never an error -- nobody navigated here
        to find out about free space.
        """
        def handle(reply: Reply) -> None:
            if reply.id != tab.space_id:
                return
            tab.space_id = None
            payload = reply.payload if reply.status is Status.OK else None
            if isinstance(payload, dict):
                tab.space_text = (f"{format_size(payload['free'])} free of "
                                  f"{format_size(payload['total'])}")
            else:
                tab.space_text = ""
            if tab is self.current:
                self.spaceChanged.emit(tab.space_text)

        tab.space_text = ""
        if tab is self.current:
            self.spaceChanged.emit("")
        tab.space_id = self._bridge.submit(
            Op.FREE_SPACE, tab.path,
            timeout=float(self._config.get("timeout.free_space")),
            on_reply=handle,
        )

    def _abandon(self, tab: Tab) -> None:
        """Stop waiting on whatever this tab had in flight.

        Cancelled rather than merely forgotten, because an abandoned 50,000-row
        listing over SMB is not free -- it holds the volume's worker while the
        folder the user actually wants waits behind it.
        """
        if tab.request_id is not None:
            self._bridge.cancel(tab.request_id)
            self._bridge.forget(tab.request_id)
            tab.request_id = None
        if tab.space_id is not None:
            self._bridge.forget(tab.space_id)
            tab.space_id = None

    def _set_status(self, tab: Tab, text: str, state: str) -> None:
        tab.status_text, tab.status_state = text, state
        if tab is self.current:
            self.statusChanged.emit(text, state)

    def _announce(self, tab: Tab) -> None:
        self.statusChanged.emit(tab.status_text, tab.status_state)
        self.pathChanged.emit(self.display(tab.path))
        self.spaceChanged.emit(tab.space_text)


def _explain(reply: Reply) -> str:
    """A failure in the words a person can act on.

    The distinction that matters to someone looking at the window is whether
    waiting or retrying is the sensible thing, so that is what the wording is
    about, not which exception was raised.
    """
    if reply.status is Status.GONE:
        return "not reachable — the volume or folder is gone. Retry to reconnect."
    if reply.status is Status.TIMEOUT:
        return "stopped responding — the worker was restarted. Retry to try again."
    if reply.status is Status.DENIED:
        return "access denied"
    if reply.status is Status.CANCELLED:
        return "cancelled"
    return reply.message or "failed"

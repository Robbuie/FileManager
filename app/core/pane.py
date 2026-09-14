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

from app.core.clipboard import refusal
from app.core.listing import ListingModel, format_size
from app.io import elevate, paths
from app.io.protocol import Conflict, Op, Reply, Status

#: What a status line says while a listing is in flight. Named because the
#: widget styles on it.
BUSY = "busy"
IDLE = "idle"
BAD = "bad"

#: How many tabs one pane will hold. Not a limitation anybody will meet by
#: working; it is there so a key held down, or a stored session someone has
#: edited, cannot produce a strip with a thousand entries in it.
MAX_TABS = 40


class Tab:
    """One folder being looked at, with where it has been."""

    def __init__(self, path: str, icons=None, overlays=None, sizes=None, *,
                 locked: bool = False, file_icons=None, clipboard=None) -> None:
        self.path = paths.normalize(path)
        #: A locked tab keeps its folder. Navigating away from one opens a new
        #: tab at the target rather than refusing to move, which is what makes
        #: it useful: the tab you always want on the job folder stays there
        #: while a double click still goes somewhere.
        self.locked = locked
        self.model = ListingModel()
        self.model.set_icons(icons)
        self.model.set_overlays(overlays)
        self.model.set_file_icons(file_icons)
        self.model.set_sizes(sizes)
        self.model.set_cut(clipboard)
        self.model.set_folder(self.path)
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
        return not self.locked and self.position > 0

    @property
    def can_go_forward(self) -> bool:
        return not self.locked and self.position < len(self.history) - 1


class Pane(QObject):
    """The tabs on one side of the window."""

    tabsChanged = Signal()
    currentChanged = Signal()
    statusChanged = Signal(str, str)   # text, state
    pathChanged = Signal(str)          # display form
    spaceChanged = Signal(str)         # free space on this tab's volume, or ""
    revealRequested = Signal(str)      # put the cursor on this name, once it is there
    folderChanged = Signal(str)        # something in this folder was created or removed
    #: Windows refused an operation. The plan that would run it again with
    #: administrator rights, and the sentence describing it. Nothing happens
    #: unless somebody answers the dialog this puts on screen.
    elevationOffered = Signal(object, str)

    def __init__(self, bridge, config, side: str, icons=None, overlays=None,
                 menu=None, sizes=None, siblings=None, parent=None,
                 file_icons=None, transfers=None, clipboard=None) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._config = config
        self._side = side
        # Shared with the other pane and with every tab either of them opens:
        # the picture for a .pdf is the same on both sides of the window.
        self.icons = icons
        self.overlays = overlays
        # Shared for the icons' reason rather than the overlays': what an
        # executable looks like is the same on both sides of the window, and
        # the cache is what stops the second pane reading the file again.
        self.file_icons = file_icons
        # Shared for a different reason: there is one shell host, and one
        # context menu can be open at a time whichever pane it belongs to.
        self.menu = menu
        # Shared for the third reason: a folder walk holds a volume's worker,
        # so the queue that runs one at a time has to be the same queue for
        # every tab in the window rather than one per pane.
        self.sizes = sizes
        # Shared for the fourth reason, and it is the shell menu's reason
        # again: one dropdown is open at a time whichever crumb bar it hangs
        # off, so one outstanding scan is the right number for the window. It
        # follows that a reply arrives at the pane that did not ask, and that
        # pane's bar must do nothing with it -- which it decides by having no
        # menu open.
        self.siblings = siblings
        # Shared for the fifth reason, and the plainest of them: there is one
        # queue in the application. A copy started from the left pane and a
        # delete started from the right are two jobs in one list, which is the
        # whole point of having a list.
        self.transfers = transfers
        # Shared for the sixth reason, which is Windows': there is one system
        # clipboard. A file cut in the left pane is greyed in the right one
        # and in every tab showing that folder, because all of them are asking
        # the same object about the same clipboard.
        self.clipboard = clipboard
        self.tabs: list[Tab] = self._restore()
        self.index = min(max(0, int(config.get(f"{side}.tab") or 0)),
                         len(self.tabs) - 1)

    def _restore(self) -> list["Tab"]:
        """The tabs this pane had when the window last closed.

        Anything malformed in the stored list is dropped rather than repaired.
        A settings file is not a schema, the cost of a bad entry is one tab
        that does not come back, and the alternative is a pane that fails to
        build because somebody hand-edited their config.
        """
        stored = self._config.get(f"{self._side}.tabs")
        tabs: list[Tab] = []
        if isinstance(stored, list):
            for item in stored[:MAX_TABS]:
                if isinstance(item, str):
                    item = {"path": item}
                if not isinstance(item, dict):
                    continue
                path = item.get("path")
                if not isinstance(path, str) or not path:
                    continue
                tabs.append(Tab(path, self.icons, self.overlays, self.sizes,
                                locked=bool(item.get("locked")),
                                file_icons=self.file_icons,
                                clipboard=self.clipboard))
        if not tabs:
            tabs.append(Tab(self._config.get(f"{self._side}.path"),
                            self.icons, self.overlays, self.sizes,
                            file_icons=self.file_icons,
                            clipboard=self.clipboard))
        return tabs

    def session(self) -> list[dict]:
        """What to write out so the tabs come back. Paths, not models."""
        return [{"path": tab.path, "locked": tab.locked} for tab in self.tabs]

    # ------------------------------------------------------------------ state

    @property
    def config(self):
        """The settings, for the widget above. Read-only by convention: the
        pane is what writes them, so that what a setting means lives in one
        place rather than in whichever widget got there first."""
        return self._config

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
        if tab.locked and target != tab.path:
            # Not a refusal: the whole value of a locked tab is that the
            # folder is still one double click away from being opened, just
            # not from being lost.
            self.open_tab(target)
            return

        tab.path = target
        tab.model.set_folder(target)
        if self.overlays is not None:
            # Asked again rather than remembered. A badge is exactly the thing
            # that changes while the file does not -- a commit turns forty red
            # marks green without an mtime moving -- so a refresh that kept
            # them would show the state before the commit.
            self.overlays.forget(target)
        if self.sizes is not None:
            # Same reasoning, and more so: a folder's size is precisely what
            # changes without the folder itself changing.
            self.sizes.forget(target)
        if record and (not tab.history or tab.history[tab.position] != target):
            del tab.history[tab.position + 1:]
            tab.history.append(target)
            tab.position = len(tab.history) - 1

        self._list(tab, announce=True)

    def _list(self, tab: Tab, *, announce: bool = False) -> None:
        """Ask for the rows of whatever folder a tab is on.

        Separate from `navigate` because a background tab lists without the
        pane's path bar, status line or drive picker changing -- those belong
        to whatever is on screen, and a tab opened behind is not it.
        """
        self._abandon(tab)
        tab.model.begin(has_parent=paths.parent(tab.path) is not None)
        self._set_status(tab, "listing", BUSY)
        if announce:
            self.pathChanged.emit(self.display(tab.path))
        self.tabsChanged.emit()

        tab.request_id = self._bridge.submit(
            Op.LIST, tab.path,
            timeout=float(self._config.get("timeout.listing")),
            on_reply=self._replier(tab),
        )

    def refresh(self) -> None:
        self.navigate(self.current.path, record=False)

    def parent_path(self) -> str | None:
        """The folder above this tab's, or None at a root.

        Here rather than in the widget because it is path arithmetic, which
        the widget does not do -- the same rule that keeps `..` meaningful
        without the model holding a fake row for it.
        """
        return paths.parent(self.current.path)

    def crumbs(self, path: str | None = None) -> list[tuple[str, str]]:
        """This tab's path as `(label, path)` from the root down.

        Here for the reason `parent_path` is: splitting a path is path
        arithmetic and the widget does not do any. The widget is handed a list
        of labels and the places they go, and renders that.

        Takes a path so the bar can be built from what is being *displayed* --
        which is the UNC or the letter depending on this tab's preference --
        rather than from the resolved form the pane works in.
        """
        return paths.crumbs(self.current.path if path is None else path)

    def children(self, folder: str, names) -> list[tuple[str, str]]:
        """Bare names in a folder, as `(label, path)` the widget can navigate.

        Here for the same reason `crumbs` is: joining a folder to a name is
        path arithmetic. The names come back from a worker as names, because
        a name is what a menu draws and a path is what a click needs, and this
        is the one place that knows how to turn one into the other.
        """
        return [(name, paths.join(folder, name)) for name in names]

    def go_up(self) -> None:
        above = self.parent_path()
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
        """Remove named items from this folder, through the queue.

        Names, not rows: by the time the answer lands the listing has been
        replaced, and a row number that meant something when the user pressed
        the key would mean something else by now.

        The queue rather than a worker request, since 0.14, and the reason is
        the deadline. A delete on the worker path was one request against
        `timeout.delete`, so a recycle of 30,000 files on a share expired --
        the pane said the delete had not finished while the shell carried on
        deleting, which is the worst of both answers. A job has no deadline; it
        has a person watching it.

        Without a queue -- a preview render, a test that builds a pane on its
        own -- this does nothing rather than falling back to the worker. A
        second path to a destructive operation is a second path that only gets
        exercised half the time, and this is not the operation to have two of.
        """
        tab = self.current
        if not names or self.transfers is None:
            return
        paths_ = [paths.join(tab.path, name) for name in names]
        kind = "deleting permanently" if permanent else "deleting"
        self._set_status(tab, f"{kind} {len(names)} item(s)", BUSY)
        if permanent:
            self.transfers.erase(paths_)
        else:
            self.transfers.recycle(paths_)

    def to_clipboard(self, names: list[str], *, cut: bool = False) -> int:
        """Put the named items on the system clipboard. Returns how many.

        Names for `delete`'s reason: a row number stops meaning anything the
        moment the listing is replaced, and the clipboard outlives the
        listing by design -- copying here and pasting ten minutes later in
        Explorer has to work.
        """
        if not names or self.clipboard is None:
            return 0
        items = self.paths_for(names)
        placed = self.clipboard.cut(items) if cut else self.clipboard.copy(items)
        if not placed:
            return 0
        word = "cut" if cut else "copied"
        self._set_status(self.current, f"{word} {len(items)} item(s)", IDLE)
        return len(items)

    def paste(self) -> str:
        """Paste into this folder. Empty when a job started, or why it did not.

        The destination is this pane's folder and there is no prompt, which is
        the one place this application picks a destination without asking --
        and it is not really picking one. `Ctrl+V` in a folder is a person
        pointing at it; a dialog asking "into here?" after they have already
        said where would be the kind of confirmation that trains people to
        dismiss confirmations. What is refused instead is the paste that could
        not be undone by looking at it: a folder into itself or into its own
        subtree, and a cut pasted back where it came from.

        A copy pasted into the folder it came from is the way a duplicate is
        made, so it goes in with `RENAME` rather than asking about every name
        the user has already collided with on purpose.
        """
        if self.clipboard is None or self.transfers is None:
            return "no clipboard"
        sources, cut = self.clipboard.contents()
        destination = self.current.path
        why = refusal(sources, destination, cut=cut)
        if why:
            # On this pane's own line, not the window's. A refused paste is
            # the pane answering, and the pane answers where it answers
            # everything else -- under the listing, beside the folder it is
            # about. The window's status bar is the far corner of the window
            # from the pane that was right-clicked, and six seconds later it
            # is gone: reported there, a refusal reads as a key that did
            # nothing, which is exactly what it is not.
            self._set_status(self.current, why, BAD)
            return why
        same = {paths.resolve(paths.parent(item) or "").lower() for item in sources}
        conflict = Conflict.RENAME \
            if not cut and same == {paths.resolve(destination).lower()} \
            else Conflict.ASK
        if cut:
            self.transfers.move(sources, destination, conflict=conflict)
            # The way Explorer does it: a cut is spent once it has been
            # pasted. Leaving it there would offer to move the same files
            # again, out of a folder they are no longer in.
            self.clipboard.clear()
        else:
            self.transfers.copy(sources, destination, conflict=conflict)
        word = "moving" if cut else "copying"
        self._set_status(self.current, f"{word} {len(sources)} item(s) here", BUSY)
        return ""

    def measure(self, names: list[str]) -> None:
        """Count what is under the named folders in this one.

        Folders only. A file's size is already in the listing, and asking a
        worker to walk one would be a round trip for a number on screen.
        """
        if self.sizes is None:
            return
        tab = self.current
        folders = [name for name in names
                   if (entry := tab.model.entry_named(name)) is not None
                   and entry.is_dir]
        if folders:
            self.sizes.request(tab.path, folders)

    def measure_all(self) -> None:
        """Every folder in this one. Deliberately a separate command.

        Deliberately, because it is the expensive one: on a share this is a
        walk per folder, one after another, and it should be something asked
        for rather than something that happens because a folder was opened.
        """
        tab = self.current
        self.measure(tab.model.folder_names())

    def stop_measuring(self) -> None:
        if self.sizes is not None:
            self.sizes.cancel()

    def context_menu(self, names: list[str], *, extended: bool = False) -> None:
        """Ask the shell for the menu for a selection in this tab's folder.

        Names rather than paths, and this pane's folder rather than one the
        widget worked out: the widget knows which rows are marked, and where
        those rows are is this layer's business as it is everywhere else.
        """
        if self.menu is None or not self._config.get("menu.shell"):
            return
        self.menu.request(self.current.path, names, extended=extended)

    def elevate(self, plan: dict) -> None:
        """Run one refused operation again, as administrator.

        Only ever reached from the dialog `elevationOffered` puts on screen.
        The reply is treated exactly like the original operation's: the folder
        is re-listed, because what is on disk is the truth and this is a
        second guess at changing it.
        """
        tab = self.current
        self._set_status(tab, f"{elevate.describe(plan)}, as administrator", BUSY)

        def handle(reply: Reply) -> None:
            if reply.status is Status.OK:
                if tab is self.current:
                    self.refresh()
                self.folderChanged.emit(tab.path)
                return
            if reply.status is Status.DENIED:
                self._set_status(tab, "the request to run as administrator was "
                                      "refused", BAD)
                return
            self._set_status(tab, f"as administrator: {_explain(reply)}", BAD)

        self._bridge.submit(
            Op.ELEVATE, tab.path,
            timeout=float(self._config.get("timeout.elevate")),
            on_reply=handle, args={"plan": plan},
        )

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
                if reply.status is Status.DENIED:
                    # Denied in a protected folder is the one failure with a
                    # way forward, and it is a person's decision rather than
                    # this layer's: what goes out is an offer.
                    plan = elevate.plan_for(op, path, args)
                    if plan is not None:
                        self.elevationOffered.emit(plan, elevate.describe(plan))
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

    def open_tab(self, path: str | None = None, *, background: bool = False,
                 locked: bool = False) -> None:
        """A new tab, here or beside the current one.

        `background` is what a middle click means: the folder is opened without
        the pane leaving what is on screen, so a handful of folders can be
        queued up in one pass down a listing.
        """
        if len(self.tabs) >= MAX_TABS:
            return
        tab = Tab(path or self.current.path, self.icons, self.overlays,
                  self.sizes, locked=locked, file_icons=self.file_icons,
                  clipboard=self.clipboard)
        self.tabs.append(tab)
        self.tabsChanged.emit()
        if background:
            # Listed anyway. A background tab that is empty until it is looked
            # at makes switching to it feel slower than opening it did.
            self._list(tab)
            return
        self.index = len(self.tabs) - 1
        self.currentChanged.emit()
        self.navigate(tab.path, record=False)

    def duplicate_tab(self, index: int | None = None) -> None:
        """A second tab on the same folder, which is how a copy within one
        tree gets set up without losing the place already found."""
        source = self.tabs[index] if index is not None and 0 <= index < len(self.tabs) \
            else self.current
        self.open_tab(source.path)

    def close_tab(self, index: int) -> None:
        if len(self.tabs) <= 1 or not 0 <= index < len(self.tabs):
            return
        if self.tabs[index].locked:
            return
        self._abandon(self.tabs[index])
        del self.tabs[index]
        self.index = min(self.index if index > self.index else self.index - 1,
                         len(self.tabs) - 1)
        self.index = max(0, self.index)
        self.tabsChanged.emit()
        self.currentChanged.emit()
        self._announce(self.current)

    def close_others(self, index: int) -> None:
        """Everything but this one, locked tabs excepted.

        Locked tabs survive because that is what the lock is for: this is the
        command most likely to be reached for by accident, and the tab somebody
        pinned to a job folder is the one they would least like to lose to it.
        """
        if not 0 <= index < len(self.tabs):
            return
        keep = self.tabs[index]
        self._close_all(lambda tab: tab is keep or tab.locked)

    def close_to_right(self, index: int) -> None:
        if not 0 <= index < len(self.tabs):
            return
        keep = set(id(tab) for tab in self.tabs[:index + 1])
        self._close_all(lambda tab: id(tab) in keep or tab.locked)

    def move_tab(self, source: int, target: int) -> None:
        """Follow a drag in the strip.

        Without this the widget's order and this list's order disagree after a
        drag, and every index after it -- the one a click selects, the one a
        close button reports -- names a different tab than the one under it.
        """
        if source == target:
            return
        if not (0 <= source < len(self.tabs) and 0 <= target < len(self.tabs)):
            return
        current = self.current
        self.tabs.insert(target, self.tabs.pop(source))
        self.index = self.tabs.index(current)
        self.tabsChanged.emit()

    def select_tab(self, index: int) -> None:
        if not 0 <= index < len(self.tabs) or index == self.index:
            return
        self.index = index
        self.currentChanged.emit()
        self._announce(self.current)

    def cycle_tab(self, step: int) -> None:
        """The next tab along, wrapping. One tab is not a special case."""
        if len(self.tabs) > 1:
            self.select_tab((self.index + step) % len(self.tabs))

    def set_locked(self, index: int, locked: bool) -> None:
        if not 0 <= index < len(self.tabs):
            return
        self.tabs[index].locked = bool(locked)
        self.tabsChanged.emit()
        if index == self.index:
            # Back and forward are disabled on a locked tab, and the widget
            # reads that off the status.
            self._announce(self.current)

    def toggle_lock(self, index: int | None = None) -> None:
        target = self.index if index is None else index
        if 0 <= target < len(self.tabs):
            self.set_locked(target, not self.tabs[target].locked)

    def _close_all(self, keep) -> None:
        survivors = [tab for tab in self.tabs if keep(tab)]
        if len(survivors) == len(self.tabs) or not survivors:
            return
        current = self.current
        for tab in self.tabs:
            if tab not in survivors:
                self._abandon(tab)
        self.tabs = survivors
        self.index = survivors.index(current) if current in survivors else 0
        self.tabsChanged.emit()
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

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

import datetime
import re
import time
from typing import Callable

from PySide6.QtCore import QObject, Qt, QTimer, Signal

from app.core import naming
from app.core.clipboard import refusal
from app.core.listing import Column, ListingModel, format_size
from app.core.remembered import Remembered
from app.core import undo
from app.core.sorts import SortMemory
from app.io.archive import SUFFIXES as ARCHIVE_SUFFIXES, is_archive_name
from app.io.archive import split as archive_split
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

#: How many steps of one tab's history are kept. `MAX_TABS`' reasoning applied
#: to the other list that only ever grows: forty tabs each remembering every
#: folder visited since the window opened is a list nobody is navigating and a
#: dropdown (0.27) nobody can read. Dropping from the front costs the oldest
#: Alt+Left, which is a step past the point anybody backs up to.
MAX_HISTORY = 200


#: How often a pane looks at whether the folder on screen is due a check.
#: Not the check interval -- that is a setting per kind of volume -- just the
#: resolution of the schedule.
CHECK_TICK = 1.0

#: A check costs at most a tenth of the time: the wait after a listing is at
#: least ten times as long as the listing took.
CHECK_COST_FACTOR = 10.0

#: The shortest wait after a check that failed, before it doubles. Long enough
#: that a dead share's worker cannot be killed three times inside the pool's
#: restart window by checks alone.
CHECK_FAILURE_WAIT = 30.0

#: The longest any folder waits between checks, however slow or broken.
CHECK_LONGEST = 300.0


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
        #: Whether the model holds a finished listing of `path`, which is what
        #: lets a refresh of the same folder be reconciled into it rather than
        #: start from nothing.
        self.listed = False
        #: The rows of a listing being reconciled, gathered until it ends, or
        #: None when rows stream straight into the model as they arrive.
        self.buffer: list | None = None
        #: Whether the request in flight is the live check nobody asked for,
        #: which reports nothing unless it finds a change.
        self.quiet = False
        self.started = 0.0
        #: When this tab is next due a live check, on `time.monotonic`.
        self.check_after = 0.0
        self.check_failures = 0
        #: Flat view (0.25): this tab lists every file under `path` rather
        #: than what is in it. Ends when the tab goes to another folder.
        self.flat = False
        #: 0.31: when the rows on screen were listed, on the wall clock, and
        #: whether a later listing has failed to reach the folder -- so those
        #: rows are what was there then rather than what is there now.
        self.listed_at = 0.0
        self.stale = False
        #: 0.40: the order last clicked in this tab, as (column, 0/1), which a
        #: folder with no remembered order of its own is listed in. None until
        #: a heading is clicked: the model's own order stands.
        self.free_sort: tuple[int, int] | None = None
        #: 0.42: a search tab is a flat view with a filter: the search's
        #: fields as `app/io/search.py` reads them, or None. `duplicates`
        #: makes it the duplicate finder. Both end with the flat view.
        self.search: dict | None = None
        self.duplicates = False

    @property
    def label(self) -> str:
        if self.duplicates:
            return f"Duplicates in {paths.leaf(self.path)}"
        if self.search is not None:
            what = (self.search.get("names") or "").strip() \
                or (f'"{self.search.get("text")}"' if self.search.get("text") else "")
            return f"Search: {what or 'everything'}"
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
    #: 0.50.5: a share refused the session's account -- the share, and
    #: Windows' reason. The window answers with a login prompt, as Double
    #: Commander does with Windows' own; nothing here asks anybody anything.
    loginNeeded = Signal(str, str)
    #: A tab went into or out of flat view, or the flat layout changed.
    flatChanged = Signal()
    #: 0.31: the tab in front started or stopped showing rows that are out of
    #: date because its share stopped answering. `current.stale` says which.
    staleChanged = Signal()
    #: 0.44: something done here that undo can take back (`core.undo.Action`).
    undoable = Signal(object)

    def __init__(self, bridge, config, side: str, icons=None, overlays=None,
                 menu=None, sizes=None, siblings=None, parent=None,
                 file_icons=None, transfers=None, clipboard=None,
                 previews=None, thumbnails=None, remembered=None) -> None:
        super().__init__(parent)
        # Shared with the other pane, for the reason the name gives: what a
        # folder on a share held ten minutes ago is the same answer whichever
        # side of the window goes back to it.
        self.remembered = remembered if remembered is not None else Remembered()
        self._bridge = bridge
        self._config = config
        self._side = side
        #: 0.40: per-folder sort orders, kept in the settings both panes share.
        self.sorts = SortMemory(config)
        self._checks: QTimer | None = None
        self._live = True
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
        # Shared for the seventh reason, which is the shell menu's and the
        # siblings' again: one file is being previewed at a time whichever pane
        # the cursor is in, and an abandoned decode still holds the volume the
        # next one wants. Sharing it is what makes moving the cursor in the left
        # pane cancel the right pane's outstanding read rather than queue behind
        # it.
        self.previews = previews
        # Shared for the icons' reason: what a photograph looks like at 128
        # pixels is the same on both sides of the window, and the cache is what
        # stops the second pane decoding it again.
        self.thumbnails = thumbnails
        #: 0.38: colour labels and notes, set by the window; None draws none.
        self.labels = None
        #: 0.38: git's marks, set by the window; None asks nothing.
        self.git = None
        self.tabs: list[Tab] = self._restore()
        for tab in self.tabs:
            self._apply_rules(tab)
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

    def _apply_rules(self, tab: "Tab") -> None:
        """The settings every tab's model follows, handed to one tab."""
        tab.model.set_attribute_rule(
            hidden=bool(self._config.get("listing.hidden")),
            system=bool(self._config.get("listing.system")))
        tab.model.set_folder_bars(bool(self._config.get("listing.folder_bars")))
        tab.model.set_labels(self.labels if bool(self._config.get("labels.shown"))
                             else None)
        tab.model.set_git(self.git)

    def set_git(self, git) -> None:
        self.git = git
        self.apply_rules()

    @property
    def in_archive(self) -> bool:
        """0.41: whether the tab in front is inside an archive, by its name.

        Name only, which is all this side may know: a folder that is merely
        called `backup.zip` answers yes here and is then listed as the folder
        it is. Used for courtesy -- saying "read-only" before a request rather
        than after -- and never as the rule; the worker and the engine refuse
        writes into a real archive whatever this says.
        """
        return archive_split(self.current.path) is not None

    def archive_extract_name(self, row: int) -> str | None:
        """The folder an archive row would be extracted into: its name without
        the archive suffix. None for a row that is not an archive."""
        entry = self.current.model.entry(row)
        if entry is None or entry.is_dir or not is_archive_name(entry.name):
            return None
        lowered = entry.name.lower()
        for suffix in ARCHIVE_SUFFIXES:
            if lowered.endswith(suffix):
                return entry.name[:-len(suffix)]
        return None

    def extract(self, row: int, destination: str) -> bool:
        """0.41: all of an archive row, into a folder named for it under
        `destination`, as a copy job. The trailing separator on the source is
        what tells the engine "the contents", not "the file"."""
        name = self.archive_extract_name(row)
        source = self.row_path(row)
        if name is None or source is None or self.transfers is None:
            return False
        self._set_status(self.current, f"extracting {paths.leaf(source)}", BUSY)
        self.transfers.extract(source + "\\", destination, name)
        return True

    def ask_git(self) -> None:
        """The folder in front has been listed: let git's marks catch up."""
        if self.in_archive:
            return
        if self.git is not None and not self.current.flat:
            self.git.ask(self.current.path)

    def set_labels(self, labels) -> None:
        self.labels = labels
        self.apply_rules()

    def apply_rules(self) -> None:
        """A setting behind `_apply_rules` changed: every tab, at once.

        Re-filtered from the rows already here rather than listed again --
        the attributes arrived with the listing -- so this costs nothing on a
        share however many tabs are open.
        """
        for tab in self.tabs:
            self._apply_rules(tab)
            if tab.request_id is None and tab.listed:
                self._set_status(tab, tab.model.summary(), tab.status_state)

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

    def resolved(self, path: str | None = None) -> str:
        """The form real work keys on: `display`'s opposite number.

        For a caller that has to act on the *volume* rather than draw it -- a
        reconnect, which has to know which share the pane is standing in even
        when the pane is showing a drive letter. It reads the same local
        session table `display` already reads on this thread and touches no
        server.
        """
        return paths.resolve(path or self.current.path)

    def set_show_unc(self, value: bool) -> None:
        self._config.set(f"{self._side}.show_unc", bool(value))
        self.pathChanged.emit(self.display())

    @property
    def columns(self) -> list[int]:
        """The listing's column widths as this pane was left, or [].

        View state rather than model state, and it lives here for the reason
        `show_unc` does: the widget is rebuilt on a theme change and the tabs
        come and go, while this has to outlast both and be written to the
        settings file by the same path as everything else.
        """
        stored = self._config.get(f"{self._side}.columns")
        return [int(width) for width in stored] if isinstance(stored, list) else []

    def set_columns(self, widths) -> None:
        self._config.set(f"{self._side}.columns", [int(width) for width in widths])

    @property
    def hidden_columns(self) -> list[int]:
        stored = self._config.get(f"{self._side}.columns_hidden")
        return [int(column) for column in stored] if isinstance(stored, list) else []

    def set_hidden_columns(self, columns) -> None:
        self._config.set(f"{self._side}.columns_hidden",
                         sorted({int(column) for column in columns}))

    @property
    def view_mode(self) -> str:
        """"list" or "grid", for every tab in this pane.

        Per pane rather than per tab, deliberately: a view that varied by tab
        would make Ctrl+Tab change the shape of the window, and the tabs in one
        pane are usually one job being looked at one way. Stored rather than
        remembered in the widget, because it survives a restart -- somebody who
        works in the grid should not have to press a key every morning.
        """
        return "grid" if self._config.get(f"{self._side}.view") == "grid" else "list"

    def set_view_mode(self, mode: str) -> None:
        self._config.set(f"{self._side}.view",
                         "grid" if mode == "grid" else "list")

    def file_names(self) -> list[str]:
        """The files in this listing, in the order it is sorted in.

        What the viewer walks. Files only -- there is nothing to view in a
        folder, and stepping through a list that jumped over every folder in it
        would read as the arrow keys skipping. The order is the model's, which
        is the order the eye just saw: sorting it here would open a viewer on a
        folder sorted by date in alphabetical order instead.
        """
        model = self.current.model
        names: list[str] = []
        for row in range(model.rowCount()):
            if model.is_parent_row(row):
                continue
            entry = model.entry(row)
            if entry is not None and not entry.is_dir:
                names.append(entry.name)
        return names

    # ------------------------------------------------------------- 0.40 sort

    def _sort_for(self, tab: Tab, target: str) -> None:
        """Set the order a folder is about to be listed in, before it is."""
        chosen = self.sorts.get(target) or tab.free_sort
        if chosen is None:
            return
        column, order = chosen
        wanted = Qt.DescendingOrder if order else Qt.AscendingOrder
        if (int(tab.model.sort_column), tab.model.sort_order) != (column, wanted):
            tab.model.set_sort(column, wanted)

    def sorted_by_hand(self, column: int, descending: bool) -> None:
        """A heading was clicked: the tab's order, and this folder's."""
        tab = self.current
        order = 1 if descending else 0
        tab.free_sort = (int(column), order)
        if not tab.flat:
            self.sorts.put(tab.path, int(column), order)

    def forget_sort(self) -> bool:
        return self.sorts.forget(self.current.path)

    def has_own_sort(self) -> bool:
        return self.sorts.has(self.current.path)

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

        same_folder = tab.listed and target == tab.path
        if tab.flat and target != tab.path:
            # Flat view belongs to the folder it was asked for. Going somewhere
            # else -- a location clicked, Backspace, a favourite -- is a normal
            # listing of that place. A search ends the same way.
            tab.search, tab.duplicates = None, False
            tab.flat = False
            tab.model.set_flat(False)
            self.flatChanged.emit()
        if not same_folder and not tab.flat:
            self._sort_for(tab, target)
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
            if len(tab.history) > MAX_HISTORY:
                # From the front, and `position` moves with it: the index is
                # into this list, so trimming without adjusting it would point
                # Alt+Left at a folder somebody was never in.
                going = len(tab.history) - MAX_HISTORY
                del tab.history[:going]
            tab.position = len(tab.history) - 1

        self._list(tab, announce=True, keep=same_folder)

    def _list(self, tab: Tab, *, announce: bool = False, keep: bool = False,
              quiet: bool = False) -> None:
        """Ask for the rows of whatever folder a tab is on.

        Separate from `navigate` because a background tab lists without the
        pane's path bar, status line or drive picker changing -- those belong
        to whatever is on screen, and a tab opened behind is not it.

        `keep` is a listing of the folder already on screen: the rows are
        gathered and reconciled into the model when the listing ends, so the
        marks, the cursor and the scroll position survive it. A new folder
        streams into an empty model instead, because there is nothing to keep
        and a 50,000-row folder should start painting at once. `quiet` is the
        live check, which says nothing unless it finds something.
        """
        if not quiet:
            self._abandon(tab)
        if tab.flat:
            # Never reconciled: a refresh of a flat view walks again from the
            # top, streaming, and the grouped layout's headings are laid out
            # from a model that was reset.
            keep = False
        if keep:
            tab.buffer = []
        else:
            tab.buffer = None
            tab.listed = False
            self._set_stale(tab, False)
            tab.model.begin(has_parent=not tab.flat
                            and paths.parent(tab.path) is not None)
        tab.quiet = quiet
        if not quiet:
            self._set_status(tab, "refreshing" if keep else "listing", BUSY)
            if announce:
                self.pathChanged.emit(self.display(tab.path))
            self.tabsChanged.emit()

        tab.started = time.monotonic()
        if tab.flat:
            args: dict = {"limit": int(self._config.get("flat.limit"))}
            if tab.search is not None or tab.duplicates:
                args = {"limit": int(self._config.get("search.limit")),
                        "search": dict(tab.search or {})}
                if tab.duplicates:
                    args["duplicates"] = True
            tab.request_id = self._bridge.submit(
                Op.WALK, tab.path,
                timeout=float(self._config.get("timeout.listing")),
                on_reply=self._replier(tab),
                args=args,
            )
            return
        tab.request_id = self._bridge.submit(
            Op.LIST, tab.path,
            timeout=float(self._config.get("timeout.listing")),
            on_reply=self._replier(tab),
        )

    # --------------------------------------------------------------- flat view

    @property
    def flat_layout(self) -> str:
        """"column" for a Location column, "groups" for a heading per folder."""
        value = str(self._config.get("flat.layout"))
        return value if value in ("column", "groups") else "column"

    def set_flat_layout(self, layout: str) -> None:
        if layout not in ("column", "groups"):
            return
        self._config.set("flat.layout", layout)
        for tab in self.tabs:
            if tab.flat:
                tab.model.set_flat(True, grouped=layout == "groups")
        self.flatChanged.emit()

    def set_flat(self, on: bool) -> None:
        """Flat view on or off for the tab in front, then list it again."""
        tab = self.current
        on = bool(on)
        if on == tab.flat:
            return
        tab.flat = on
        if not on:
            tab.search, tab.duplicates = None, False
        tab.model.set_flat(on, grouped=self.flat_layout == "groups")
        self.flatChanged.emit()
        self.tabsChanged.emit()
        self._list(tab, announce=False)

    def go_to_location(self, row: int) -> None:
        """Leave flat view for the folder a row is in, cursor on the file."""
        tab = self.current
        entry = tab.model.entry(row)
        if not tab.flat or entry is None:
            return
        where, _, name = entry.name.rpartition("\\")
        target = paths.join(tab.path, where) if where else tab.path
        tab.reveal_name = name
        if target == tab.path:
            self.set_flat(False)
        else:
            self.navigate(target)

    def search(self, folder: str, spec: dict, *, duplicates: bool = False) -> bool:
        """0.42: a new tab listing what matches under `folder`.

        A flat view with a filter, so the results are rows like any other --
        marked, copied, deleted, previewed, opened -- and the Location column
        (or the folder headings) says where each one is. Leaving the folder,
        or Ctrl+B, ends it; F5-refresh runs it again. The duplicate finder is
        the same tab laid out by size, so each set of twins sits together.
        """
        if len(self.tabs) >= MAX_TABS:
            self.say("too many tabs open to start a search", BAD)
            return False
        tab = Tab(folder, self.icons, self.overlays, self.sizes,
                  file_icons=self.file_icons, clipboard=self.clipboard)
        self._apply_rules(tab)
        tab.flat = True
        tab.search = dict(spec)
        tab.duplicates = bool(duplicates)
        tab.model.set_flat(True, grouped=self.flat_layout == "groups" and not duplicates)
        if duplicates:
            tab.model.set_sort(int(Column.SIZE), Qt.DescendingOrder)
        self.tabs.append(tab)
        self.index = len(self.tabs) - 1
        self.tabsChanged.emit()
        self.currentChanged.emit()
        self.flatChanged.emit()
        self._list(tab, announce=True)
        return True

    def toggle_flat(self) -> None:
        self.set_flat(not self.current.flat)

    def stop_walk(self) -> bool:
        """Stop a flat view that is still walking, keeping what it found.
        Returns whether there was one to stop."""
        tab = self.current
        if not tab.flat or tab.request_id is None:
            return False
        self._abandon(tab)
        tab.model.finish()
        tab.listed = True
        self._set_status(tab, f"{tab.model.summary()}  ·  stopped", IDLE)
        return True

    def refresh(self) -> None:
        self.navigate(self.current.path, record=False)

    # ------------------------------------------------------------ live folders

    @property
    def busy(self) -> bool:
        """Whether the tab in front has a listing in flight that somebody asked
        for. A live check does not count: anything that wants to re-list the
        folder may go ahead, and it cancels the check."""
        tab = self.current
        return tab.request_id is not None and not tab.quiet

    def start_checks(self) -> None:
        """Start looking for changes to the folder on screen.

        Started by the application rather than in `__init__`, so a pane built
        for a test or a preview render never lists anything on its own.
        """
        if self._checks is None:
            self._checks = QTimer(self)
            self._checks.setInterval(int(CHECK_TICK * 1000))
            self._checks.timeout.connect(self.check)
        self._checks.start()

    def set_live(self, live: bool) -> None:
        """Whether checks run at all: off while the window is minimised, where
        nobody is looking and a share would be asked for nothing."""
        self._live = live

    def check_now(self) -> None:
        """Check the folder on screen at the next opportunity rather than on
        its schedule -- for coming back to the window, which is when a change
        made in another program is most likely to be waiting."""
        self.current.check_after = 0.0
        self.check()

    def check(self, now: float | None = None) -> bool:
        """List the folder on screen again if it is due, and say whether it was.

        Polled, on every kind of volume, and deliberately not watched. SMB
        change notification is not reliable enough to trust a view to, which is
        in `PROJECT-CONTEXT.md`; a Hyper-V redirected drive is further from
        reliable than that; and a watch is a request that never finishes, which
        a worker answering one request at a time cannot hold. A poll is one
        ordinary listing, with the listing's deadline and cancel, and the rows
        are reconciled so nothing on screen moves unless the folder did.

        Only the tab in front, and only one that has a finished listing and
        nothing else in flight: a check never stands in front of something a
        person asked for.
        """
        tab = self.current
        if not self._live or not tab.listed or tab.request_id is not None:
            return False
        if tab.flat:
            return False        # a walk of a whole tree is not a live check
        if (now if now is not None else time.monotonic()) < tab.check_after:
            return False
        if self._check_interval(tab) <= 0:
            return False
        self._list(tab, keep=True, quiet=True)
        return True

    def _check_interval(self, tab: Tab) -> float:
        local = paths.volume_key(tab.path) == paths.LOCAL_VOLUME_KEY
        key = "refresh.local_seconds" if local else "refresh.network_seconds"
        return float(self._config.get(key) or 0.0)

    def _schedule(self, tab: Tab, elapsed: float, *, ok: bool) -> None:
        """When the next check is due, after a listing of this tab ended.

        The interval is the setting, **stretched for a folder that is expensive
        to list**: a check is never more than a tenth of the time, so a folder
        of 50,000 rows that takes four seconds over a share is checked every
        forty seconds rather than every five. A failure backs off, doubling,
        because a share that has gone away should be asked less often rather
        than on a schedule that keeps its worker being killed.
        """
        base = self._check_interval(tab)
        now = time.monotonic()
        if base <= 0:
            tab.check_after = float("inf")
            return
        if ok:
            tab.check_failures = 0
            wait = max(base, elapsed * CHECK_COST_FACTOR)
        else:
            tab.check_failures += 1
            wait = max(base, CHECK_FAILURE_WAIT) * (2 ** (tab.check_failures - 1))
        tab.check_after = now + min(wait, CHECK_LONGEST)

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

    def go_to_history(self, index: int) -> None:
        """Jump straight to one step of this tab's history (0.27's dropdown)."""
        tab = self.current
        if tab.locked or not 0 <= index < len(tab.history) or index == tab.position:
            return
        tab.position = index
        self.navigate(tab.history[index], record=False)

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
        elif (is_archive_name(entry.name) and not tab.flat and not self.in_archive
              and self._config.get("archives.browse")):
            # 0.41: into the archive, like a folder. Not from inside one:
            # archives inside archives are not opened.
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

    def drop_target(self, row: int) -> str:
        """Where a drop on `row` goes. Pure arithmetic on the listing.

        Onto a folder is into it, and onto `..` is into the folder above --
        Double Commander's gesture, and the only way to drop upwards without
        opening a second tab. Onto a file or the empty space below the rows is
        into the folder on screen.
        """
        folder = self.current.path
        model = self.current.model
        if row >= 0:
            if model.is_parent_row(row):
                return paths.parent(folder) or folder
            entry = model.entry(row)
            if entry is not None and entry.is_dir:
                return paths.join(folder, entry.name)
        return folder

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
        folder = tab.path
        self._mutate(tab, Op.MKDIR, paths.join(tab.path, name),
                     timeout=float(self._config.get("timeout.mkdir")),
                     reveal=name, failed=f"could not create {name}",
                     done=lambda: self.undoable.emit(undo.for_mkdir(folder, name)))

    def rename(self, row: int, name: str) -> None:
        tab = self.current
        source = self.row_path(row)
        if source is None or not name:
            return
        self._set_status(tab, f"renaming to {name}", BUSY)
        old = paths.leaf(source)
        folder = tab.path

        def carry() -> None:
            # 0.38: a label is kept by path, so a rename made here takes it along.
            if self.labels is not None:
                self.labels.moved(folder, old, folder, name)
            self.undoable.emit(undo.for_rename(folder, [(old, name)]))

        self._mutate(tab, Op.RENAME, source,
                     timeout=float(self._config.get("timeout.rename")),
                     args={"name": name}, reveal=name,
                     failed=f"could not rename to {name}", done=carry)

    def rename_items(self, names: list[str]) -> list:
        """0.41: the named rows as `renamer.Item`s, in listing order."""
        from app.core.renamer import Item

        items = []
        model = self.current.model
        for name in names:
            entry = model.entry(model.row_of(name))
            if entry is not None:
                items.append(Item(entry.name, float(entry.mtime or 0.0), bool(entry.is_dir)))
        return items

    def rename_many(self, steps: list[tuple[str, str]], moves: list[tuple[str, str]],
                    folder: str | None = None, *, record: bool = True) -> None:
        """0.41: run a rename plan in this folder as one request.

        `steps` is the plan, temporary names and all; `moves` is old name to
        final name, which is what the labels follow and which name the cursor
        goes to. The timeout grows with the plan, because on a share each
        step is a round trip.
        """
        tab = self.current
        if not steps:
            return
        folder = folder or tab.path
        count = len(moves)
        self._set_status(tab, f"renaming {count} item{'s' if count != 1 else ''}", BUSY)

        def carry() -> None:
            if self.labels is not None:
                for old, new in moves:
                    self.labels.moved(folder, old, folder, new)
            self._set_status(tab, f"renamed {count} item{'s' if count != 1 else ''}", IDLE)

        def handle(reply: Reply) -> None:
            if reply.status is Status.OK:
                carry()
                if record:
                    self.undoable.emit(undo.for_rename(folder, list(moves)))
                if paths.normalize(folder).lower() == tab.path.lower():
                    tab.reveal_name = moves[0][1] if moves else None
            else:
                payload = reply.payload or {}
                note = "" if not payload.get("done") else (
                    " -- every name was put back" if payload.get("undone")
                    else " -- and some names could not be put back; check the folder")
                self._set_status(tab, f"rename stopped: {_explain(reply)}{note}", BAD)
            if tab is self.current:
                self.refresh()
            self.folderChanged.emit(tab.path)

        timeout = float(self._config.get("timeout.rename")) + 0.5 * len(steps)
        self._bridge.submit(Op.RENAME_MANY, folder, timeout=timeout, on_reply=handle,
                            args={"steps": [list(step) for step in steps]})

    def checksums(self, names: list[str], algorithm: str, on_done) -> int | None:
        """0.43: checksums of named rows in this folder, from its worker.

        `on_done(payload_or_None, message)` is called once, on this thread.
        Returns the request id, for cancelling when the dialog is closed.
        """
        if not names:
            return None
        count = sum(1 for name in names)

        def handle(reply: Reply) -> None:
            if reply.status is Status.PARTIAL:
                return
            if reply.status is Status.OK:
                on_done(reply.payload, "")
            else:
                on_done(None, _explain(reply))

        timeout = float(self._config.get("timeout.listing"))
        self._set_status(self.current, f"reading {count} file{'s' if count != 1 else ''} "
                                       f"for {algorithm.upper()}", BUSY)
        return self._bridge.submit(Op.HASH, self.current.path, timeout=timeout,
                                   on_reply=handle,
                                   args={"names": list(names), "algorithm": algorithm})

    def set_attributes(self, names: list[str], change: dict) -> None:
        """0.43: attributes and dates for named rows, then the folder again."""
        tab = self.current
        if not names:
            return
        count = len(names)
        self._set_status(tab, f"changing {count} item{'s' if count != 1 else ''}", BUSY)

        def handle(reply: Reply) -> None:
            payload = reply.payload or {}
            failed = payload.get("failed") or {}
            done = int(payload.get("changed") or 0)
            if failed:
                first = next(iter(failed.items()))
                self._set_status(tab, f"changed {done:,}; {len(failed):,} could not be "
                                      f"changed -- {first[0]}: {first[1]}", BAD)
            elif reply.status is not Status.OK:
                self._set_status(tab, f"could not change them: {_explain(reply)}", BAD)
            else:
                self._set_status(tab, f"changed {done:,} item{'s' if done != 1 else ''}", IDLE)
            if tab is self.current:
                self.refresh()

        timeout = float(self._config.get("timeout.listing")) * (4 if change.get("recursive") else 1)
        self._bridge.submit(Op.ATTRIBUTES, tab.path, timeout=timeout, on_reply=handle,
                            args={"names": list(names), **change})

    def make_link(self, folder: str, name: str, target: str, kind: str) -> None:
        """0.45: a link in `folder` -- this pane's or the other's -- pointing
        at `target`, made by that folder's worker."""
        tab = self.current

        def handle(reply: Reply) -> None:
            if reply.status is Status.OK:
                self._set_status(tab, f"made {name}", IDLE)
                self.folderChanged.emit(folder)
            else:
                self._set_status(tab, f"could not make {name}: {_explain(reply)}", BAD)

        self._bridge.submit(Op.LINK, folder, timeout=float(self._config.get("timeout.rename")),
                            on_reply=handle, args={"name": name, "target": target, "kind": kind})

    def follow_link(self, row: int) -> None:
        """0.45: go where the link on a row points: into it for a folder, to
        the folder it is in, cursor on it, for a file."""
        tab = self.current
        entry = tab.model.entry(row)
        source = self.row_path(row)
        if entry is None or source is None:
            return

        def handle(reply: Reply) -> None:
            if reply.status is not Status.OK:
                self._set_status(tab, f"{entry.name}: {_explain(reply)}", BAD)
                return
            target = paths.normalize(str((reply.payload or {}).get("target") or ""))
            if not target:
                return
            if entry.is_dir:
                self.navigate(target)
                return
            where = paths.parent(target)
            if where:
                tab.reveal_name = paths.leaf(target)
                self.navigate(where)

        self._bridge.submit(Op.LINK_TARGET, source, timeout=float(self._config.get("timeout.rename")),
                            on_reply=handle)

    def cancel_request(self, request_id: int | None) -> None:
        if request_id is not None:
            self._bridge.cancel(request_id)
        self._set_status(self.current, self.current.model.summary(), IDLE)

    def duplicate_suggestion(self, row: int, today: datetime.date | None = None) -> str | None:
        """The name a duplicate of this row is offered, or None for no row.

        Today's date in the old one's place, in the form it was written; see
        `app/core/naming.py`. Checked against every name in the folder,
        including the ones a filter is hiding.
        """
        entry = self.current.model.entry(row)
        if entry is None:
            return None
        return naming.duplicate_name(entry.name, self.current.model.names(),
                                     today or datetime.date.today(),
                                     is_dir=entry.is_dir)

    def name_taken(self, name: str) -> bool:
        """Whether this folder, as last listed, already has something so named."""
        wanted = name.strip().lower()
        return any(existing.lower() == wanted for existing in self.current.model.names())

    def duplicate(self, row: int, name: str) -> bool:
        """Copy a row beside itself under `name`, through the queue.

        The queue rather than a worker request for `delete`'s reason: a day's
        worth of PLC projects is a real copy, and a copy has a person watching
        it rather than a deadline. Without a queue this does nothing.
        """
        tab = self.current
        source = self.row_path(row)
        if source is None or not name or self.transfers is None:
            return False
        self._set_status(tab, f"duplicating to {name}", BUSY)
        self.transfers.duplicate(source, tab.path, name)
        return True

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
        if self.in_archive:
            # 0.41: the clipboard hands paths to Explorer and every other
            # program, and these are not paths any of them can open.
            self._set_status(self.current,
                             "files inside an archive are copied out with F5", BAD)
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
        if not why and self.in_archive:
            why = "inside an archive is read-only here"
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
                failed: str = "the operation failed", done=None) -> None:
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
            if done is not None:
                done()
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
        self._apply_rules(tab)
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

    def replace_tabs(self, items: list[dict], index: int = 0) -> None:
        """0.38: every tab replaced by these, as a workspace opens them.

        Locked tabs go too: a workspace is a whole arrangement, and one that
        kept whatever happened to be locked would not be the one that was
        saved. The tab in front is listed now; the others list as they would
        after a restart, when they are looked at.
        """
        if not items:
            return
        for tab in self.tabs:
            self._abandon(tab)
        self.tabs = []
        for item in items[:MAX_TABS]:
            tab = Tab(item["path"], self.icons, self.overlays, self.sizes,
                      locked=bool(item.get("locked")), file_icons=self.file_icons,
                      clipboard=self.clipboard)
            self._apply_rules(tab)
            self.tabs.append(tab)
        self.index = max(0, min(int(index), len(self.tabs) - 1))
        self.tabsChanged.emit()
        self.currentChanged.emit()
        self.navigate(self.current.path, record=False)

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
            if tab.buffer is not None:
                tab.buffer.extend(reply.payload or [])
                if not tab.quiet:
                    self._set_status(tab, f"refreshing, {len(tab.buffer):,} rows", BUSY)
                return
            tab.model.add(reply.payload or [])
            if tab.flat:
                verb = ("finding duplicates" if tab.duplicates
                        else "searching" if tab.search is not None else "walking")
                noun = "found" if tab.search is not None or tab.duplicates else "files"
                self._set_status(
                    tab, f"{verb}, {tab.model.rowCount():,} {noun} so far  ·  Esc stops",
                    BUSY)
                return
            self._set_status(tab, f"listing, {tab.model.rowCount():,} rows", BUSY)
            return

        tab.request_id = None
        quiet, tab.quiet = tab.quiet, False
        elapsed = time.monotonic() - tab.started
        if reply.status is Status.OK:
            if tab.buffer is not None:
                rows, tab.buffer = tab.buffer + list(reply.payload or []), None
                changed = tab.model.reconcile(rows)
            else:
                tab.model.add(reply.payload or [])
                tab.model.finish()
                changed = True
            tab.listed = True
            tab.listed_at = time.time()
            if not tab.flat and _on_a_share(tab.path):
                self.remembered.keep(tab.path, tab.model.everything(), tab.listed_at)
            self._set_stale(tab, False)
            self._schedule(tab, elapsed, ok=True)
            if not quiet or changed or tab.status_state == BAD:
                self._set_status(tab, tab.model.summary() + _walk_note(tab, reply)
                                 + _archive_status(reply), IDLE)
            if not quiet:
                self._request_space(tab)
            if tab.reveal_name and tab is self.current:
                name, tab.reveal_name = tab.reveal_name, None
                self.revealRequested.emit(name)
            return

        kept, tab.buffer = tab.buffer is not None, None
        self._schedule(tab, elapsed, ok=False)
        # A share that has stopped answering, as opposed to a folder that said
        # no. Local disks keep the old behaviour: a local disk that times out
        # has not gone anywhere, and its rows are not "what was there then".
        unreachable = (reply.status in (Status.GONE, Status.TIMEOUT)
                       and not tab.flat and _on_a_share(tab.path))
        if quiet:
            # Nobody asked, so nobody is told about an ordinary failure. The
            # one exception since 0.31 is the share going away: the rows stay,
            # but a listing that looks current and is not is worse than one
            # that says it is old, and the check is the only thing that can
            # notice before somebody acts on it.
            if unreachable and tab.listed and not tab.stale:
                self._set_stale(tab, True)
                self._set_status(tab, _stale_note(tab), BAD)
            return
        if not kept and unreachable:
            found = self.remembered.recall(tab.path)
            if found is not None:
                # A folder this pane has seen before, on a share that has
                # stopped answering. What was there, marked as such, rather
                # than nothing; Retry lists it for real and reconciles, so a
                # mark made meanwhile survives if the file is still there.
                tab.listed_at, rows = found
                tab.model.add(rows)
                tab.model.finish()
                tab.listed = True
                self._set_stale(tab, True)
                self._set_status(tab, _stale_note(tab), BAD)
                return
        if not kept:
            tab.model.finish()
        if kept and unreachable and tab.listed_at:
            self._set_stale(tab, True)
            self._set_status(tab, _stale_note(tab), BAD)
            return
        share = _login_share(self.resolved(tab.path), reply)
        if share:
            self._set_status(tab, f"{share} needs a different account -- "
                                  f"Ctrl+Shift+R to connect", BAD)
            self.loginNeeded.emit(share, _windows_words(reply))
            return
        self._set_status(tab, _explain(reply), BAD)

    def _set_stale(self, tab: Tab, stale: bool) -> None:
        if tab.stale == stale:
            return
        tab.stale = stale
        if tab is self.current:
            self.staleChanged.emit()

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

    def say(self, text: str, state: str = IDLE) -> None:
        """Put a sentence on the status line of the tab in front."""
        self._set_status(self.current, text, state)

    def _set_status(self, tab: Tab, text: str, state: str) -> None:
        tab.status_text, tab.status_state = text, state
        if tab is self.current:
            self.statusChanged.emit(text, state)

    def _announce(self, tab: Tab) -> None:
        self.statusChanged.emit(tab.status_text, tab.status_state)
        self.pathChanged.emit(self.display(tab.path))
        self.spaceChanged.emit(tab.space_text)


def _walk_note(tab: "Tab", reply: Reply) -> str:
    """What a flat view's final reply adds to the summary: that it stopped at
    the limit, and how many folders it could not read."""
    if not tab.flat:
        return ""
    words = (reply.message or "").split()
    notes = []
    if tab.duplicates:
        values = dict(word.split("=", 1) for word in words if "=" in word)
        groups, wasted = int(values.get("groups", 0) or 0), int(values.get("wasted", 0) or 0)
        notes.append(f"{groups:,} set{'s' if groups != 1 else ''} of identical files"
                     + (f", {format_size(wasted)} in extra copies" if wasted else ""))
    if "limit" in words:
        notes.append("stopped at the search limit" if tab.search is not None
                     else "stopped at the flat view limit")
    for word in words:
        if word.startswith("skipped="):
            count = int(word.partition("=")[2] or 0)
            if count:
                notes.append(f"{count:,} folder{'s' if count != 1 else ''} could not be read")
    return "".join(f"  ·  {note}" for note in notes)


def _on_a_share(path: str) -> bool:
    """Whether a folder is on a network volume, which is what gets remembered."""
    return paths.volume_key(path) != paths.LOCAL_VOLUME_KEY


def _stale_note(tab: "Tab", now: float | None = None) -> str:
    """The status line over rows that are out of date: how out of date."""
    listed = time.localtime(tab.listed_at)
    today = time.localtime(time.time() if now is None else now)
    same_day = listed[:3] == today[:3]
    when = time.strftime("%H:%M" if same_day else "%Y-%m-%d %H:%M", listed)
    return (f"not answering — showing the listing from {when}. "
            "Retry to reconnect.")


_WINERROR_RE = re.compile(r"(?:winerror|WinError)\s*(\d+)")


def _login_share(resolved: str, reply: Reply) -> str | None:
    r"""The share to log into, when a listing failed for want of an account.

    Only on a share, and only for the reasons a different account fixes: one
    of Windows' logon failures anywhere on it, or "access is denied" at the
    share's own root -- a folder further down that says no is a permission on
    that folder, which is what the elevation offer is for, and no login
    prompt should stand in front of it.
    """
    share = paths.share_root(resolved or "")
    if not share:
        return None
    found = _WINERROR_RE.search(reply.message or "")
    number = int(found.group(1)) if found else None
    if number in paths.LOGON_WINERRORS:
        return share
    if reply.status is Status.DENIED or number == 5:
        if paths.normalize(resolved).rstrip("\\").lower() == share.lower():
            return share
    return None


def _windows_words(reply: Reply) -> str:
    """Windows' sentence out of a worker's `Type: [WinError n] words: 'path'`."""
    message = reply.message or ""
    found = re.search(r"\[WinError \d+\]\s*([^:]+)", message)
    if found:
        return found.group(1).strip().rstrip(".") + "."
    return _explain(reply)


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


def _archive_status(reply: Reply) -> str:
    """0.41: what a listing inside an archive adds to the summary."""
    message = reply.message or ""
    if not message.startswith("archive"):
        return ""
    note = "  ·  archive, read-only"
    found = re.search(r"skipped=(\d+)", message)
    if found and int(found.group(1)):
        count = int(found.group(1))
        note += f"  ·  {count} unusable name{'s' if count != 1 else ''} left out"
    return note

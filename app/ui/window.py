"""The window: two panes, a menu, and the shortcuts that drive them.

Everything the menu does to the look goes through one call. Changing theme,
accent or density re-renders the whole sheet and re-applies it, then hands the
panes the metrics a stylesheet cannot set. There is no partial update path, and
adding one is how a picker ends up half working.
"""

from __future__ import annotations

import os.path

from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QMenu,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import QEvent, Qt, QTimer

from app import __version__
from app.core import commands as core_commands
from app.core import compare as core_compare
from app.core import places as core_places
from app.core.favorites import UNGROUPED
from app.io import elevate, paths
from app.io.protocol import THUMB_SIZES, JobKind, Op
from app.theme import sheet
from app.theme.tokens import (
    ACCENT_LABELS,
    DENSITY_LABELS,
    THEME_LABELS,
)
from app.ui import dialogs, winframe
from app.ui.deck import Deck
from app.ui.hints import HintBar
from app.ui.titlebar import TitleBar
from app.ui.pane import PaneWidget
from app.ui.rail import NavigationRail
from app.ui.transfers import ConflictDialog, QueueDialog, TransferBar, TransferPrompt
from app.ui.viewer import Viewer

TITLE = "File Manager"


def _outcome(job) -> str:
    """One line saying how a job went, in the words of what it did.

    A copy reports what it copied and a delete reports what it removed, and the
    same sentence for both would have a recycle of forty files announcing that
    forty files were copied. `copied` is the count of items the job got through
    whatever the job was; the verb is this function's business.
    """
    where = job.destination or (os.path.dirname(job.sources[0]) if job.sources else "")
    verb = "removed" if job.kind.removes else "copied"
    if job.refused:
        # Its own sentence, and it already names the destination -- putting
        # `where` in front of it would say the folder twice.
        return job.refused
    if job.cancelled:
        summary = f"cancelled after {job.copied:,} item(s)"
    elif job.failed:
        summary = (f"finished with {job.failed:,} failed, "
                   f"{job.copied:,} {verb}, {job.skipped:,} skipped")
    elif job.kind.removes:
        summary = f"{job.copied:,} removed"
    else:
        summary = f"{job.copied:,} copied, {job.skipped:,} skipped"
    return f"{where}: {summary}" if where else summary


def _tool_tip(command) -> str:
    """What a Tools entry says when the pointer rests on it.

    The program and the template, because that is what somebody is checking
    when they wonder why a key did nothing -- and it is the fastest route to
    the editor, which is where they can change it.
    """
    line = command.program
    if command.arguments:
        line = f"{line} {command.arguments}"
    if command.working:
        line = f"{line}    (in {command.working})"
    return line


class MainWindow(QMainWindow):

    def __init__(self, config, left, right, volumes, transfers, updates=None,
                 favorites=None, capacity=None, commands=None, network=None,
                 parent: QWidget | None = None, *, backdrop: str = "solid",
                 frame: str | None = None, ejector=None) -> None:
        super().__init__(parent)
        self._config = config
        #: 0.27: USB eject, or None for a window built without io.
        self._ejector = ejector
        #: "glass" or "solid", decided before the window exists by
        #: `core.backdrop.choose`, because a translucent window has to be
        #: translucent from the moment it is created. Changing it takes a
        #: restart, and the View menu says so.
        self._backdrop = backdrop if backdrop in ("glass", "solid") else "solid"
        #: "custom" draws the 0.26 title bar; "system" is the Windows title bar
        #: and the menu bar, kept as the way back if the custom one misbehaves
        #: on a machine it was not tried on.
        chosen = frame if frame is not None else config.get("window.frame")
        self._frame_kind = "system" if chosen == "system" else "custom"
        self._frame: winframe.NativeFrame | None = None
        self._titlebar: TitleBar | None = None
        #: The external command table, or None for a window built without one
        #: -- a preview render, a test. None means the Tools menu is drawn and
        #: inert rather than a menu bar that is a different shape from the real
        #: one, which is the rule the favourites menu already follows.
        self._commands = commands
        #: The network locations, or None for a window built without them.
        self._network = network
        self._updates = updates
        self._favorites = favorites
        self._capacity = capacity
        self._panes = (left, right)
        self._icons = left.icons
        self._overlays = left.overlays
        self._file_icons = left.file_icons
        self._shell_menu = left.menu
        self._volumes = volumes
        self._transfers = transfers
        self._queue_dialog: QueueDialog | None = None
        self._previews = left.previews
        self._thumbnails = left.thumbnails
        #: The viewer, built the first time F3 is pressed and kept afterwards.
        #: Kept rather than remade because it holds the zoom and the window
        #: geometry, and a viewer that opened at a different size every time
        #: would be one a person had to arrange on every use.
        self._viewer: Viewer | None = None

        metrics = sheet.metrics(config.get("density"))
        self._widgets = (PaneWidget(left, volumes, metrics, favorites),
                         PaneWidget(right, volumes, metrics, favorites))
        # The panes paint two things the sheet cannot express -- the chrome
        # icons and the rows -- so they need the same token set the sheet was
        # rendered from. Handed down rather than fetched, so a pane cannot end
        # up painted from a different render than the one it is styled by.
        tokens = sheet.tokens(config.get("theme"), config.get("accent"),
                              config.get("density"), self._backdrop)
        self._tokens = tokens
        for widget in self._widgets:
            widget.apply_tokens(tokens)

        # One rail for the window, at the left of the same splitter the panes
        # are in, so the width it is dragged to is the width it keeps. It is
        # built only when there is a list and a capacity to draw from -- a
        # preview render or a test gets a window without one rather than a
        # rail that is a different shape from the real one.
        self._rail = None
        if favorites is not None and capacity is not None:
            self._rail = NavigationRail(favorites, volumes, capacity, config,
                                        network)
            self._rail.set_places(core_places.places())
            self._rail.apply_tokens(tokens)
            self._rail.chosen.connect(self._on_rail_chosen)
            self._rail.measureRequested.connect(capacity.measure)
            self._rail.reconnectRequested.connect(self._reconnect)
            self._rail.ejectRequested.connect(self._eject)
            self._rail.rescanRequested.connect(
                lambda: self._volumes.refresh(rescan=True))
            self._rail.addFavoriteRequested.connect(self._add_favorite)
            self._rail.manageFavoritesRequested.connect(self._manage_favorites)
            self._rail.groupRequested.connect(self._set_favorite_group)
            self._rail.addLocationRequested.connect(self._add_location)
            self._rail.forgetLocationRequested.connect(self._forget_location)
            if network is not None:
                self._rail.refreshNetworkRequested.connect(network.refresh)
                network.changed.connect(self._rebuild_rail)
                network.reconnected.connect(self._on_reconnected)
            self._rail.setVisible(bool(config.get("rail.shown")))
            # Measured once the letters are known, and only the local fixed
            # ones -- see `core/capacity.py`. Nothing here touches a server.
            volumes.changed.connect(self._measure_local_drives)

        self._splitter = Deck()
        if self._rail is not None:
            self._splitter.addWidget(self._rail)
        for widget in self._widgets:
            widget.activated.connect(self._on_pane_activated)
            self._splitter.addWidget(widget)
        for pane in self._panes:
            pane.folderChanged.connect(self._on_folder_changed)
            pane.flatChanged.connect(self._sync_flat_action)
            pane.currentChanged.connect(self._sync_flat_action)
            pane.elevationOffered.connect(self._offer_elevation(pane))
            # The rail marks the row the active pane is standing on, which is
            # the one thing a rail can say that a menu cannot. Both panes are
            # watched and the mark follows whichever is active, so the answer
            # changes when the pane does as well as when the folder does.
            pane.pathChanged.connect(self._sync_rail_mark)
        if self._shell_menu is not None:
            self._shell_menu.invoked.connect(self._on_shell_invoked)
            self._shell_menu.problem.connect(
                lambda message: self.statusBar().showMessage(message, 8000))
        for widget in self._widgets:
            widget.transferRequested.connect(self._on_transfer_requested)
            widget.clipboardRequested.connect(self._on_clipboard_requested)
            widget.addFavoriteRequested.connect(self._add_favorite)
            widget.manageFavoritesRequested.connect(self._manage_favorites)
            widget.viewRequested.connect(self._on_view_requested)
            widget.commandRequested.connect(self._on_command)
        if self._commands is not None:
            self._commands.changed.connect(self._fill_tools)
            self._commands.changed.connect(self._share_command_keys)
            self._commands.ran.connect(self._on_command_ran)
            self._commands.problem.connect(
                lambda _id, why: self.statusBar().showMessage(why, 8000))
        if self._ejector is not None:
            self._ejector.finished.connect(self._on_ejected)
        transfers.conflict.connect(self._on_conflict)
        transfers.finished.connect(self._on_transfer_finished)
        if updates is not None:
            updates.available.connect(self._on_update_available)
            updates.uptodate.connect(self._on_up_to_date)
            updates.problem.connect(self._on_update_problem)
            updates.progress.connect(self._on_update_progress)
            updates.ready.connect(self._on_update_ready)
        self._splitter.setChildrenCollapsible(False)
        # The panes take the slack; the rail keeps whatever width it was
        # dragged to. Without this a window resize grows all three, and a rail
        # that grows when the window does is a rail that ends up half the
        # screen after a maximise.
        if self._rail is not None:
            self._splitter.setStretchFactor(0, 0)
            self._splitter.setStretchFactor(1, 1)
            self._splitter.setStretchFactor(2, 1)
        if self._frame_kind == "custom":
            self._titlebar = TitleBar()
            self._titlebar.menuRequested.connect(self._show_app_menu)
            self._titlebar.goRequested.connect(lambda: self._current_widget().focus_path())
            self._titlebar.minimizeRequested.connect(self.showMinimized)
            self._titlebar.maximizeRequested.connect(self.toggle_maximized)
            self._titlebar.closeRequested.connect(self.close)
            self._titlebar.apply_tokens(tokens)
            root = QWidget()
            root.setProperty("role", "root")
            stack = QVBoxLayout(root)
            stack.setContentsMargins(0, 0, 0, 0)
            stack.setSpacing(0)
            stack.addWidget(self._titlebar)
            stack.addWidget(self._splitter, 1)
            self.setCentralWidget(root)
            self.setWindowFlags(self.windowFlags() | Qt.FramelessWindowHint)
            if self._backdrop == "glass":
                self.setAttribute(Qt.WA_TranslucentBackground, True)
            dark = sum(sheet.qss.unhex(tokens["bg_0"])) < 382
            self._frame = winframe.NativeFrame(self, glass=self._backdrop == "glass",
                                               dark=dark)
        else:
            self.setCentralWidget(self._splitter)
        self._splitter.set_glow_colour(tokens["accent"])

        self.setWindowTitle(TITLE)
        self.setStatusBar(QStatusBar())
        self._hints = HintBar()
        self._hints.apply_tokens(tokens)
        self.statusBar().addWidget(self._hints, 1)
        self._transfer_bar = TransferBar(transfers)
        self._transfer_bar.opened.connect(self._show_queue)
        self.statusBar().addPermanentWidget(self._transfer_bar)
        self.resize(int(config.get("window.width")), int(config.get("window.height")))

        self._build_menus()
        if self._frame_kind == "custom":
            # The menus are reached from the mark in the title bar now. A hidden
            # menu bar also stops honouring the shortcuts of the actions in its
            # menus -- Qt checks the bar is visible before it matches a key --
            # so every action that carries a key is put on the window itself.
            self.menuBar().hide()
        self._adopt_shortcuts()
        # Which pane is active follows the *focus*, not this widget's own.
        # A `QFrame` never takes focus itself -- its listing or its path bar
        # does -- so `focusInEvent` on the pane fires for the Tab key, which
        # moves focus explicitly, and never for a mouse click. That left the
        # active pane stuck wherever Tab last put it: Ctrl+D saved the other
        # pane's folder, F5 copied the wrong way, and the accent border said
        # so the whole time.
        QApplication.instance().focusChanged.connect(self._on_focus_changed)
        if self._favorites is not None:
            # Rebuilt rather than patched. The list is a dozen entries and
            # the alternative is a diff against a menu.
            self._favorites.changed.connect(self._fill_favorites)
            self._fill_favorites()
        # Before the first key can be pressed. A pane with no map answers its
        # own keys and nothing else, which is what a window built without a
        # table gets and is the right answer for it.
        self._share_command_keys()
        self._active = 0
        self._set_active(0)
        if self._rail is not None:
            # After `resize`, so the sizes are being shared out of a window
            # that is already the width it will be.
            self._restore_rail_width()
        # 0.26: the hints are a widget in the status bar rather than a timed
        # message. A message still covers them while it is showing, which is
        # the property the old timeout was buying: a transfer's report and the
        # hints never share the line.

    # ------------------------------------------------------------------- menus

    def _build_menus(self) -> None:
        files = self.menuBar().addMenu("&File")
        # These four carry their keys in the label rather than as shortcuts.
        # A window shortcut on Delete would take the key from the path bar and
        # the filter box, so a backspace over a typo could start deleting
        # files; the pane handles them where focus makes that safe.
        self._hint(files, "Copy\tF5", lambda: self._on_transfer_requested("copy"))
        self._hint(files, "Move\tF6", lambda: self._on_transfer_requested("move"))
        self._hint(files, "New folder\tF7", lambda: self._current_widget().new_folder())
        self._hint(files, "Rename\tF2", lambda: self._current_widget().rename_current())
        self._hint(files, "Delete\tDel", lambda: self._current_widget().delete_selection())
        self._hint(files, "Delete permanently\tShift+Del",
                   lambda: self._current_widget().delete_selection(permanent=True))
        files.addSeparator()
        # Hints again, all three. Ctrl+C, Ctrl+X and Ctrl+V as window
        # shortcuts would take copy, cut and paste away from the path bar and
        # the filter box, which is the same failure Delete is kept off the
        # window for -- and there the cost is only a lost keystroke, while
        # here it is a paste of files into a folder because the user meant to
        # paste text into a field.
        self._hint(files, "Copy\tCtrl+C",
                   lambda: self._on_clipboard_requested("copy"))
        self._hint(files, "Cut\tCtrl+X",
                   lambda: self._on_clipboard_requested("cut"))
        self._hint(files, "Paste\tCtrl+V",
                   lambda: self._on_clipboard_requested("paste"))
        files.addSeparator()
        self._action(files, "Queue", "Ctrl+J", self._show_queue)
        files.addSeparator()
        self._action(files, "Quit", "Ctrl+Q", self.close)

        tabs = self.menuBar().addMenu("&Tabs")
        self._action(tabs, "New tab", "Ctrl+T", lambda: self._current_pane().open_tab())
        self._action(tabs, "Duplicate tab", "Ctrl+Shift+T",
                     lambda: self._current_pane().duplicate_tab())
        # A hint, not a shortcut: the key belongs to the pane, which knows
        # the path bar has focus and that a new tab is not what Ctrl+Enter
        # means there.
        self._hint(tabs, "Open folder in new tab\tCtrl+Enter",
                   lambda: self._current_widget().open_in_new_tab(background=False))
        tabs.addSeparator()
        self._action(tabs, "Close tab", "Ctrl+W",
                     lambda: self._current_pane().close_tab(self._current_pane().index))
        self._action(tabs, "Close other tabs", "Ctrl+Shift+W",
                     lambda: self._current_pane().close_others(
                         self._current_pane().index))
        tabs.addSeparator()
        self._action(tabs, "Next tab", "Ctrl+Tab",
                     lambda: self._current_pane().cycle_tab(1))
        self._action(tabs, "Previous tab", "Ctrl+Shift+Tab",
                     lambda: self._current_pane().cycle_tab(-1))
        lock = self._action(tabs, "Lock this tab", "Ctrl+Shift+L",
                            lambda: self._current_pane().toggle_lock())
        lock.setToolTip("A locked tab keeps its folder. Opening one from it "
                        "opens a new tab instead.")
        tabs.addSeparator()
        # Alt rather than Ctrl for the numbers: Ctrl+1 through Ctrl+9 are what
        # a future column or view mode would want, and Alt+n is what a browser
        # already trained these fingers on.
        for position in range(1, 10):
            entry = QAction(f"Tab {position}", self)
            entry.setShortcut(QKeySequence(f"Alt+{position}"))
            entry.setShortcutContext(Qt.WindowShortcut)
            entry.triggered.connect(
                lambda _checked=False, n=position - 1:
                self._current_pane().select_tab(n))
            tabs.addAction(entry)
            # Only the first three are worth a line in the menu; the rest are
            # keys that work without a menu entry advertising each one.
            entry.setVisible(position <= 3)

        # The Norton keypad, which is what Double Commander uses and what
        # these fingers already know, with a Ctrl equivalent for a keyboard
        # that has no numeric pad.
        #
        # Hints rather than shortcuts, every one of them. Ctrl+A as a window
        # shortcut would take select-all away from the path bar and the filter
        # box, which is the same failure the function keys are kept off the
        # window for; and the keypad keys have to be told apart from the same
        # characters typed into a quick search, which only the widget holding
        # the search can do. The pane handles all of them where focus makes
        # that safe.
        select = self.menuBar().addMenu("Se&lect")
        widget = self._current_widget
        self._hint(select, "Select group\tNum +  /  Ctrl+=",
                   lambda: widget().ask_and_select(on=True))
        self._hint(select, "Unselect group\tNum -  /  Ctrl+-",
                   lambda: widget().ask_and_select(on=False))
        self._hint(select, "Invert selection\tNum *  /  Ctrl+8",
                   lambda: widget().invert_selection())
        select.addSeparator()
        self._hint(select, "Select all\tCtrl+A", lambda: widget().select_all())
        self._hint(select, "Unselect all\tCtrl+Shift+A",
                   lambda: widget().select_all(on=False))
        select.addSeparator()
        same = self._hint(select, "Select all of this kind\tAlt+Num +",
                          lambda: widget().select_same_extension(on=True))
        same.setToolTip("Every file with the same extension as the one under "
                        "the cursor.")
        self._hint(select, "Unselect all of this kind\tAlt+Num -",
                   lambda: widget().select_same_extension(on=False))
        select.setToolTipsVisible(True)

        self._favorites_menu = self.menuBar().addMenu("F&avorites")
        self._favorites_menu.setToolTipsVisible(True)
        self._add_favorite_action = QAction("Add this folder", self)
        self._add_favorite_action.setShortcut(QKeySequence("Ctrl+D"))
        self._add_favorite_action.setShortcutContext(Qt.WindowShortcut)
        self._add_favorite_action.setToolTip("Save the folder this pane is showing.")
        self._add_favorite_action.triggered.connect(self._add_favorite)
        self._manage_favorites_action = QAction("Manage favorites", self)
        self._manage_favorites_action.triggered.connect(self._manage_favorites)
        self._show_bar_action = QAction("Show the favorites bar", self,
                                       checkable=True)
        self._show_bar_action.setChecked(bool(self._config.get("favorites.bar")))
        self._show_bar_action.triggered.connect(self._set_favorites_bar)
        # Ctrl+1 to Ctrl+9, made here and not in the rebuilt part of the menu:
        # a shortcut action remade on every change leaves the old ones alive
        # on the window and Qt then honours none of them. Alt+n is the tabs,
        # so the numbers with Ctrl are the places.
        self._favorite_keys = []
        for position in range(1, 10):
            entry = QAction(f"Favorite {position}", self)
            entry.setShortcut(QKeySequence(f"Ctrl+{position}"))
            entry.setShortcutContext(Qt.WindowShortcut)
            entry.triggered.connect(
                lambda _checked=False, n=position - 1:
                self._current_widget().go_to_favorite(n))
            entry.setVisible(False)   # a key, not an entry to read
            self.addAction(entry)
            self._favorite_keys.append(entry)
        self._favorites_menu.addAction(self._add_favorite_action)
        self._favorites_menu.addAction(self._manage_favorites_action)
        self._favorites_menu.addAction(self._show_bar_action)
        # A window built without a list -- a preview render, a test -- gets the
        # menu drawn and inert rather than a menu bar that is a different shape
        # from the real one.
        self._favorites_menu.setEnabled(self._favorites is not None)

        go = self.menuBar().addMenu("&Go")
        self._action(go, "Up", "Backspace", lambda: self._current_pane().go_up())
        self._action(go, "Back", "Alt+Left", lambda: self._current_pane().go_back())
        self._action(go, "Forward", "Alt+Right", lambda: self._current_pane().go_forward())
        self._action(go, "Refresh", "Ctrl+R", lambda: self._current_pane().refresh())
        self._action(go, "Reconnect", "Ctrl+Shift+R", self._reconnect_pane)
        go.addSeparator()
        self._action(go, "Edit path", "Ctrl+L", lambda: self._current_widget().focus_path())
        self._action(go, "Other pane", "Tab", self._switch_pane)
        self._action(go, "Swap panes", "Ctrl+U", self._swap_panes)
        self._action(go, "Other pane here", "Ctrl+Shift+M", self._mirror_pane)
        go.addSeparator()
        self._action(go, "Rescan drives", "Ctrl+Shift+D",
                     lambda: self._volumes.refresh(rescan=True))
        self._action(go, "Copy path", "Ctrl+Shift+C", self._copy_path)

        view = self.menuBar().addMenu("&View")
        view.setToolTipsVisible(True)
        # A hint rather than a shortcut, like every other function key: a window
        # shortcut on F3 would take the key from the path bar and the filter box.
        # Those two do not use F3 -- but the argument that matters is that every
        # function key in this application is the pane's, so which pane the
        # viewer opens on is decided by the same rule as which pane F5 copies
        # from, rather than by a second mechanism that agrees most of the time.
        viewing = self._hint(view, "View\tF3",
                             lambda: self._current_widget().view_current())
        viewing.setToolTip("Open the file under the cursor: a picture with zoom, "
                           "text with its encoding, or a hex dump. The arrow "
                           "keys step through the folder.")
        pane_preview = QAction("Preview pane", self, checkable=True)
        pane_preview.setShortcut(QKeySequence("Ctrl+P"))
        pane_preview.setShortcutContext(Qt.WindowShortcut)
        pane_preview.setChecked(bool(self._config.get("preview.pane")))
        pane_preview.setToolTip("A panel beside the listing showing whatever the "
                                "cursor is on. Reads the file, so it follows the "
                                "cursor rather than every row that goes past.")
        pane_preview.triggered.connect(self._set_preview_pane)
        view.addAction(pane_preview)
        grid = QAction("Thumbnail grid", self, checkable=True)
        grid.setShortcut(QKeySequence("Ctrl+Shift+P"))
        grid.setShortcutContext(Qt.WindowShortcut)
        grid.setChecked(self._config.get("left.view") == "grid")
        grid.setToolTip("The same rows as cells with pictures in them. The sort, "
                        "the filter and the selection are the listing's own.")
        grid.triggered.connect(self._set_grid_view)
        self._grid_action = grid
        view.addAction(grid)
        cells = view.addMenu("Cell size")
        cells.setEnabled(self._thumbnails is not None)
        size_group = QActionGroup(self)
        size_group.setExclusive(True)
        current_cell = int(self._config.get("preview.thumb_size"))
        for size in THUMB_SIZES:
            entry = QAction(f"{size} px", self, checkable=True)
            entry.setChecked(size == current_cell)
            entry.triggered.connect(
                lambda _checked=False, n=size: self._set_cell_size(n))
            size_group.addAction(entry)
            cells.addAction(entry)
        view.addSeparator()
        # Both are the pane's own keys, shown rather than claimed. Quick search
        # has no key to claim at all -- it starts when somebody types a letter
        # into the listing -- so the entry says so and does nothing.
        self._hint(view, "Folder size\tSpace",
                   lambda: self._current_widget().measure_selection())
        self._action(view, "Size of every folder here", "Ctrl+Shift+Space",
                     lambda: self._current_widget().measure_all())
        view.addSeparator()
        typed = self._hint(view, "Quick search\tType a name", lambda: None)
        typed.setEnabled(False)
        typed.setToolTip("Typing in the listing jumps to a name. Ctrl+G finds "
                         "the next match and Ctrl+Shift+G the previous, whether "
                         "or not you have just typed; Enter also steps while "
                         "the search is still live. Esc forgets the name, and "
                         "so does leaving the folder. F3 was this until 0.16 "
                         "and is the viewer now.")
        self._hint(view, "Find next\tCtrl+G", lambda: None).setEnabled(False)
        view.addSeparator()
        self._action(view, "Filter", "Ctrl+F", lambda: self._current_widget().focus_filter())
        self._action(view, "Clear filter", "Ctrl+Shift+F",
                     lambda: self._current_widget().clear_filter())
        view.addSeparator()
        flat = QAction("Flat view", self, checkable=True)
        flat.setShortcut(QKeySequence("Ctrl+B"))
        flat.setShortcutContext(Qt.WindowShortcut)
        flat.setToolTip("Every file under this folder in one list, in this tab. "
                        "Going to another folder, or Ctrl+B again, ends it. Esc "
                        "stops a walk that is still going.")
        flat.triggered.connect(lambda _checked=False: self._current_pane().toggle_flat())
        self._flat_action = flat
        view.addAction(flat)
        layouts = view.addMenu("Flat view layout")
        layout_group = QActionGroup(self)
        layout_group.setExclusive(True)
        self._flat_layout_actions = {}
        for key, label, tip in (
                ("column", "Location column",
                 "A column saying which subfolder each file is in."),
                ("groups", "Grouped by folder",
                 "A heading for each subfolder, with its files under it.")):
            entry = QAction(label, self, checkable=True)
            entry.setToolTip(tip)
            entry.setChecked(self._config.get("flat.layout") == key)
            entry.triggered.connect(
                lambda _checked=False, k=key: self._set_flat_layout(k))
            layout_group.addAction(entry)
            layouts.addAction(entry)
            self._flat_layout_actions[key] = entry
        layouts.setToolTipsVisible(True)
        view.addSeparator()
        rail = QAction("Navigation rail", self, checkable=True)
        # Ctrl+Shift+B since 0.25, when Ctrl+B became flat view.
        rail.setShortcut(QKeySequence("Ctrl+Shift+B"))
        rail.setShortcutContext(Qt.WindowShortcut)
        rail.setChecked(bool(self._config.get("rail.shown")))
        rail.setEnabled(self._rail is not None)
        rail.setToolTip("Places, drives and the saved folders, down the left.")
        rail.triggered.connect(self._toggle_rail)
        view.addAction(rail)
        view.addSeparator()
        self._axis_menu(view, "Theme", THEME_LABELS, "theme")
        self._axis_menu(view, "Accent", ACCENT_LABELS, "accent")
        self._axis_menu(view, "Density", DENSITY_LABELS, "density")
        self._window_menu(view)
        view.addSeparator()
        unc = QAction("Show UNC paths", self, checkable=True)
        unc.setChecked(bool(self._config.get("left.show_unc")))
        unc.triggered.connect(self._set_show_unc)
        view.addAction(unc)
        badges = QAction("Type badges instead of icons", self, checkable=True)
        badges.setChecked(self._config.get("icons.style") == "badges")
        badges.setToolTip("A tag with the extension, coloured by kind of file: "
                          "Logix, HMI, drawings, PDF. Off shows Windows' icons.")
        badges.triggered.connect(self._set_badges)
        view.addAction(badges)
        header = QAction("Folder header", self, checkable=True)
        header.setChecked(bool(self._config.get("pane.header")))
        header.setToolTip("The folder's name above the listing, and a bar of "
                          "what it holds by kind of file.")
        header.triggered.connect(self._set_header)
        view.addAction(header)
        shell_icons = QAction("Shell icons", self, checkable=True)
        shell_icons.setChecked(bool(self._config.get("icons.shell")))
        shell_icons.setEnabled(self._icons is not None)
        shell_icons.triggered.connect(self._set_shell_icons)
        view.addAction(shell_icons)
        overlays = QAction("Icon overlays", self, checkable=True)
        overlays.setChecked(bool(self._config.get("icons.overlays")))
        overlays.setEnabled(self._overlays is not None)
        overlays.setToolTip("Shared folders, OneDrive and source control badges. "
                            "The one icon lookup that asks about a file rather "
                            "than about its type.")
        overlays.triggered.connect(self._set_overlays)
        view.addAction(overlays)
        file_icons = QAction("Icons from the file itself", self, checkable=True)
        file_icons.setChecked(bool(self._config.get("icons.per_file")))
        file_icons.setEnabled(self._file_icons is not None)
        file_icons.setToolTip("Programs, shortcuts and .ico files draw their own "
                              "icon rather than the one for their type. Reads "
                              "the file, for the rows on screen only.")
        file_icons.triggered.connect(self._set_file_icons)
        view.addAction(file_icons)
        thumbs = QAction("Pictures in the grid", self, checkable=True)
        thumbs.setChecked(bool(self._config.get("preview.thumbnails")))
        thumbs.setEnabled(self._thumbnails is not None)
        thumbs.setToolTip("Off means the grid draws the icon for each kind, "
                          "which is still a grid and costs no reads.")
        thumbs.triggered.connect(self._set_thumbnails)
        view.addAction(thumbs)
        shell_preview = QAction("Windows thumbnail handlers", self, checkable=True)
        shell_preview.setChecked(bool(self._config.get("preview.shell")))
        shell_preview.setToolTip("Ask Windows for a picture of the kinds this "
                                 "application cannot decode itself: video "
                                 "frames, Office documents, .heic, .psd. This is "
                                 "the one part of the previewer that runs "
                                 "somebody else's code.")
        shell_preview.triggered.connect(self._set_shell_previews)
        view.addAction(shell_preview)
        shell_commands = QAction("Explorer context menu", self, checkable=True)
        shell_commands.setChecked(bool(self._config.get("menu.shell")))
        shell_commands.setEnabled(self._shell_menu is not None)
        shell_commands.triggered.connect(
            lambda checked: self._config.set("menu.shell", bool(checked)))
        view.addAction(shell_commands)

        self._tools_menu = self.menuBar().addMenu("&Tools")
        self._tools_menu.setToolTipsVisible(True)
        # Made once and put back by `_fill_tools`, for the favourites menu's
        # reason: an action remade on every rebuild stays alive on the window,
        # and after two edits Qt would have three actions claiming Ctrl+Shift+F2
        # and honour none of them.
        self._compare_action = QAction("Compare the panes", self)
        self._compare_action.setShortcut(QKeySequence("Ctrl+Shift+F2"))
        self._compare_action.setShortcutContext(Qt.WindowShortcut)
        self._compare_action.setToolTip(
            "Mark what each side has that the other does not: newer, missing, "
            "or the same age and a different size. Reads no files, so it costs "
            "nothing on a share.")
        self._compare_action.triggered.connect(self._compare_panes)
        self._edit_commands_action = QAction("Commands", self)
        self._edit_commands_action.setToolTip(
            "The programs on the Tools menu and the keys that reach them.")
        self._edit_commands_action.triggered.connect(self._edit_commands)
        self._edit_commands_action.setEnabled(self._commands is not None)
        self._fill_tools()

        helping = self.menuBar().addMenu("&Help")
        version = QAction(f"Version {__version__}", self)
        version.setEnabled(False)
        helping.addAction(version)
        helping.addSeparator()
        check = QAction("Check for updates", self)
        check.triggered.connect(self._check_for_updates)
        check.setEnabled(self._updates is not None)
        helping.addAction(check)
        automatic = QAction("Check on launch", self, checkable=True)
        automatic.setChecked(bool(self._config.get("updates.check_on_launch")))
        automatic.triggered.connect(
            lambda checked: self._config.set("updates.check_on_launch", bool(checked)))
        automatic.setEnabled(self._updates is not None)
        helping.addAction(automatic)

    # ------------------------------------------------------------------- rail

    def _on_rail_chosen(self, path: str, new_tab: bool) -> None:
        """A place from the rail goes into the pane that has the keyboard.

        Which is the whole reason the rail takes no focus anywhere: clicking
        in it must not change the answer to "which pane", or every click would
        go wherever the previous one left things. `Pane.navigate` still decides
        what going there means -- a locked tab opens a new one.
        """
        pane = self._current_pane()
        if new_tab:
            pane.open_tab(path)
        else:
            pane.navigate(path)
        self._current_widget().focus_listing()

    def _set_favorite_group(self, index: int, group: str) -> None:
        """Move a favourite under a heading. An empty name asks for a new one.

        `UNGROUPED` is the label the rail draws the ungrouped ones under, so
        being sent there means having no group rather than having one called
        that -- otherwise a group would appear that could then be renamed.
        """
        if self._favorites is None:
            return
        if group == UNGROUPED:
            self._favorites.set_group(index, "")
            return
        if not group:
            group = dialogs.ask_name(
                self, title="New group", label="Call the group",
                initial="", ok_text="Create") or ""
            if not group:
                return
        self._favorites.set_group(index, group)

    # ---------------------------------------------------------------- network

    def _rebuild_rail(self) -> None:
        if self._rail is not None:
            self._rail.rebuild()

    def _add_location(self) -> None:
        """Type a UNC path and keep it in the rail.

        The one way in for a location Windows will not enumerate: a share
        nobody has connected to yet is in no table, so it has to be named once.
        Nothing here checks whether it exists -- that check is a blocking call
        in a dialog, and the answer arrives on its own the moment somebody
        clicks the row.
        """
        if self._network is None:
            return
        typed = dialogs.ask_name(
            self, title="Add a network location",
            label="The path, as \\\\server\\share",
            initial="\\\\", ok_text="Add")
        if not typed:
            return
        added = self._network.add(typed)
        if not added:
            self.statusBar().showMessage(
                "A network location is a path like \\\\server\\share", 8000)
            return
        self.statusBar().showMessage(f"added {added}", 4000)

    def _forget_location(self, path: str) -> None:
        if self._network is not None:
            self._network.remove(path)

    def _reconnect_pane(self) -> None:
        r"""Ctrl+Shift+R, and what it means depends on where the pane is.

        What this used to do was restart the volume's worker and ask for the
        listing again. That is the right answer for a worker that wedged and
        the wrong one for a session that has gone: the worker was never the
        problem, and the second listing fails exactly as the first one did.
        A share is reattached first now, and the listing follows on its own --
        `_on_reconnected` retries every pane standing in it, which is also what
        makes the rail's Reconnect and this key the same operation rather than
        two.

        The *share* is what gets reconnected, not the folder: Windows attaches
        to `\\server\share` and the folder inside it is only where the pane
        happens to be standing. And the pane's resolved path is what is asked,
        so this works identically on `S:\Jobs` and on the UNC it is mapped to.

        A local path has nothing to reconnect to and keeps the old behaviour,
        which is still the right one for a disk that stopped answering.
        """
        pane = self._current_pane()
        share = paths.share_root(pane.resolved())
        if share and self._network is not None:
            self._reconnect(share)
            return
        pane.retry()

    def _eject(self, letter: str) -> None:
        if self._ejector is None:
            self.statusBar().showMessage("ejecting is not available here", 6000)
            return
        why = self._ejector.eject(letter, self._panes, self._transfers, self._volumes)
        if why:
            self.statusBar().showMessage(why, 10000)
        else:
            self.statusBar().showMessage(f"ejecting {letter.rstrip(chr(92))}", 0)

    def _on_ejected(self, message: str, ok: bool) -> None:
        self.statusBar().showMessage(message, 8000 if ok else 15000)
        if ok:
            self._volumes.refresh(rescan=True)

    def _reconnect(self, path: str) -> None:
        if self._network is None:
            return
        self.statusBar().showMessage(f"reconnecting to {path}", 0)
        self._network.reconnect(path)

    def _on_reconnected(self, path: str, why: str) -> None:
        """What a reconnect came back with.

        A failure says why rather than only that it failed: "the network name
        cannot be found" and "access is denied" are different problems with
        different answers, and the second one is the only case where this
        application genuinely cannot help.
        """
        if why:
            self.statusBar().showMessage(f"{path}: {why}", 10000)
            return
        self.statusBar().showMessage(f"{path} is connected", 6000)
        # The pane on it, if there is one, can stop saying it is not there.
        # Against the *resolved* path: a pane showing `S:\Jobs` is standing in
        # the share that was just reconnected, and comparing what it displays
        # would leave exactly that pane sitting on its error.
        for pane in self._panes:
            if pane.resolved().lower().startswith(path.lower()):
                pane.retry()

    def _sync_rail_mark(self, *_ignored) -> None:
        """Put the rail's mark on the folder the active pane is showing."""
        if self._rail is not None:
            self._rail.set_current(self._current_pane().display())

    def _measure_local_drives(self) -> None:
        if self._capacity is not None:
            self._capacity.measure_local(self._volumes.drives)

    def _toggle_rail(self, shown: bool) -> None:
        """Ctrl+B. Hidden rather than collapsed to nothing: a splitter section
        of zero width is one the handle can be dragged back out of by accident,
        and this is a setting rather than a gesture."""
        if self._rail is None:
            return
        if not shown:
            self._remember_rail_width()
        self._config.set("rail.shown", bool(shown))
        self._rail.setVisible(bool(shown))
        if shown:
            self._restore_rail_width()

    def _restore_rail_width(self) -> None:
        """Give the rail the width it was left at and the panes the rest."""
        if self._rail is None or not self._rail.isVisible():
            return
        width = max(96, int(self._config.get("rail.width")))
        rest = max(200, self._splitter.width() - width)
        self._splitter.setSizes([width, rest // 2, rest - rest // 2])

    def _remember_rail_width(self) -> None:
        if self._rail is None or not self._rail.isVisible():
            return
        sizes = self._splitter.sizes()
        if sizes and sizes[0] > 0:
            self._config.set("rail.width", int(sizes[0]))

    # -------------------------------------------------------------- favorites

    def _fill_favorites(self) -> None:
        """The saved locations, with the two commands that maintain them.

        The commands go at the top rather than the bottom so they stay in one
        place as the list grows; a menu whose first entry moves every time
        something is added is a menu that has to be read before it is used.
        """
        menu = self._favorites_menu
        menu.clear()
        # The two commands are made once, in `_build_menus`, and put back
        # here. Made afresh on every rebuild they would leave the previous
        # copies alive on the window -- they are parented to it, so
        # `QMenu.clear` does not delete them -- and after a few favourites
        # Qt would have several actions claiming Ctrl+D and honour none of
        # them. Everything below the separator is parented to the menu
        # instead, so clearing it does delete them.
        menu.addAction(self._add_favorite_action)
        menu.addAction(self._manage_favorites_action)
        menu.addAction(self._show_bar_action)
        menu.addSeparator()
        entries = self._favorites.entries
        if not entries:
            empty = QAction("No favorites yet", menu)
            empty.setEnabled(False)
            menu.addAction(empty)
            return
        for position, entry in enumerate(entries):
            action = QAction(entry.name, menu)
            action.setToolTip(entry.path)
            if position < 9:
                # Shown, not claimed here: the key belongs to the action made
                # once in `_build_menus`, and this entry only says what it is.
                action.setText(f"{entry.name}\tCtrl+{position + 1}")
            action.triggered.connect(
                lambda _checked=False, path=entry.path: self._go_to_favorite(path))
            menu.addAction(action)

    # ------------------------------------------------------------------ tools

    def _fill_tools(self) -> None:
        """The command table as a menu, with the two fixed entries above it.

        The commands carry their keys as *hints*. Every one of them is answered
        by the pane -- `PaneWidget.keyPressEvent` matches the keystroke against
        the same table -- because a window shortcut on F4 would take the key
        from the path bar and the filter box, which is the rule every function
        key in this application already follows.
        """
        menu = self._tools_menu
        menu.clear()
        menu.addAction(self._compare_action)
        menu.addSeparator()
        if self._commands is None:
            empty = QAction("No commands", menu)
            empty.setEnabled(False)
            menu.addAction(empty)
        else:
            shown = self._commands.visible()
            if not shown:
                empty = QAction("No commands", menu)
                empty.setEnabled(False)
                menu.addAction(empty)
            for command in shown:
                label = (f"{command.name}\t{command.shortcut}"
                         if command.shortcut else command.name)
                action = QAction(label, menu)
                action.setToolTip(_tool_tip(command))
                action.triggered.connect(
                    lambda _checked=False, i=command.id: self._on_command(i))
                menu.addAction(action)
        menu.addSeparator()
        menu.addAction(self._edit_commands_action)
        self._adopt_shortcuts()

    def _share_command_keys(self) -> None:
        """Hand both panes the key map, so a keystroke finds its command.

        Both, not the active one: a key pressed in either pane means the same
        thing, and it is the pane it was pressed in that decides what `%P` is.
        """
        keys = self._commands.keys() if self._commands is not None else {}
        for widget in self._widgets:
            widget.set_command_keys(keys)

    def _command_context(self):
        """What the panes hold, as the command table's `Context`.

        Read at the moment the command is asked for rather than kept up to
        date, because there is nothing to keep it up to date for: it is used
        once and thrown away, and anything cached here would be a second
        answer to "which pane is active" that agrees most of the time.
        """
        pane = self._current_pane()
        widget = self._current_widget()
        other = self._panes[1 - self._active]
        entry = pane.current.model.entry(widget.current_row())
        return core_commands.Context(
            path=pane.current.path,
            other_path=other.current.path,
            name=entry.name if entry is not None else "",
            names=tuple(widget.selected_names()),
        )

    def _on_command(self, identity: str) -> None:
        if self._commands is None:
            return
        self._commands.run(identity, self._command_context())

    def _on_command_ran(self, identity: str, program: str) -> None:
        command = core_commands.find(self._commands.commands, identity)
        name = command.name if command is not None else identity
        self.statusBar().showMessage(f"started {name}", 4000)

    def _edit_commands(self) -> None:
        if self._commands is None:
            return
        answer = dialogs.edit_commands(self, self._commands.commands)
        if answer is not None:
            self._commands.replace(answer)

    def _compare_panes(self) -> None:
        """Mark, in each pane, what that side has and the other does not.

        Left against right rather than active against inactive: both sides are
        marked, so which one has the keyboard changes nothing about the answer.

        Nothing is read. The rows are already in memory with their sizes and
        times, which is what makes this a keystroke rather than a job -- see
        `core/compare.py`.
        """
        models = [pane.current.model for pane in self._panes]
        result = core_compare.compare(
            models[0].entries(), models[1].entries(),
            tolerance=float(self._config.get("compare.tolerance")))
        for widget, verdicts in zip(self._widgets,
                                    (result.left, result.right)):
            widget.select_all(on=False)
            widget.select_names(core_compare.marks(verdicts))
        self.statusBar().showMessage(core_compare.summary(result), 12000)

    def _go_to_favorite(self, path: str) -> None:
        """Into the pane that has focus, and into a new tab if it is locked --
        which `Pane.navigate` decides, not this."""
        self._current_pane().navigate(path)
        self._current_widget().focus_listing()

    def _add_favorite(self) -> None:
        """Save the folder on screen, under a name the user confirms.

        A name is asked for rather than taken from the folder because the
        whole point of the list is getting somewhere quickly, and four folders
        called `2026` in four job trees do not do that.
        """
        pane = self._current_pane()
        path = pane.current.path
        known = self._favorites.index_of(path)
        name = dialogs.ask_name(
            self, title="Add favorite",
            label=("Rename this favorite" if known >= 0
                   else f"Save {pane.display(path)} as"),
            initial=(self._favorites.entries[known].name if known >= 0
                     else self._favorites.suggested_name(path)),
            ok_text="Save",
        )
        if not name:
            return
        if not self._favorites.add(name, path):
            self.statusBar().showMessage(
                "the favorites list is full; remove one first", 6000)
            return
        self.statusBar().showMessage(f"saved {name}", 4000)

    def _manage_favorites(self) -> None:
        dialogs.edit_favorites(self, self._favorites)

    def _set_favorites_bar(self, checked: bool) -> None:
        self._config.set("favorites.bar", bool(checked))
        for widget in self._widgets:
            widget.show_favorites_bar(bool(checked))

    def _set_badges(self, checked: bool) -> None:
        self._config.set("icons.style", "badges" if checked else "icons")
        for widget in self._widgets:
            widget.set_badges(bool(checked))

    def _set_header(self, checked: bool) -> None:
        self._config.set("pane.header", bool(checked))
        for widget in self._widgets:
            widget.set_header_shown(bool(checked))

    def _set_shell_icons(self, checked: bool) -> None:
        """Turn the pictures off, or back on, without a restart.

        Without a restart because of when it gets reached for: a shell
        extension misbehaving is happening now, and a setting that only takes
        effect next launch is no help in that moment.
        """
        self._config.set("icons.shell", bool(checked))
        if self._icons is not None:
            self._icons.reload()

    def _set_overlays(self, checked: bool) -> None:
        """Turn the badges off, or back on, without a restart.

        Same reasoning as the icons above, and rather more urgent: this is the
        one thing in the listing that asks the shell about a file by name, so
        it is the switch to reach for when a folder full of somebody's
        source control working copy starts feeling slow.
        """
        self._config.set("icons.overlays", bool(checked))
        if self._overlays is not None:
            self._overlays.reload()

    def _set_file_icons(self, checked: bool) -> None:
        """Turn the per-file pictures off, or back on, without a restart.

        The second switch of the three, and the one to reach for in a folder
        of programs on a share that has gone slow: this is the request that
        opens the file, and off means every row draws as its kind again.
        """
        self._config.set("icons.per_file", bool(checked))
        if self._file_icons is not None:
            self._file_icons.reload()

    # --------------------------------------------------------------- previews

    def _on_view_requested(self, folder: str, names: list, at: int) -> None:
        """F3. Build the viewer if there is not one, and show it the file.

        The window owns it rather than the pane for the reason the queue panel
        and every dialog is the window's: it is a top-level window, both panes
        open the same one, and a viewer per pane would mean two of them on screen
        arguing about which folder is being looked at.
        """
        if self._previews is None:
            self.statusBar().showMessage(
                "the previewer is not available in this window", 6000)
            return
        if self._viewer is None:
            self._viewer = Viewer(self._previews, self._tokens, self)
            # The listing follows the viewer's walk, so closing it leaves the
            # cursor on the file that was last on screen. Connected once, and it
            # asks the window which pane is active rather than remembering the
            # one that opened it -- otherwise stepping in the viewer would move
            # the cursor in a pane the user has since clicked away from.
            self._viewer.showing.connect(self._on_viewer_showing)
        self._viewer.show_file(folder, list(names), int(at))
        self._viewer.show()
        self._viewer.raise_()
        self._viewer.activateWindow()

    def _on_viewer_showing(self, path: str) -> None:
        widget = self._current_widget()
        name = os.path.basename(path.replace("\\", "/"))
        if name:
            widget.reveal_name(name)

    def _set_preview_pane(self, checked: bool) -> None:
        """Both panes at once, because it is one setting.

        A preview panel in one pane and not the other would be two different
        answers to the same question, and the setting is stored once. Which pane
        the *cursor* is in still decides which panel is doing any reading, and
        the panel in the pane nobody is using shows the last thing it was told
        and asks for nothing.
        """
        for widget in self._widgets:
            widget.show_preview(bool(checked))

    def _set_grid_view(self, checked: bool) -> None:
        """The active pane only, and this one deliberately is not both.

        The opposite of the preview panel above, and the difference is what the
        two are for. A preview panel is a place to look at one file, so having
        one is a preference. A view mode is how a folder is being read, and the
        case that makes a dual-pane file manager worth using is a grid of
        photographs on one side and a listing of where they are going on the
        other.
        """
        self._current_widget().set_view_mode("grid" if checked else "list")

    def _set_cell_size(self, size: int) -> None:
        self._config.set("preview.thumb_size", int(size))
        for widget in self._widgets:
            widget.set_cell_size(int(size))

    def _set_thumbnails(self, checked: bool) -> None:
        self._config.set("preview.thumbnails", bool(checked))
        if self._thumbnails is not None:
            self._thumbnails.reload()

    def _set_shell_previews(self, checked: bool) -> None:
        """Turn the third-party rung of the decoder off, and forget what it said.

        Both caches are cleared, and that is the point rather than tidiness: the
        pictures already in them came from the handler being turned off, so
        leaving them would mean the setting appearing to do nothing until
        somebody navigated away and back.
        """
        self._config.set("preview.shell", bool(checked))
        if self._previews is not None:
            self._previews.clear()
        if self._thumbnails is not None:
            self._thumbnails.reload()

    def _offer_elevation(self, pane):
        """Windows refused something. Ask, then run that one operation elevated.

        A closure per pane rather than one handler, because which pane asked
        decides which folder gets re-listed afterwards, and the signal does
        not carry it.
        """
        def offer(plan, description: str) -> None:
            if dialogs.confirm_elevate(self, description):
                pane.elevate(plan)
        return offer

    def _on_shell_invoked(self, verb: str, folder: str) -> None:
        """A shell command ran. What it did is not knowable from here.

        The shell does not report what a verb changed, and half of them change
        something: an extension that commits, an archiver that writes a zip,
        the shell's own Cut. So any pane showing that folder lists it again,
        which is the same thing this window does after its own operations and
        for the same reason -- the folder is the truth.
        """
        self._on_folder_changed(folder)
        if verb:
            self.statusBar().showMessage(f"ran {verb}", 4000)

    def _axis_menu(self, parent, title: str, labels: dict[str, str], key: str) -> None:
        """One submenu per axis of the design system.

        Built from the labels rather than hardcoded, so a theme added to
        `tokens.py` appears here without anybody remembering to add it.
        """
        menu = parent.addMenu(title)
        group = QActionGroup(self)
        group.setExclusive(True)
        current = self._config.get(key)
        for name, label in labels.items():
            action = QAction(label, self, checkable=True)
            action.setChecked(name == current)
            action.triggered.connect(lambda _checked=False, k=key, n=name: self._set_axis(k, n))
            group.addAction(action)
            menu.addAction(action)

    def _window_menu(self, parent) -> None:
        """The title bar and the backdrop. Both are decided when the window is
        created, so both say that a change waits for the next start."""
        menu = parent.addMenu("Window")
        for key, title, choices in (
            ("window.frame", "Title bar",
             (("custom", "This application's"), ("system", "Windows' own, with the menu bar"))),
            ("window.backdrop", "Backdrop",
             (("auto", "Automatic"), ("glass", "Glass"), ("solid", "Solid"))),
        ):
            heading = QAction(f"{title} (after a restart)", self)
            heading.setEnabled(False)
            menu.addAction(heading)
            group = QActionGroup(self)
            group.setExclusive(True)
            current = self._config.get(key)
            for name, label in choices:
                action = QAction(label, self, checkable=True)
                action.setChecked(name == current)
                action.triggered.connect(
                    lambda _checked=False, k=key, n=name: self._config.set(k, n))
                group.addAction(action)
                menu.addAction(action)
            menu.addSeparator()
        now = QAction(f"Now: {self._backdrop}", self)
        now.setEnabled(False)
        menu.addAction(now)

    def _hint(self, menu, text: str, slot) -> QAction:
        """A menu entry that shows a key without claiming it.

        Everything after the tab is drawn in the shortcut column, so the entry
        reads exactly like the ones that do have a shortcut -- while the key
        itself stays with the widget that knows when it is safe to act on it.
        """
        action = QAction(text, self)
        action.triggered.connect(slot)
        menu.addAction(action)
        return action

    def _action(self, menu, text: str, shortcut: str, slot) -> QAction:
        action = QAction(text, self)
        action.setShortcut(QKeySequence(shortcut))
        action.setShortcutContext(Qt.WindowShortcut)
        action.triggered.connect(slot)
        menu.addAction(action)
        return action

    # ------------------------------------------------------------------ theme

    def _set_axis(self, key: str, value: str) -> None:
        self._config.set(key, value)
        self.apply_theme()

    def apply_theme(self) -> None:
        tokens = sheet.apply(
            QApplication.instance(),
            theme=self._config.get("theme"),
            accent=self._config.get("accent"),
            density=self._config.get("density"),
            backdrop=self._backdrop,
        )
        if self._titlebar is not None:
            self._titlebar.apply_tokens(tokens)
        self._hints.apply_tokens(tokens)
        self._splitter.set_glow_colour(tokens["accent"])
        if self._frame is not None:
            # Mica takes its tint from the window's dark-mode flag, so a switch
            # to a light theme has to reach Windows as well as the sheet.
            self._frame.set_dark(sum(sheet.qss.unhex(tokens["bg_0"])) < 382)
        metrics = sheet.metrics(self._config.get("density"))
        for widget in self._widgets:
            widget.apply_metrics(metrics)
            widget.apply_tokens(tokens)
        if self._rail is not None:
            # The meters are painted rather than styled, so the rail needs the
            # same render the sheet was made from -- the reason the panes get
            # them handed down rather than fetching their own.
            self._rail.apply_tokens(tokens)
        self._tokens = tokens
        if self._queue_dialog is not None:
            # The queue's rows paint their own bars, so a theme change has to
            # reach a panel that may be open behind the window. It is the third
            # thing in the application that paints rather than styles, and the
            # third one that would silently stop following the picker.
            self._queue_dialog.apply_tokens(tokens)
        if self._viewer is not None:
            # And the fourth, for the same reason and with the same trap: the
            # backdrop behind a picture is painted, so a viewer left open behind
            # the window would keep the old theme's black on a light theme.
            self._viewer.apply_tokens(tokens)
        self._set_active(self._active)

    def _on_folder_changed(self, path: str) -> None:
        """One pane changed a folder; the other may be showing it.

        Both panes on the same folder is the normal way to work, and a pane
        that keeps showing a file somebody just deleted is the oldest bug in
        dual-pane file managers.
        """
        for pane in self._panes:
            if pane.current.path == path and not pane.busy:
                pane.refresh()

    # -------------------------------------------------------------- transfers

    def _on_transfer_requested(self, kind: str) -> None:
        """F5 and F6: to the other pane's folder, once it has been confirmed.

        The other pane is where a dual-pane file manager copies to, and filling
        the destination in from it is the whole point of having two. It is
        still only a suggestion in a field the user can edit -- nothing moves
        until this dialog is accepted.
        """
        widget, pane = self._current_widget(), self._current_pane()
        other = self._panes[1 - self._active]
        names = widget.selected_names()
        if not names:
            return
        transfer = JobKind.COPY if kind == "copy" else JobKind.MOVE
        prompt = TransferPrompt(transfer, names, other.display(), self)
        if prompt.exec() != QueueDialog.Accepted:
            return
        destination = pane.as_path(prompt.destination())
        if not destination:
            return
        sources = pane.paths_for(names)
        if transfer is JobKind.COPY:
            self._transfers.copy(sources, destination)
        else:
            self._transfers.move(sources, destination)

    def _on_clipboard_requested(self, what: str) -> None:
        """Ctrl+C, Ctrl+X and Ctrl+V, in the pane that has the keyboard.

        **The pane says what happened, not this window.** All three report on
        the pane's own status line, which is under the listing they are about,
        and none of them says anything here. The window's status bar was tried
        first and was wrong twice over: a successful copy was announced in two
        places at once, and a refused paste was announced only in the far
        corner of the window from the pane that had just been right-clicked,
        for six seconds. It read as a key that had done nothing.

        None of the three puts a dialog up. A copy that did would be unusable,
        and a paste already reports itself twice over without one -- as a job
        in the queue, and again when the folder relists.
        """
        widget, pane = self._current_widget(), self._current_pane()
        if what == "paste":
            pane.paste()
            return
        names = widget.selected_names()
        if not names:
            return
        pane.to_clipboard(names, cut=what == "cut")

    def _on_conflict(self, job_id: int, payload: dict) -> None:
        dialog = ConflictDialog(payload.get("name", ""), payload.get("source", {}),
                                payload.get("target", {}), self)
        if dialog.exec() != QueueDialog.Accepted or dialog.action is None:
            self._transfers.cancel(job_id)
            return
        self._transfers.answer(job_id, dialog.action, apply_to_all=dialog.apply_to_all)

    def _on_transfer_finished(self, job) -> None:
        """Re-list the folders a job touched, say how it went, and offer to
        retry what Windows refused.

        Only those folders: a pane showing something else has no reason to pay
        for a listing because a copy finished somewhere on the disk. The job
        works out which they are -- the destination, and where the sources came
        from -- so a delete refreshes the folder it emptied without this method
        having to know that a delete has no destination.
        """
        touched = job.folders
        for pane in self._panes:
            if pane.current.path in touched and not pane.busy:
                pane.refresh()
        self.statusBar().showMessage(_outcome(job), 8000)
        if job.problems:
            self._transfer_bar.refresh()
        if job.denied:
            self._offer_elevated_delete(job)

    def _offer_elevated_delete(self, job) -> None:
        """Windows refused a delete. Offer the same consent prompt the worker
        path offers, for exactly the items it refused.

        The plan runs `Op.DELETE`, which is the worker's own handler -- so an
        elevated delete is the same code as an ordinary one, run by a process
        that was allowed to. The queue does not run elevated and will not: a
        long job holding a consent prompt open is the thing `CLAUDE.md` says
        never to queue anything behind.
        """
        if not job.kind.removes:
            return
        folders = {os.path.dirname(source) for source in job.sources}
        if len(folders) != 1:
            # Every source came out of one folder in practice, because a
            # selection is a selection in one listing. If that ever stops being
            # true the plan has no single path to name, and no offer is better
            # than an offer about the wrong folder.
            return
        folder = folders.pop()
        plan = elevate.plan_for(Op.DELETE, folder, {
            "names": list(dict.fromkeys(job.denied)),
            "permanent": job.kind is JobKind.ERASE,
        })
        if plan is None:
            return
        description = elevate.describe(plan)
        if dialogs.confirm_elevate(self, description):
            self._current_pane().elevate(plan)

    def _show_queue(self) -> None:
        if self._queue_dialog is None:
            self._queue_dialog = QueueDialog(self._transfers, self)
            self._queue_dialog.apply_tokens(self._tokens)
        self._queue_dialog.show()
        self._queue_dialog.raise_()
        self._queue_dialog.activateWindow()

    def _swap_panes(self) -> None:
        """Put each pane where the other one was.

        Both are navigations rather than a swap of anything held: the tabs, the
        history and the requests in flight stay where they are, and each side
        lists the other's folder for itself.
        """
        left, right = (pane.current.path for pane in self._panes)
        if left != right:
            self._panes[0].navigate(right)
            self._panes[1].navigate(left)

    def _mirror_pane(self) -> None:
        """Send the other pane to this one's folder, which is where a copy goes."""
        other = self._panes[1 - self._active]
        here = self._current_pane().current.path
        if other.current.path != here:
            other.navigate(here)

    def _copy_path(self) -> None:
        """The full path of the row under the cursor, or of the folder itself."""
        pane = self._current_pane()
        path = pane.row_path(self._current_widget().current_row()) or pane.current.path
        QApplication.clipboard().setText(pane.display(path))
        self.statusBar().showMessage(f"copied {pane.display(path)}", 4000)

    def _set_show_unc(self, checked: bool) -> None:
        for pane in self._panes:
            pane.set_show_unc(checked)

    # ---------------------------------------------------------------- updates

    def _check_for_updates(self) -> None:
        if self._updates is None:
            return
        self.statusBar().showMessage("checking for updates", 4000)
        self._updates.check(manual=True)

    def _on_update_available(self, release) -> None:
        """Found something newer. Nothing is downloaded until this is answered."""
        answer = dialogs.offer_update(self, version=release.version,
                                      current=__version__, size=release.size)
        if answer == dialogs.UpdateOffer.DOWNLOAD:
            self._updates.accept(release)
        elif answer == dialogs.UpdateOffer.SKIP:
            self._updates.skip(release)

    def _on_up_to_date(self, current: str) -> None:
        self.statusBar().showMessage(f"{current} is the latest version", 6000)

    def _on_update_problem(self, message: str) -> None:
        self.statusBar().showMessage(message, 8000)

    def _on_update_progress(self, done: int, total: int) -> None:
        """In the status bar rather than a dialog.

        A download that has to be watched is a download that stops somebody
        working for the length of it, and this one has no reason to.
        """
        share = (done / total * 100) if total else 0
        self.statusBar().showMessage(f"downloading update  {share:.0f}%", 2000)

    def _on_update_ready(self, release) -> None:
        """Downloaded and verified. It runs when this window closes, either now
        or the next time -- `app/__main__.py` starts it after the pool is down.
        """
        self.statusBar().showMessage(
            f"File Manager {release.version} installs when you quit", 10000)
        if dialogs.confirm_install(self, version=release.version,
                                   transfers=bool(self._transfers.active)):
            self.close()

    # ------------------------------------------------------------------ panes

    def _current_pane(self):
        return self._panes[self._active]

    def _current_widget(self) -> PaneWidget:
        return self._widgets[self._active]

    def _switch_pane(self) -> None:
        self._set_active(1 - self._active)
        self._current_widget().focus_listing()

    def _on_pane_activated(self, widget: PaneWidget) -> None:
        self._set_active(self._widgets.index(widget))

    def _on_focus_changed(self, old, new) -> None:
        """Follow the keyboard into whichever pane now holds it.

        Anything that is not in a pane -- a dialog, the menu bar, the transfer
        queue -- leaves the active pane where it was, which is what somebody
        who opens a dialog and closes it again expects.
        """
        if new is None:
            return
        for index, widget in enumerate(self._widgets):
            if widget is new or widget.isAncestorOf(new):
                if index != self._active:
                    self._set_active(index)
                return

    def _set_active(self, index: int) -> None:
        self._active = index
        for position, widget in enumerate(self._widgets):
            widget.set_active(position == index)
        self._splitter.glow_on(self._widgets[index])
        self._sync_rail_mark()
        self._sync_flat_action()

    def _sync_flat_action(self) -> None:
        """The View menu's tick follows the tab in front of the active pane."""
        action = getattr(self, "_flat_action", None)
        if action is not None:
            action.setChecked(bool(self._current_pane().current.flat))

    def _set_flat_layout(self, layout: str) -> None:
        # Both panes: the layout is a preference, not a property of one tab.
        for pane in self._panes:
            pane.set_flat_layout(layout)

    # ------------------------------------------------------------ the frame

    def _adopt_shortcuts(self) -> None:
        """Put every action that carries a key onto the window itself.

        Needed only while the menu bar is hidden, and harmless when it is not:
        an action added to a second widget is still one shortcut.
        """
        if self._frame_kind != "custom":
            return
        owned = set(self.actions())

        def walk(menu) -> None:
            for action in menu.actions():
                if action.menu() is not None:
                    walk(action.menu())
                elif not action.shortcut().isEmpty() and action not in owned:
                    self.addAction(action)
                    owned.add(action)

        walk(self.menuBar())

    def _show_app_menu(self, at) -> None:
        """Every menu the bar used to show, as one menu under the mark."""
        menu = QMenu(self)
        for action in self.menuBar().actions():
            if action.menu() is not None:
                menu.addMenu(action.menu())
        menu.aboutToHide.connect(menu.deleteLater)
        menu.popup(at)

    def hit_parts(self, pos) -> tuple[bool, bool]:
        """For `winframe`: is `pos` the maximise button, and is it caption."""
        bar = self._titlebar
        if bar is None:
            return False, False
        local = bar.mapFrom(self, pos)
        over_max = bar.max_button.geometry().contains(local)
        return over_max, bar.is_caption(local)

    def set_max_hover(self, hot: bool) -> None:
        if self._titlebar is not None:
            self._titlebar.set_max_hover(hot)

    def toggle_maximized(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().showEvent(event)
        if self._frame is not None and not self._frame.active:
            self._frame.install()
            if self._frame.problem:
                self.statusBar().showMessage(self._frame.problem, 12000)

    def nativeEvent(self, event_type, message):  # noqa: N802 - Qt naming
        # 0.27: a drive plugged in or pulled out. Windows broadcasts volume
        # arrivals to every top-level window, so the rail follows a USB stick
        # without anybody pressing Rescan and without polling for it.
        if bytes(event_type) == b"windows_generic_MSG":
            from ctypes import wintypes

            msg = wintypes.MSG.from_address(int(message))
            if msg.message == 0x0219 and msg.wParam in (0x8000, 0x8004):
                QTimer.singleShot(500, lambda: self._volumes.refresh(rescan=True))
        if self._frame is not None:
            answer = self._frame.handle(event_type, message)
            if answer is not None:
                return answer
        return super().nativeEvent(event_type, message)

    # ----------------------------------------------------------- live folders

    def changeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """Checks stop while the window is minimised and catch up the moment
        it comes back, which is when a file saved from another program is most
        likely to be waiting to appear."""
        if event.type() == QEvent.WindowStateChange:
            if self._titlebar is not None:
                self._titlebar.set_maximized(self.isMaximized())
            minimised = bool(self.windowState() & Qt.WindowMinimized)
            for pane in self._panes:
                pane.set_live(not minimised)
                if not minimised:
                    pane.check_now()
        elif event.type() == QEvent.ActivationChange and self.isActiveWindow():
            for pane in self._panes:
                pane.check_now()
        super().changeEvent(event)

    # ------------------------------------------------------------------ close

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """Remember where the panes were. Failing to save is not worth a dialog.

        A job still running is the one thing worth stopping for. The ops
        process is a child of this one, so closing the window ends it: the
        honest thing is to say so and let the user decide, rather than to
        promise work that outlives the window and not deliver it.

        A recycle is the case to keep in mind here. It is one shell call, so
        "closing stops them where they are" is not quite true of it -- the
        process goes, the call it was inside does not. Nothing is lost either
        way, which is why the dialog does not try to explain it.
        """
        running = self._transfers.active
        if running:
            names = [f"{job.label} to {job.destination}" if job.destination
                     else job.label for job in running]
            if not dialogs.confirm_stop(self, names):
                event.ignore()
                return
        self._transfers.shutdown()
        if self._updates is not None:
            self._updates.shutdown()
        self._config.set("window.width", self.width())
        self._config.set("window.height", self.height())
        self._remember_rail_width()
        for side, pane in zip(("left", "right"), self._panes):
            self._config.set(f"{side}.path", pane.current.path)
            self._config.set(f"{side}.tabs", pane.session())
            self._config.set(f"{side}.tab", pane.index)
        self._config.save()
        super().closeEvent(event)

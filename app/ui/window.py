"""The window: two panes, a menu, and the shortcuts that drive them.

Everything the menu does to the look goes through one call. Changing theme,
accent or density re-renders the whole sheet and re-applies it, then hands the
panes the metrics a stylesheet cannot set. There is no partial update path, and
adding one is how a picker ends up half working.
"""

from __future__ import annotations

import os.path
import sys
import time

from PySide6.QtGui import QAction, QActionGroup, QColor, QKeySequence, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QMenu,
    QStatusBar,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import QEvent, Qt, QTimer

from app import __version__
from app.core import commands as core_commands
from app.core import compare as core_compare
from app.core import themeswitch, when
from app.core.transfers import worth_notifying
from app.core import places as core_places
from app.core.favorites import UNGROUPED
from app.io import elevate, paths
from app.io.protocol import THUMB_SIZES, JobKind, Op
from app.theme import sheet
from app.theme.tokens import (
    ACCENT_LABELS,
    DENSITY_LABELS,
    GLASS_FLOOR,
    THEME_LABELS,
)
from app.ui import dialogs, winframe

#: 0.50: how strongly the accent washes the desktop in the tinted glass look.
TINT_ALPHA = 0.24

#: 0.50.1: the glass watchdog -- a beat, how late counts as slow, and how many
#: slow beats in how long mean glass is too much for this machine.
GLASS_BEAT_MS = 500
GLASS_LATE_S = 0.4
GLASS_STRIKES = 4
GLASS_WINDOW_S = 20.0
from app.ui.deck import Deck
from app.core import palette as core_palette
from app.ui.hints import HintBar
from app.ui.palette import CommandPalette
from app.ui.titlebar import TitleBar
from app.ui.pane import PaneWidget
from app.ui.rail import NavigationRail
from app.ui.pill import TransferPill
from app.ui.transfers import ConflictDialog, HistoryDialog, QueueDialog, TransferPrompt
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
        if getattr(job, "retryable", False):
            summary += " -- Ctrl+J to retry the failed ones"
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
                 frame: str | None = None, ejector=None, sync=None,
                 accent_source=None, health=None, git=None, folder_map=None) -> None:
        super().__init__(parent)
        self._config = config
        #: 0.43: the folder map's walk, and its window while one is open.
        self._folder_map = folder_map
        self._map_window = None
        #: 0.35: where a Windows or wallpaper accent comes from, and the
        #: triple it gave, or None to use the named accent.
        self._accent_source = accent_source
        self._accent_rgb = None
        #: 0.37: share health, or None for a window built without io.
        self._health = health
        #: 0.30: the icon a finished-job notification is shown through. Made on
        #: the first one and hidden again once it has been read, so the
        #: notification area gains nothing permanent.
        self._tray: QSystemTrayIcon | None = None
        self._tray_timer: QTimer | None = None
        #: 0.32: the walk behind a sync, or None for a window built without io.
        self._sync = sync
        self._sync_dialog = None
        #: 0.27: USB eject, or None for a window built without io.
        self._ejector = ejector
        #: "glass" or "solid", decided before the window exists by
        #: `core.backdrop.choose`, because a translucent window has to be
        #: translucent from the moment it is created. Changing it takes a
        #: restart, and the View menu says so.
        self._backdrop = backdrop if backdrop in ("glass", "solid") else "solid"
        #: 0.50: the glass look, read once like the backdrop itself -- a
        #: different material is a different window as far as Windows is
        #: concerned, so it waits for the next start.
        look = str(config.get("window.glass"))
        self._glass_look = look if look in ("mica", "acrylic", "frosted", "tinted") \
            else "mica"
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
        # 0.50.5: login prompts -- shares whose prompt was cancelled, shares
        # with one on screen, and the user last typed for each (never the
        # password, and none of it is written anywhere).
        self._declined_logins: set[str] = set()
        self._asking_logins: set[str] = set()
        self._login_users: dict[str, str] = {}
        self._updates = updates
        self._favorites = favorites
        self._capacity = capacity
        self._panes = (left, right)
        # 0.43: what the panes and the status bar said, for Copy diagnostics.
        # Kept in memory only; see core/diagnostics.py.
        from app.core.diagnostics import Events
        self._events = Events()
        for side, pane in (("left", left), ("right", right)):
            pane.statusChanged.connect(
                lambda text, state, s=side: self._events.add(s, text) if state == "bad" else None)
        # 0.44: undo, for both panes and the queue.
        from app.core.undo import UndoStack
        self._undo = UndoStack(self)
        #: Jobs started by an undo, which are not themselves recorded -- an
        #: undo that could be undone would be a redo, and there is none.
        self._undo_jobs: set[int] = set()
        for pane in (left, right):
            pane.undoable.connect(self._undo.push)
        #: 0.43: set once settings have been restored, so closing does not
        #: write the old panes' tabs over the restored ones.
        self._restored = False
        self._icons = left.icons
        self._overlays = left.overlays
        self._file_icons = left.file_icons
        self._shell_menu = left.menu
        self._volumes = volumes
        self._transfers = transfers
        self._queue_dialog: QueueDialog | None = None
        self._history_dialog: HistoryDialog | None = None
        #: 0.46: jobs queued for another application (`core/handoff.py`),
        #: job id -> the request they came from, and each request's jobs
        #: still running and those finished. When the last one ends the
        #: outcome is written beside the request for the sender to read.
        self._handoff_jobs: dict[int, object] = {}
        self._handoff_open: dict[int, set[int]] = {}
        self._handoff_done: dict[int, list] = {}
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
        #: 0.48: the theme actually on screen, which is `theme` unless it is
        #: set to follow Windows or the clock. See `core/themeswitch.py`.
        self._theme_shown = themeswitch.current(config)
        tokens = sheet.tokens(self._theme_shown, config.get("accent"),
                              config.get("density"), self._paint_backdrop,
                              self._accent_rgb, config.get("look.font"),
                              config.get("look.corners"))
        self._tokens = tokens
        self._pane_tokens = [tokens, self._right_tokens(tokens)]
        for widget, own in zip(self._widgets, self._pane_tokens):
            widget.apply_tokens(own)

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
            self._rail.favoriteRequested.connect(self._save_favorite)
            if network is not None:
                self._rail.refreshNetworkRequested.connect(network.refresh)
                network.changed.connect(self._rebuild_rail)
                network.reconnected.connect(self._on_reconnected)
            self._rail.setVisible(bool(config.get("rail.shown")))
            if health is not None:
                self._rail.set_health(health)
            # Measured once the letters are known, and only the local fixed
            # ones -- see `core/capacity.py`. Nothing here touches a server.
            volumes.changed.connect(self._measure_local_drives)

        self._splitter = Deck()
        if self._backdrop == "glass":
            # 0.50.1: on glass every repaint sends the whole window to
            # Windows, and a live splitter re-lays both panes out on every
            # pixel of a drag -- which was reported as the panes not resizing
            # and the window feeling locked. A line follows the drag instead,
            # and the panes are laid out once, where it is let go.
            self._splitter.setOpaqueResize(False)
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
            # 0.50.5: a share that refused this session's account asks for
            # another one, as Double Commander does, rather than only saying so.
            pane.loginNeeded.connect(self._on_login_needed)
            # The rail marks the row the active pane is standing on, which is
            # the one thing a rail can say that a menu cannot. Both panes are
            # watched and the mark follows whichever is active, so the answer
            # changes when the pane does as well as when the folder does.
            pane.pathChanged.connect(self._sync_rail_mark)
            if health is not None:
                # Only a share a pane has been to is ever measured: this is
                # the whole of how the health learns which shares exist.
                pane.pathChanged.connect(
                    lambda _shown, p=pane: health.watch(
                        p.current.path, getattr(volumes, "drives", [])))
        if self._shell_menu is not None:
            self._shell_menu.invoked.connect(self._on_shell_invoked)
            self._shell_menu.problem.connect(
                lambda message: self.statusBar().showMessage(message, 8000))
        for widget in self._widgets:
            widget.transferRequested.connect(self._on_transfer_requested)
            widget.settingRequested.connect(self.apply_setting)
            widget.dropRequested.connect(
                lambda sources, destination, move, w=widget:
                self._on_drop_requested(w, sources, destination, move))
            widget.clipboardRequested.connect(self._on_clipboard_requested)
            widget.undoRequested.connect(self._undo_last)
            widget.extractRequested.connect(
                lambda name, w=widget: self._on_extract_requested(w, name))
            widget.addFavoriteRequested.connect(self._add_favorite)
            widget.manageFavoritesRequested.connect(self._manage_favorites)
            widget.viewRequested.connect(self._on_view_requested)
            widget.peekRequested.connect(self._open_peek)
            widget.basketRequested.connect(self._fill_basket)
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
            self._titlebar.goRequested.connect(self._open_palette)
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
                                               material="mica" if self._glass_look == "mica"
                                               else "acrylic",
                                               dark=dark)
        else:
            self.setCentralWidget(self._splitter)
        self._splitter.set_glow_colour(tokens["accent"])
        self._splitter.set_motion(bool(config.get("look.motion")))
        self._splitter.set_glow_enabled(bool(config.get("look.pane_glow")))
        # 0.39: the drafting grid lives in the listings, not on the Deck
        # behind them -- see app/ui/drafting.py for why.
        from app.ui.drafting import DraftingGrid
        self._drafting = DraftingGrid(self)
        for widget in self._widgets:
            for view in widget.listing_views():
                self._drafting.attach(view)
        self._apply_grid(tokens)

        self.setWindowTitle(TITLE)
        self.setStatusBar(QStatusBar())
        self._hints = HintBar()
        self._hints.apply_tokens(tokens)
        self.statusBar().addWidget(self._hints, 1)
        # 0.37: "Switch to Logix Designer", after a job failed on a file some
        # program had open. Hidden until there is one, and hidden again a
        # little after, so it never becomes part of the furniture.
        from PySide6.QtWidgets import QToolButton

        self._holder_button = QToolButton()
        self._holder_button.setProperty("role", "status")
        self._holder_button.setFocusPolicy(Qt.NoFocus)
        self._holder_button.hide()
        self._holder_button.clicked.connect(self._switch_to_holder)
        self._holder_pid = 0
        self._holder_timer = QTimer(self)
        self._holder_timer.setSingleShot(True)
        self._holder_timer.setInterval(20000)
        self._holder_timer.timeout.connect(self._holder_button.hide)
        self.statusBar().addPermanentWidget(self._holder_button)
        # 0.29: the transfer readout floats over the bottom of the window
        # rather than sitting in the status bar. See `app/ui/pill.py`.
        self._transfer_bar = TransferPill(transfers, self)
        self._transfer_bar.apply_tokens(tokens)
        self._transfer_bar.opened.connect(self._show_queue)
        self._transfer_bar.set_motion(bool(config.get("look.motion")))
        self._transfer_bar.set_speedline(bool(config.get("transfers.speedline")))
        # 0.49: the taskbar button as a progress bar. Made on first use, so a
        # window that never copies anything never asks COM for the interface.
        self._taskbar = None
        self._taskbar_failed = False
        if transfers is not None:
            transfers.changed.connect(self._update_taskbar)
            transfers.finished.connect(self._on_job_for_taskbar)
        self.resize(int(config.get("window.width")), int(config.get("window.height")))

        #: Said once. See `_watch_for_remote`.
        self._said_remote = False
        self._remote_timer = QTimer(self)
        self._remote_timer.setInterval(30000)
        self._remote_timer.timeout.connect(self._watch_for_remote)
        self._remote_timer.start()
        # 0.48: a theme that follows Windows or the clock is checked once a
        # minute. Both answers are local and instant -- a registry value and
        # the time -- and nothing is re-rendered unless the answer changed.
        self._theme_timer = QTimer(self)
        self._theme_timer.setInterval(60000)
        self._theme_timer.timeout.connect(self._check_theme)
        self._theme_timer.start()
        # 0.50.1: glass that makes this machine's window slow turns itself off
        # for the next start. See `_watch_glass`.
        self._glass_beat = None
        self._glass_slow = []
        if self._backdrop == "glass":
            import time as _time

            self._glass_beat = _time.monotonic()
            self._glass_timer = QTimer(self)
            self._glass_timer.setInterval(GLASS_BEAT_MS)
            self._glass_timer.timeout.connect(self._watch_glass)
            self._glass_timer.start()
            for widget in self._widgets:
                widget.set_placeholder_motion(False)

        self._palette = CommandPalette(self)
        self._palette.apply_tokens(tokens)
        self._palette.chosen.connect(self._on_palette_chosen)
        #: 0.36: the peek card, built the first time Space peeks.
        self._peek = None
        #: 0.38: the basket, and its tray over the bottom left.
        from app.core.basket import Basket
        from app.ui.basket import BasketTray

        self._basket = Basket(self)
        # 0.38: colour labels and notes, one store for both panes.
        from app.core.labels import Labels

        self._labels = Labels(config, self)
        for pane in self._panes:
            pane.set_labels(self._labels)
        self._labels.changed.connect(
            lambda: [w.update_rows() for w in self._widgets])
        # 0.38: git's marks, or None for a window built without io.
        self._git = git
        if git is not None:
            for pane in self._panes:
                pane.set_git(git)
            git.changed.connect(lambda: [w.update_rows() for w in self._widgets])
        self._basket_tray = BasketTray(self._basket, self)
        self._basket_tray.set_enabled(bool(config.get("basket.enabled")))
        self._basket_tray.copyRequested.connect(lambda: self._empty_basket(move=False))
        self._basket_tray.moveRequested.connect(lambda: self._empty_basket(move=True))
        #: 0.33: the Options dialog, built on first use and kept, and the
        #: menu entries that show a setting, so a change made in either place
        #: is shown in the other. A checkable action per switch; a group of
        #: actions per choice, keyed by the value each one sets.
        self._options = None
        self._bound: dict[str, QAction] = {}
        self._bound_choices: dict[str, dict] = {}
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
        # And follows the mouse, for every press anywhere inside a pane. The
        # focus rule above cannot see a click on something that takes no
        # focus -- a tab, the new-tab button, a crumb -- and `_claim` on each
        # control only made the pane *look* active while the keyboard stayed
        # in the other one. Watching presses at the application is the one
        # place that covers every control, including the next one added.
        QApplication.instance().installEventFilter(self)
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
        if self._accent_source is not None:
            self._accent_source.found.connect(self._on_accent_found)
            self._resolve_accent()
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
        """The application menu.

        0.50: six menus where there were nine, in the order a person reaches
        for them -- File, Edit, Go, View, Tools, Help -- with Options at the
        end. Tabs, Favorites and Workspaces are submenus of Go; Select is part
        of Edit; the on/off switches that used to fill the View menu live in
        Options, where each one is explained, and in `_switches`, a menu that
        is never shown but that the command palette still searches -- so
        Ctrl+K finds every one of them by name, as before.
        """
        files = self.menuBar().addMenu("&File")
        edit = self.menuBar().addMenu("&Edit")
        go = self.menuBar().addMenu("&Go")
        view = self.menuBar().addMenu("&View")
        self._tools_menu = self.menuBar().addMenu("&Tools")
        helping = self.menuBar().addMenu("&Help")
        #: Settings with a tick that are not on any menu shown, for Ctrl+K.
        self._switches = QMenu("Settings", self)
        viewing = self._hint(files, "View\tF3",
                             lambda: self._current_widget().view_current())
        viewing.setToolTip("Open the file under the cursor: a picture with zoom, "
                           "text with its encoding, or a hex dump. The arrow "
                           "keys step through the folder.")
        files.addSeparator()
        # These four carry their keys in the label rather than as shortcuts.
        # A window shortcut on Delete would take the key from the path bar and
        # the filter box, so a backspace over a typo could start deleting
        # files; the pane handles them where focus makes that safe.
        self._hint(files, "Copy\tF5", lambda: self._on_transfer_requested("copy"))
        self._hint(files, "Move\tF6", lambda: self._on_transfer_requested("move"))
        self._hint(files, "New folder\tF7", lambda: self._current_widget().new_folder())
        self._hint(files, "Rename\tF2", lambda: self._current_widget().rename_current())
        self._action(files, "Rename several...", "Ctrl+M",
                     lambda: self._current_widget().rename_several())
        self._hint(files, "Duplicate\tShift+F5",
                   lambda: self._current_widget().duplicate_current())
        files.addSeparator()
        self._hint(files, "Delete\tDel", lambda: self._current_widget().delete_selection())
        self._hint(files, "Delete permanently\tShift+Del",
                   lambda: self._current_widget().delete_selection(permanent=True))
        files.addSeparator()
        self._file_more = files.addMenu("More")
        self._file_more.setToolTipsVisible(True)
        self._hint(self._file_more, "Checksums...",
                   lambda: self._current_widget().checksums())
        self._hint(self._file_more, "Attributes and dates...",
                   lambda: self._current_widget().attributes())
        self._hint(self._file_more, "New link in other pane...", self._new_link)
        files.addSeparator()
        self._action(files, "Queue", "Ctrl+J", self._show_queue)
        self._hint(files, "Job history", self._show_history)
        files.addSeparator()
        self._action(files, "Quit", "Ctrl+Q", self.close)

        self._undo_action = self._hint(edit, "Undo\tCtrl+Z", self._undo_last)
        self._undo_action.setEnabled(False)
        self._undo.changed.connect(self._sync_undo)
        edit.addSeparator()
        # Hints again, all three. Ctrl+C, Ctrl+X and Ctrl+V as window
        # shortcuts would take copy, cut and paste away from the path bar and
        # the filter box, which is the same failure Delete is kept off the
        # window for -- and there the cost is only a lost keystroke, while
        # here it is a paste of files into a folder because the user meant to
        # paste text into a field.
        self._hint(edit, "Cut\tCtrl+X",
                   lambda: self._on_clipboard_requested("cut"))
        self._hint(edit, "Copy\tCtrl+C",
                   lambda: self._on_clipboard_requested("copy"))
        self._hint(edit, "Paste\tCtrl+V",
                   lambda: self._on_clipboard_requested("paste"))
        edit.addSeparator()

        tabs = QMenu("&Tabs", self)
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
        select = edit
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
        select.addSeparator()
        # By date, with no keys: the keypad is spent, and a date window is
        # picked often enough to want a menu and rarely enough not to want a
        # key taken from the command table for it. The palette finds these.
        dated = select.addMenu("Select by date")
        for key, label in when.WINDOWS:
            self._hint(dated, label,
                       lambda _=False, key=key: widget().select_modified(key))
        dated.addSeparator()
        day = self._hint(dated, "Same day as this one",
                         lambda: widget().select_same_day())
        day.setToolTip("Every row modified on the day the one under the "
                       "cursor was.")
        dated.setToolTipsVisible(True)
        select.setToolTipsVisible(True)
        edit.addSeparator()
        self._action(edit, "Copy path", "Ctrl+Shift+C", self._copy_path)
        self._action(edit, "Copy path as UNC", "Ctrl+Alt+C", self._copy_unc_path)

        self._favorites_menu = QMenu("F&avorites", self)
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
        self._show_bar_action.triggered.connect(
            lambda checked: self.apply_setting("favorites.bar", bool(checked)))
        self._bound["favorites.bar"] = self._show_bar_action
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

        # 0.38: workspaces. Rebuilt on every change, like the favourites; the
        # nine keys are made once, for the favourites' reason.
        from app.core.workspaces import Workspaces

        self._workspaces = Workspaces(self._config, self)
        self._workspaces_menu = QMenu("&Workspaces", self)
        self._workspaces_menu.setToolTipsVisible(True)
        self._workspace_keys = []
        for position in range(1, 10):
            entry = QAction(f"Workspace {position}", self)
            entry.setShortcut(QKeySequence(f"Ctrl+Alt+{position}"))
            entry.setShortcutContext(Qt.WindowShortcut)
            entry.triggered.connect(
                lambda _checked=False, n=position - 1: self._open_workspace_at(n))
            entry.setVisible(False)
            self.addAction(entry)
            self._workspace_keys.append(entry)
        self._workspaces.changed.connect(self._fill_workspaces)
        self._fill_workspaces()

        self._action(go, "Up", "Backspace", lambda: self._current_pane().go_up())
        self._action(go, "Back", "Alt+Left", lambda: self._current_pane().go_back())
        self._action(go, "Forward", "Alt+Right", lambda: self._current_pane().go_forward())
        self._action(go, "Refresh", "Ctrl+R", lambda: self._current_pane().refresh())
        self._action(go, "Reconnect", "Ctrl+Shift+R", self._reconnect_pane)
        go.addSeparator()
        self._action(go, "Edit path", "Ctrl+L", lambda: self._current_widget().focus_path())
        self._action(go, "Other pane", "Tab", self._switch_pane)
        self._palette_action = self._action(go, "Command palette", "Ctrl+K",
                                            self._open_palette)
        go.addSeparator()
        go.addMenu(tabs)
        go.addMenu(self._favorites_menu)
        go.addMenu(self._workspaces_menu)
        go.addSeparator()
        self._action(go, "Rescan drives", "Ctrl+Shift+D",
                     lambda: self._volumes.refresh(rescan=True))

        view.setToolTipsVisible(True)
        # A hint rather than a shortcut, like every other function key: a window
        # shortcut on F3 would take the key from the path bar and the filter box.
        # Those two do not use F3 -- but the argument that matters is that every
        # function key in this application is the pane's, so which pane the
        # viewer opens on is decided by the same rule as which pane F5 copies
        # from, rather than by a second mechanism that agrees most of the time.
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
                lambda _checked=False, n=size: self.apply_setting(
                    "preview.thumb_size", n))
            self._bound_choices.setdefault("preview.thumb_size", {})[size] = entry
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
                lambda _checked=False, k=key: self.apply_setting("flat.layout", k))
            self._bound_choices.setdefault("flat.layout", {})[key] = entry
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
        rail.triggered.connect(
            lambda checked: self.apply_setting("rail.shown", bool(checked)))
        self._bound["rail.shown"] = rail
        view.addAction(rail)
        unc = QAction("Show UNC paths", self, checkable=True)
        unc.setChecked(bool(self._config.get("left.show_unc")))
        unc.triggered.connect(self._set_show_unc)
        view.addAction(unc)
        view.addSeparator()
        self._action(view, "Swap panes", "Ctrl+U", self._swap_panes)
        self._action(view, "Other pane here", "Ctrl+Shift+M", self._mirror_pane)
        view.addSeparator()
        self._axis_menu(view, "Theme", THEME_LABELS, "theme")
        self._axis_menu(view, "Accent", ACCENT_LABELS, "accent")
        self._axis_menu(view, "Density", DENSITY_LABELS, "density")
        view.addSeparator()
        more_look = self._hint(view, "More look settings...",
                               lambda: self.open_options("look"))
        more_look.setToolTip("Themes that follow Windows or the clock, the font, "
                             "corners, glass and the rest.")
        switches = self._switches
        self._window_menu(switches)
        badges = QAction("Type badges instead of icons", self, checkable=True)
        badges.setChecked(self._config.get("icons.style") == "badges")
        badges.setToolTip("A tag with the extension, coloured by kind of file: "
                          "Logix, HMI, drawings, PDF. Off shows Windows' icons.")
        badges.triggered.connect(
            lambda checked: self.apply_setting(
                "icons.style", "badges" if checked else "icons"))
        self._bound["icons.style"] = badges
        switches.addAction(badges)
        motion = QAction("Animations", self, checkable=True)
        motion.setChecked(bool(self._config.get("look.motion")))
        motion.setToolTip("Folders fade in, the active pane's glow moves across, "
                          "and the transfer readout slides in and out.")
        motion.triggered.connect(
            lambda checked: self.apply_setting("look.motion", bool(checked)))
        self._bound["look.motion"] = motion
        switches.addAction(motion)
        header = QAction("Folder header", self, checkable=True)
        header.setChecked(bool(self._config.get("pane.header")))
        header.setToolTip("The folder's name above the listing, and a bar of "
                          "what it holds by kind of file.")
        header.triggered.connect(
            lambda checked: self.apply_setting("pane.header", bool(checked)))
        self._bound["pane.header"] = header
        switches.addAction(header)
        shell_icons = QAction("Shell icons", self, checkable=True)
        shell_icons.setChecked(bool(self._config.get("icons.shell")))
        shell_icons.setEnabled(self._icons is not None)
        shell_icons.triggered.connect(
            lambda checked: self.apply_setting("icons.shell", bool(checked)))
        self._bound["icons.shell"] = shell_icons
        switches.addAction(shell_icons)
        overlays = QAction("Icon overlays", self, checkable=True)
        overlays.setChecked(bool(self._config.get("icons.overlays")))
        overlays.setEnabled(self._overlays is not None)
        overlays.setToolTip("Shared folders, OneDrive and source control badges. "
                            "The one icon lookup that asks about a file rather "
                            "than about its type.")
        overlays.triggered.connect(
            lambda checked: self.apply_setting("icons.overlays", bool(checked)))
        self._bound["icons.overlays"] = overlays
        switches.addAction(overlays)
        file_icons = QAction("Icons from the file itself", self, checkable=True)
        file_icons.setChecked(bool(self._config.get("icons.per_file")))
        file_icons.setEnabled(self._file_icons is not None)
        file_icons.setToolTip("Programs, shortcuts and .ico files draw their own "
                              "icon rather than the one for their type. Reads "
                              "the file, for the rows on screen only.")
        file_icons.triggered.connect(
            lambda checked: self.apply_setting("icons.per_file", bool(checked)))
        self._bound["icons.per_file"] = file_icons
        switches.addAction(file_icons)
        thumbs = QAction("Pictures in the grid", self, checkable=True)
        thumbs.setChecked(bool(self._config.get("preview.thumbnails")))
        thumbs.setEnabled(self._thumbnails is not None)
        thumbs.setToolTip("Off means the grid draws the icon for each kind, "
                          "which is still a grid and costs no reads.")
        thumbs.triggered.connect(
            lambda checked: self.apply_setting("preview.thumbnails", bool(checked)))
        self._bound["preview.thumbnails"] = thumbs
        switches.addAction(thumbs)
        shell_preview = QAction("Windows thumbnail handlers", self, checkable=True)
        shell_preview.setChecked(bool(self._config.get("preview.shell")))
        shell_preview.setToolTip("Ask Windows for a picture of the kinds this "
                                 "application cannot decode itself: video "
                                 "frames, Office documents, .heic, .psd. This is "
                                 "the one part of the previewer that runs "
                                 "somebody else's code.")
        shell_preview.triggered.connect(
            lambda checked: self.apply_setting("preview.shell", bool(checked)))
        self._bound["preview.shell"] = shell_preview
        switches.addAction(shell_preview)
        shell_commands = QAction("Explorer context menu", self, checkable=True)
        shell_commands.setChecked(bool(self._config.get("menu.shell")))
        shell_commands.setEnabled(self._shell_menu is not None)
        shell_commands.triggered.connect(
            lambda checked: self.apply_setting("menu.shell", bool(checked)))
        self._bound["menu.shell"] = shell_commands
        switches.addAction(shell_commands)
        self._options_action = QAction("Options...", self)
        self._options_action.setShortcut(QKeySequence("Ctrl+,"))
        self._options_action.setShortcutContext(Qt.WindowShortcut)
        self._options_action.triggered.connect(lambda: self.open_options())
        self._options_action.setToolTip("Every setting in one place, each one "
                                        "applied as it is changed.")

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
        self._sync_action = QAction("Synchronize folders...", self)
        self._sync_action.setToolTip(
            "Make the other pane's folder match this one, subfolders included "
            "-- new and newer files copied, and in mirror mode what is not here "
            "removed. Shows everything it would do before doing any of it.")
        self._sync_action.triggered.connect(self._synchronize)
        self._sync_action.setEnabled(self._sync is not None)
        # 0.42: search, and the duplicate finder that shares its dialog.
        self._search_action = QAction("Search...", self)
        self._search_action.setShortcut(QKeySequence("Alt+F7"))
        self._search_action.setShortcutContext(Qt.WindowShortcut)
        self._search_action.setToolTip(
            "Find files by name, contents, date and size under a folder. The "
            "results open in a new tab, where they can be marked, copied and "
            "deleted like any other rows.")
        self._search_action.triggered.connect(lambda: self._search(duplicates=False))
        self.addAction(self._search_action)
        self._duplicates_action = QAction("Find duplicates...", self)
        self._duplicates_action.setToolTip(
            "Files under a folder that have an identical copy somewhere else "
            "under it, compared by content, in a new tab.")
        self._duplicates_action.triggered.connect(lambda: self._search(duplicates=True))
        self._map_action = QAction("Folder map...", self)
        self._map_action.setToolTip(
            "Where the space under this folder has gone: blocks sized by bytes, "
            "nested as the folders are, coloured by the kind of file.")
        self._map_action.triggered.connect(self._open_map)
        self._map_action.setEnabled(self._folder_map is not None)
        self._split_action = QAction("Split file...", self)
        self._split_action.setToolTip("The file under the cursor into numbered parts "
                                      "(.001, .002 ...) in the other pane's folder.")
        self._split_action.triggered.connect(lambda: self._split_or_join(joining=False))
        self._join_action = QAction("Join files...", self)
        self._join_action.setToolTip("Put a file split into .001, .002 ... back together: "
                                     "run it on the .001 part.")
        self._join_action.triggered.connect(lambda: self._split_or_join(joining=True))
        self._file_more.addSeparator()
        self._file_more.addAction(self._split_action)
        self._file_more.addAction(self._join_action)
        self._edit_commands_action = QAction("Commands", self)
        self._edit_commands_action.setToolTip(
            "The programs on the Tools menu and the keys that reach them.")
        self._edit_commands_action.triggered.connect(self._edit_commands)
        self._edit_commands_action.setEnabled(self._commands is not None)
        self._fill_tools()

        check = QAction("Check for updates", self)
        check.triggered.connect(self._check_for_updates)
        check.setEnabled(self._updates is not None)
        helping.addAction(check)
        automatic = QAction("Check on launch", self, checkable=True)
        automatic.setChecked(bool(self._config.get("updates.check_on_launch")))
        automatic.triggered.connect(
            lambda checked: self.apply_setting("updates.check_on_launch", bool(checked)))
        self._bound["updates.check_on_launch"] = automatic
        automatic.setEnabled(self._updates is not None)
        helping.addAction(automatic)
        helping.addSeparator()
        # 0.43: the report to send when something misbehaves, and settings
        # backups. None of the four needs a worker: the report is built in
        # memory, and the backups folder is the settings file's own.
        diagnostics = QAction("Copy diagnostics", self)
        diagnostics.setToolTip("Version, Windows, Qt, changed settings (your folders "
                               "and labels by count only) and recent problems, on the "
                               "clipboard to paste into a report. Nothing is sent.")
        diagnostics.triggered.connect(self._copy_diagnostics)
        helping.addAction(diagnostics)
        helping.addSeparator()
        settings = helping.addMenu("Settings")
        settings.setToolTipsVisible(True)
        backup = QAction("Back up settings", self)
        backup.setToolTip("A dated copy of every setting -- favourites, workspaces, "
                          "labels, commands -- beside the settings file.")
        backup.triggered.connect(self._back_up_settings)
        settings.addAction(backup)
        restore = QAction("Restore settings...", self)
        restore.triggered.connect(self._restore_settings)
        settings.addAction(restore)
        folder = QAction("Show settings folder", self)
        folder.setToolTip("Opens the folder holding the settings and their backups in "
                          "the active pane, to copy them to another machine.")
        folder.triggered.connect(
            lambda: self._current_pane().open_tab(os.path.dirname(self._config.path)))
        settings.addAction(folder)
        helping.addSeparator()
        version = QAction(f"Version {__version__}", self)
        version.setEnabled(False)
        helping.addAction(version)
        # Options last, on the bar itself: the one place every setting is.
        self.menuBar().addSeparator()
        self.menuBar().addAction(self._options_action)


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
                initial="", ok_text="Create", filename=False) or ""
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
            initial="\\\\", ok_text="Add", filename=False)
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
        # Asked for by hand, so a login prompt cancelled earlier is offered again.
        self._declined_logins.discard(path.lower())
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
            # 0.43: a failure an account could fix gets a login prompt; any
            # other failure is only reported, as before. A wrong password
            # lands back here and asks again, with the user already filled in.
            from app.ui import credentials

            if credentials.wants_login(why):
                self._ask_login(path, why)
            return
        self._declined_logins.discard(path.lower())
        self.statusBar().showMessage(f"{path} is connected", 6000)
        # The pane on it, if there is one, can stop saying it is not there.
        # Against the *resolved* path: a pane showing `S:\Jobs` is standing in
        # the share that was just reconnected, and comparing what it displays
        # would leave exactly that pane sitting on its error.
        for pane in self._panes:
            if pane.resolved().lower().startswith(path.lower()):
                pane.retry()

    def _on_login_needed(self, share: str, why: str) -> None:
        """A pane's listing was refused for want of an account.

        Asked once per share until somebody acts: a Cancel is remembered so
        that the next refresh of the same folder does not put the prompt
        straight back, and Ctrl+Shift+R (Reconnect) is the way to be asked
        again. Deferred to the event loop because this arrives in the middle
        of a reply being handled, and a modal dialog is a loop of its own.
        """
        if share.lower() in self._declined_logins:
            return
        QTimer.singleShot(0, lambda: self._ask_login(share, why))

    def _ask_login(self, share: str, why: str) -> None:
        from app.ui import credentials

        key = share.lower()
        if self._network is None or key in self._asking_logins:
            return  # both panes on the same share: one prompt, not two
        self._asking_logins.add(key)
        try:
            answer = credentials.ask(self, share=share, reason=why,
                                     user=self._login_users.get(key, ""))
        finally:
            self._asking_logins.discard(key)
        if answer is None:
            self._declined_logins.add(key)
            return
        user, password, save = answer
        self._declined_logins.discard(key)
        self._login_users[key] = user
        self.statusBar().showMessage(f"connecting to {share} as {user}", 10000)
        self._network.reconnect(share, user=user, password=password, save=save)

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

    def _fill_workspaces(self) -> None:
        """The saved workspaces, with Save at the top and Delete at the end."""
        menu = self._workspaces_menu
        # `clear` deletes the actions this menu owns, which is every one made
        # below -- so nothing from an earlier fill outlives it.
        menu.clear()
        save = QAction("Save this layout as...", menu)
        save.setToolTip("Both panes' tabs, and which one is in front on each "
                        "side, under a name. Saving under a name already here "
                        "replaces it.")
        save.triggered.connect(self._save_workspace)
        menu.addAction(save)
        names = self._workspaces.names()
        if names:
            menu.addSeparator()
        for position, name in enumerate(names):
            key = f"\tCtrl+Alt+{position + 1}" if position < 9 else ""
            entry = QAction(f"{name}{key}", menu)
            entry.triggered.connect(
                lambda _checked=False, n=name: self._open_workspace(n))
            menu.addAction(entry)
        if names:
            menu.addSeparator()
            removing = menu.addMenu("Delete")
            for name in names:
                entry = QAction(name, removing)
                entry.triggered.connect(
                    lambda _checked=False, n=name: self._workspaces.remove(n))
                removing.addAction(entry)

    def _save_workspace(self) -> None:
        name = dialogs.ask_name(self, title="Save workspace",
                                label="Name for this layout:", filename=False)
        if name:
            self._workspaces.save(name, *self._panes)
            self.statusBar().showMessage(f"saved workspace {name}", 4000)

    def _open_workspace_at(self, position: int) -> None:
        names = self._workspaces.names()
        if 0 <= position < len(names):
            self._open_workspace(names[position])

    def _open_workspace(self, name: str) -> None:
        """Both panes' tabs replaced by the ones saved under `name`."""
        from app.core.workspaces import side

        entry = self._workspaces.named(name)
        if entry is None:
            return
        for pane, which in zip(self._panes, ("left", "right")):
            tabs, index = side(entry, which)
            if tabs:
                pane.replace_tabs(tabs, index)
        self.statusBar().showMessage(f"workspace {name}", 4000)
        self.focus_active_pane()

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
        menu.addAction(self._search_action)
        menu.addAction(self._duplicates_action)
        menu.addAction(self._map_action)
        menu.addSeparator()
        menu.addAction(self._compare_action)
        menu.addAction(self._sync_action)
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
            # Marks only, not the cursor: see `Context.other_names`.
            other_names=tuple(self._widgets[1 - self._active].marked_names()),
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

    def _synchronize(self) -> None:
        """Preview, then queue, a one-way sync from this pane to the other.

        From the pane with the keyboard to the other one, which is the
        direction F5 copies in; the dialog can swap it. Refused before
        anything is read when the two folders are the same place or one is
        inside the other.
        """
        if self._sync is None:
            return
        from app.core import sync as core_sync
        from app.ui.sync import SyncDialog

        if self._sync_dialog is not None and self._sync_dialog.isVisible():
            self._sync_dialog.raise_()
            self._sync_dialog.activateWindow()
            return
        left, right = (pane.current.path for pane in self._panes)
        problem = core_sync.refusal(left, right)
        if problem:
            self.statusBar().showMessage(f"Cannot synchronize: {problem}", 8000)
            return
        self._sync_dialog = SyncDialog(self._sync, self._transfers, left, right,
                                       from_left=self._active == 0, parent=self)
        self._sync_dialog.show()

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
        self._save_favorite(pane.current.path, pane.display(pane.current.path))

    def _save_favorite(self, path: str, shown: str = "") -> None:
        """Ask for a name and save `path`. The rail's network rows come in
        here directly, with the location's own path rather than the pane's.

        `filename=False`: a favourite's name is a label, not a name on disk.
        With the file-name rule a share root could not be saved at all, since
        the name offered for `\\\\tsclient\\C` was that path, backslashes
        included, and the OK button stayed grey.
        """
        if self._favorites is None or not path:
            return
        known = self._favorites.index_of(path)
        name = dialogs.ask_name(
            self, title="Add favorite",
            label=("Rename this favorite" if known >= 0
                   else f"Save {shown or path} as"),
            initial=(self._favorites.entries[known].name if known >= 0
                     else self._favorites.suggested_name(path)),
            ok_text="Save", filename=False,
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

    def _set_motion(self, checked: bool) -> None:
        self._config.set("look.motion", bool(checked))
        self._splitter.set_motion(bool(checked))
        self._transfer_bar.set_motion(bool(checked))
        for widget in self._widgets:
            # Never on glass: a pulse redraws through the see-through window
            # ten times a second for as long as a slow folder takes.
            widget.set_placeholder_motion(bool(checked) and self._backdrop != "glass")

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

    # ---------------------------------------------------------------- 0.36 peek

    def _peek_card(self):
        from app.ui.peek import PeekCard

        if self._peek is None:
            self._peek = PeekCard(self)
            self._peek.apply_tokens(self._tokens)
            self._peek.closed.connect(self.focus_active_pane)
            self._peek.stepRequested.connect(self._step_peek)
            self._peek.openRequested.connect(
                lambda: self._current_widget()._open_current())
            self._peek.viewRequested.connect(
                lambda: self._current_widget().view_current())
            if self._previews is not None:
                self._previews.ready.connect(self._on_peek_ready)
                self._previews.unavailable.connect(
                    lambda path, why: self._peek.problem(path, why))
        return self._peek

    def _open_peek(self) -> None:
        """Space on a file, with Space set to peek."""
        widget = self._current_widget()
        target = widget.peek_target()
        if target is None or self._previews is None:
            return
        card = self._peek_card()
        card.set_motion(bool(self._config.get("preview.peek_motion"))
                        and bool(self._config.get("look.motion")))
        row = widget.cursor_rect()
        origin = row.translated(widget.mapTo(self, row.topLeft()) - row.topLeft()) \
            if row.isValid() else row
        self._ask_peek(target)
        card.open_from(origin)

    def _ask_peek(self, target) -> None:
        from app.io.protocol import PREVIEW_BOX, PREVIEW_TEXT_BYTES

        path, entry = target
        self._peek.waiting(path, entry.name)
        self._previews.ask(path, box=PREVIEW_BOX, text_bytes=PREVIEW_TEXT_BYTES,
                           mtime=entry.mtime, size=entry.size, delay_ms=0)

    def _step_peek(self, step: int) -> None:
        widget = self._current_widget()
        widget.step_cursor(step)
        target = widget.peek_target()
        if target is not None:
            self._ask_peek(target)
            return
        row = widget.current_row()
        entry = self._current_pane().current.model.entry(row) if row >= 0 else None
        self._peek.not_a_file(entry.name if entry is not None else "..")

    def _on_peek_ready(self, path: str, answer) -> None:
        if self._peek is not None and self._peek.isVisible():
            self._peek.show_answer(path, paths.leaf(path), answer)

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
            action.triggered.connect(
                lambda _checked=False, k=key, n=name: self.apply_setting(k, n))
            self._bound_choices.setdefault(key, {})[name] = action
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
                    lambda _checked=False, k=key, n=name: self.apply_setting(k, n))
                self._bound_choices.setdefault(key, {})[name] = action
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
        self.apply_setting(key, value)

    # ---------------------------------------------------------------- options

    def open_options(self, section: str = "") -> None:
        """The Options dialog, built once and brought forward after that.

        Kept rather than rebuilt so it stays on the page it was left on, and
        not modal so the window behind it can be watched while a switch is
        flipped -- which is most of the reason for applying at once.
        """
        from app.ui.options import OptionsDialog

        if self._options is None:
            self._options = OptionsDialog(self._config, self)
            self._options.apply_tokens(self._tokens)
            self._options.changed.connect(self.apply_setting)
        if section:
            self._options.show_section(section)
        self._options.show()
        self._options.raise_()
        self._options.activateWindow()

    def _appliers(self) -> dict:
        """What each setting has to do to the window when it changes.

        Only the ones that change something already on screen. The rest --
        a deadline, a threshold, whether to check for updates -- are read
        where they are used, so setting them is the whole of applying them.
        """
        panes = self._panes
        widgets = self._widgets
        return {
            "theme": lambda _v: self._resolve_accent(),
            "theme.follow": lambda _v: self._resolve_accent(),
            "theme.light": lambda _v: self._resolve_accent(),
            "theme.dark": lambda _v: self._resolve_accent(),
            "theme.day_from": lambda _v: self._resolve_accent(),
            "theme.night_from": lambda _v: self._resolve_accent(),
            "look.font": lambda _v: self.apply_theme(),
            "look.corners": lambda _v: self.apply_theme(),
            "accent.right": lambda _v: self.apply_theme(),
            "accent.source": lambda _v: self._resolve_accent(),
            "look.pane_glow": lambda v: self._splitter.set_glow_enabled(bool(v)),
            "look.blueprint_grid": lambda _v: self._apply_grid(self._tokens),
            "accent": lambda _v: self.apply_theme(),
            "density": lambda _v: self.apply_theme(),
            "look.motion": self._set_motion,
            "pane.header": self._set_header,
            "icons.style": lambda v: self._set_badges(v == "badges"),
            "icons.shell": self._set_shell_icons,
            "icons.overlays": self._set_overlays,
            "icons.per_file": self._set_file_icons,
            "rail.shown": self._toggle_rail,
            "favorites.bar": self._set_favorites_bar,
            "preview.thumbnails": self._set_thumbnails,
            "preview.shell": self._set_shell_previews,
            "preview.thumb_size": lambda v: self._set_cell_size(int(v)),
            "flat.layout": self._set_flat_layout,
            "listing.hidden": lambda _v: [pane.apply_rules() for pane in panes],
            "listing.system": lambda _v: [pane.apply_rules() for pane in panes],
            "listing.folder_bars": lambda _v: self._refilter(),
            "listing.recency": lambda v: [w.set_row_style(recency=v)
                                          for w in widgets],
            "listing.fade_days": lambda v: [w.set_row_style(fade_days=v)
                                            for w in widgets],
            "listing.scrollmap": lambda v: [w.set_scrollmap(v) for w in widgets],
            "listing.column_edges": lambda v: [w.set_row_style(edges=v)
                                               for w in widgets],
            "listing.size_bar": lambda v: [w.set_row_style(size_bar=v)
                                           for w in widgets],
            "listing.stripes": lambda v: [w.set_row_style(stripes=v)
                                          for w in widgets],
            "listing.shell_drag": lambda v: [w.set_shell_drag(v) for w in widgets],
            "listing.date_chips": lambda v: [w.set_row_style(date_chips=v)
                                             for w in widgets],
            "listing.location_stripe": lambda _v: [w.update_location()
                                                   for w in widgets],
            "places.live": lambda _v: [w.update_location() for w in widgets],
            "listing.selection_pill": lambda _v: [w.update_selection_pill()
                                                  for w in widgets],
            "listing.placeholders": lambda _v: [w.update_placeholders()
                                                for w in widgets],
            "transfers.taskbar": lambda _v: self._update_taskbar(),
            "rail.capacity": lambda _v: self._rebuild_rail(),
            "network.ping": lambda _v: self._health.configure()
            if self._health is not None else None,
            "network.ping_seconds": lambda _v: self._health.configure()
            if self._health is not None else None,
            "network.amber_ms": lambda _v: self._rebuild_rail(),
            "transfers.speedline": lambda v: self._transfer_bar.set_speedline(bool(v)),
            "basket.enabled": lambda v: self._basket_tray.set_enabled(bool(v)),
            "labels.shown": lambda _v: self._refilter(),
            "git.badges": lambda _v: (self._git.forget() if self._git is not None
                                      else None, [p.ask_git() for p in panes],
                                      self._refilter()),
            "preview.logix": lambda _v: self._previews.clear()
            if self._previews is not None else None,
        }

    def _apply_grid(self, tokens: dict) -> None:
        """The drafting grid, drawn only for Blueprint and only if wanted."""
        from app.ui.rows import parse_colour

        if tokens.get("theme_name") == "blueprint" \
                and bool(self._config.get("look.blueprint_grid")):
            self._drafting.set_colours(parse_colour(tokens.get("grid_minor")),
                                       parse_colour(tokens.get("grid_major")))
        else:
            self._drafting.set_colours(None)

    def _resolve_accent(self) -> None:
        """Work the accent out again from its source, then re-render.

        On a theme change too, because a colour made readable against a dark
        backdrop may not be against a light one. A named source is immediate;
        Windows' accent is a registry read and answers at once; the wallpaper
        is a picture read by a worker and answers when it arrives, until when
        the named accent stands in.
        """
        source = str(self._config.get("accent.source"))
        if source == "named" or self._accent_source is None:
            self._accent_rgb = None
            self.apply_theme()
            return
        # Now, with whatever accent is in hand, so a theme change shows at
        # once rather than when a wallpaper arrives.
        self.apply_theme()
        backdrop = sheet.qss.unhex(sheet.tokens(self._theme_shown)["bg_0"])
        self._accent_source.resolve(backdrop)

    def _on_accent_found(self, colour, why: str) -> None:
        self._accent_rgb = tuple(colour) if colour is not None else None
        if why:
            self.statusBar().showMessage(f"accent: {why}; using the named one", 8000)
        self.apply_theme()

    def _refilter(self) -> None:
        for pane in self._panes:
            pane.apply_rules()
        for widget in self._widgets:
            widget.update_rows()

    def apply_setting(self, key: str, value) -> None:
        """Set one setting and make the window agree with it, now.

        The one entry point for a change from the Options dialog and from the
        View menu, so the two cannot drift: both end here, the menu's tick and
        the dialog's control are both moved to match, and neither has to know
        the other exists.
        """
        self._config.set(key, value)
        applier = self._appliers().get(key)
        if applier is not None:
            applier(value)
        action = self._bound.get(key)
        if action is not None:
            on = (value == "badges") if key == "icons.style" else bool(value)
            if action.isChecked() != on:
                action.setChecked(on)
        for known, entry in self._bound_choices.get(key, {}).items():
            if known == value and not entry.isChecked():
                entry.setChecked(True)
        if self._options is not None:
            self._options.sync(key)

    @property
    def _paint_backdrop(self) -> str:
        """What the sheet is built for: "frosted" when the panes themselves
        are to let the desktop through, otherwise the backdrop as decided."""
        if self._backdrop == "glass" and getattr(self, "_glass_look", "") == "frosted":
            return "frosted"
        return self._backdrop

    def _check_theme(self) -> None:
        """0.48: the timer's question -- has Windows or the clock moved the
        theme? -- answered without re-rendering anything when it has not."""
        if str(self._config.get("theme.follow")) == themeswitch.FOLLOW_OFF:
            return
        if themeswitch.current(self._config) != self._theme_shown:
            self._resolve_accent()

    def _right_tokens(self, tokens: dict) -> dict:
        """0.48: the right-hand pane's tokens -- the same render with another
        accent when `accent.right` names one, otherwise the window's own."""
        right = str(self._config.get("accent.right"))
        if right == "same" or right not in ACCENT_LABELS:
            return tokens
        return sheet.tokens(self._theme_shown, right, self._config.get("density"),
                            self._paint_backdrop, None, self._config.get("look.font"),
                            self._config.get("look.corners"))

    def apply_theme(self) -> None:
        self._theme_shown = themeswitch.current(self._config)
        tokens = sheet.apply(
            QApplication.instance(),
            theme=self._theme_shown,
            accent=self._config.get("accent"),
            density=self._config.get("density"),
            backdrop=self._paint_backdrop,
            accent_rgb=self._accent_rgb,
            font=self._config.get("look.font"),
            corners=self._config.get("look.corners"),
        )
        if self._titlebar is not None:
            self._titlebar.apply_tokens(tokens)
        self._hints.apply_tokens(tokens)
        self._palette.apply_tokens(tokens)
        self._transfer_bar.apply_tokens(tokens)
        self._splitter.set_glow_colour(tokens["accent"])
        self._apply_grid(tokens)
        if self._frame is not None:
            # Mica takes its tint from the window's dark-mode flag, so a switch
            # to a light theme has to reach Windows as well as the sheet.
            self._frame.set_dark(sum(sheet.qss.unhex(tokens["bg_0"])) < 382)
        metrics = sheet.metrics(self._config.get("density"))
        self._pane_tokens = [tokens, self._right_tokens(tokens)]
        for widget, own in zip(self._widgets, self._pane_tokens):
            widget.apply_metrics(metrics)
            widget.apply_tokens(own)
            widget.set_own_accent(None if own is tokens else own)
        self._splitter.set_glow_colour(
            self._pane_tokens[getattr(self, "_active", 0)]["accent"])
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
        if self._options is not None:
            self._options.apply_tokens(tokens)
        if self._peek is not None:
            self._peek.apply_tokens(tokens)
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
        # 0.41: said now rather than after the prompt. The engine refuses
        # these as well; this is only so nobody fills in a dialog for nothing.
        if transfer is JobKind.MOVE and pane.in_archive:
            pane.say("inside an archive is read-only: F5 copies files out", "bad")
            return
        if other.in_archive:
            pane.say("the other pane is inside an archive, which is read-only here", "bad")
            return
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

    def _fill_basket(self, full_paths: list) -> None:
        added = self._basket.add(full_paths)
        total = len(self._basket)
        self.statusBar().showMessage(
            f"{added:,} added to the basket, {total:,} in it" if added
            else "already in the basket", 4000)

    def _empty_basket(self, *, move: bool) -> None:
        """Copy here / Move here: the basket into the active pane's folder,
        through the prompt F5 puts up. Emptied only once the job is queued."""
        if not len(self._basket):
            return
        pane = self._current_pane()
        transfer = JobKind.MOVE if move else JobKind.COPY
        prompt = TransferPrompt(transfer, self._basket.names(), pane.display(), self)
        if prompt.exec() != QueueDialog.Accepted:
            return
        destination = pane.as_path(prompt.destination())
        if not destination:
            return
        sources = self._basket.paths
        if move:
            self._transfers.move(sources, destination)
        else:
            self._transfers.copy(sources, destination)
        self._basket.clear()

    def _on_drop_requested(self, widget: PaneWidget, sources: list,
                           destination: str, move: bool) -> None:
        """Rows dropped onto a pane: the same prompt F5 and F6 put up, with
        the destination filled in from where the drop landed.

        The prompt is the rule rather than a courtesy -- nothing in this
        application writes a file that no dialog has confirmed, and a drag is
        the gesture most likely to be let go a folder early. Enter accepts it,
        so a deliberate drop costs one key.
        """
        if not sources or not destination or self._transfers is None:
            return
        pane = self._panes[self._widgets.index(widget)]
        transfer = JobKind.MOVE if move else JobKind.COPY
        # 0.41: the same courtesy F6 gets. The engine is the rule.
        from app.io.archive import inside as in_archive, split as archive_split

        if archive_split(destination) is not None:
            pane.say("that is inside an archive, which is read-only here", "bad")
            return
        if move and any(in_archive(source) for source in sources):
            pane.say("files inside an archive can be copied out, not moved", "bad")
            return
        names = [paths.leaf(source) for source in sources]
        prompt = TransferPrompt(transfer, names, pane.display(destination), self)
        if prompt.exec() != QueueDialog.Accepted:
            return
        target = pane.as_path(prompt.destination())
        if not target:
            return
        if transfer is JobKind.COPY:
            self._transfers.copy(list(sources), target)
        else:
            self._transfers.move(list(sources), target)

    def _search(self, *, duplicates: bool) -> None:
        """0.42: Alt+F7. The dialog, then a results tab in the active pane."""
        from app.ui import search as search_dialog

        pane = self._current_pane()
        answer = search_dialog.ask(self, folder=pane.display(),
                                   last=self._config.get("search.last"),
                                   duplicates=duplicates)
        if answer is None:
            return
        folder, spec, dup, remembered = answer
        self._config.set("search.last", remembered)
        target = pane.as_path(folder)
        if target and pane.search(target, spec, duplicates=dup):
            self._current_widget().focus_listing()

    def _split_or_join(self, *, joining: bool) -> None:
        """0.44: the file under the cursor, into parts or from them, as a job."""
        from app.ui.split import SplitDialog

        pane, widget = self._current_pane(), self._current_widget()
        other = self._panes[1 - self._active]
        row = widget.current_row()
        entry = pane.current.model.entry(row) if row >= 0 else None
        if entry is None or entry.is_dir:
            pane.say("put the cursor on a file first", "bad")
            return
        if joining and not entry.name.endswith((".001", ".0001")):
            pane.say("run Join on the first part, the one ending in .001", "bad")
            return
        if self._transfers is None:
            return
        dialog = SplitDialog(self, name=entry.name, size=int(entry.size),
                             destination=other.display(), joining=joining)
        if dialog.exec() != SplitDialog.Accepted:
            return
        target = pane.as_path(dialog.destination())
        source = pane.row_path(pane.current.model.row_of(entry.name))
        if not target or source is None:
            return
        if other.in_archive and target == other.current.path:
            pane.say("the other pane is inside an archive, which is read-only here", "bad")
            return
        if joining:
            self._transfers.join(source, target)
        else:
            self._transfers.split(source, target, dialog.part_size())

    def _new_link(self) -> None:
        """0.45: a link in the other pane's folder to the item under the cursor
        here -- Double Commander's direction, and the one that needs no typing."""
        from app.ui.links import LinkDialog

        pane, widget = self._current_pane(), self._current_widget()
        other = self._panes[1 - self._active]
        row = widget.current_row()
        entry = pane.current.model.entry(row) if row >= 0 else None
        target = pane.row_path(row) if entry is not None else pane.current.path
        if other.in_archive or pane.in_archive:
            pane.say("links cannot be made into or out of an archive", "bad")
            return
        dialog = LinkDialog(self, folder=other.display(), target=pane.resolved(target),
                            target_is_dir=entry is None or entry.is_dir,
                            name=paths.leaf(target) if entry is not None else paths.leaf(target))
        if dialog.exec() != LinkDialog.Accepted:
            return
        name, where, kind = dialog.answer()
        if other.name_taken(name):
            pane.say(f"{name} already exists in the other pane", "bad")
            return
        pane.make_link(other.current.path, name, pane.as_path(where), kind)

    def _sync_undo(self) -> None:
        action = self._undo.peek()
        self._undo_action.setText(f"Undo {action.label}\tCtrl+Z" if action else "Undo\tCtrl+Z")
        self._undo_action.setEnabled(action is not None)

    def _undo_last(self) -> None:
        """0.44: take back the last thing done, behind a confirmation.

        Every undo is an ordinary operation -- a rename plan, a recycle, a
        move -- so it goes through the same worker or queue, and a folder that
        changed meanwhile fails the way it would have anyway, with a reason.
        """
        from app.core import undo
        from app.core.renamer import Preview, Row, plan

        action = self._undo.peek()
        pane = self._current_pane()
        if action is None:
            pane.say("nothing to undo", "idle")
            return
        if action.kind == undo.RENAME:
            back = [(new, old) for old, new in action.moves]
            if not dialogs.confirm(self, title="Undo", action="Rename back",
                                   text=f"Undo the {action.label} in {action.folder}?",
                                   names=[f"{new}  ->  {old}" for new, old in back]):
                return
            steps = plan(Preview(rows=[Row(new, old) for new, old in back]))
            self._undo.pop()
            pane.rename_many(steps, back, folder=action.folder, record=False)
            return
        if self._transfers is None:
            return
        if action.kind in (undo.MKDIR, undo.COPY):
            targets = ([paths.join(action.folder, action.name)] if action.kind == undo.MKDIR
                       else list(action.targets))
            if not dialogs.confirm_delete(self, names=[undo.leaf(t) for t in targets],
                                          folder=undo.parent(targets[0]), permanent=False):
                return
            self._undo.pop()
            self._undo_jobs.add(self._transfers.recycle(targets))
            return
        if action.kind == undo.MOVE:
            now = [where for _came, where in action.moves]
            homes = [undo.parent(came) for came, _where in action.moves]
            if not dialogs.confirm(self, title="Undo", action="Move back",
                                   text=f"Undo the {action.label}: move "
                                        f"{'it' if len(now) == 1 else 'them'} back to "
                                        f"{homes[0]}{' and elsewhere' if len(set(homes)) > 1 else ''}?",
                                   names=[undo.leaf(where) for where in now]):
                return
            self._undo.pop()
            self._undo_jobs.add(self._transfers.move_into(now, homes[0], homes))

    def _open_map(self) -> None:
        """0.43: the folder map for the folder the active pane is in."""
        from app.ui.foldermap import FolderMapWindow

        if self._folder_map is None:
            return
        pane = self._current_pane()
        folder = pane.current.path
        if self._map_window is not None:
            self._map_window.close()
        window = FolderMapWindow(
            self, folder_label=pane.display(folder),
            on_go=lambda where, zoomed, p=pane, f=folder: self._map_go(p, f, where),
            on_refresh=lambda f=folder: self._folder_map.start(f))
        window.set_tokens(self._tokens)
        self._folder_map.progress.connect(window.walking)
        self._folder_map.ready.connect(window.show_tree)
        self._folder_map.failed.connect(window.failed)
        window.finished.connect(lambda _code, w=window: self._map_closed(w))
        self._map_window = window
        window.show()
        self._folder_map.start(folder)

    def _map_closed(self, window) -> None:
        for signal, slot in ((self._folder_map.progress, window.walking),
                             (self._folder_map.ready, window.show_tree),
                             (self._folder_map.failed, window.failed)):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        if self._map_window is window:
            self._folder_map.cancel()
            self._map_window = None

    def _map_go(self, pane, folder: str, where: tuple) -> None:
        """A double click, or Go there: the pane goes to that folder, or to the
        folder a file is in with the file under the cursor."""
        if not where:
            pane.navigate(folder)
            return
        node = None
        if self._map_window is not None and self._map_window._root is not None:  # noqa: SLF001
            node = self._map_window._root.find(list(where))  # noqa: SLF001
        target = folder
        for part in (where if node is None or node.is_dir else where[:-1]):
            target = paths.join(target, part)
        if node is not None and not node.is_dir:
            pane.current.reveal_name = where[-1]
        pane.navigate(target)

    def _on_extract_requested(self, widget: PaneWidget, name: str) -> None:
        """0.41: all of an archive, into a folder named for it in the other
        pane's folder -- through the prompt, like every other write."""
        if self._transfers is None:
            return
        index = self._widgets.index(widget)
        pane, other = self._panes[index], self._panes[1 - index]
        row = pane.current.model.row_of(name)
        folder = pane.archive_extract_name(row) if row >= 0 else None
        if folder is None:
            return
        if other.in_archive:
            pane.say("the other pane is inside an archive, which is read-only here", "bad")
            return
        prompt = TransferPrompt(JobKind.COPY, [f"{name}  ->  {folder}"],
                                other.display(), self)
        if prompt.exec() != QueueDialog.Accepted:
            return
        target = pane.as_path(prompt.destination())
        row = pane.current.model.row_of(name)          # found again after the dialog
        if target and row >= 0:
            pane.extract(row, target)

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

    def queue_from_outside(self, request) -> None:
        """0.46: jobs File Compare asked the queue to run.

        `request` is a `handoff.Request` already read and checked off the UI
        thread, or the `handoff.Refused` saying why it was not. Somebody saw
        these jobs in File Compare's preview before they were written, so
        they go into the queue as they are, as ordinary jobs; the queue panel
        opens so the person who pressed the button watches them run.
        """
        from app.core import handoff

        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()
        if isinstance(request, handoff.Refused) or not isinstance(request, handoff.Request):
            self.statusBar().showMessage(f"A request for the queue was refused: {request}",
                                         15000)
            return
        if request.removals or request.replaces:
            # 0.46.1: this is the application that removes and overwrites, so
            # it asks itself, once, naming the folder -- File Compare's
            # preview was the plan; this is the go-ahead.
            from app.core import sync as core_sync

            lines = []
            if request.removals:
                where = ("permanently -- it is a network folder, where Windows skips "
                         "the Recycle Bin" if core_sync.on_a_share(request.target_root)
                         else "to the Recycle Bin")
                lines.append(f"remove {request.removals:,} item"
                             f"{'s' if request.removals != 1 else ''} {where}")
            if request.replaces:
                lines.append(f"copy {request.copies:,} item"
                             f"{'s' if request.copies != 1 else ''}, replacing what is "
                             "there whether or not it is newer")
            names = [path for job in request.jobs if job.kind == handoff.RECYCLE
                     for path in job.sources]
            if not dialogs.confirm(
                    self, title=f"{request.sender or 'Another application'} sync",
                    text=(f"{request.sender or 'Another application'} asks to "
                          + " and ".join(lines) + f", in {request.target_root}."),
                    action="Queue them", names=names or None):
                self._answer_later(request.path, handoff.refused("declined in File Manager"))
                self.statusBar().showMessage("The sync was not queued.", 8000)
                return
        ids: set[int] = set()
        for job in request.jobs:
            if job.kind == handoff.COPY:
                job_id = self._transfers.copy_into(job.sources, job.destination,
                                                   job.into, conflict=job.conflict)
            else:
                job_id = self._transfers.recycle(job.sources)
            ids.add(job_id)
            # An undo of a job another application planned is an undo nobody
            # here decided on; the job is recorded as not undoable.
            self._undo_jobs.add(job_id)
        key = id(request)
        self._handoff_open[key] = ids
        self._handoff_done[key] = []
        for job_id in ids:
            self._handoff_jobs[job_id] = request
        self.statusBar().showMessage(request.summary(), 10000)
        self._answer_later(request.path, None)
        self._show_queue()

    def _answer_later(self, path: str, result: dict | None) -> None:
        """Write an answer beside a request, off this thread: the result
        when there is one, else the mark that says it was queued."""
        from app.core import handoff
        import threading

        if result is None:
            target, args = handoff.write_taken, (path,)
        else:
            target, args = handoff.write_result, (path, result)
        threading.Thread(target=target, args=args, name="handoff-answer",
                         daemon=True).start()

    def _handoff_finished(self, job) -> None:
        request = self._handoff_jobs.pop(job.id, None)
        if request is None:
            return
        key = id(request)
        self._handoff_open.get(key, set()).discard(job.id)
        self._handoff_done.setdefault(key, []).append(job)
        if self._handoff_open.get(key):
            return
        from app.core import handoff
        import threading

        states = self._handoff_done.pop(key, [])
        self._handoff_open.pop(key, None)
        result = handoff.outcome(request, states)
        # A file, so not on this thread -- however small and local it is.
        threading.Thread(target=handoff.write_result, args=(request.path, result),
                         name="handoff-result", daemon=True).start()

    def _on_transfer_finished(self, job) -> None:
        """Re-list the folders a job touched, say how it went, and offer to
        retry what Windows refused.

        Only those folders: a pane showing something else has no reason to pay
        for a listing because a copy finished somewhere on the disk. The job
        works out which they are -- the destination, and where the sources came
        from -- so a delete refreshes the folder it emptied without this method
        having to know that a delete has no destination.
        """
        self._handoff_finished(job)
        # 0.44: a job that finished whole can be undone; one an undo started
        # cannot be undone again.
        if job.id in self._undo_jobs:
            self._undo_jobs.discard(job.id)
        else:
            from app.core import undo
            self._undo.push(undo.for_job(job))
        touched = job.folders
        for pane in self._panes:
            if pane.current.path in touched and not pane.busy:
                pane.refresh()
        self.statusBar().showMessage(_outcome(job), 8000)
        if job.problems:
            self._transfer_bar.refresh()
        if job.denied:
            self._offer_elevated_delete(job)
        if getattr(job, "holders", None):
            self._offer_holder(*job.holders[0])
        self._notify(job)

    def _offer_holder(self, pid: int, name: str) -> None:
        """A failure named the program holding a file: offer to go to it."""
        self._holder_pid = int(pid)
        self._holder_button.setText(f"Switch to {name}")
        self._holder_button.setToolTip(
            f"{name} (PID {pid}) had a file open when the job ran. Close it "
            "there, then Ctrl+J to retry what failed.")
        self._holder_button.show()
        self._holder_timer.start()

    def _switch_to_holder(self) -> None:
        from app.core import programs

        self._holder_button.hide()
        if not programs.bring_forward(self._holder_pid):
            self.statusBar().showMessage(
                "that program has no window to bring forward, or has closed", 6000)

    def _notify(self, job) -> None:
        """Say a long job has ended, when this window is not the one in front.

        Two signals, because either alone misses a case: the flashing taskbar
        button is there when somebody comes back to the desk, and the
        notification reaches them while they are working in something else.
        The tray icon exists only to carry the notification -- Windows shows
        one through an icon or not at all -- and is hidden again after it.
        """
        if not worth_notifying(job, window_active=self.isActiveWindow(),
                               threshold=float(self._config.get("notify.after"))):
            return
        QApplication.alert(self)
        if not (QSystemTrayIcon.isSystemTrayAvailable()
                and QSystemTrayIcon.supportsMessages()):
            return
        if self._tray is None:
            self._tray = QSystemTrayIcon(self.windowIcon(), self)
            self._tray.setToolTip("File Manager")
            self._tray.messageClicked.connect(self._come_forward)
            self._tray.activated.connect(lambda _reason: self._come_forward())
            self._tray_timer = QTimer(self)
            self._tray_timer.setSingleShot(True)
            self._tray_timer.setInterval(20000)
            self._tray_timer.timeout.connect(self._tray.hide)
        what = {JobKind.COPY: "Copy", JobKind.MOVE: "Move",
                JobKind.RECYCLE: "Recycle", JobKind.ERASE: "Erase",
                JobKind.SPLIT: "Split", JobKind.JOIN: "Join"}.get(job.kind, "Job")
        failed = bool(job.failed or job.refused)
        title = f"{what} finished" + (" with problems" if failed else "")
        icon = QSystemTrayIcon.Warning if failed else QSystemTrayIcon.Information
        self._tray.show()
        self._tray.showMessage(title, _outcome(job), icon, 10000)
        self._tray_timer.start()

    def _come_forward(self) -> None:
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()
        if self._tray is not None:
            self._tray.hide()

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

    def _show_history(self) -> None:
        if self._history_dialog is None:
            self._history_dialog = HistoryDialog(self._transfers, self)
            self._history_dialog.opened.connect(
                lambda folder: self._current_pane().navigate(folder))
        self._history_dialog.show()
        self._history_dialog.raise_()
        self._history_dialog.activateWindow()

    def _show_queue(self) -> None:
        if self._queue_dialog is None:
            self._queue_dialog = QueueDialog(self._transfers, self)
            self._queue_dialog.apply_tokens(self._tokens)
            self._queue_dialog.history_requested.connect(self._show_history)
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

    def open_from_outside(self, folder: str) -> None:
        """0.41: a folder sent from Explorer, or from a second start.

        Opened in a new tab in the pane that has the keyboard, so nothing on
        screen is lost; an empty message only brings the window forward. The
        folder is not checked here -- that is a filesystem question -- and a
        path that is not there fails in its tab the way a typed one does.
        """
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()
        folder = (folder or "").strip().strip('"')
        if folder:
            self._current_pane().open_tab(folder)

    def _copy_unc_path(self) -> None:
        """0.40: the same path, as \\\\server\\share\\... whatever the pane shows.

        A mapped letter means nothing to somebody without that mapping, and
        pasting `S:\\Jobs\\...` into an email is how a link arrives dead. The
        letter is looked up in this session's own connection table, which
        touches no server. A path on a local disk has no UNC form and is
        copied as it is.
        """
        pane = self._current_pane()
        path = pane.row_path(self._current_widget().current_row()) or pane.current.path
        text = pane.resolved(path)
        QApplication.clipboard().setText(text)
        self.statusBar().showMessage(f"copied {text}", 4000)

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

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt naming
        if event.type() == QEvent.MouseButtonPress and isinstance(watched, QWidget):
            self._follow_press(watched)
        return super().eventFilter(watched, event)

    def _follow_press(self, target: QWidget) -> None:
        """A press inside a pane makes it the active pane, keyboard and all.

        The pane becomes active at once, so a tab clicked in the inactive pane
        is the tab F5 then copies from. The keyboard follows a moment later
        and only if it is still somewhere else: a press on the listing or the
        path field focuses that widget itself, and taking the focus from it
        would put the caret somewhere the user did not click.

        `isAncestorOf` stops at a window boundary, so a menu or a dialog a
        pane opened is not "inside" it, and the rail is not in a pane at all
        -- it still goes to whichever pane already had the keyboard.
        """
        for index, widget in enumerate(self._widgets):
            if widget is target or widget.isAncestorOf(target):
                if index != self._active:
                    self._set_active(index)
                QTimer.singleShot(0, self, lambda w=widget: self._keyboard_into(w))
                return

    def _keyboard_into(self, widget: PaneWidget) -> None:
        app = QApplication.instance()
        if app.activeModalWidget() is not None:
            return
        if app.activePopupWidget() is not None:
            # A right click on the tab strip opens its menu on the press, and
            # "New tab" from it should land the keyboard in the new tab. Look
            # again once the menu has gone; a press elsewhere in the meantime
            # makes the other pane active and the check below then declines.
            QTimer.singleShot(150, self, lambda: self._keyboard_into(widget))
            return
        focus = app.focusWidget()
        if focus is not None and (focus is widget or widget.isAncestorOf(focus)):
            return
        if self._widgets[self._active] is widget:
            widget.focus_listing()

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
        tokens = getattr(self, "_pane_tokens", None)
        if tokens is not None:
            self._splitter.set_glow_colour(tokens[index]["accent"])
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

    def focus_active_pane(self) -> None:
        self._current_widget().focus_listing()

    def palette_sources(self) -> list:
        """Everything Ctrl+K can reach, in the order ties are broken."""
        items: list = []

        def walk(menu, trail: str) -> None:
            for action in menu.actions():
                if action.isSeparator() or not action.isVisible():
                    continue
                label, key = core_palette.clean_label(action.text())
                if action.menu() is not None:
                    walk(action.menu(), f"{trail} > {label}" if trail else label)
                    continue
                if not label or not action.isEnabled() or action is self._palette_action:
                    continue
                shortcut = action.shortcut().toString() or key
                items.append(core_palette.Item(
                    "command", label, detail=trail, shortcut=shortcut, target=action,
                    checked=action.isChecked() if action.isCheckable() else None))

        walk(self.menuBar(), "")
        walk(self._switches, "Settings")
        pane = self._current_pane()
        if self._favorites is not None:
            for entry in self._favorites.entries:
                items.append(core_palette.Item("favorite", entry.name,
                                               detail=pane.display(entry.path),
                                               target=entry.path))
        histories = [list(tab.history[:tab.position + 1])
                     for side in self._panes for tab in side.tabs]
        on_screen = {side.current.path for side in self._panes}
        for path in core_palette.unique_recent(histories, on_screen):
            shown = pane.display(path)
            items.append(core_palette.Item("recent", paths.leaf(shown) or shown,
                                           detail=shown, target=path))
        folder = pane.current.path
        for name in pane.current.model.folder_names()[:500]:
            items.append(core_palette.Item("here", name, detail=pane.display(folder),
                                           target=paths.join(folder, name)))
        return items

    def _open_palette(self) -> None:
        self._palette.open(self.palette_sources())

    def _on_palette_chosen(self, item) -> None:
        if item.kind == "command":
            item.target.trigger()
            return
        pane = self._current_pane()
        pane.navigate(str(item.target))
        self._current_widget().focus_listing()

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
        """Every menu the bar used to show, as one menu under the mark, with
        Options after them as it is on the bar."""
        menu = QMenu(self)
        for action in self.menuBar().actions():
            if action.menu() is not None:
                menu.addMenu(action.menu())
            elif action.isSeparator():
                menu.addSeparator()
            else:
                menu.addAction(action)
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

    def _watch_glass(self) -> None:
        """0.50.1: give up glass on a machine where it makes the window slow.

        A timer that should fire every `GLASS_BEAT_MS` measures how late it
        actually fires. Late by more than `GLASS_LATE_S`, `GLASS_STRIKES`
        times inside `GLASS_WINDOW_S`, and the event loop is spending its time
        drawing the see-through window rather than answering the user. Nothing
        in this application blocks the UI thread on a volume, so on glass this
        is the drawing. The backdrop is set to solid for the next start --
        changing it live would mean recreating the native window -- and the
        status bar says so and how to put it back.
        """
        import time as _time

        now = _time.monotonic()
        late = now - self._glass_beat - GLASS_BEAT_MS / 1000.0
        self._glass_beat = now
        if late < GLASS_LATE_S:
            return
        self._glass_slow = [t for t in self._glass_slow if now - t < GLASS_WINDOW_S]
        self._glass_slow.append(now)
        if len(self._glass_slow) < GLASS_STRIKES:
            return
        self._glass_timer.stop()
        if self._config.get("window.backdrop") != "solid":
            self._config.set("window.backdrop", "solid")
            self._config.save()
        self.statusBar().showMessage(
            "the glass backdrop is making this window slow on this PC, so it "
            "will be solid from the next start (Options > Look > Glass backdrop "
            "to turn it back on)", 30000)

    def _watch_for_remote(self) -> None:
        """Notice a remote session that started after this window did.

        `core.backdrop` decides between glass and solid once, at startup, and
        a remote session is one of the three machines where glass is the wrong
        answer. Remoting *into* a machine where the application is already
        running changes that answer underneath it -- and it matters more than
        a look: a translucent window is drawn by sending a picture of the
        whole window, which over a remote connection at full size is slow
        enough to read as the window having stopped. The setting cannot be
        changed without a restart, so this says so rather than pretending to
        fix it. Once, and only while glass is actually on.
        """
        if self._said_remote or self._backdrop != "glass":
            return
        if not winframe.probe().remote:
            return
        self._said_remote = True
        wanted = self._config.get("window.backdrop")
        tail = ("it will use the solid one next time this starts"
                if wanted not in ("glass",) else
                "View > Backdrop > Solid, then restart, is faster here")
        self.statusBar().showMessage(
            f"this is a remote session and the glass backdrop is slow in one; {tail}",
            20000)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """On glass, a floor that is almost but not quite transparent.

        See `GLASS_FLOOR`: without it a click on any part of the window that
        nothing had painted went to the program behind. Children paint on top
        of this, so it shows only where they leave the backdrop bare.
        """
        if self._backdrop == "glass" and self._frame_kind == "custom":
            painter = QPainter(self)
            painter.setCompositionMode(QPainter.CompositionMode_Source)
            floor = QColor(*GLASS_FLOOR)
            if self._glass_look == "tinted":
                # 0.50: the accent washed over the blurred desktop. Still a
                # floor with alpha, so clicks still land on this window.
                floor = QColor(self._tokens.get("accent", "#4a91ff"))
                floor.setAlphaF(TINT_ALPHA)
            painter.fillRect(event.rect(), floor)
            painter.end()
        super().paintEvent(event)

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
            if not self.windowState() & Qt.WindowMinimized:
                # 0.50.5: see `_settle_geometry`. Twice, because Windows'
                # maximise animation can still be running at the first look.
                QTimer.singleShot(60, self._settle_geometry)
                QTimer.singleShot(400, self._settle_geometry)
            minimised = bool(self.windowState() & Qt.WindowMinimized)
            for pane in self._panes:
                pane.set_live(not minimised)
                if not minimised:
                    pane.check_now()
        elif event.type() == QEvent.ActivationChange and self.isActiveWindow():
            for pane in self._panes:
                pane.check_now()
            if self._taskbar_failed:
                # Seen: the red on the taskbar button has done its job.
                self._taskbar_failed = False
                self._update_taskbar()
        super().changeEvent(event)

    def _settle_geometry(self) -> None:
        """0.50.5: make the window's contents fill the window after a maximise.

        Reported: maximising a window from the middle of the screen made the
        window fill the screen while the panes, the key hints and the title
        bar stayed the size they were -- the rest an empty band -- and only a
        restart put it right. With the drawn title bar, Windows is told the
        frame takes no room (`winframe`), and Qt keeps its own idea of where
        the frame is; when the two disagree after a state change, Qt lays the
        window out for a size the screen is not showing.

        So after every maximise and restore: compare the size Windows gives
        the client area with the size Qt laid out, and if they differ have
        Windows send the frame and size messages again, then lay out afresh.
        Each mismatch found is written to `window.log` beside the settings,
        because this could not be reproduced here and that line is what will
        say which half was wrong if it is still seen.
        """
        layout = self.layout()
        if layout is not None:
            layout.invalidate()
            layout.activate()
        if sys.platform != "win32" or self._frame is None or not self._frame.active:
            return
        try:
            import ctypes
            from ctypes import wintypes

            hwnd = wintypes.HWND(int(self.winId()))
            rect = wintypes.RECT()
            ctypes.windll.user32.GetClientRect(hwnd, ctypes.byref(rect))
            ratio = self.devicePixelRatioF() or 1.0
            native_w = round((rect.right - rect.left) / ratio)
            native_h = round((rect.bottom - rect.top) / ratio)
            status = self.statusBar().geometry()
            short = (abs(native_w - self.width()) > 2 or abs(native_h - self.height()) > 2
                     or (self.statusBar().isVisible()
                         and status.bottom() < self.height() - 3))
            if not short:
                return
            self._log_geometry(
                f"state={int(self.windowState())} ratio={ratio:g} "
                f"native={native_w}x{native_h} qt={self.width()}x{self.height()} "
                f"central={self.centralWidget().geometry().getRect()} "
                f"status={status.getRect()}")
            # NOMOVE | NOSIZE | NOZORDER | FRAMECHANGED: the same window, its
            # frame and size worked out again and announced to Qt.
            ctypes.windll.user32.SetWindowPos(hwnd, None, 0, 0, 0, 0,
                                              0x0002 | 0x0001 | 0x0004 | 0x0020)
            if layout is not None:
                layout.invalidate()
                layout.activate()
            self.update()
        except Exception:  # noqa: BLE001 - a check, never a failure
            pass

    def _log_geometry(self, line: str) -> None:
        try:
            folder = os.path.dirname(self._config.path)
            with open(os.path.join(folder, "window.log"), "a", encoding="utf-8") as out:
                out.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {__version__} {line}\n")
        except Exception:  # noqa: BLE001
            pass

    def _on_job_for_taskbar(self, job) -> None:
        if getattr(job, "failed", 0) or getattr(job, "refused", ""):
            self._taskbar_failed = not self.isActiveWindow()
        self._update_taskbar()

    def _update_taskbar(self) -> None:
        """0.49: what the taskbar button says about the queue."""
        from app.ui import taskbar

        if not self._config.get("transfers.taskbar"):
            flag, fraction = taskbar.NOPROGRESS, 0.0
        else:
            flag, fraction = taskbar.state(self._transfers,
                                           failed_since=self._taskbar_failed)
        if self._taskbar is None:
            if flag == taskbar.NOPROGRESS:
                return
            self._taskbar = taskbar.Taskbar()
        self._taskbar.show(int(self.winId()), flag, fraction)

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
        # 0.46.1: a request whose jobs did not all finish is answered, so the
        # application that sent it stops waiting. On this thread, at exit,
        # for the settings file's reason: one small local file, and a thread
        # would not outlive the process to write it.
        if self._handoff_open:
            from app.core import handoff

            for request in {id(r): r for r in self._handoff_jobs.values()}.values():
                handoff.write_result(request.path, handoff.cancelled())
            self._handoff_jobs.clear()
            self._handoff_open.clear()
        if self._updates is not None:
            self._updates.shutdown()
        if not self._restored:
            # 0.50.5: a maximised window keeps the size it restores to, and
            # says it was maximised. Saving the maximised size as the size is
            # what made the next start fill the screen without being
            # maximised, so the maximise button then did nothing useful.
            maximized = self.isMaximized()
            size = self.normalGeometry().size() if maximized else self.size()
            if size.width() > 0 and size.height() > 0:
                self._config.set("window.width", size.width())
                self._config.set("window.height", size.height())
            self._config.set("window.maximized", bool(maximized))
            self._remember_rail_width()
            for side, pane in zip(("left", "right"), self._panes):
                self._config.set(f"{side}.path", pane.current.path)
                self._config.set(f"{side}.tabs", pane.session())
                self._config.set(f"{side}.tab", pane.index)
            self._config.save()
        super().closeEvent(event)

    # ------------------------------------------------------------ 0.43 help

    def _copy_diagnostics(self) -> None:
        from app.core import diagnostics
        from app.core.config import DEFAULTS

        extra = {"backdrop": self._backdrop, "settings": self._config.path}
        text = diagnostics.report(version=__version__, values=self._config.values(),
                                  defaults=DEFAULTS, events=self._events.lines(),
                                  extra=extra)
        QApplication.clipboard().setText(text)
        self.statusBar().showMessage("diagnostics copied -- paste them into a report", 6000)

    def _back_up_settings(self) -> None:
        from app.core import backups

        try:
            made = backups.make(self._config)
        except OSError as exc:
            self.statusBar().showMessage(f"could not back up the settings: {exc}", 8000)
            return
        self.statusBar().showMessage(f"settings backed up as {os.path.basename(made.path)}",
                                     6000)

    def _restore_settings(self) -> None:
        from app.core import backups
        from app.ui.restore import RestoreDialog

        dialog = RestoreDialog(self, backups.listing(self._config))
        if dialog.exec() != RestoreDialog.Accepted or not dialog.chosen():
            return
        try:
            backups.restore(self._config, dialog.chosen())
        except (OSError, ValueError) as exc:
            self.statusBar().showMessage(str(exc), 8000)
            return
        self._restored = True
        self.statusBar().showMessage(
            "settings restored -- close File Manager and start it again to use them",
            0)

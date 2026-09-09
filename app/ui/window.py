"""The window: two panes, a menu, and the shortcuts that drive them.

Everything the menu does to the look goes through one call. Changing theme,
accent or density re-renders the whole sheet and re-applies it, then hands the
panes the metrics a stylesheet cannot set. There is no partial update path, and
adding one is how a picker ends up half working.
"""

from __future__ import annotations

from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QSplitter,
    QStatusBar,
    QWidget,
)
from PySide6.QtCore import Qt

from app import __version__
from app.io.protocol import Transfer
from app.theme import sheet
from app.theme.tokens import (
    ACCENT_LABELS,
    DENSITY_LABELS,
    THEME_LABELS,
)
from app.ui import dialogs
from app.ui.pane import PaneWidget
from app.ui.transfers import ConflictDialog, QueueDialog, TransferBar, TransferPrompt

TITLE = "File Manager"


class MainWindow(QMainWindow):

    def __init__(self, config, left, right, volumes, transfers, updates=None,
                 favorites=None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config = config
        self._updates = updates
        self._favorites = favorites
        self._panes = (left, right)
        self._icons = left.icons
        self._overlays = left.overlays
        self._shell_menu = left.menu
        self._volumes = volumes
        self._transfers = transfers
        self._queue_dialog: QueueDialog | None = None
        self._transfer_sources: dict[int, str] = {}

        metrics = sheet.metrics(config.get("density"))
        self._widgets = (PaneWidget(left, volumes, metrics, favorites),
                         PaneWidget(right, volumes, metrics, favorites))

        self._splitter = QSplitter(Qt.Horizontal)
        for widget in self._widgets:
            widget.activated.connect(self._on_pane_activated)
            self._splitter.addWidget(widget)
        for pane in self._panes:
            pane.folderChanged.connect(self._on_folder_changed)
            pane.elevationOffered.connect(self._offer_elevation(pane))
        if self._shell_menu is not None:
            self._shell_menu.invoked.connect(self._on_shell_invoked)
            self._shell_menu.problem.connect(
                lambda message: self.statusBar().showMessage(message, 8000))
        for widget in self._widgets:
            widget.transferRequested.connect(self._on_transfer_requested)
            widget.addFavoriteRequested.connect(self._add_favorite)
            widget.manageFavoritesRequested.connect(self._manage_favorites)
        transfers.conflict.connect(self._on_conflict)
        transfers.finished.connect(self._on_transfer_finished)
        if updates is not None:
            updates.available.connect(self._on_update_available)
            updates.uptodate.connect(self._on_up_to_date)
            updates.problem.connect(self._on_update_problem)
            updates.progress.connect(self._on_update_progress)
            updates.ready.connect(self._on_update_ready)
        self._splitter.setChildrenCollapsible(False)
        self.setCentralWidget(self._splitter)

        self.setWindowTitle(TITLE)
        self.setStatusBar(QStatusBar())
        self._transfer_bar = TransferBar(transfers)
        self._transfer_bar.opened.connect(self._show_queue)
        self.statusBar().addPermanentWidget(self._transfer_bar)
        self.resize(int(config.get("window.width")), int(config.get("window.height")))

        self._build_menus()
        if self._favorites is not None:
            # Rebuilt rather than patched. The list is a dozen entries and
            # the alternative is a diff against a menu.
            self._favorites.changed.connect(self._fill_favorites)
            self._fill_favorites()
        self._active = 0
        self._set_active(0)
        # Timed, not permanent. The status bar is where a transfer reports
        # itself, and a hint that never goes away means the two share a line
        # and neither of them fits. The keys are in the menus, which is where
        # somebody looks the second time.
        self.statusBar().showMessage(
            "F5 copies  ·  F6 moves  ·  F7 new folder  ·  F2 renames  ·  "
            "Del recycles  ·  type a name to jump to it",
            20000,
        )

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
        self._action(files, "Transfers", "Ctrl+J", self._show_queue)
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
        self._action(go, "Reconnect", "Ctrl+Shift+R", lambda: self._current_pane().retry())
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
        typed.setToolTip("Typing in the listing jumps to a name. F3 finds the "
                         "next match, Shift+F3 the previous, Esc stops.")
        self._hint(view, "Find next\tF3", lambda: None).setEnabled(False)
        view.addSeparator()
        self._action(view, "Filter", "Ctrl+F", lambda: self._current_widget().focus_filter())
        self._action(view, "Clear filter", "Ctrl+Shift+F",
                     lambda: self._current_widget().clear_filter())
        view.addSeparator()
        self._axis_menu(view, "Theme", THEME_LABELS, "theme")
        self._axis_menu(view, "Accent", ACCENT_LABELS, "accent")
        self._axis_menu(view, "Density", DENSITY_LABELS, "density")
        view.addSeparator()
        unc = QAction("Show UNC paths", self, checkable=True)
        unc.setChecked(bool(self._config.get("left.show_unc")))
        unc.triggered.connect(self._set_show_unc)
        view.addAction(unc)
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
        shell_commands = QAction("Explorer context menu", self, checkable=True)
        shell_commands.setChecked(bool(self._config.get("menu.shell")))
        shell_commands.setEnabled(self._shell_menu is not None)
        shell_commands.triggered.connect(
            lambda checked: self._config.set("menu.shell", bool(checked)))
        view.addAction(shell_commands)

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
        sheet.apply(
            QApplication.instance(),
            theme=self._config.get("theme"),
            accent=self._config.get("accent"),
            density=self._config.get("density"),
        )
        metrics = sheet.metrics(self._config.get("density"))
        for widget in self._widgets:
            widget.apply_metrics(metrics)
        self._set_active(self._active)

    def _on_folder_changed(self, path: str) -> None:
        """One pane changed a folder; the other may be showing it.

        Both panes on the same folder is the normal way to work, and a pane
        that keeps showing a file somebody just deleted is the oldest bug in
        dual-pane file managers.
        """
        for pane in self._panes:
            if pane.current.path == path and pane.current.request_id is None:
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
        transfer = Transfer.COPY if kind == "copy" else Transfer.MOVE
        prompt = TransferPrompt(transfer, names, other.display(), self)
        if prompt.exec() != QueueDialog.Accepted:
            return
        destination = pane.as_path(prompt.destination())
        if not destination:
            return
        sources = pane.paths_for(names)
        if transfer is Transfer.COPY:
            job = self._transfers.copy(sources, destination)
        else:
            job = self._transfers.move(sources, destination)
        self._transfer_sources[job] = pane.current.path

    def _on_conflict(self, job_id: int, payload: dict) -> None:
        dialog = ConflictDialog(payload.get("name", ""), payload.get("source", {}),
                                payload.get("target", {}), self)
        if dialog.exec() != QueueDialog.Accepted or dialog.action is None:
            self._transfers.cancel(job_id)
            return
        self._transfers.answer(job_id, dialog.action, apply_to_all=dialog.apply_to_all)

    def _on_transfer_finished(self, job) -> None:
        """Re-list the folders a transfer touched, and say how it went.

        Only those folders: a pane showing something else has no reason to pay
        for a listing because a copy finished somewhere on the disk.
        """
        touched = {job.destination, self._transfer_sources.pop(job.id, "")}
        for pane in self._panes:
            if pane.current.path in touched and pane.current.request_id is None:
                pane.refresh()
        if job.cancelled:
            summary = f"cancelled after {job.copied:,} item(s)"
        elif job.failed:
            summary = (f"finished with {job.failed:,} failed, "
                       f"{job.copied:,} copied, {job.skipped:,} skipped")
        else:
            summary = f"{job.copied:,} copied, {job.skipped:,} skipped"
        self.statusBar().showMessage(f"{job.destination}: {summary}", 8000)
        if job.problems:
            self._transfer_bar.refresh()

    def _show_queue(self) -> None:
        if self._queue_dialog is None:
            self._queue_dialog = QueueDialog(self._transfers, self)
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

    def _set_active(self, index: int) -> None:
        self._active = index
        for position, widget in enumerate(self._widgets):
            widget.set_active(position == index)

    # ------------------------------------------------------------------ close

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """Remember where the panes were. Failing to save is not worth a dialog.

        A transfer still running is the one thing worth stopping for. The ops
        process is a child of this one, so closing the window ends it: the
        honest thing is to say so and let the user decide, rather than to
        promise a transfer that outlives the window and not deliver it.
        """
        running = self._transfers.active
        if running:
            names = [f"{job.label} to {job.destination}" for job in running]
            if not dialogs.confirm_stop(self, names):
                event.ignore()
                return
        self._transfers.shutdown()
        if self._updates is not None:
            self._updates.shutdown()
        self._config.set("window.width", self.width())
        self._config.set("window.height", self.height())
        for side, pane in zip(("left", "right"), self._panes):
            self._config.set(f"{side}.path", pane.current.path)
            self._config.set(f"{side}.tabs", pane.session())
            self._config.set(f"{side}.tab", pane.index)
        self._config.save()
        super().closeEvent(event)

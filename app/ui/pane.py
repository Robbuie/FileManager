"""One pane: a drive picker, a tab strip, a path bar, the listing, and a status line.

Presentation and input, and nothing else. Every question this widget has about
the filesystem is asked of `core.Pane`, which asks a worker. There is no `os`,
no `pathlib`, and no path arithmetic here -- not even a `..`, which is why the
model carries the parent row and `core` decides what activating it means.

The active pane is drawn with the accent on its border. With two panes and one
keyboard, knowing where the next keystroke lands is not decoration.
"""

from __future__ import annotations

from PySide6.QtCore import (
    QItemSelection,
    QItemSelectionModel,
    QModelIndex,
    QPoint,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtCore import QEvent, QTimer
from PySide6.QtGui import QAction, QIcon, QImage, QPixmap

from app.core.icons import ROW_ICON
from app.core.listing import Column, count_of, format_size, split_name
from app.io.protocol import MENU_COMMAND, MENU_SEPARATOR, MENU_SUBMENU, MenuItem
from app.ui import dialogs
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QTabBar,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


#: Shell commands the pane already offers itself, by the shell's own name for
#: them rather than by their label. A verb is stable across languages and
#: across the wording Windows uses this year; the label is neither.
#:
#: The decision is here rather than in the shell host on purpose. The host
#: reads the menu Windows built and does not edit it -- what a menu ends up
#: showing is a question about this application's own verbs, and this is where
#: those are. Only the top level is filtered: a verb inside somebody's submenu
#: means what that extension says it means.
#:
#: Cut, Copy and Paste are deliberately not in here. The shell's Copy is the
#: clipboard and this application's Copy is the other pane, which is why the
#: pane's own entries say so.
SHELL_VERBS_WE_HAVE = frozenset({"open", "delete", "rename", "refresh"})

#: Milliseconds of not typing before a quick search forgets what was typed.
#: Long enough to think about the next letter of a long name, short enough
#: that coming back to the keyboard starts a new search rather than extending
#: one nobody remembers making.
SEARCH_FORGETS_AFTER = 1500


class PaneWidget(QFrame):

    activated = Signal(object)          # this widget, when it takes focus
    transferRequested = Signal(str)     # "copy" or "move", from F5 and F6

    def __init__(self, pane, volumes, metrics: dict[str, int],
                 parent: QWidget | None = None):
        super().__init__(parent)
        self._pane = pane
        self._volumes = volumes
        self._summary = ("", "idle")
        self.setProperty("pane", "true")
        self.setProperty("active", "false")
        self.setFrameShape(QFrame.NoFrame)

        self._tabs = QTabBar()
        self._tabs.setExpanding(False)
        # Not `setTabsClosable`: that draws the platform's close icon, which
        # follows the OS rather than the theme and lands as a bright red X in
        # the middle of a dark grey strip. A button of our own follows the
        # tokens like the rest of the chrome.
        self._tabs.setTabsClosable(False)
        self._tabs.setMovable(True)
        self._tabs.setDrawBase(False)
        self._tabs.currentChanged.connect(self._pane.select_tab)
        self._tabs.tabCloseRequested.connect(self._pane.close_tab)
        # Without this the strip's order and the pane's disagree after a drag,
        # and every index afterwards -- the one a click selects, the one a
        # close button reports -- names a different tab than the one under it.
        self._tabs.tabMoved.connect(self._pane.move_tab)
        self._tabs.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tabs.customContextMenuRequested.connect(self._on_tab_menu)
        # Middle click closes a tab and opens a folder in one behind. Both are
        # filtered rather than handled in a subclass: a QTabBar subclass would
        # be a class to find, and the behaviour belongs with the rest of this
        # widget's input.
        self._tabs.installEventFilter(self)

        # The drive picker. It lists what the session table says exists and
        # probes nothing, so it opens instantly even with a mapped server that
        # is down -- the failure only shows once a listing is asked for.
        self._drives = QComboBox()
        self._drives.setProperty("role", "drives")
        self._drives.setFocusPolicy(Qt.NoFocus)
        self._drives.setToolTip("Drive")
        self._drives.activated.connect(self._on_drive_chosen)

        # Arrows, not icons. The shell icons in the listing are the file
        # types; chrome is a different question, and a glyph that follows the
        # text colour through five themes beats a bitmap that does not.
        self._back = self._button("←", "Back (Alt+Left)", self._pane.go_back)
        self._forward = self._button("→", "Forward (Alt+Right)", self._pane.go_forward)
        self._up = self._button("↑", "Up (Backspace)", self._pane.go_up)
        self._reload = self._button("↻", "Refresh (Ctrl+R)", self._pane.refresh)

        self._path = QLineEdit()
        self._path.setClearButtonEnabled(False)
        self._path.returnPressed.connect(self._on_path_entered)

        # The filter is hidden until asked for. A filter box that is always
        # there is a box that eventually has something left in it, and a folder
        # that looks empty for a reason nobody can see is worse than no filter.
        self._filter = QLineEdit()
        self._filter.setProperty("role", "filter")
        self._filter.setPlaceholderText("Filter this folder  (Esc to clear)")
        self._filter.textChanged.connect(self._pane.set_filter)
        self._filter.hide()

        self._view = QTableView()
        self._view.setModel(self._pane.current.model)
        self._view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._view.setShowGrid(False)
        self._view.setAlternatingRowColors(True)
        self._view.setWordWrap(False)
        self._view.setSortingEnabled(True)
        # Said rather than left to the style, which picks a size from the
        # platform and would leave the rows taller than the density asked for.
        # A larger pixmap on a scaled display still lands in this box: it
        # carries its own density and Qt draws it at the logical size.
        self._view.setIconSize(QSize(ROW_ICON, ROW_ICON))
        self._view.setTabKeyNavigation(False)  # Tab belongs to the window
        self._view.verticalHeader().setVisible(False)
        self._view.horizontalHeader().setStretchLastSection(False)
        # `activated` and nothing else. Qt emits it for both Enter and a
        # double click, so connecting `doubleClicked` as well opened everything
        # twice -- two copies of whatever the shell launched, from one gesture.
        # It also means single click opens where the user has told Windows that
        # is what a click does, which is the correct answer to that setting.
        self._view.activated.connect(self._on_activated)
        self._view.setContextMenuPolicy(Qt.CustomContextMenu)
        self._view.customContextMenuRequested.connect(self._on_context_menu)
        self._view.viewport().installEventFilter(self)
        # The view too, and for the keyboard. `QAbstractItemView` answers a
        # printable key with `keyboardSearch`, its own prefix jump, which never
        # reaches this widget and cannot say what it matched or that it matched
        # nothing. Intercepting before the view is the only place the search
        # can be this application's.
        self._view.installEventFilter(self)
        self._layout_columns()
        # A header defaults its indicator to *descending*, and enabling sorting
        # applies it, so a model that sorted itself ascending gets flipped the
        # moment it is shown. Said once, out loud, rather than left to a reader
        # to rediscover from a listing that starts at Z.
        self._view.sortByColumn(int(Column.NAME), Qt.AscendingOrder)

        self._status = QLabel("")
        self._status.setProperty("role", "status")
        self._space = QLabel("")
        self._space.setProperty("role", "space")
        self._space.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(4)
        controls.addWidget(self._drives)
        for widget in (self._back, self._forward, self._up):
            controls.addWidget(widget)
        controls.addWidget(self._path, 1)
        controls.addWidget(self._reload)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(8)
        footer.addWidget(self._status, 1)
        footer.addWidget(self._space)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)
        layout.addWidget(self._tabs)
        layout.addLayout(controls)
        layout.addWidget(self._filter)
        layout.addWidget(self._view, 1)
        layout.addLayout(footer)

        self._pane.tabsChanged.connect(self._sync_tabs)
        self._pane.currentChanged.connect(self._sync_current)
        self._pane.statusChanged.connect(self._sync_status)
        self._pane.pathChanged.connect(self._on_path_changed)
        self._pane.spaceChanged.connect(self._space.setText)
        self._pane.revealRequested.connect(self._reveal)
        self._volumes.changed.connect(self._sync_drives)
        if self._pane.icons is not None:
            # A repaint, not a model signal. The view asks the model for the
            # decoration of the rows it is about to draw and no others, so
            # telling it to draw again is both the cheapest way to show a
            # newly arrived icon and the only one that stays cheap at 50,000
            # rows -- a dataChanged over the whole model would have Qt build
            # an index per row to find out it is not on screen.
            self._pane.icons.changed.connect(self._view.viewport().update)

        if self._pane.overlays is not None:
            self._pane.overlays.changed.connect(self._view.viewport().update)
        if self._pane.sizes is not None:
            # A repaint rather than a model signal, for the reason the icons
            # give: the view asks the model about the rows it is drawing and
            # no others, which is what stays cheap at 50,000 rows.
            self._pane.sizes.changed.connect(self._view.viewport().update)
            self._pane.sizes.changed.connect(self._render_status)
        if self._pane.menu is not None:
            self._pane.menu.ready.connect(self._on_shell_items)
            self._pane.menu.unavailable.connect(self._on_shell_unavailable)

        #: The menu currently on screen, and what its shell entries mean. Both
        #: are None whenever no menu is open, which is what tells the replies
        #: arriving from the shell host that they are too late to be drawn.
        self._menu: QMenu | None = None
        self._menu_slot: QAction | None = None
        self._menu_commands: dict = {}

        #: What has been typed into the listing so far, and the timer that
        #: forgets it. A quick search that never expires means the letters
        #: typed a minute ago are still narrowing the next one.
        self._search = ""
        self._search_state = "idle"
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(SEARCH_FORGETS_AFTER)
        self._search_timer.timeout.connect(self._clear_search)

        # Switching back to a tab must not connect its model a second time.
        self._watched: set = set()
        self._watch(self._pane.current.model)
        self._watch_selection()

        self.apply_metrics(metrics)
        self._sync_tabs()
        self._sync_drives()
        self._sync_status(*self._current_status())

    # ------------------------------------------------------------------ chrome

    def apply_metrics(self, metrics: dict[str, int]) -> None:
        """Take the numbers a stylesheet cannot set.

        Row height is the one that matters: it is the measurement being looked
        at 50,000 times, and Qt reads it from the vertical header rather than
        from the sheet.
        """
        header = self._view.verticalHeader()
        header.setDefaultSectionSize(metrics["row_h"])
        header.setMinimumSectionSize(metrics["row_h"])

    def set_active(self, active: bool) -> None:
        self.setProperty("active", "true" if active else "false")
        # A property a stylesheet selects on only takes effect on a repolish.
        self.style().unpolish(self)
        self.style().polish(self)

    def focus_listing(self) -> None:
        self._view.setFocus(Qt.OtherFocusReason)

    def focus_path(self) -> None:
        self._path.setFocus(Qt.ShortcutFocusReason)
        self._path.selectAll()

    def focus_filter(self) -> None:
        self._filter.show()
        self._filter.setFocus(Qt.ShortcutFocusReason)
        self._filter.selectAll()

    def current_row(self) -> int:
        index = self._view.currentIndex()
        return index.row() if index.isValid() else -1

    def selected_names(self) -> list[str]:
        """What an operation acts on: the marked rows, or the row under the cursor.

        Falling back to the cursor is what every file manager does and what the
        keyboard expects -- pressing Delete with nothing marked deletes the
        thing being looked at, not nothing. The parent row is never in the
        answer, so `..` cannot be deleted by holding a key down.
        """
        rows = self._selected_rows()
        if not rows:
            row = self.current_row()
            if row < 0:
                return []
            rows = {row}
        return self._pane.names_for(rows)

    # ------------------------------------------------------------ operations

    def new_folder(self) -> None:
        name = dialogs.ask_name(
            self.window(), title="New folder",
            label=f"Create a folder in {self._pane.display()}",
            ok_text="Create",
        )
        if name:
            self._pane.make_folder(name)

    def rename_current(self) -> None:
        row = self.current_row()
        names = self._pane.names_for({row}) if row >= 0 else []
        if not names:
            return
        name = dialogs.ask_name(
            self.window(), title="Rename", label=f"Rename {names[0]} to",
            initial=names[0], ok_text="Rename", stem=True,
        )
        if name and name != names[0]:
            self._pane.rename(row, name)

    def delete_selection(self, *, permanent: bool = False) -> None:
        names = self.selected_names()
        if not names:
            return
        if dialogs.confirm_delete(self.window(), names=names,
                                  folder=self._pane.display(), permanent=permanent):
            self._pane.delete(names, permanent=permanent)

    # ---------------------------------------------------------- context menu

    def _on_context_menu(self, point: QPoint) -> None:
        """The menu for what was right-clicked: this application's verbs, then
        Explorer's.

        The application's own first, and not as a matter of taste. They are the
        operations whose keys are in the user's fingers, they are the ones that
        run in a worker where a dead share cannot take the window down with it,
        and they are there whether or not the shell answers. Explorer's follow
        because the whole point of them is the entries this application will
        never have: TortoiseSVN, 7-Zip, whatever else is installed.

        Right-clicking a row that is not part of the selection moves to it
        first, the way Explorer does. Anything else means a menu whose title
        says one thing and whose commands act on another.
        """
        index = self._view.indexAt(point)
        on_row = index.isValid() and not self._pane.current.model.is_parent_row(index.row())
        if on_row and index.row() not in self._selected_rows():
            self._view.setCurrentIndex(index)
            self._view.selectionModel().clearSelection()
        names = self.selected_names() if on_row else []

        menu = QMenu(self)
        # The help line an extension supplies is worth showing, and Qt hides
        # action tooltips in a menu unless it is told not to.
        menu.setToolTipsVisible(True)
        self._menu = menu
        self._menu_commands = {}
        self._add_verbs(menu, names, on_row=on_row)
        self._menu_slot = None
        if self._pane.menu is not None and self._shell_wanted():
            menu.addSeparator()
            self._menu_slot = menu.addAction("Explorer commands")
            self._menu_slot.setEnabled(False)
            # Asked for after the menu is built rather than before it is
            # shown. The menu appears immediately with the verbs that are
            # always there, and the shell's entries land in it a moment later
            # -- a menu that waits for a shell extension to load is a menu
            # that is sometimes not there when the mouse button comes up.
            self._pane.context_menu(names, extended=self._extended())

        chosen = menu.exec(self._view.viewport().mapToGlobal(point))
        command = self._menu_commands.get(chosen)
        self._menu = None
        self._menu_slot = None
        self._menu_commands = {}
        if self._pane.menu is None:
            return
        if command is None:
            # Nothing of the shell's was chosen, so let go of it: a live
            # IContextMenu keeps somebody else's DLL loaded and, in a few
            # cases, holds the folder open.
            self._pane.menu.release()
            return
        token, item_id = command
        self._pane.menu.invoke(token, item_id)

    def _add_verbs(self, menu: QMenu, names: list[str], *, on_row: bool) -> None:
        """The application's own operations, in the words and keys they have
        everywhere else. The keys are shown, not claimed: they belong to the
        pane, which is what makes them safe to press in a text field.
        """
        if on_row:
            menu.addAction("Open\tEnter", self._open_current)
            row = self.current_row()
            entry = self._pane.current.model.entry(row) if row >= 0 else None
            if entry is not None and entry.is_dir:
                menu.addAction("Open in new tab\tCtrl+Enter",
                               lambda: self._open_row_in_tab(row, background=False))
            menu.addSeparator()
            menu.addAction("Copy to other pane\tF5",
                           lambda: self.transferRequested.emit("copy"))
            menu.addAction("Move to other pane\tF6",
                           lambda: self.transferRequested.emit("move"))
            menu.addAction("Rename\tF2", self.rename_current)
            menu.addAction("Delete\tDel", self.delete_selection)
            menu.addAction("Delete permanently\tShift+Del",
                           lambda: self.delete_selection(permanent=True))
            menu.addSeparator()
        if on_row and self._pane.sizes is not None:
            counted = menu.addAction("Folder size\tSpace", self.measure_selection)
            counted.setToolTip("Walk what is under it and put the total in the "
                               "size column.")
            menu.addSeparator()
        menu.addAction("New folder\tF7", self.new_folder)
        menu.addAction("Refresh\tCtrl+R", self._pane.refresh)

    def _on_shell_items(self, token: int, items) -> None:
        """Put Explorer's entries into a menu that is already open.

        Both panes are connected to the one shell menu, because there is one
        shell host and one menu on screen at a time. So this fires on the pane
        that did not ask as well, and that pane must do *nothing*: releasing
        here would let go of the menu the other pane is about to draw, and
        every entry on it would then be dead when clicked. Whoever opened the
        menu releases it when it closes.
        """
        if self._menu is None or self._menu_slot is None:
            return
        slot, self._menu_slot = self._menu_slot, None
        self._menu.removeAction(slot)
        if not items:
            disabled = self._menu.addAction("No Explorer commands here")
            disabled.setEnabled(False)
            return
        self._fill(self._menu, items, token, top=True)

    def _on_shell_unavailable(self, message: str) -> None:
        """Say why there are none, in the menu, without taking it over."""
        if self._menu is None or self._menu_slot is None:
            return
        self._menu_slot.setText(message)
        self._menu_slot.setEnabled(False)
        self._menu_slot = None

    def _fill(self, menu: QMenu, items, token: int, *, top: bool = False) -> None:
        """One level of the shell's menu, drawn with this application's look.

        Which is the whole reason the entries are walked in the shell host
        rather than shown by it: the same fonts, the same accent on the
        highlight, the same corner radius as everything else in the window.
        What it costs is the entries an extension paints itself rather than
        naming, which arrive labelled from their verb.
        """
        for item in _tidy(items, drop_verbs=SHELL_VERBS_WE_HAVE if top else frozenset()):
            if item.kind == MENU_SEPARATOR:
                menu.addSeparator()
                continue
            if item.kind == MENU_SUBMENU:
                child = menu.addMenu(item.text)
                child.setEnabled(item.enabled)
                icon = _menu_icon(item)
                if icon is not None:
                    child.setIcon(icon)
                self._fill(child, item.items, token)
                continue
            action = menu.addAction(item.text)
            action.setEnabled(item.enabled)
            if item.default:
                # What a double click would have done, drawn the way Explorer
                # draws it. The listing already opens on Enter, so this is the
                # menu agreeing with the keyboard rather than a new promise.
                font = action.font()
                font.setBold(True)
                action.setFont(font)
            if item.checked:
                action.setCheckable(True)
                action.setChecked(True)
            if item.help:
                action.setToolTip(item.help)
            icon = _menu_icon(item)
            if icon is not None:
                action.setIcon(icon)
            # The id means nothing without its token, and nothing at all after
            # this menu closes. Both travel together, back to the one process
            # that can still turn them into a command.
            self._menu_commands[action] = (token, item.id)

    def _shell_wanted(self) -> bool:
        return self._pane.menu is not None and not self._pane.menu.busy

    def _extended(self) -> bool:
        """Shift-right-click asks for the entries Explorer hides behind Shift."""
        from PySide6.QtWidgets import QApplication

        return bool(QApplication.keyboardModifiers() & Qt.ShiftModifier)

    def _open_current(self) -> None:
        row = self.current_row()
        if row >= 0:
            self._pane.activate(row)

    def _selected_rows(self) -> set:
        picker = self._view.selectionModel()
        return {index.row() for index in picker.selectedRows()} if picker else set()

    def clear_filter(self) -> None:
        self._filter.clear()   # the model is told through textChanged
        self._filter.hide()
        self.focus_listing()

    # ------------------------------------------------------------------ events

    def focusInEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self.activated.emit(self)
        super().focusInEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """The keys, handled here rather than as window shortcuts.

        Deliberately: a window shortcut on Delete would take the key away from
        the path bar and the filter box, so backspacing over a typo would start
        deleting files. Handling them at the pane means a text field that wants
        a key keeps it, since a focused `QLineEdit` consumes what it uses and
        only the rest reaches this. The two fields that do not consume the
        function keys are checked for explicitly.
        """
        key = event.key()
        if key == Qt.Key_Escape and self._pane.sizes is not None \
                and self._pane.sizes.busy:
            # Before the filter, because a walk of a tree over SMB is the more
            # expensive thing to be stuck with and Escape is what a person
            # presses to stop something.
            self._pane.stop_measuring()
            return
        if key == Qt.Key_Escape and self._filter.isVisible():
            self.clear_filter()
            return
        if key == Qt.Key_Backspace:
            self._pane.go_up()
            return
        if key == Qt.Key_Insert:
            self._mark_and_advance()
            return

        if key in (Qt.Key_Return, Qt.Key_Enter) and \
                event.modifiers() & Qt.ControlModifier and \
                not self._path.hasFocus() and not self._filter.hasFocus():
            self.open_in_new_tab(background=False)
            return

        if self._path.hasFocus() or self._filter.hasFocus():
            super().keyPressEvent(event)
            return
        shift = bool(event.modifiers() & Qt.ShiftModifier)
        if key == Qt.Key_F5:
            self.transferRequested.emit("copy")
            return
        if key == Qt.Key_F6:
            self.transferRequested.emit("move")
            return
        if key == Qt.Key_F7:
            self.new_folder()
            return
        if key == Qt.Key_F2:
            self.rename_current()
            return
        if key in (Qt.Key_Delete, Qt.Key_F8):
            self.delete_selection(permanent=shift)
            return
        super().keyPressEvent(event)

    def _on_activated(self, index: QModelIndex) -> None:
        if index.isValid():
            self._pane.activate(index.row())

    def _on_path_entered(self) -> None:
        text = self._path.text().strip()
        if text:
            self._pane.navigate(text)
        self._view.setFocus(Qt.OtherFocusReason)

    def _on_path_changed(self, text: str) -> None:
        self._path.setText(text)
        # A search is about the rows on screen, and these are about to be
        # different rows.
        self._clear_search()
        # Navigating clears the model's filter; the box has to agree with it.
        if self._filter.text():
            self._filter.blockSignals(True)
            self._filter.clear()
            self._filter.blockSignals(False)
        self._filter.hide()
        self._sync_drives()

    def _on_drive_chosen(self, index: int) -> None:
        letter = self._drives.itemData(index)
        if letter:
            self._pane.navigate(letter + "\\")
            self.focus_listing()

    def _mark_and_advance(self) -> None:
        """Insert marks the row and moves down, the way a file manager does.

        Marking without moving means holding Insert does nothing after the
        first press, which is not what anybody's fingers expect.
        """
        model = self._view.model()
        index = self._view.currentIndex()
        if model is None or not index.isValid():
            return
        row = index.row()
        selection = QItemSelection(model.index(row, 0),
                                   model.index(row, model.columnCount() - 1))
        picker = self._view.selectionModel()
        picker.select(selection, QItemSelectionModel.Toggle | QItemSelectionModel.Rows)
        if row + 1 < model.rowCount():
            picker.setCurrentIndex(model.index(row + 1, 0),
                                   QItemSelectionModel.NoUpdate)
            self._view.scrollTo(model.index(row + 1, 0))

    # ------------------------------------------------------------------- sync

    def _current_status(self) -> tuple[str, str]:
        tab = self._pane.current
        return tab.status_text, tab.status_state

    def _sync_tabs(self) -> None:
        blocked = self._tabs.blockSignals(True)
        try:
            while self._tabs.count() > len(self._pane.tabs):
                self._tabs.removeTab(self._tabs.count() - 1)
            while self._tabs.count() < len(self._pane.tabs):
                self._tabs.addTab("")
            closable = len(self._pane.tabs) > 1
            for index, tab in enumerate(self._pane.tabs):
                # Brackets rather than an icon for the lock. Status in this
                # application is text and colour, and a bracketed name reads as
                # held in place at any density without a bitmap to scale.
                self._tabs.setTabText(index,
                                      f"[{tab.label}]" if tab.locked else tab.label)
                tip = self._pane.display(tab.path)
                if tab.locked:
                    tip += "\nLocked. Opening a folder here opens a new tab."
                self._tabs.setTabToolTip(index, tip)
                existing = self._tabs.tabButton(index, QTabBar.RightSide)
                if tab.locked and existing is not None:
                    # A locked tab refuses to close, so it does not offer to.
                    self._tabs.setTabButton(index, QTabBar.RightSide, None)
                    existing = None
                if tab.locked:
                    continue
                if closable and existing is None:
                    self._tabs.setTabButton(index, QTabBar.RightSide,
                                            self._close_button())
                elif not closable and existing is not None:
                    self._tabs.setTabButton(index, QTabBar.RightSide, None)
            self._tabs.setCurrentIndex(self._pane.index)
        finally:
            self._tabs.blockSignals(blocked)

    def _sync_drives(self) -> None:
        """Rebuild the picker and put it on the drive this tab is looking at.

        Rebuilt rather than patched, because the list is a dozen items and the
        alternative is a diff against a combo box. A path on no listed letter
        -- a UNC typed straight in -- gets an entry of its own rather than
        leaving the picker showing a drive the pane is not on.
        """
        current = self._pane.display()
        blocked = self._drives.blockSignals(True)
        try:
            self._drives.clear()
            for drive in self._volumes.drives:
                letter = drive["letter"]
                self._drives.addItem(letter, letter)
                index = self._drives.count() - 1
                target = drive.get("unc")
                self._drives.setItemData(
                    index,
                    f"{letter}  {target}" if target else f"{letter}  {drive['type']}",
                    Qt.ToolTipRole,
                )
            letter = self._volumes.letter_for(current)
            if letter is None:
                self._drives.addItem(_root_label(current), None)
                self._drives.setCurrentIndex(self._drives.count() - 1)
            else:
                self._drives.setCurrentIndex(self._drives.findData(letter))
        finally:
            self._drives.blockSignals(blocked)

    def _layout_columns(self) -> None:
        """Name takes the slack; the rest are fixed.

        Deliberately not `ResizeToContents`: it measures every row, which at
        50,000 rows is the one thing this application is built to avoid.
        """
        header = self._view.horizontalHeader()
        header.setSectionResizeMode(int(Column.NAME), QHeaderView.Stretch)
        for column, width in ((Column.EXT, 70), (Column.SIZE, 100), (Column.MODIFIED, 140)):
            header.setSectionResizeMode(int(column), QHeaderView.Interactive)
            header.resizeSection(int(column), width)

    def _watch(self, model) -> None:
        if model not in self._watched:
            model.modelReset.connect(self._on_rows_settled)
            self._watched.add(model)

    def _watch_selection(self) -> None:
        """`setModel` replaces the selection model, so this is reconnected."""
        picker = self._view.selectionModel()
        if picker is not None:
            picker.selectionChanged.connect(self._render_status)

    def _on_rows_settled(self) -> None:
        """Put the cursor on the first row so the keyboard has somewhere to be.

        The cursor, not a selection. A first row that arrives already selected
        reads as a mark the user made, and marks are what an operation will act
        on -- a file manager that starts every folder with something selected
        is a file manager that eventually copies something nobody chose.
        """
        model = self._view.model()
        picker = self._view.selectionModel()
        if (model is not None and picker is not None and model.rowCount()
                and not self._view.currentIndex().isValid()):
            picker.setCurrentIndex(model.index(0, 0), QItemSelectionModel.NoUpdate)
        self._render_status()

    def _sync_current(self) -> None:
        self._clear_search()
        model = self._pane.current.model
        self._view.setModel(model)
        self._layout_columns()
        self._view.horizontalHeader().setSortIndicator(
            int(model.sort_column), model.sort_order,
        )
        self._watch(model)
        self._watch_selection()
        self._sync_tabs()
        self._sync_drives()

    def _reveal(self, name: str) -> None:
        """Put the cursor on a name once its listing has arrived.

        The cursor, not a selection, for the same reason the first row of a
        folder is not selected: what is marked is what an operation will act
        on, and the user marked nothing by creating a folder.
        """
        model = self._pane.current.model
        row = model.row_of(name)
        picker = self._view.selectionModel()
        if row < 0 or picker is None:
            return
        index = model.index(row, 0)
        picker.setCurrentIndex(index, QItemSelectionModel.NoUpdate)
        self._view.scrollTo(index)

    def _sync_status(self, text: str, state: str) -> None:
        self._summary = (text, state)
        self._render_status()
        self._back.setEnabled(self._pane.current.can_go_back)
        self._forward.setEnabled(self._pane.current.can_go_forward)

    def _render_status(self, *_args) -> None:
        """What the folder holds, with what is selected in front of it.

        The selection goes first because it is the part that changes as the
        user works; the folder totals sit behind it and stay put.
        """
        text, state = self._summary
        sizes = self._pane.sizes
        if sizes is not None and sizes.busy:
            outstanding = sizes.busy
            text = (f"counting {outstanding:,} folder(s)  ·  {text}"
                    if outstanding > 1 else f"counting  ·  {text}")
        if self._search:
            # In front of everything, because it is the thing that changes as
            # the user types and the thing they are looking at the line for.
            found = "" if self._search_state != "bad" else "  (no match)"
            text = f"search: {self._search}{found}  ·  {text}"
            state = self._search_state if self._search_state == "bad" else state
        picker = self._view.selectionModel()
        rows = {index.row() for index in picker.selectedRows()} if picker else set()
        model = self._pane.current.model
        if rows:
            folders, files, total = model.selection(rows)
            parts = [count_of(folders, "folder"), count_of(files, "file")]
            marked = ", ".join(part for part in parts if part)
            if marked:
                text = f"{marked} selected, {format_size(total)}  ·  {text}"
        self._status.setText(text)
        self._status.setProperty("state", state)
        self._status.style().unpolish(self._status)
        self._status.style().polish(self._status)

    def _close_button(self) -> QToolButton:
        """Close the tab this button ends up on, wherever it ends up.

        The index is looked up at click time rather than captured, because tabs
        are movable and a captured index closes the wrong one after a drag.
        """
        button = QToolButton()
        button.setText("×")
        button.setProperty("role", "tabclose")
        button.setToolTip("Close tab (Ctrl+W)")
        button.setFocusPolicy(Qt.NoFocus)
        button.setCursor(Qt.ArrowCursor)

        def close() -> None:
            for index in range(self._tabs.count()):
                if self._tabs.tabButton(index, QTabBar.RightSide) is button:
                    self._pane.close_tab(index)
                    return

        button.clicked.connect(close)
        return button

    # -------------------------------------------------------------- selecting

    def _on_selection_key(self, event) -> bool:
        """Answer a key on behalf of the selection commands. True means ours.

        Taken here rather than as window shortcuts for two reasons. Ctrl+A as
        a window shortcut takes select-all away from the path bar and the
        filter box, which is the failure the function keys are kept off the
        window for. And a bare `+` is either the keypad key that selects a
        group or a character somebody is typing into a quick search -- only
        the widget holding the search can tell those apart, and it does it by
        insisting the keypad ones carry `KeypadModifier`.
        """
        key = event.key()
        modifiers = event.modifiers()
        control = bool(modifiers & Qt.ControlModifier)
        shift = bool(modifiers & Qt.ShiftModifier)
        alt = bool(modifiers & Qt.AltModifier)
        pad = bool(modifiers & Qt.KeypadModifier)

        if key == Qt.Key_A and control and not alt:
            self.select_all(on=not shift)
            return True

        if pad and not control and not alt:
            # The Norton keys, on the pad and bare.
            if key == Qt.Key_Plus:
                self.ask_and_select(on=True)
                return True
            if key == Qt.Key_Minus:
                self.ask_and_select(on=False)
                return True
            if key == Qt.Key_Asterisk:
                self.invert_selection()
                return True
            return False

        if pad and alt and not control:
            # The rest of the files of the kind under the cursor.
            if key in (Qt.Key_Plus, Qt.Key_Minus):
                self.select_same_extension(on=key == Qt.Key_Plus)
                return True
            return False

        if control and not alt:
            # A keyboard with no numeric pad. Ctrl+8 is where the asterisk
            # lives on the row above, which is what makes it the invert.
            if key in (Qt.Key_Plus, Qt.Key_Equal):
                self.ask_and_select(on=True)
                return True
            if key == Qt.Key_Minus:
                self.ask_and_select(on=False)
                return True
            if key in (Qt.Key_Asterisk, Qt.Key_8):
                self.invert_selection()
                return True
        return False

    def select_matching(self, pattern: str, *, on: bool = True) -> None:
        """Mark, or unmark, every row whose name answers to a pattern."""
        if not pattern:
            return
        self._apply_selection(self._pane.current.model.rows_matching(pattern), on=on)

    def select_same_extension(self, *, on: bool = True) -> None:
        """The rest of the files of the kind under the cursor.

        The row under the cursor rather than the marked rows, because this is
        the command for "and all the other drawings" and the cursor is what
        names the kind. A folder has no extension, so it does nothing.
        """
        model = self._pane.current.model
        entry = model.entry(self.current_row())
        if entry is None or entry.is_dir:
            return
        suffix = split_name(entry)[1]
        if not suffix:
            return
        self._apply_selection(model.rows_with_extension(suffix), on=on)

    def select_all(self, *, on: bool = True) -> None:
        self._apply_selection(self._pane.current.model.all_rows(), on=on)

    def invert_selection(self) -> None:
        model = self._pane.current.model
        marked = self._selected_rows()
        rows = model.all_rows()
        self._apply_selection([row for row in rows if row in marked], on=False)
        self._apply_selection([row for row in rows if row not in marked], on=True)

    def ask_and_select(self, *, on: bool = True) -> None:
        """The pattern dialog behind the two group commands."""
        pattern = dialogs.ask_pattern(
            self.window(),
            title="Select" if on else "Unselect",
            label=("Mark everything matching" if on
                   else "Unmark everything matching"),
            ok_text="Select" if on else "Unselect",
        )
        if pattern:
            self.select_matching(pattern, on=on)

    def _apply_selection(self, rows, *, on: bool) -> None:
        """Mark or unmark a set of rows in as few calls as Qt will take.

        Coalesced into runs rather than sent one row at a time. At 50,000 rows
        a selection command that emits a range per row is seconds of the view
        rebuilding its selection, and this is the folder size this application
        is built around.
        """
        picker = self._view.selectionModel()
        model = self._view.model()
        if picker is None or model is None or not rows:
            return
        flag = QItemSelectionModel.Select if on else QItemSelectionModel.Deselect
        span = QItemSelection()
        last = model.columnCount() - 1
        for start, end in _runs(sorted(rows)):
            span.select(model.index(start, 0), model.index(end, last))
        picker.select(span, flag | QItemSelectionModel.Rows)
        self._render_status()

    # ----------------------------------------------------------- folder sizes

    def measure_selection(self) -> None:
        """Count what is under the marked folders, or the one under the cursor.

        The same fallback the operations use, and for the same reason:
        pressing a key with nothing marked means the thing being looked at.
        """
        names = self.selected_names()
        if names:
            self._pane.measure(names)

    def measure_all(self) -> None:
        self._pane.measure_all()

    # ------------------------------------------------------------ quick search

    def _on_search_key(self, event) -> bool:
        """Answer a key on behalf of the quick search. True means it was ours.

        Only the keys the search actually uses are taken. Everything else --
        the arrows, Enter, the function keys, Delete -- reaches the view and
        then this widget exactly as before, so a live search does not quietly
        change what the rest of the keyboard does.
        """
        key = event.key()
        if key == Qt.Key_F3:
            if not self._search:
                return False
            self._step_search(-1 if event.modifiers() & Qt.ShiftModifier else 1)
            return True
        if not self._search:
            if not self._is_search_key(event):
                return False
            self._set_search(event.text())
            return True
        if key == Qt.Key_Escape:
            self._clear_search()
            return True
        if key == Qt.Key_Backspace:
            # Shortens the search rather than leaving the folder. Leaving is
            # what Backspace does the rest of the time, and losing the folder
            # to one mistyped letter is not what anybody meant by it.
            self._set_search(self._search[:-1])
            return True
        if self._is_search_key(event):
            self._set_search(self._search + event.text())
            return True
        return False

    def _is_search_key(self, event) -> bool:
        """Whether a keystroke is somebody typing a name.

        A printable character with no Ctrl or Alt held. Shift is allowed
        through because it is how capitals and most punctuation are typed, and
        the search is case-insensitive anyway. Space is deliberately excluded:
        it is the folder-size key, and a name with a space in it is reachable
        by typing past it.
        """
        text = event.text()
        if not text or not text.isprintable() or text == " ":
            return False
        return not event.modifiers() & (Qt.ControlModifier | Qt.AltModifier)

    def _set_search(self, text: str) -> None:
        if not text:
            self._clear_search()
            return
        self._search = text
        self._search_timer.start()
        model = self._pane.current.model
        # From the row the cursor is on, so a second letter narrows the answer
        # rather than restarting the walk.
        row = model.find(text, start=max(0, self.current_row()))
        self._search_state = "idle" if row >= 0 else "bad"
        if row >= 0:
            self._go_to(row)
        self._render_status()

    def _step_search(self, direction: int) -> None:
        """The next match, or the previous one. Wraps, because the model does."""
        model = self._pane.current.model
        start = self.current_row() + direction
        row = model.find(self._search, start=max(0, start), forward=direction > 0)
        self._search_timer.start()
        self._search_state = "idle" if row >= 0 else "bad"
        if row >= 0:
            self._go_to(row)
        self._render_status()

    def _clear_search(self) -> None:
        if not self._search:
            return
        self._search = ""
        self._search_state = "idle"
        self._search_timer.stop()
        self._render_status()

    def _go_to(self, row: int) -> None:
        """Put the cursor on a row without marking it.

        The cursor, not a selection, for the reason the first row of a folder
        is not selected: what is marked is what an operation acts on, and
        typing three letters is not a decision to act on anything.
        """
        model = self._view.model()
        picker = self._view.selectionModel()
        if model is None or picker is None or not 0 <= row < model.rowCount():
            return
        index = model.index(row, 0)
        picker.setCurrentIndex(index, QItemSelectionModel.NoUpdate)
        self._view.scrollTo(index)

    # --------------------------------------------------------------- the tabs

    def _on_tab_menu(self, point: QPoint) -> None:
        """The menu for one tab, or for the empty part of the strip.

        Which tab was clicked is asked of the bar rather than remembered,
        because tabs are movable and every command here takes an index.
        """
        index = self._tabs.tabAt(point)
        menu = QMenu(self)
        menu.addAction("New tab\tCtrl+T", self._pane.open_tab)
        if index >= 0:
            tab = self._pane.tabs[index]
            menu.addAction("Duplicate tab\tCtrl+Shift+T",
                           lambda: self._pane.duplicate_tab(index))
            menu.addSeparator()
            lock = menu.addAction("Locked", lambda: self._pane.toggle_lock(index))
            lock.setCheckable(True)
            lock.setChecked(tab.locked)
            lock.setToolTip("A locked tab keeps its folder. Opening one from "
                            "here opens a new tab instead.")
            menu.addSeparator()
            close = menu.addAction("Close tab\tCtrl+W",
                                   lambda: self._pane.close_tab(index))
            close.setEnabled(not tab.locked and len(self._pane.tabs) > 1)
            others = menu.addAction("Close other tabs",
                                    lambda: self._pane.close_others(index))
            right = menu.addAction("Close tabs to the right",
                                   lambda: self._pane.close_to_right(index))
            others.setEnabled(self._closable_besides(index))
            right.setEnabled(any(not t.locked
                                 for t in self._pane.tabs[index + 1:]))
        menu.setToolTipsVisible(True)
        menu.exec(self._tabs.mapToGlobal(point))

    def _closable_besides(self, index: int) -> bool:
        return any(position != index and not tab.locked
                   for position, tab in enumerate(self._pane.tabs))

    def open_in_new_tab(self, *, background: bool = True) -> None:
        """The folder under the cursor, in a tab of its own.

        A file has no folder to open, so it is passed over rather than
        refused with a message -- the gesture is a middle click, and a middle
        click that produces a dialog is a middle click nobody makes twice.
        """
        row = self.current_row()
        if row < 0:
            return
        self._open_row_in_tab(row, background=background)

    def _open_row_in_tab(self, row: int, *, background: bool) -> None:
        model = self._pane.current.model
        if model.is_parent_row(row):
            above = self._pane.parent_path()
            if above:
                self._pane.open_tab(above, background=background)
            return
        entry = model.entry(row)
        if entry is None or not entry.is_dir:
            return
        path = self._pane.row_path(row)
        if path:
            self._pane.open_tab(path, background=background)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt naming
        """The two gestures the widgets underneath would otherwise answer
        themselves: a middle click, and typing in the listing.

        Middle click is taken on release rather than press, so one that
        started on one row and finished on another does nothing -- the same
        rule a browser follows, and for the same reason.
        """
        if watched is self._view and event.type() == QEvent.KeyPress:
            # Space, before the view: `QAbstractItemView` answers it by
            # toggling the selection, so a key press that never reaches this
            # widget would mark a row instead of counting it.
            if event.key() == Qt.Key_Space and not event.modifiers():
                self.measure_selection()
                return True
            if self._on_selection_key(event):
                return True
            if self._on_search_key(event):
                return True
        if event.type() == QEvent.MouseButtonRelease and \
                event.button() == Qt.MiddleButton:
            if watched is self._tabs:
                index = self._tabs.tabAt(event.position().toPoint())
                if index >= 0:
                    self._pane.close_tab(index)
                    return True
            elif watched is self._view.viewport():
                index = self._view.indexAt(event.position().toPoint())
                if index.isValid():
                    self._open_row_in_tab(index.row(), background=True)
                    return True
        return super().eventFilter(watched, event)

    def _button(self, text: str, tip: str, slot) -> QToolButton:
        button = QToolButton()
        button.setText(text)
        button.setToolTip(tip)
        button.setFocusPolicy(Qt.NoFocus)  # the listing keeps the focus
        button.clicked.connect(slot)
        return button


def _runs(rows) -> list[tuple[int, int]]:
    """Consecutive row numbers, as `(first, last)` pairs.

    A sorted list in, contiguous blocks out. Selecting 40,000 rows is then a
    handful of ranges rather than 40,000 of them, which is the difference
    between a command that feels instant and one that redraws for seconds.
    """
    blocks: list[tuple[int, int]] = []
    for row in rows:
        if blocks and row == blocks[-1][1] + 1:
            blocks[-1] = (blocks[-1][0], row)
        else:
            blocks.append((row, row))
    return blocks


def _tidy(items, *, drop_verbs) -> list[MenuItem]:
    """The entries to draw: the shell's, less the ones this pane already has,
    with the gaps that leaves closed up.

    Dropping an entry leaves its separator behind, and two separators with
    nothing between them read as a menu that failed to draw rather than as one
    that was tidied. So separators are collapsed afterwards rather than
    decided at the same time -- which also means a menu that turns out to be
    entirely duplicates comes back empty instead of coming back as a row of
    lines.
    """
    kept: list[MenuItem] = []
    for item in items:
        if not isinstance(item, MenuItem):
            continue
        if item.kind == MENU_COMMAND and item.verb.lower() in drop_verbs:
            continue
        if item.kind == MENU_SEPARATOR and (not kept or kept[-1].kind == MENU_SEPARATOR):
            continue
        kept.append(item)
    while kept and kept[-1].kind == MENU_SEPARATOR:
        kept.pop()
    return kept


def _menu_icon(item: MenuItem) -> QIcon | None:
    """A menu entry's own picture, if it came with one.

    The same premultiplied BGRA the row icons arrive as, for the same reason:
    a bitmap handle does not cross a process boundary. An entry without one
    draws without one, which is what most of them do.
    """
    if not item.icon or not item.icon_size:
        return None
    size = int(item.icon_size)
    if len(item.icon) != size * size * 4:
        return None
    image = QImage(bytes(item.icon), size, size, size * 4,
                   QImage.Format_ARGB32_Premultiplied).copy()
    if image.isNull():
        return None
    pixmap = QPixmap.fromImage(image)
    return None if pixmap.isNull() else QIcon(pixmap)


def _root_label(path: str) -> str:
    """A short name for a place the drive list does not cover.

    String work on something already displayed rather than path arithmetic:
    the picker only needs something to show, and getting it wrong costs a
    label, not a navigation.
    """
    text = (path or "").rstrip("\\")
    if text.startswith("\\\\"):
        parts = text.split("\\")
        server = parts[2] if len(parts) > 2 else ""
        share = parts[3] if len(parts) > 3 else ""
        return f"\\\\{server}\\{share}" if share else f"\\\\{server}"
    return text[:2] if text[1:2] == ":" else (text or "?")

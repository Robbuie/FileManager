"""One pane: a drive picker, a tab strip, a path bar, the listing, and a status line.

Presentation and input, and nothing else. Every question this widget has about
the filesystem is asked of `core.Pane`, which asks a worker. There is no `os`,
no `pathlib`, and no path arithmetic here -- not even a `..`, which is why the
model carries the parent row and `core` decides what activating it means.

The active pane is drawn with the accent on its border. With two panes and one
keyboard, knowing where the next keystroke lands is not decoration.
"""

from __future__ import annotations

from PySide6.QtCore import QItemSelection, QItemSelectionModel, QModelIndex, Qt, Signal

from app.core.listing import Column, count_of, format_size
from app.ui import dialogs
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QTabBar,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


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

        # The drive picker. It lists what the session table says exists and
        # probes nothing, so it opens instantly even with a mapped server that
        # is down -- the failure only shows once a listing is asked for.
        self._drives = QComboBox()
        self._drives.setProperty("role", "drives")
        self._drives.setFocusPolicy(Qt.NoFocus)
        self._drives.setToolTip("Drive")
        self._drives.activated.connect(self._on_drive_chosen)

        # Arrows, not icons. Real SVG icons come with the shell integration,
        # when there is a way to tint them from the accent; until then a glyph
        # that follows the text colour beats a bitmap that does not.
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
        self._view.setTabKeyNavigation(False)  # Tab belongs to the window
        self._view.verticalHeader().setVisible(False)
        self._view.horizontalHeader().setStretchLastSection(False)
        # `activated` and nothing else. Qt emits it for both Enter and a
        # double click, so connecting `doubleClicked` as well opened everything
        # twice -- two copies of whatever the shell launched, from one gesture.
        # It also means single click opens where the user has told Windows that
        # is what a click does, which is the correct answer to that setting.
        self._view.activated.connect(self._on_activated)
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
        picker = self._view.selectionModel()
        rows = {index.row() for index in picker.selectedRows()} if picker else set()
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
        if key == Qt.Key_Escape and self._filter.isVisible():
            self.clear_filter()
            return
        if key == Qt.Key_Backspace:
            self._pane.go_up()
            return
        if key == Qt.Key_Insert:
            self._mark_and_advance()
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
                self._tabs.setTabText(index, tab.label)
                self._tabs.setTabToolTip(index, self._pane.display(tab.path))
                existing = self._tabs.tabButton(index, QTabBar.RightSide)
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

    def _button(self, text: str, tip: str, slot) -> QToolButton:
        button = QToolButton()
        button.setText(text)
        button.setToolTip(tip)
        button.setFocusPolicy(Qt.NoFocus)  # the listing keeps the focus
        button.clicked.connect(slot)
        return button


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

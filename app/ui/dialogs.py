"""The dialogs: ask for a name, confirm a delete, offer an update.

Written rather than taken from `QInputDialog` and `QMessageBox` for two
reasons. The stock ones draw the platform's own icons and button order, which
is exactly the sort of half-ported detail that makes an application look like
it was assembled rather than designed. And the delete confirmation has a job
neither of them does well: naming what is about to go. A dialog that says
"delete 6 items?" is a dialog that gets clicked through.

Nothing here touches the filesystem. A dialog is handed names and shows them.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.core import commands as core_commands

#: How many names a confirmation lists before it summarises the rest. Enough to
#: recognise a selection, few enough that the dialog stays a dialog.
NAMES_SHOWN = 12


class NamePrompt(QDialog):
    """One line of text, for a new folder, a rename or a duplicate."""

    def __init__(self, parent: QWidget | None, *, title: str, label: str,
                 initial: str = "", ok_text: str = "OK",
                 taken: Callable[[str], bool] | None = None) -> None:
        super().__init__(parent)
        self._taken = taken
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(420)

        self._field = QLineEdit(initial)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText(ok_text)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self._ok = buttons.button(QDialogButtonBox.Ok)
        self._field.textChanged.connect(self._validate)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        caption = QLabel(label)
        caption.setWordWrap(True)
        layout.addWidget(caption)
        layout.addWidget(self._field)
        # Only shown when a name is refused for being in use. A disabled OK
        # with no reason reads as a dialog that has stopped working.
        self._why = QLabel("")
        self._why.setProperty("role", "warn")
        self._why.setVisible(False)
        layout.addWidget(self._why)
        layout.addWidget(buttons)
        self._validate(initial)

    def _validate(self, text: str) -> None:
        """A name that cannot exist is refused here rather than by the worker.

        The characters are Windows's, and the check is duplicated in the worker
        on purpose: this one is a courtesy, that one is the rule. A dialog is
        not a place to enforce anything, because a dialog can be bypassed.
        """
        name = text.strip()
        usable = bool(name) and not any(ch in name for ch in '\\/:*?"<>|')
        usable = usable and name not in (".", "..")
        in_use = usable and self._taken is not None and self._taken(name)
        self._why.setText(f"{name} already exists here" if in_use else "")
        self._why.setVisible(bool(in_use))
        self._ok.setEnabled(usable and not in_use)

    def value(self) -> str:
        return self._field.text().strip()

    def select_stem(self) -> None:
        """Select the name without its extension, the way a rename should open.

        Renaming almost never means changing the extension, so the extension is
        left out of the selection and typing replaces only the part that is
        actually being changed.
        """
        text = self._field.text()
        stem, dot, _ = text.rpartition(".")
        self._field.setSelection(0, len(stem) if dot and stem else len(text))


class PatternPrompt(QDialog):
    """One pattern, for the two group selection commands.

    Separate from `NamePrompt` because the two validate opposite things: a
    name may not contain `*`, and a pattern is mostly why somebody would type
    one. The note under the field is the whole documentation of what a pattern
    means, and it is here because this is where it is needed.
    """

    def __init__(self, parent: QWidget | None, *, title: str, label: str,
                 initial: str = "", ok_text: str = "OK") -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(460)

        self._field = QLineEdit(initial)
        self._field.setPlaceholderText("*.dwg")
        self._field.selectAll()

        note = QLabel("A pattern with * or ? matches the whole name; anything "
                      "else matches part of it. Several at once, separated by "
                      "a semicolon: *.dwg;*.dxf")
        note.setWordWrap(True)
        note.setProperty("role", "note")

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText(ok_text)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        caption = QLabel(label)
        caption.setWordWrap(True)
        layout.addWidget(caption)
        layout.addWidget(self._field)
        layout.addWidget(note)
        layout.addWidget(buttons)

    def value(self) -> str:
        return self._field.text().strip()


class DeleteConfirm(QDialog):
    """What is about to be deleted, by name, and where it will go."""

    def __init__(self, parent: QWidget | None, *, names: list[str], folder: str,
                 permanent: bool) -> None:
        super().__init__(parent)
        self.setWindowTitle("Delete permanently" if permanent else "Delete")
        self.setModal(True)
        self.setMinimumWidth(460)

        headline = QLabel(f"Delete {_count(len(names))} from {folder}?")
        headline.setWordWrap(True)

        listing = QListWidget()
        listing.setSelectionMode(QListWidget.NoSelection)
        listing.setFocusPolicy(Qt.NoFocus)
        listing.setUniformItemSizes(True)
        listing.addItems(names[:NAMES_SHOWN])
        if len(names) > NAMES_SHOWN:
            listing.addItem(f"and {len(names) - NAMES_SHOWN:,} more")
        # Sized to whole rows. A box that ends halfway through a name looks
        # like something failed to draw rather than like a list that stops.
        row = listing.sizeHintForRow(0) if listing.count() else 18
        listing.setFixedHeight(listing.count() * row + 8)

        where = QLabel(
            "This cannot be undone."
            if permanent else
            "They go to the Recycle Bin, where they can be restored."
        )
        where.setWordWrap(True)
        where.setProperty("role", "warn" if permanent else "note")

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        confirm = buttons.button(QDialogButtonBox.Ok)
        confirm.setText("Delete permanently" if permanent else "Delete")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        # Cancel takes the Return key. Someone who pressed Delete by accident
        # and then Enter by reflex should end up having done nothing.
        buttons.button(QDialogButtonBox.Cancel).setDefault(True)
        confirm.setAutoDefault(False)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.addWidget(headline)
        layout.addWidget(listing)
        layout.addWidget(where)
        layout.addWidget(buttons)


class StopConfirm(QDialog):
    """Closing the window while jobs are running.

    Transfers and deletes both, since 0.14, which is why nothing here says
    "transfer" any more: a dialog that offered to stop two transfers while one
    of them was a delete would be describing the wrong thing at the one moment
    it matters.
    """

    def __init__(self, parent: QWidget | None, *, names: list[str]) -> None:
        super().__init__(parent)
        self.setWindowTitle("Something is still running")
        self.setModal(True)
        self.setMinimumWidth(460)

        headline = QLabel(
            f"{_count(len(names))} still running."
            if len(names) != 1 else "One job is still running."
        )
        headline.setWordWrap(True)

        listing = QListWidget()
        listing.setSelectionMode(QListWidget.NoSelection)
        listing.setFocusPolicy(Qt.NoFocus)
        listing.setUniformItemSizes(True)
        listing.addItems(names[:NAMES_SHOWN])
        row = listing.sizeHintForRow(0) if listing.count() else 18
        listing.setFixedHeight(listing.count() * row + 8)

        note = QLabel("Closing stops them where they are. Whatever has already "
                      "been copied or removed stays that way; nothing "
                      "half-written is left behind, and nothing part-deleted "
                      "is put back.")
        note.setWordWrap(True)
        note.setProperty("role", "note")

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Close anyway")
        buttons.button(QDialogButtonBox.Cancel).setText("Keep working")
        buttons.button(QDialogButtonBox.Cancel).setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.addWidget(headline)
        layout.addWidget(listing)
        layout.addWidget(note)
        layout.addWidget(buttons)


class UpdateOffer(QDialog):
    """A newer version exists. Three answers, and none of them is automatic."""

    DOWNLOAD = "download"
    SKIP = "skip"
    LATER = "later"

    def __init__(self, parent: QWidget | None, *, version: str, current: str,
                 size: int) -> None:
        super().__init__(parent)
        self.setWindowTitle("Update available")
        self.setModal(True)
        self.setMinimumWidth(460)
        self.answer = self.LATER

        headline = QLabel(f"File Manager {version} is available.")
        headline.setWordWrap(True)

        note = QLabel(
            f"You are on {current}. The download is about {size / (1024 * 1024):.0f} MB "
            "and installs when you quit -- nothing is interrupted and no transfer "
            "is touched."
        )
        note.setWordWrap(True)
        note.setProperty("role", "note")

        buttons = QDialogButtonBox()
        download = buttons.addButton("Download", QDialogButtonBox.AcceptRole)
        skip = buttons.addButton("Skip this version", QDialogButtonBox.DestructiveRole)
        later = buttons.addButton("Not now", QDialogButtonBox.RejectRole)
        download.clicked.connect(lambda: self._answer(self.DOWNLOAD))
        skip.clicked.connect(lambda: self._answer(self.SKIP))
        later.clicked.connect(lambda: self._answer(self.LATER))
        download.setDefault(True)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.addWidget(headline)
        layout.addWidget(note)
        layout.addWidget(buttons)

    def _answer(self, value: str) -> None:
        self.answer = value
        self.accept()


class UpdateReady(QDialog):
    """The installer is downloaded and checked. When it runs is the question."""

    def __init__(self, parent: QWidget | None, *, version: str,
                 transfers: bool) -> None:
        super().__init__(parent)
        self.setWindowTitle("Update ready")
        self.setModal(True)
        self.setMinimumWidth(460)

        headline = QLabel(f"File Manager {version} is ready to install.")
        headline.setWordWrap(True)

        note = QLabel(
            "A transfer is still running. Installing now stops it."
            if transfers else
            "The installer runs after this window closes and replaces this "
            "version in place. Settings and pane positions are kept."
        )
        note.setWordWrap(True)
        note.setProperty("role", "warn" if transfers else "note")

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Quit and install")
        buttons.button(QDialogButtonBox.Cancel).setText("When I quit")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        # The safe answer takes Return. Quitting is not something to arrive at
        # by reflex while a folder is half copied.
        buttons.button(QDialogButtonBox.Cancel).setDefault(True)
        buttons.button(QDialogButtonBox.Ok).setAutoDefault(False)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.addWidget(headline)
        layout.addWidget(note)
        layout.addWidget(buttons)


class ElevateOffer(QDialog):
    """Windows refused an operation. Offer to do exactly that one, elevated."""

    def __init__(self, parent: QWidget | None, *, description: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("Access denied")
        self.setModal(True)
        self.setMinimumWidth(460)

        headline = QLabel("Windows refused that operation in this folder.")
        headline.setWordWrap(True)

        what = QLabel(description)
        what.setWordWrap(True)
        what.setProperty("role", "note")

        note = QLabel(
            "Running it as administrator asks Windows for consent and then "
            "does this one operation. The application itself is not elevated, "
            "and nothing else is carried over."
        )
        note.setWordWrap(True)
        note.setProperty("role", "note")

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Run as administrator")
        buttons.button(QDialogButtonBox.Cancel).setText("Leave it")
        buttons.button(QDialogButtonBox.Cancel).setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.addWidget(headline)
        layout.addWidget(what)
        layout.addWidget(note)
        layout.addWidget(buttons)


class FavoritesEditor(QDialog):
    """Rename, reorder and remove saved locations.

    It edits the list directly rather than collecting changes and applying
    them on OK. There is no destructive step to confirm here -- a removed
    favourite is a line in a settings file, not a file on disk -- and a dialog
    that has to be accepted before a rename takes is a dialog people close
    without meaning to lose anything.
    """

    def __init__(self, parent: QWidget | None, favorites) -> None:
        super().__init__(parent)
        self.setWindowTitle("Favorites")
        self.setModal(True)
        self.setMinimumWidth(520)
        self._favorites = favorites

        self._list = QListWidget()
        self._list.setUniformItemSizes(True)
        self._list.currentRowChanged.connect(self._on_row_changed)
        self._list.itemDoubleClicked.connect(lambda _item: self._rename())

        self._name = QLineEdit()
        self._name.setPlaceholderText("Name")
        self._name.returnPressed.connect(self._rename)

        self._up = QPushButton("Move up")
        self._down = QPushButton("Move down")
        self._remove = QPushButton("Remove")
        self._up.clicked.connect(lambda: self._move(-1))
        self._down.clicked.connect(lambda: self._move(1))
        self._remove.clicked.connect(self._on_remove)
        for button in (self._up, self._down, self._remove):
            button.setAutoDefault(False)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)

        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(self._name, 1)
        row.addWidget(self._up)
        row.addWidget(self._down)
        row.addWidget(self._remove)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        caption = QLabel("Saved locations, in the order they appear in the menu.")
        caption.setWordWrap(True)
        layout.addWidget(caption)
        layout.addWidget(self._list, 1)
        layout.addLayout(row)
        layout.addWidget(buttons)

        self._reload(0)

    def _reload(self, row: int) -> None:
        self._list.blockSignals(True)
        self._list.clear()
        for entry in self._favorites.entries:
            item = QListWidgetItem(f"{entry.name}      {entry.path}")
            item.setToolTip(entry.path)
            self._list.addItem(item)
        self._list.blockSignals(False)
        count = self._list.count()
        self._list.setCurrentRow(min(max(0, row), count - 1) if count else -1)
        self._on_row_changed(self._list.currentRow())

    def _on_row_changed(self, row: int) -> None:
        entries = self._favorites.entries
        live = 0 <= row < len(entries)
        self._name.setText(entries[row].name if live else "")
        self._name.setEnabled(live)
        self._remove.setEnabled(live)
        self._up.setEnabled(live and row > 0)
        self._down.setEnabled(live and row < len(entries) - 1)

    def _rename(self) -> None:
        row = self._list.currentRow()
        name = self._name.text().strip()
        if row >= 0 and name:
            self._favorites.rename(row, name)
            self._reload(row)

    def _move(self, step: int) -> None:
        row = self._list.currentRow()
        if row < 0:
            return
        landed = self._favorites.move(row, step)
        self._favorites.commit_order()
        self._reload(landed)

    def _on_remove(self) -> None:
        row = self._list.currentRow()
        if row >= 0:
            self._favorites.remove(row)
            self._reload(row)


class CommandsEditor(QDialog):
    """The table of external programs: what runs, with what, and on what key.

    Unlike `FavoritesEditor` this one collects its changes and hands them back
    on OK, because the two lists are not the same kind of thing. A favourite is
    one line and removing it loses nothing; a command is four fields somebody
    has just worked out, and a half-typed row applied as it was typed would put
    a broken command on a key and save it.

    The one thing it does eagerly is refuse a shortcut, and it refuses while it
    is being typed rather than on OK. A key that is already the pane's, or
    already another row's, is a key that would make something else stop working
    -- and the moment to say so is while the person can still see what they
    pressed.
    """

    #: Two lines on purpose rather than one that wraps wherever the width
    #: happens to run out: the tokens are a list and a list that breaks in a
    #: different place at every size is one nobody scans.
    HELP = ("%P  this folder      %T  the other pane      "
            "%N  the name under the cursor      %F  its full path\n"
            "%S  the marked files      %s  their names      "
            "%L  a file listing them")

    def __init__(self, parent: QWidget | None, commands) -> None:
        super().__init__(parent)
        self.setWindowTitle("Commands")
        self.setModal(True)
        self.setMinimumWidth(640)
        self._commands = [core_commands.renamed(command) for command in commands]
        self._row = -1

        self._list = QListWidget()
        self._list.setUniformItemSizes(True)
        # Enough for the shipped table without scrolling. The form below it is
        # five fields and a row of buttons, so left to the layout the list ends
        # up the smallest thing in the dialog -- which is the wrong way round
        # for the part somebody is choosing from.
        self._list.setMinimumHeight(180)
        self._list.currentRowChanged.connect(self._on_row_changed)

        self._name = QLineEdit()
        self._program = QLineEdit()
        self._program.setPlaceholderText("powershell.exe, or a full path")
        self._arguments = QLineEdit()
        self._working = QLineEdit()
        self._working.setPlaceholderText("%P to start in this folder")
        self._shortcut = QLineEdit()
        self._shortcut.setPlaceholderText("F9, Ctrl+F2, Shift+F9")
        self._shown = QCheckBox("Show it on the Tools menu")
        for field in (self._name, self._program, self._arguments,
                      self._working, self._shortcut):
            field.textEdited.connect(self._collect)
        self._shown.toggled.connect(self._collect)

        self._why = QLabel("")
        self._why.setWordWrap(True)

        self._add = QPushButton("Add")
        self._remove = QPushButton("Remove")
        self._up = QPushButton("Move up")
        self._down = QPushButton("Move down")
        self._reset = QPushButton("Reset all")
        self._add.clicked.connect(self._on_add)
        self._remove.clicked.connect(self._on_remove)
        self._up.clicked.connect(lambda: self._move(-1))
        self._down.clicked.connect(lambda: self._move(1))
        self._reset.clicked.connect(self._on_reset)
        for button in (self._add, self._remove, self._up, self._down,
                       self._reset):
            button.setAutoDefault(False)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        form = QFormLayout()
        form.setSpacing(6)
        form.addRow("Name", self._name)
        form.addRow("Program", self._program)
        form.addRow("Arguments", self._arguments)
        form.addRow("Start in", self._working)
        form.addRow("Shortcut", self._shortcut)
        form.addRow("", self._shown)

        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(self._add)
        row.addWidget(self._remove)
        row.addWidget(self._up)
        row.addWidget(self._down)
        row.addStretch(1)
        row.addWidget(self._reset)

        help_text = QLabel(self.HELP)
        help_text.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        caption = QLabel(
            "Programs this application does not contain. A key here is "
            "answered by whichever pane has the keyboard, so %P is that "
            "pane's folder.")
        caption.setWordWrap(True)
        layout.addWidget(caption)
        layout.addWidget(self._list, 1)
        layout.addLayout(row)
        layout.addLayout(form)
        layout.addWidget(help_text)
        layout.addWidget(self._why)
        layout.addWidget(buttons)

        self._reload(0)

    @property
    def commands(self) -> list:
        return list(self._commands)

    def _reload(self, row: int) -> None:
        self._list.blockSignals(True)
        self._list.clear()
        for command in self._commands:
            label = command.name
            if command.shortcut:
                label = f"{label}      {command.shortcut}"
            item = QListWidgetItem(label)
            item.setToolTip(f"{command.program} {command.arguments}".strip())
            self._list.addItem(item)
        self._list.blockSignals(False)
        count = self._list.count()
        self._list.setCurrentRow(min(max(0, row), count - 1) if count else -1)
        self._on_row_changed(self._list.currentRow())

    def _on_row_changed(self, row: int) -> None:
        self._row = row
        live = 0 <= row < len(self._commands)
        command = self._commands[row] if live else None
        for field, value in (
            (self._name, command.name if live else ""),
            (self._program, command.program if live else ""),
            (self._arguments, command.arguments if live else ""),
            (self._working, command.working if live else ""),
            (self._shortcut, command.shortcut if live else ""),
        ):
            field.blockSignals(True)
            field.setText(value)
            field.setEnabled(live)
            field.blockSignals(False)
        self._shown.blockSignals(True)
        self._shown.setChecked(bool(command.shown) if live else False)
        self._shown.setEnabled(live)
        self._shown.blockSignals(False)
        self._remove.setEnabled(live)
        self._up.setEnabled(live and row > 0)
        self._down.setEnabled(live and row < len(self._commands) - 1)
        self._why.setText("")

    def _collect(self) -> None:
        """Write the fields back into the row, refusing a shortcut that clashes.

        The shortcut is the only field that can be wrong on its own, so it is
        the only one checked here. A refused key leaves the row's own key
        alone rather than clearing it: somebody halfway through typing
        `Ctrl+F` has not asked for their existing key to be thrown away.
        """
        row = self._row
        if not 0 <= row < len(self._commands):
            return
        typed = self._shortcut.text()
        why = core_commands.shortcut_refusal(
            typed, self._commands, this_one=self._commands[row].id)
        self._why.setText(why)
        shortcut = (self._commands[row].shortcut if why
                    else core_commands.normalise_shortcut(typed))
        self._commands[row] = core_commands.renamed(
            self._commands[row],
            name=self._name.text().strip() or self._commands[row].name,
            program=self._program.text().strip(),
            arguments=self._arguments.text(),
            working=self._working.text().strip(),
            shortcut=shortcut,
            shown=self._shown.isChecked(),
        )
        item = self._list.item(row)
        if item is not None:
            label = self._commands[row].name
            if shortcut:
                label = f"{label}      {shortcut}"
            item.setText(label)

    def _on_add(self) -> None:
        identity = f"custom-{len(self._commands) + 1}"
        while any(command.id == identity for command in self._commands):
            identity += "x"
        self._commands.append(core_commands.Command(
            id=identity, name="New command", program=""))
        self._reload(len(self._commands) - 1)
        self._name.setFocus()
        self._name.selectAll()

    def _on_remove(self) -> None:
        row = self._list.currentRow()
        if 0 <= row < len(self._commands):
            self._commands.pop(row)
            self._reload(row)

    def _move(self, step: int) -> None:
        row = self._list.currentRow()
        landed = row + step
        if 0 <= row < len(self._commands) and 0 <= landed < len(self._commands):
            self._commands[row], self._commands[landed] = (
                self._commands[landed], self._commands[row])
            self._reload(landed)

    def _on_reset(self) -> None:
        self._commands = list(core_commands.DEFAULTS)
        self._reload(0)


def _count(value: int) -> str:
    return "1 item" if value == 1 else f"{value:,} items"


# --------------------------------------------------------------------------
# What the pane actually calls.
# --------------------------------------------------------------------------


def ask_name(parent: QWidget, *, title: str, label: str, initial: str = "",
             ok_text: str = "OK", stem: bool = False,
             taken: Callable[[str], bool] | None = None) -> str | None:
    """A name, or None if the dialog was cancelled or nothing was typed.

    `taken` refuses a name already in use, in the dialog, while it can still be
    changed.
    """
    dialog = NamePrompt(parent, title=title, label=label, initial=initial,
                        ok_text=ok_text, taken=taken)
    if stem:
        dialog.select_stem()
    if dialog.exec() != QDialog.Accepted:
        return None
    return dialog.value() or None


def ask_pattern(parent: QWidget, *, title: str, label: str,
                initial: str = "", ok_text: str = "OK") -> str | None:
    """A selection pattern, or None if the dialog was cancelled or left empty."""
    dialog = PatternPrompt(parent, title=title, label=label, initial=initial,
                           ok_text=ok_text)
    if dialog.exec() != QDialog.Accepted:
        return None
    return dialog.value() or None


def confirm_delete(parent: QWidget, *, names: list[str], folder: str,
                   permanent: bool) -> bool:
    dialog = DeleteConfirm(parent, names=names, folder=folder, permanent=permanent)
    return dialog.exec() == QDialog.Accepted


def confirm_stop(parent: QWidget, names: list[str]) -> bool:
    """True when the user is willing to lose what is still running."""
    return StopConfirm(parent, names=names).exec() == QDialog.Accepted


def offer_update(parent: QWidget, *, version: str, current: str, size: int) -> str:
    """Which of download, skip or later the user chose."""
    dialog = UpdateOffer(parent, version=version, current=current, size=size)
    if dialog.exec() != QDialog.Accepted:
        return UpdateOffer.LATER
    return dialog.answer


def confirm_elevate(parent: QWidget, description: str) -> bool:
    """True to run one refused operation again with administrator rights."""
    return ElevateOffer(parent, description=description).exec() == QDialog.Accepted


def edit_favorites(parent: QWidget, favorites) -> None:
    """Open the favourites editor. It writes as it goes; there is nothing to
    return."""
    FavoritesEditor(parent, favorites).exec()


def confirm_install(parent: QWidget, *, version: str, transfers: bool) -> bool:
    """True to quit and install now; False to leave it staged for the next quit."""
    return UpdateReady(parent, version=version,
                       transfers=transfers).exec() == QDialog.Accepted


def edit_commands(parent: QWidget, commands):
    """The command table, or None when the dialog was cancelled.

    None rather than the unchanged list, so the caller can tell "no change" from
    "changed back to what it was" and save nothing in the first case.
    """
    dialog = CommandsEditor(parent, commands)
    if dialog.exec() != QDialog.Accepted:
        return None
    return dialog.commands

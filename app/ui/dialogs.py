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

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QVBoxLayout,
    QWidget,
)

#: How many names a confirmation lists before it summarises the rest. Enough to
#: recognise a selection, few enough that the dialog stays a dialog.
NAMES_SHOWN = 12


class NamePrompt(QDialog):
    """One line of text, for a new folder or a rename."""

    def __init__(self, parent: QWidget | None, *, title: str, label: str,
                 initial: str = "", ok_text: str = "OK") -> None:
        super().__init__(parent)
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
        self._ok.setEnabled(usable and name not in (".", ".."))

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
    """Closing the window while transfers are running."""

    def __init__(self, parent: QWidget | None, *, names: list[str]) -> None:
        super().__init__(parent)
        self.setWindowTitle("Transfers are running")
        self.setModal(True)
        self.setMinimumWidth(460)

        headline = QLabel(
            f"{_count(len(names))} still transferring."
            if len(names) != 1 else "A transfer is still running."
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
                      "been copied stays; nothing half-written is left behind.")
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


def _count(value: int) -> str:
    return "1 item" if value == 1 else f"{value:,} items"


# --------------------------------------------------------------------------
# What the pane actually calls.
# --------------------------------------------------------------------------


def ask_name(parent: QWidget, *, title: str, label: str, initial: str = "",
             ok_text: str = "OK", stem: bool = False) -> str | None:
    """A name, or None if the dialog was cancelled or nothing was typed."""
    dialog = NamePrompt(parent, title=title, label=label, initial=initial,
                        ok_text=ok_text)
    if stem:
        dialog.select_stem()
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


def confirm_install(parent: QWidget, *, version: str, transfers: bool) -> bool:
    """True to quit and install now; False to leave it staged for the next quit."""
    return UpdateReady(parent, version=version,
                       transfers=transfers).exec() == QDialog.Accepted

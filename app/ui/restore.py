"""Restore settings (0.43): pick a backup.

The list is `core/backups.listing`, which reads the local backups folder --
the settings file's own exception, see that module. Picking one is the only
thing this does; the window restores it and says what happens next.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.ui.dialogs import Dialog


class RestoreDialog(Dialog):
    def __init__(self, parent: QWidget | None, backups: list) -> None:
        super().__init__(parent)
        self.setWindowTitle("Restore settings")
        self.setModal(True)
        self.setMinimumWidth(420)
        note = QLabel("Every setting is replaced by the backup's -- favourites, "
                      "workspaces, labels and notes, the commands, the look. What is "
                      "there now is backed up first. File Manager then needs to be "
                      "closed and started again.")
        note.setWordWrap(True)
        self._list = QListWidget()
        for backup in backups:
            item = QListWidgetItem(f"{backup.label}    {backup.size / 1024:,.0f} KB")
            item.setData(256, backup.path)
            self._list.addItem(item)
        if backups:
            self._list.setCurrentRow(0)
        self._list.itemDoubleClicked.connect(lambda _item: self.accept())
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Restore")
        buttons.button(QDialogButtonBox.Ok).setEnabled(bool(backups))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(note)
        layout.addWidget(self._list, 1)
        layout.addWidget(buttons)

    def chosen(self) -> str:
        item = self._list.currentItem()
        return str(item.data(256)) if item is not None else ""

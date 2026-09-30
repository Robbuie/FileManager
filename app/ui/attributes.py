"""Attributes and dates (0.43): change them for many files at once.

Explorer's Properties sheet (still on the right-click menu, from the shell)
does one file's dates not at all and several files' attributes clumsily. This
is the bulk version: each attribute is a three-way box -- ticked, cleared, or
left as each file has it, which is how it opens when the marked files
disagree -- and the modified and created dates are set only when their own box
is ticked. Folders can pass the change on to everything inside them.

What comes back is a plain dict for `Op.ATTRIBUTES`; nothing here touches a
file. The attributes it opens with come from the listing, which already has
them, so opening it reads nothing either.
"""

from __future__ import annotations

from PySide6.QtCore import QDateTime, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDateTimeEdit,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from app.ui.dialogs import Dialog

#: The attributes offered, with their Windows bits.
ATTRIBUTES = (("Read-only", 0x1), ("Hidden", 0x2), ("System", 0x4), ("Archive", 0x20))


def initial_state(values: list[int], bit: int) -> Qt.CheckState:
    """Ticked when every file has the bit, clear when none does, mixed otherwise."""
    have = [bool(value & bit) for value in values]
    if have and all(have):
        return Qt.Checked
    if not any(have):
        return Qt.Unchecked
    return Qt.PartiallyChecked


class AttributesDialog(Dialog):
    def __init__(self, parent: QWidget | None, *, names: list[str],
                 attributes: list[int], mtime: float, has_folders: bool) -> None:
        super().__init__(parent)
        self.setWindowTitle("Attributes and dates")
        self.setModal(True)
        self.setMinimumWidth(460)
        count = len(names)
        heading = QLabel(names[0] if count == 1 else f"{count} items")
        heading.setProperty("role", "title")

        self._boxes: list[tuple[QCheckBox, int, Qt.CheckState]] = []
        grid = QGridLayout()
        for index, (label, bit) in enumerate(ATTRIBUTES):
            box = QCheckBox(label)
            box.setTristate(True)
            start = initial_state(attributes, bit)
            box.setCheckState(start)
            if start != Qt.PartiallyChecked:
                # Only a mixed start can be left mixed; a box that began
                # decided cycles between the two answers.
                box.setTristate(False)
            self._boxes.append((box, bit, start))
            grid.addWidget(box, index // 2, index % 2)

        when = QDateTime.fromSecsSinceEpoch(int(mtime)) if mtime else QDateTime.currentDateTime()
        self._set_modified = QCheckBox("Set modified to")
        self._modified = QDateTimeEdit(when)
        self._modified.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self._modified.setCalendarPopup(True)
        self._set_created = QCheckBox("Set created to")
        self._created = QDateTimeEdit(when)
        self._created.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self._created.setCalendarPopup(True)
        for check, edit in ((self._set_modified, self._modified),
                            (self._set_created, self._created)):
            edit.setEnabled(False)
            check.toggled.connect(edit.setEnabled)
        dates = QGridLayout()
        dates.addWidget(self._set_modified, 0, 0)
        dates.addWidget(self._modified, 0, 1)
        dates.addWidget(self._set_created, 1, 0)
        dates.addWidget(self._created, 1, 1)

        self._inside = QCheckBox("Also everything inside the marked folders")
        self._inside.setVisible(has_folders)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Apply")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.addWidget(heading)
        layout.addLayout(grid)
        layout.addLayout(dates)
        layout.addWidget(self._inside)
        layout.addWidget(buttons)

    def change(self) -> dict:
        """What to do: bits to set and to clear, dates, and whether to recurse.
        Only what was changed from how the dialog opened is in it."""
        to_set = to_clear = 0
        for box, bit, start in self._boxes:
            state = box.checkState()
            if state == start or state == Qt.PartiallyChecked:
                continue
            if state == Qt.Checked:
                to_set |= bit
            else:
                to_clear |= bit
        out: dict = {"set": to_set, "clear": to_clear,
                     "recursive": self._inside.isVisible() and self._inside.isChecked()}
        if self._set_modified.isChecked():
            out["mtime"] = float(self._modified.dateTime().toSecsSinceEpoch())
        if self._set_created.isChecked():
            out["ctime"] = float(self._created.dateTime().toSecsSinceEpoch())
        return out


def nothing_to_do(change: dict) -> bool:
    return not (change.get("set") or change.get("clear") or "mtime" in change
                or "ctime" in change)

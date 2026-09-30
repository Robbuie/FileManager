"""Search and Find duplicates (0.42): the dialog.

One dialog for both, because the duplicate finder is a search that keeps only
the files with an identical twin -- the same folder, the same name patterns,
the same date and size limits. What it hands back is the folder and a plain
dict in the shape `app/io/search.py` reads; the pane opens a tab and the
folder's worker does the rest. Nothing here touches the filesystem, including
the folder typed into "Look in": a folder that is not there fails in its tab
like one typed into the path bar.
"""

from __future__ import annotations

import time

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from app.ui.dialogs import Dialog

#: "Modified" choices: label, and how many days back (0 for any time).
WHEN = (("Any time", 0), ("Today", -1), ("In the last 7 days", 7),
        ("In the last 30 days", 30), ("In the last year", 365))

#: "Size" choices: label, at least, at most (0 for no limit).
SIZES = (("Any size", 0, 0), ("Under 100 KB", 0, 100 * 1024),
         ("Over 1 MB", 1024 * 1024, 0), ("Over 10 MB", 10 * 1024 * 1024, 0),
         ("Over 100 MB", 100 * 1024 * 1024, 0), ("Over 1 GB", 1024 ** 3, 0))


def after_for(days: int, now: float | None = None) -> float:
    """The earliest modified time a "Modified" choice allows; 0 for any."""
    now = time.time() if now is None else now
    if days == 0:
        return 0.0
    if days < 0:
        local = time.localtime(now)
        return time.mktime((local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1))
    return now - days * 86400


class SearchDialog(Dialog):
    def __init__(self, parent: QWidget | None, *, folder: str, last: dict | None = None,
                 duplicates: bool = False) -> None:
        super().__init__(parent)
        last = last if isinstance(last, dict) else {}
        self.setWindowTitle("Find duplicates" if duplicates else "Search")
        self.setModal(True)
        self.setMinimumWidth(520)

        self._folder = QLineEdit(folder)
        self._names = QLineEdit(str(last.get("names", "")))
        self._names.setPlaceholderText("*.L5K; *.acd   -- blank for every name")
        self._text = QLineEdit(str(last.get("text", "")))
        self._text.setPlaceholderText("text inside the file -- blank for none")
        self._case = QCheckBox("Match case")
        self._case.setChecked(bool(last.get("case")))
        self._regex = QCheckBox("Regular expression")
        self._regex.setChecked(bool(last.get("regex")))
        self._folders = QCheckBox("Folders whose names match, too")
        self._folders.setChecked(bool(last.get("folders")))
        self._when = QComboBox()
        for label, days in WHEN:
            self._when.addItem(label, days)
        self._when.setCurrentIndex(max(0, self._when.findData(int(last.get("when", 0) or 0))))
        self._size = QComboBox()
        for index, (label, _low, _high) in enumerate(SIZES):
            self._size.addItem(label, index)
        self._size.setCurrentIndex(max(0, min(int(last.get("size", 0) or 0), len(SIZES) - 1)))
        self._duplicates = QCheckBox("Only files with an identical copy somewhere here")
        self._duplicates.setChecked(duplicates)
        self._duplicates.setToolTip(
            "Files of the same size are compared by content. Only the ones "
            "that match go into the results, laid out by size so each set "
            "sits together.")

        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        form.addRow("Look in", self._folder)
        form.addRow("Names", self._names)
        form.addRow("Containing", self._text)
        form.addRow("", self._row(self._case, self._regex))
        form.addRow("", self._folders)
        form.addRow("Modified", self._when)
        form.addRow("Size", self._size)
        form.addRow("", self._duplicates)

        self._why = QLabel("")
        self._why.setProperty("role", "warn")
        self._why.setWordWrap(True)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self._ok = buttons.button(QDialogButtonBox.Ok)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.addLayout(form)
        layout.addWidget(self._why)
        layout.addWidget(buttons)

        for field in (self._folder, self._text):
            field.textChanged.connect(self._validate)
        self._regex.toggled.connect(self._validate)
        self._duplicates.toggled.connect(self._validate)
        self._validate()
        (self._names if not duplicates else self._folder).setFocus()

    @staticmethod
    def _row(*widgets: QWidget) -> QWidget:
        from PySide6.QtWidgets import QHBoxLayout

        holder = QWidget()
        line = QHBoxLayout(holder)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(16)
        for widget in widgets:
            line.addWidget(widget)
        line.addStretch(1)
        return holder

    def _validate(self) -> None:
        """A pattern that does not compile is refused here, where it can be
        fixed; the worker refuses it again. Content is not looked for in a
        duplicate search -- the comparison already reads every candidate."""
        import re

        dup = self._duplicates.isChecked()
        for widget in (self._text, self._case, self._regex, self._folders):
            widget.setEnabled(not dup)
        self._ok.setText("Find duplicates" if dup else "Search")
        why = ""
        if not self._folder.text().strip():
            why = "Say where to look."
        elif not dup and self._regex.isChecked() and self._text.text():
            try:
                re.compile(self._text.text())
            except re.error as exc:
                why = f"The pattern does not work: {exc}"
        self._why.setText(why)
        self._why.setVisible(bool(why))
        self._ok.setEnabled(not why)

    def folder(self) -> str:
        return self._folder.text().strip().strip('"')

    @property
    def duplicates(self) -> bool:
        return self._duplicates.isChecked()

    def remembered(self) -> dict:
        """What the next search starts from: the fields, not the folder."""
        return {"names": self._names.text(), "text": self._text.text(),
                "case": self._case.isChecked(), "regex": self._regex.isChecked(),
                "folders": self._folders.isChecked(),
                "when": int(self._when.currentData() or 0),
                "size": int(self._size.currentData() or 0)}

    def spec(self, now: float | None = None) -> dict:
        """The search as the worker reads it."""
        kept = self.remembered()
        _label, low, high = SIZES[kept["size"]]
        spec = {"names": kept["names"], "after": after_for(kept["when"], now),
                "min_size": low, "max_size": high}
        if not self.duplicates:
            spec.update(text=kept["text"], case=kept["case"], regex=kept["regex"],
                        folders=kept["folders"])
        return spec


def ask(parent: QWidget, *, folder: str, last: dict | None = None,
        duplicates: bool = False):
    """`(folder, spec, duplicates, remembered)`, or None if cancelled."""
    dialog = SearchDialog(parent, folder=folder, last=last, duplicates=duplicates)
    if dialog.exec() != SearchDialog.Accepted:
        return None
    return dialog.folder(), dialog.spec(), dialog.duplicates, dialog.remembered()

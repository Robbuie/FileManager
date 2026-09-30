"""Rename several (0.41): the dialog in front of `core/renamer.py`.

Everything that decides a name is in the core module and tested there. This
draws the rule as fields, shows every row old and new as the rule is typed,
names the rows that cannot be renamed and why, and keeps Rename grey until
there are none. It never touches the filesystem: the names come from the
listing on screen and the plan goes back to the pane, which sends it to the
folder's worker as one request.

The last rule used is kept in the settings, the way Double Commander keeps
its last mask: renaming a day's exports is usually the same rule as last time.
"""

from __future__ import annotations

from dataclasses import asdict

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.core import renamer
from app.ui.dialogs import Dialog

#: What the token buttons insert, and what their tooltips say.
TOKENS = (
    ("[N]", "The old name, without its extension. [N2-5] is characters 2 to 5, "
            "[N3-] from the 3rd on, [N1] the first."),
    ("[E]", "The old extension, without the dot."),
    ("[C]", "A counter: start, step and digits are set below."),
    ("[D]", "The file's modified date, as 2026-09-29."),
    ("[P]", "The name of the folder the file is in."),
)

#: How long typing has to pause before the preview is worked out again. Short
#: enough to feel live; long enough that a few thousand rows are not
#: recomputed on every keystroke.
DEBOUNCE_MS = 120


class RenameDialog(Dialog):
    def __init__(self, parent: QWidget | None, *, items: list[renamer.Item],
                 existing: list[str], folder_name: str, last: dict | None = None) -> None:
        super().__init__(parent)
        self._items = items
        self._existing = existing
        self._folder = folder_name
        self._result = renamer.Preview()
        self.setWindowTitle("Rename several")
        self.setModal(True)
        self.resize(760, 560)

        rule = _rule_from(last)
        self._name = QLineEdit(rule.name)
        self._extension = QLineEdit(rule.extension)
        self._find = QLineEdit(rule.find)
        self._replace = QLineEdit(rule.replace)
        self._regex = QCheckBox("Regular expression")
        self._regex.setChecked(rule.regex)
        self._whole = QCheckBox("Include the extension")
        self._whole.setChecked(rule.whole)
        self._case = QComboBox()
        for value, label in renamer.CASES:
            self._case.addItem(label, value)
        self._case.setCurrentIndex(max(0, self._case.findData(rule.case)))
        self._start, self._step, self._width = QSpinBox(), QSpinBox(), QSpinBox()
        for box, value, low in ((self._start, rule.start, 0), (self._step, rule.step, 1),
                                (self._width, rule.width, 1)):
            box.setRange(low, 999_999 if box is not self._width else 9)
            box.setValue(value)
            box.setMinimumWidth(72)
        #: Which mask a token button types into: the last one that had focus.
        self._target = self._name

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        grid.addWidget(QLabel("Name"), 0, 0)
        grid.addWidget(self._name, 0, 1)
        grid.addWidget(QLabel("Extension"), 0, 2)
        grid.addWidget(self._extension, 0, 3)
        tokens = QHBoxLayout()
        tokens.setSpacing(4)
        for token, help_text in TOKENS:
            button = QPushButton(token)
            button.setToolTip(help_text)
            button.setFocusPolicy(Qt.NoFocus)
            button.clicked.connect(lambda _c=False, t=token: self._insert(t))
            tokens.addWidget(button)
        tokens.addStretch(1)
        grid.addLayout(tokens, 1, 1, 1, 3)
        grid.addWidget(QLabel("Find"), 2, 0)
        grid.addWidget(self._find, 2, 1)
        grid.addWidget(QLabel("Replace with"), 2, 2)
        grid.addWidget(self._replace, 2, 3)
        options = QHBoxLayout()
        options.addWidget(self._regex)
        options.addWidget(self._whole)
        options.addStretch(1)
        grid.addLayout(options, 3, 1, 1, 3)
        grid.addWidget(QLabel("Case"), 4, 0)
        grid.addWidget(self._case, 4, 1)
        counter = QHBoxLayout()
        counter.setSpacing(8)
        for label, box in (("Counter from", self._start), ("step", self._step),
                           ("digits", self._width)):
            counter.addWidget(QLabel(label))
            counter.addWidget(box)
        counter.addStretch(1)
        grid.addLayout(counter, 4, 2, 1, 2)
        grid.setColumnStretch(1, 3)
        grid.setColumnStretch(3, 2)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["Now", "Becomes", ""])
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSelectionMode(QAbstractItemView.NoSelection)
        self._table.setShowGrid(False)
        self._table.setWordWrap(False)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Interactive)
        header.setSectionResizeMode(1, QHeaderView.Interactive)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.resizeSection(0, 240)
        header.resizeSection(1, 240)

        self._summary = QLabel("")
        self._summary.setProperty("role", "warn")
        self._count = QLabel("")

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self._ok = buttons.button(QDialogButtonBox.Ok)
        self._ok.setText("Rename")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.addLayout(grid)
        layout.addWidget(self._table, 1)
        bottom = QHBoxLayout()
        bottom.addWidget(self._count)
        bottom.addWidget(self._summary, 1)
        bottom.addWidget(buttons)
        layout.addLayout(bottom)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(DEBOUNCE_MS)
        self._timer.timeout.connect(self._refresh)
        for field in (self._name, self._extension, self._find, self._replace):
            field.textChanged.connect(self._timer.start)
        self._name.installEventFilter(self)
        self._extension.installEventFilter(self)
        for box in (self._regex, self._whole):
            box.toggled.connect(self._timer.start)
        self._case.currentIndexChanged.connect(self._timer.start)
        for box in (self._start, self._step, self._width):
            box.valueChanged.connect(self._timer.start)
        self._refresh()
        self._name.setFocus()
        self._name.selectAll()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt naming
        if event.type() == event.Type.FocusIn and watched in (self._name, self._extension):
            self._target = watched
        return super().eventFilter(watched, event)

    def _insert(self, token: str) -> None:
        self._target.insert(token)
        self._target.setFocus()

    def rule(self) -> renamer.Rule:
        return renamer.Rule(
            name=self._name.text(), extension=self._extension.text(),
            find=self._find.text(), replace=self._replace.text(),
            regex=self._regex.isChecked(), whole=self._whole.isChecked(),
            case=str(self._case.currentData()), start=self._start.value(),
            step=self._step.value(), width=self._width.value(),
        )

    def _refresh(self) -> None:
        self._result = renamer.preview(self._items, self.rule(), self._existing, self._folder)
        result = self._result
        self._table.setRowCount(len(result.rows))
        for row, entry in enumerate(result.rows):
            now = QTableWidgetItem(entry.old)
            becomes = QTableWidgetItem(entry.new if not entry.problem else entry.new or "")
            note = QTableWidgetItem(entry.problem or ("" if entry.changes else "unchanged"))
            if entry.problem:
                font = note.font()
                font.setBold(True)
                note.setFont(font)
            elif not entry.changes:
                for cell in (becomes, note):
                    font = cell.font()
                    font.setItalic(True)
                    cell.setFont(font)
            self._table.setItem(row, 0, now)
            self._table.setItem(row, 1, becomes)
            self._table.setItem(row, 2, note)
        total = len(result.rows)
        self._count.setText(f"{result.changing} of {total} will change")
        if result.error:
            self._summary.setText(result.error)
        elif result.problems:
            self._summary.setText(f"{result.problems} cannot be renamed as the rule stands")
        else:
            self._summary.setText("")
        self._ok.setEnabled(result.ready)

    def outcome(self) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
        """The steps to run, and each old name with its final one."""
        self._timer.stop()
        self._refresh()
        moves = [(row.old, row.new) for row in self._result.rows if row.changes]
        return renamer.plan(self._result), moves

    def remembered(self) -> dict:
        return asdict(self.rule())


def _rule_from(last: dict | None) -> renamer.Rule:
    if not isinstance(last, dict):
        return renamer.Rule()
    allowed = {key: value for key, value in last.items()
               if key in renamer.Rule.__dataclass_fields__}
    try:
        return renamer.Rule(**allowed)
    except TypeError:
        return renamer.Rule()


def ask(parent: QWidget, *, items: list[renamer.Item], existing: list[str],
        folder_name: str, last: dict | None = None):
    """Show the dialog; the plan, the moves and the rule to keep, or None."""
    dialog = RenameDialog(parent, items=items, existing=existing,
                          folder_name=folder_name, last=last)
    if dialog.exec() != RenameDialog.Accepted:
        return None
    steps, moves = dialog.outcome()
    if not steps:
        return None
    return steps, moves, dialog.remembered()

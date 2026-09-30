"""Checksums (0.43): the dialog.

The marked files' checksums, worked out by the folder's worker -- never here,
because reading a file is a filesystem call and a 4 GB image on a share is
minutes of one. The dialog asks, shows "reading" until the answer comes, and
lets the algorithm be changed, which asks again.

Two things somebody does with a checksum, and both are here: compare it with
one they were given (paste it in; the matching row is marked, or the field
says nothing matched), and hand it on (Copy puts every line on the clipboard
in the `hash *name` form `sha256sum -c` and friends read). With two or more
files it also says whether they are all identical, which is the other reason
people reach for a checksum.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.io.protocol import HASH_ALGORITHMS
from app.ui.dialogs import Dialog


def verdict(sums: dict[str, str], expected: str) -> tuple[str, set[str]]:
    """What to say, and which names to mark, for the sums and a pasted value."""
    wanted = "".join(expected.split()).lower()
    matched = {name for name, value in sums.items() if wanted and value.lower() == wanted}
    if wanted:
        return ("matches " + ", ".join(sorted(matched)) if matched
                else "no file here has that checksum"), matched
    if len(sums) >= 2:
        same = len(set(sums.values())) == 1
        return ("all identical" if same else "not identical"), set()
    return "", set()


def lines(sums: dict[str, str]) -> str:
    return "\n".join(f"{value} *{name}" for name, value in sums.items())


class ChecksumDialog(Dialog):
    def __init__(self, parent: QWidget | None, *, names: list[str], algorithm: str,
                 ask) -> None:
        """`ask(algorithm, on_done)` starts a request and returns its id;
        `on_done(payload, message)` is how the answer comes back."""
        super().__init__(parent)
        self._names = list(names)
        self._ask = ask
        self._sums: dict[str, str] = {}
        self._failed: dict[str, str] = {}
        self.request_id = None
        self.setWindowTitle("Checksums")
        self.setModal(True)
        self.resize(760, 360)

        self._algorithm = QComboBox()
        for name in HASH_ALGORITHMS:
            self._algorithm.addItem(name.upper(), name)
        self._algorithm.setCurrentIndex(max(0, self._algorithm.findData(algorithm)))
        self._algorithm.currentIndexChanged.connect(self._start)
        self._expected = QLineEdit()
        self._expected.setPlaceholderText("paste a checksum to compare")
        self._expected.textChanged.connect(self._render)
        top = QHBoxLayout()
        top.addWidget(self._algorithm)
        top.addWidget(self._expected, 1)

        self._table = QTableWidget(len(self._names), 2)
        self._table.setHorizontalHeaderLabels(["File", "Checksum"])
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setShowGrid(False)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Interactive)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.resizeSection(0, 220)
        self._mono = QFont("Cascadia Mono")
        self._mono.setStyleHint(QFont.Monospace)

        self._verdict = QLabel("")
        self._verdict.setProperty("role", "warn")
        copy = QPushButton("Copy")
        copy.setToolTip("Every line as `checksum *name`, the form checksum tools read")
        copy.clicked.connect(self._copy)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        bottom = QHBoxLayout()
        bottom.addWidget(self._verdict, 1)
        bottom.addWidget(copy)
        bottom.addWidget(buttons)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self._table, 1)
        layout.addLayout(bottom)
        self._start()

    @property
    def algorithm(self) -> str:
        return str(self._algorithm.currentData())

    def _start(self) -> None:
        self._sums, self._failed = {}, {}
        self._render(reading=True)
        self.request_id = self._ask(self.algorithm, self.answered)

    def answered(self, payload, message: str) -> None:
        self.request_id = None
        if payload is None:
            self._failed = {name: message for name in self._names}
        elif payload.get("algorithm") == self.algorithm:
            self._sums = dict(payload.get("sums") or {})
            self._failed = dict(payload.get("failed") or {})
        self._render()

    def _render(self, *_args, reading: bool = False) -> None:
        said, matched = verdict(self._sums, self._expected.text())
        for row, name in enumerate(self._names):
            label = QTableWidgetItem(name)
            if name in self._sums:
                value = QTableWidgetItem(self._sums[name])
                value.setFont(self._mono)
            else:
                value = QTableWidgetItem("reading..." if reading or not (self._sums or self._failed)
                                         else self._failed.get(name, ""))
            if name in matched:
                for cell in (label, value):
                    font = cell.font()
                    font.setBold(True)
                    cell.setFont(font)
            self._table.setItem(row, 0, label)
            self._table.setItem(row, 1, value)
        self._verdict.setText(said)

    def _copy(self) -> None:
        if self._sums:
            QGuiApplication.clipboard().setText(lines(self._sums))

"""Split and join (0.44): the dialog in front of the two queue jobs.

Splitting asks for a part size and where the parts go; joining asks only for
where the file goes. Both default to the other pane's folder, the way F5 does,
and neither writes anything: the queue does, with progress and cancel.

The sizes offered are the ones people split for -- an email attachment, a
FAT32 USB stick (whose largest file is one byte under 4 GiB), a CD -- and a
custom size in MB for anything else.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.core.listing import format_size
from app.ui.dialogs import Dialog

MB = 1024 * 1024
SIZES = (("20 MB (email)", 20 * MB), ("100 MB", 100 * MB), ("700 MB (CD)", 700 * MB),
         ("1 GB", 1024 * MB), ("4 GB less a byte (FAT32 stick)", 4 * 1024 * MB - 1),
         ("Other...", 0))


def part_count(size: int, part: int) -> int:
    return -(-size // part) if part > 0 else 0


class SplitDialog(Dialog):
    def __init__(self, parent: QWidget | None, *, name: str, size: int,
                 destination: str, joining: bool = False) -> None:
        super().__init__(parent)
        self._size = size
        self._joining = joining
        self.setWindowTitle("Join files" if joining else "Split file")
        self.setModal(True)
        self.setMinimumWidth(460)
        heading = QLabel(f"Join {name} and the parts after it" if joining
                         else f"Split {name} ({format_size(size)}) into parts")
        heading.setWordWrap(True)
        self._where = QLineEdit(destination)
        self._sizes = QComboBox()
        for label, value in SIZES:
            self._sizes.addItem(label, value)
        self._sizes.setCurrentIndex(1)
        self._custom = QSpinBox()
        self._custom.setRange(1, 1024 * 1024)
        self._custom.setSuffix(" MB")
        self._custom.setValue(250)
        self._count = QLabel("")
        self._count.setProperty("role", "muted")
        form = QFormLayout()
        if not joining:
            form.addRow("Part size", self._sizes)
            form.addRow("", self._custom)
            form.addRow("", self._count)
        form.addRow("Into", self._where)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self._ok = buttons.button(QDialogButtonBox.Ok)
        self._ok.setText("Join" if joining else "Split")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(heading)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self._sizes.currentIndexChanged.connect(self._sync)
        self._custom.valueChanged.connect(self._sync)
        self._where.textChanged.connect(self._sync)
        self._sync()

    def part_size(self) -> int:
        chosen = int(self._sizes.currentData() or 0)
        return chosen or self._custom.value() * MB

    def destination(self) -> str:
        return self._where.text().strip().strip('"')

    def _sync(self) -> None:
        self._custom.setVisible(not self._joining and not self._sizes.currentData())
        parts = part_count(self._size, self.part_size())
        usable = bool(self.destination())
        if not self._joining:
            if parts <= 1:
                self._count.setText("the file already fits in one part")
                usable = False
            else:
                self._count.setText(f"{parts:,} parts")
        self._ok.setEnabled(usable)

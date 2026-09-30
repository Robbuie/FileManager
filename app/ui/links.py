"""New link (0.45): a junction, a symbolic link or a hard link, from a dialog.

The three kinds Windows has, and when each one works -- which is most of what
somebody needs to know, so the dialog says it beside the choice:

  * a **junction** points at a folder, on this machine; it needs no special
    rights, and is what `mklink /J` makes;
  * a **symbolic link** points at a file or a folder, anywhere including a
    share; Windows allows it only in Developer Mode or as administrator;
  * a **hard link** is a second name for a file on the same drive.

The link is made by the folder's worker (`Op.LINK`), never here.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from app.ui.dialogs import Dialog

KINDS = (("junction", "Junction (folders on this machine)"),
         ("symbolic", "Symbolic link (needs Developer Mode or admin)"),
         ("hard", "Hard link (files on the same drive)"))


def default_kind(target_is_dir: bool) -> str:
    return "junction" if target_is_dir else "hard"


class LinkDialog(Dialog):
    def __init__(self, parent: QWidget | None, *, folder: str, target: str,
                 target_is_dir: bool, name: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("New link")
        self.setModal(True)
        self.setMinimumWidth(520)
        heading = QLabel(f"A link in {folder}")
        heading.setWordWrap(True)
        self._name = QLineEdit(name)
        self._target = QLineEdit(target)
        self._kind = QComboBox()
        for value, label in KINDS:
            self._kind.addItem(label, value)
        self._kind.setCurrentIndex(max(0, self._kind.findData(default_kind(target_is_dir))))
        form = QFormLayout()
        form.addRow("Name", self._name)
        form.addRow("Points to", self._target)
        form.addRow("Kind", self._kind)
        self._why = QLabel("")
        self._why.setProperty("role", "warn")
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self._ok = buttons.button(QDialogButtonBox.Ok)
        self._ok.setText("Create")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(heading)
        layout.addLayout(form)
        layout.addWidget(self._why)
        layout.addWidget(buttons)
        self._name.textChanged.connect(self._validate)
        self._target.textChanged.connect(self._validate)
        self._validate()

    def _validate(self) -> None:
        name = self._name.text().strip()
        why = ""
        if not name or any(ch in name for ch in '\\/:*?"<>|') or name in (".", ".."):
            why = "Give the link a file name."
        elif not self._target.text().strip():
            why = "Say what it points to."
        self._why.setText(why)
        self._why.setVisible(bool(why))
        self._ok.setEnabled(not why)

    def answer(self) -> tuple[str, str, str]:
        return (self._name.text().strip(), self._target.text().strip().strip('"'),
                str(self._kind.currentData()))

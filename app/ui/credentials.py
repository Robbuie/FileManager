"""The login prompt for a share (0.43).

Until now a share that needed a different account than the session's failed
with Windows' reason and nothing else -- deliberately, in 0.19, "until
somebody actually hits it". This is the prompt for when they do: offered only
after a reconnect has failed for a reason that means the account is wrong, and
never before, so a share that takes the session's own credentials never shows
it.

The password goes from this dialog to the share's worker in memory and is
used for one `WNetAddConnection2`. It is never written to the settings, never
put on a status line and never in a diagnostics report. "Remember" hands it to
Windows' own Credential Manager -- what `cmdkey` does -- rather than keeping
it anywhere of this application's.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from app.ui.dialogs import Dialog

#: Words in Windows' message that mean the account is the problem -- the
#: only failures a login prompt can fix.
LOGON_WORDS = ("user name or password", "network password", "logon failure",
               "not been authenticated", "access is denied", "account")


def wants_login(message: str) -> bool:
    """Whether a failed reconnect is one a different account could fix."""
    lowered = (message or "").lower()
    return any(word in lowered for word in LOGON_WORDS)


class LoginDialog(Dialog):
    def __init__(self, parent: QWidget | None, *, share: str, reason: str = "",
                 user: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("Connect as")
        self.setModal(True)
        self.setMinimumWidth(440)
        heading = QLabel(f"{share} needs an account to connect.")
        heading.setWordWrap(True)
        why = QLabel(reason)
        why.setProperty("role", "warn")
        why.setWordWrap(True)
        why.setVisible(bool(reason))
        self._user = QLineEdit(user)
        self._user.setPlaceholderText(r"\user (the server's own), DOMAIN\user or user@domain")
        self._password = QLineEdit()
        self._password.setEchoMode(QLineEdit.Password)
        self._remember = QCheckBox("Remember in Windows' Credential Manager")
        self._remember.setToolTip("Stored by Windows, as cmdkey does -- not by this "
                                  "application. Remove it from Credential Manager.")
        form = QFormLayout()
        form.addRow("User", self._user)
        form.addRow("Password", self._password)
        form.addRow("", self._remember)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self._ok = buttons.button(QDialogButtonBox.Ok)
        self._ok.setText("Connect")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(heading)
        layout.addWidget(why)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self._user.textChanged.connect(self._validate)
        self._validate()
        (self._password if user else self._user).setFocus()

    def _validate(self) -> None:
        self._ok.setEnabled(bool(self._user.text().strip()))

    def answer(self) -> tuple[str, str, bool]:
        return self._user.text().strip(), self._password.text(), self._remember.isChecked()


def ask(parent: QWidget, *, share: str, reason: str = "", user: str = ""):
    """`(user, password, remember)`, or None if cancelled."""
    dialog = LoginDialog(parent, share=share, reason=reason, user=user)
    if dialog.exec() != LoginDialog.Accepted:
        return None
    return dialog.answer()

"""The path, as places you can click, with the plain field one key away.

A path in a text field is a wall of characters that says where you are and
offers nothing else. Every folder above the one you are in is already on
screen; it is just not reachable. This makes each of them a target, which turns
the commonest move in a file manager -- up two and along one -- from Backspace,
Backspace, scroll, double click into a single click.

What it does not do is decide what a path is made of. It is handed a list of
`(label, path)` pairs by `core.Pane`, because splitting a path is path
arithmetic and this layer does none: the same rule that keeps `..` out of the
model. Give it crumbs, it draws crumbs.

Two things it has to get right that a mock-up does not show:

  * **Narrow.** A pane can be dragged to a few hundred pixels and a UNC path on
    a job folder is long. Crumbs are dropped from the *middle*, oldest first,
    and replaced with one ellipsis -- the two ends are the server and where you
    are, which are the parts worth keeping. It never widens its pane: the
    layout is told `SetNoConstraint`, which is the bug the favorites bar
    already had once.
  * **Ctrl+L.** The field is not replaced by this, it is behind it. The bar
    hands focus over on a click in its empty space or on the shortcut, and the
    field hands it back on Enter or Escape. Anyone who types paths keeps
    typing paths.

The chevrons became targets in 0.12: each one drops down what is inside the
crumb to its left, so along one folder is a click rather than up and back down.
That was deliberately not in the chrome release, because it is not chrome -- a
dropdown is a scan of somebody's job folder over SMB, with its own timeout and
its own cancel. This widget still knows none of that: it emits which folder was
asked about and is handed a list of names back. See `core/siblings.py` for what
happens in between.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLayout,
    QMenu,
    QSizePolicy,
    QToolButton,
    QWidget,
)

#: What a dropped run of crumbs is replaced by.
ELLIPSIS = "\u2026"

#: The chevron between two crumbs. A character rather than an icon: it follows
#: the text colour through five themes, and at nine pixels a drawn triangle and
#: a glyph are the same picture.
CHEVRON = "\u203a"

#: What the dropdown says while it is waiting, and when there is nothing to
#: show. Disabled entries rather than an empty menu: a menu that opens empty
#: reads as a folder with nothing in it, which is a different fact.
WAITING = "looking..."
NOTHING = "no folders here"


class Breadcrumb(QWidget):
    """A row of path segments. Emits where a click wants to go."""

    navigate = Signal(str)      # a crumb was clicked
    editRequested = Signal()    # the empty space was, or Ctrl+L
    #: A chevron was clicked: what is inside this folder? The answer arrives
    #: through `show_siblings`, which may be a moment later or never.
    siblingsWanted = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "crumbbar")
        self._crumbs: list[tuple[str, str]] = []
        self._buttons: list[QToolButton] = []
        #: The dropdown on screen and the folder it was opened for. Both None
        #: whenever no menu is open, which is what tells a late answer that
        #: nobody is waiting for it any more.
        self._menu: QMenu | None = None
        self._asked: str | None = None

        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(4, 2, 3, 2)
        self._row.setSpacing(0)
        # Without this the layout writes its own minimum onto this widget's
        # `minimumSize` *property*, which beats every size hint below and puts
        # a floor under the pane -- a long path would then stop the splitter
        # moving. This is the same line the favorites bar needs.
        self._row.setSizeConstraint(QLayout.SetNoConstraint)

        # The rest of the bar, and the reason it is a button: clicking the
        # empty space beside a path is how everyone asks to edit it.
        self._rest = QToolButton()
        self._rest.setProperty("role", "crumbrest")
        self._rest.setCursor(Qt.IBeamCursor)
        self._rest.setFocusPolicy(Qt.NoFocus)
        self._rest.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._rest.clicked.connect(self.editRequested)
        self._row.addWidget(self._rest, 1)

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    # ------------------------------------------------------------------ input

    def set_crumbs(self, crumbs: list[tuple[str, str]]) -> None:
        self._crumbs = list(crumbs)
        self._rebuild()

    def crumbs(self) -> list[tuple[str, str]]:
        return list(self._crumbs)

    def minimumSizeHint(self) -> QSize:
        """A width this can actually be dragged to.

        A bar whose minimum is its contents is a bar that stops the splitter,
        so the minimum is one crumb's worth and the rest is dropped. The height
        still comes from the contents, which is what keeps it on one line with
        the buttons beside it.
        """
        hint = super().minimumSizeHint()
        return QSize(48, hint.height())

    # --------------------------------------------------------------- drawing

    def _rebuild(self) -> None:
        for button in self._buttons:
            self._row.removeWidget(button)
            button.deleteLater()
        self._buttons = []

        shown = self._visible()
        for index, (label, path) in enumerate(shown):
            if index:
                # The separator between two crumbs drops down what is inside
                # the one on its left, which is the crumb before this one.
                before = shown[index - 1][1]
                self._add(self._chevron(before))
            if path is None:
                self._add(self._separator(ELLIPSIS))
                continue
            self._add(self._crumb(label, path, last=path == self._crumbs[-1][1]))

    def _visible(self) -> list[tuple[str, str | None]]:
        """The crumbs that fit, oldest dropped first, with one ellipsis.

        Deliberately not measured against the current width: this runs on
        every navigation and a measure-then-relayout loop at that rate is a
        flicker. A path deeper than the cap is rare, and the cap is generous
        enough that the common case never trims.
        """
        if len(self._crumbs) <= _MAX_CRUMBS:
            return [(label, path) for label, path in self._crumbs]
        keep_end = _MAX_CRUMBS - 2
        head = self._crumbs[:1]
        tail = self._crumbs[-keep_end:]
        return [*head, ("", None), *tail]

    def _add(self, widget: QWidget) -> None:
        self._row.insertWidget(self._row.count() - 1, widget)
        self._buttons.append(widget)

    def _crumb(self, label: str, path: str, *, last: bool) -> QToolButton:
        button = QToolButton()
        button.setProperty("role", "crumb")
        # The root keeps the mono face: `C:\` and `\\vault\projects` are
        # addresses rather than names, and reading them as such is the point.
        state = "last" if last else ("root" if path == self._crumbs[0][1] else "")
        if state:
            button.setProperty("state", state)
        button.setText(label)
        button.setToolTip(path)
        button.setFocusPolicy(Qt.NoFocus)
        button.setCursor(Qt.PointingHandCursor)
        button.clicked.connect(lambda _=False, target=path: self.navigate.emit(target))
        return button

    def _separator(self, text: str = CHEVRON) -> QLabel:
        label = QLabel(text)
        label.setProperty("role", "crumbsep")
        label.setAlignment(Qt.AlignCenter)
        return label

    def _chevron(self, folder: str | None) -> QWidget:
        """A separator that is also a target, when there is a folder behind it.

        The ellipsis stands for crumbs that were dropped, so there is no one
        folder it could open -- that one stays a plain label. Everything else
        is a button, and looks like a separator until the mouse is on it, for
        the same reason a crumb does.
        """
        if folder is None:
            return self._separator()
        button = QToolButton()
        button.setProperty("role", "crumbsep")
        button.setText(CHEVRON)
        button.setToolTip("The folders in here")
        button.setFocusPolicy(Qt.NoFocus)
        button.setCursor(Qt.PointingHandCursor)
        button.clicked.connect(
            lambda _=False, owner=button, target=folder:
            self._open_siblings(owner, target))
        return button

    # ------------------------------------------------------------ siblings

    def _open_siblings(self, button: QToolButton, folder: str) -> None:
        """Put an empty menu up straight away and ask what goes in it.

        Straight away, because the answer comes from a worker and may be a
        second away on a share: a chevron that does nothing until the scan
        returns reads as a chevron that does nothing. The menu opens saying it
        is looking, and fills in or says why not.
        """
        self._close_menu()
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        waiting = menu.addAction(WAITING)
        waiting.setEnabled(False)
        menu.aboutToHide.connect(self._forget_menu)
        self._menu, self._asked = menu, folder
        menu.popup(button.mapToGlobal(button.rect().bottomLeft()))
        self.siblingsWanted.emit(folder)

    def show_siblings(self, folder: str, entries, more: bool) -> None:
        """Fill the open dropdown in, if it is still the one that asked.

        `entries` are `(label, path)` pairs, the same shape `set_crumbs`
        takes and for the same reason: joining a folder to a name is path
        arithmetic, and this layer does none of it.
        """
        if self._menu is None or folder != self._asked:
            return
        menu = self._menu
        menu.clear()
        if not entries:
            empty = menu.addAction(NOTHING)
            empty.setEnabled(False)
            return
        for label, path in entries:
            action = menu.addAction(label)
            action.triggered.connect(
                lambda _=False, target=path: self.navigate.emit(target))
        if more:
            menu.addSeparator()
            # Said rather than left to be inferred. The scan stops at its cap,
            # so past that the names are the ones the folder happened to hand
            # over first rather than the first alphabetically -- a menu that
            # quietly ends at two hundred reads as a folder that ends there.
            note = menu.addAction("more, not listed")
            note.setEnabled(False)

    def sibling_problem(self, folder: str, message: str) -> None:
        """Say why the dropdown is empty, in the dropdown."""
        if self._menu is None or folder != self._asked:
            return
        self._menu.clear()
        action = self._menu.addAction(message)
        action.setEnabled(False)

    def _forget_menu(self) -> None:
        self._menu = None
        self._asked = None

    def _close_menu(self) -> None:
        if self._menu is not None:
            self._menu.close()
        self._forget_menu()


#: How many crumbs are drawn before the middle is elided. Five is a drive, two
#: folders and where you are, which covers most of a job tree; past that the
#: middle is the part nobody is reading.
_MAX_CRUMBS = 6

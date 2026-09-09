"""The favorites bar: one row of places, one click each.

A menu costs two clicks and a read -- open it, find the entry, click it -- and
the entries move as the list grows. A bar costs one click at a target that
stays where it was put, which is the whole difference between somewhere you
go occasionally and somewhere you go forty times a day.

It lives inside a pane rather than in the window, and that is not a detail. In
a dual-pane file manager the question is never just "where" but "which side",
and a bar in the pane answers both in one gesture: the pane it is in is the
pane that goes. The panes sit side by side, so two bars cost the same height as
one would.

Nothing here knows what a path is. The bar emits one, and `core.Pane` decides
what going there means -- including the case where the tab is locked and going
there means opening a new one.

The bar shows what fits and puts the rest behind a trailing button. Widening
the pane brings them back. What it must never do is grow the pane to fit its
own contents: a favourite with a long name would then push the splitter about,
and chrome that resizes the window it is in is chrome that gets turned off.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLayout,
    QMenu,
    QSizePolicy,
    QToolButton,
    QWidget,
)

#: The trailing button, when not everything fits. Three dots rather than a
#: chevron: the chevron in this font sits high and reads as a scroll arrow.
MORE = "..."


class FavoritesBar(QWidget):
    """The saved locations, as buttons, in the pane they navigate."""

    #: A place, and whether the click asked for a new tab.
    chosen = Signal(str, bool)
    #: The user asked to change the list rather than to go somewhere.
    manageRequested = Signal()
    addRequested = Signal()

    def __init__(self, favorites, *, wanted: bool = True,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._favorites = favorites
        #: Whether the user wants the bar at all. Kept apart from whether
        #: there is anything to put in it, so turning it back on does not have
        #: to guess and an empty list does not read as a setting.
        self._wanted = bool(wanted)
        self._buttons: list[QToolButton] = []
        #: Guards the visibility pass, which resizes this widget and would
        #: otherwise re-enter through `resizeEvent` on every button hidden.
        self._fitting = False

        # Horizontally, this widget asks for nothing. Without that a layout
        # takes its minimum from the buttons in it, and one favourite called
        # "Archive 2025" is enough to set the pane's minimum width and start
        # pushing the splitter about. It was doing exactly that until the
        # preview render came back wider than it was asked for.
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        # `SetNoConstraint`, and this is the line the whole thing turns on. A
        # layout's default constraint sets its widget's *minimumSize property*
        # from the width of what is in it -- an actual property, not a hint --
        # and a property beats every size hint an override can return. With it
        # left alone, eight favourites set a floor under the pane, the splitter
        # could not be dragged past it, and a window asked for 820 pixels came
        # back 1596 wide. That is how this was found.
        layout.setSizeConstraint(QLayout.SetNoConstraint)
        self._layout = layout

        self._more = QToolButton()
        self._more.setText(MORE)
        self._more.setProperty("role", "favorite")
        self._more.setFocusPolicy(Qt.NoFocus)
        self._more.setToolTip("The favorites that do not fit")
        self._more.clicked.connect(self._show_overflow)
        layout.addWidget(self._more)
        layout.addStretch(1)

        favorites.changed.connect(self.rebuild)
        self.rebuild()

    # ------------------------------------------------------------- building

    def rebuild(self) -> None:
        """Redraw the row. Rebuilt rather than diffed: it is a dozen buttons.

        An empty list hides the bar entirely. A row of chrome that does
        nothing until somebody discovers a keystroke is worse than no row, and
        this way the bar costs nothing at all until the first Ctrl+D.
        """
        for button in self._buttons:
            self._layout.removeWidget(button)
            button.deleteLater()
        self._buttons = []

        entries = self._favorites.entries
        for position, entry in enumerate(entries):
            button = QToolButton()
            button.setText(entry.name)
            button.setProperty("role", "favorite")
            # No focus, so a click sends the pane somewhere without taking the
            # keyboard away from the listing.
            button.setFocusPolicy(Qt.NoFocus)
            button.setToolTip(self._tip(entry, position))
            button.setContextMenuPolicy(Qt.CustomContextMenu)
            button.customContextMenuRequested.connect(
                lambda point, index=position, owner=button:
                self._show_menu(owner, point, index))
            button.clicked.connect(
                lambda _checked=False, path=entry.path:
                self.chosen.emit(path, False))
            # Middle click opens it in a tab behind, the same gesture and the
            # same meaning as a middle click on a folder in the listing.
            button.installEventFilter(self)
            self._layout.insertWidget(len(self._buttons), button)
            self._buttons.append(button)

        self.setVisible(self._wanted and bool(entries))
        self._fit()

    def set_wanted(self, wanted: bool) -> None:
        self._wanted = bool(wanted)
        self.setVisible(self._wanted and bool(self._favorites.entries))
        self._fit()

    def _tip(self, entry, position: int) -> str:
        key = f"\nCtrl+{position + 1}" if position < 9 else ""
        return f"{entry.path}{key}"

    # ------------------------------------------------------------- fitting

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        """No width at all. The height is the row; the width is whatever the
        pane has left over, and what does not fit goes behind `MORE`."""
        return QSize(0, super().minimumSizeHint().height())

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        return QSize(0, super().sizeHint().height())

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self._fit()

    def _fit(self) -> None:
        """Show what fits, hide the rest, and offer them behind one button.

        Measured against this widget's own width rather than against a
        layout's idea of a minimum, which is what stops a long favourite name
        from widening the pane it is in.
        """
        if self._fitting:
            return
        self._fitting = True
        try:
            spacing = self._layout.spacing()
            room = self.width()
            reserve = self._more.sizeHint().width() + spacing
            used = 0
            shown = 0
            for index, button in enumerate(self._buttons):
                want = button.sizeHint().width() + (spacing if index else 0)
                # The last one may use the space the overflow button would
                # have taken, because if it fits there is no overflow.
                room_here = room - (reserve if index < len(self._buttons) - 1 else 0)
                if used + want <= room_here:
                    used += want
                    shown += 1
                else:
                    break
            for index, button in enumerate(self._buttons):
                button.setVisible(index < shown)
            self._more.setVisible(shown < len(self._buttons))
        finally:
            self._fitting = False

    # -------------------------------------------------------------- gestures

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt naming
        if event.type() == QEvent.MouseButtonRelease and \
                event.button() == Qt.MiddleButton and watched in self._buttons:
            index = self._buttons.index(watched)
            entries = self._favorites.entries
            if index < len(entries):
                self.chosen.emit(entries[index].path, True)
            return True
        return super().eventFilter(watched, event)

    def _show_menu(self, button: QToolButton, point, index: int) -> None:
        entries = self._favorites.entries
        if not 0 <= index < len(entries):
            return
        entry = entries[index]
        menu = QMenu(self)
        menu.addAction("Go here", lambda: self.chosen.emit(entry.path, False))
        menu.addAction("Open in new tab",
                       lambda: self.chosen.emit(entry.path, True))
        menu.addSeparator()
        menu.addAction("Move left", lambda: self._move(index, -1))
        menu.addAction("Move right", lambda: self._move(index, 1))
        menu.addAction("Remove", lambda: self._favorites.remove(index))
        menu.addSeparator()
        menu.addAction("Add this folder", self.addRequested.emit)
        menu.addAction("Manage favorites", self.manageRequested.emit)
        menu.exec(button.mapToGlobal(point))

    def _move(self, index: int, step: int) -> None:
        self._favorites.move(index, step)
        self._favorites.commit_order()

    def _show_overflow(self) -> None:
        """The ones that did not fit, as a menu under the button that says so."""
        menu = QMenu(self)
        entries = self._favorites.entries
        for index, entry in enumerate(entries):
            if self._buttons[index].isVisible():
                continue
            action = menu.addAction(entry.name)
            action.setToolTip(entry.path)
            action.triggered.connect(
                lambda _checked=False, path=entry.path:
                self.chosen.emit(path, False))
        menu.setToolTipsVisible(True)
        menu.exec(self._more.mapToGlobal(self._more.rect().bottomLeft()))

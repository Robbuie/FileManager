"""The navigation rail: places, drives, and the favourites under their headings.

One rail for the window rather than one per pane, and that is the decision the
rest of this file follows from. The favourites *bar* is per-pane because it is
a one-click move into the pane it sits in; a rail is a different thing -- it is
the list you read before you decide, and two copies of it would cost a quarter
of the window's width to say the same thing twice. So a click here goes to the
pane that has the keyboard, which is the rule `Ctrl+1` through `Ctrl+9` have
followed since 0.10, and a middle click opens a tab behind in that same pane.

That makes one thing important enough to say twice: **nothing in this widget
takes focus.** Not the rows, not the headings, not the scroll area. A rail that
took the keyboard would move the active pane to nothing at all, and then the
click that follows would go to whichever pane happened to be active before --
which is the 0.10 and 0.11 bug arriving from a third direction. The rail is
`NoFocus` throughout and never emits `activated`; it asks the window where to
send things and the window answers with the pane it already had.

Nothing here touches a filesystem, and two things in particular that look like
they must:

  * **the places** are handed in as `(label, path)` pairs by `core.places`,
    which builds them out of environment variables. There is no check that any
    of them exists, because five existence checks at startup is the startup
    probe this application refuses.
  * **the capacity meters** are drawn from whatever `core.capacity` has been
    told. It measures local fixed disks and nothing else without being asked,
    so opening this rail can never touch a server.

The rail must never widen the window it is in. That is the same lesson the
favorites bar and the breadcrumb each learned once: a layout writes its
minimum onto its widget's `minimumSize` *property*, and a favourite called
`\\\\vault\\projects\\2026 archive` would then set a floor under the splitter.
`SetNoConstraint` everywhere, a small `minimumSizeHint`, and every row elides
its own text to the width it actually got.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QPainter, QPainterPath
from PySide6.QtWidgets import (
    QFrame,
    QLayout,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.core.favorites import UNGROUPED
from app.core.listing import format_size
from app.ui import glyphs
from app.ui.rows import parse_colour

#: The fold marks. Characters rather than drawn glyphs: at nine pixels the two
#: are the same picture, and a character follows the text colour through five
#: themes without anybody remembering to repaint it.
OPEN_MARK = "⌄"     # a chevron pointing down
FOLDED_MARK = "›"   # and to the right, the same one the crumb bar uses

#: The two headings that are not favourite groups. Kept as constants because
#: the collapsed set is stored by heading, and a heading that is spelled two
#: ways in two places is a section that will not stay folded.
PLACES = "Places"
DRIVES = "Drives"
#: The heading the network locations sit under. Its own section rather than
#: more rows under Drives, because the two answer different questions: Drives
#: is what has a letter, and this is what this session can reach -- which is
#: where a Hyper-V or Remote Desktop redirected share lives, having no letter
#: anywhere for Drives to have found it by.
NETWORK = "Network"

#: A drive at or past this share of its capacity draws its meter in the warn
#: colour. A fixed number rather than the accent, for the reason the design
#: system gives: a colour that has to mean "nearly full" is a semantic colour
#: and does not follow a picker.
NEARLY_FULL = 0.9

#: The meter's own metrics, in pixels. Not from the density: this is a bar
#: three pixels tall inside a row whose height the density already sets, and a
#: bar that scales with the font stops reading as a bar.
METER_HEIGHT = 3
METER_RADIUS = 1.5

#: What a row's text loses to the column's margins and the button's own
#: padding. Written out rather than measured, because the two numbers it adds
#: up are in this file and in the stylesheet and neither moves.
_ROW_INSET = 28


class NavigationRail(QFrame):
    """Places, drives and favourites, in one column down the left."""

    #: A place, and whether the click asked for a new tab. Same shape as the
    #: favorites bar's, and handled by the same slot in the window.
    chosen = Signal(str, bool)
    #: A drive letter the user asked to have measured, or measured again.
    measureRequested = Signal(str)
    #: The drive letters themselves are stale; ask the session table again.
    rescanRequested = Signal()
    addFavoriteRequested = Signal()
    manageFavoritesRequested = Signal()
    #: A favourite was moved under a heading: its index, and the new group.
    groupRequested = Signal(int, str)
    #: The network section. Separate signals rather than one with a verb,
    #: because the window does four different things with them and a string
    #: to switch on is a place for a typo to live.
    addLocationRequested = Signal()
    refreshNetworkRequested = Signal()
    reconnectRequested = Signal(str)
    ejectRequested = Signal(str)
    forgetLocationRequested = Signal(str)

    def __init__(self, favorites, volumes, capacity, config, network=None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._favorites = favorites
        self._volumes = volumes
        self._capacity = capacity
        self._config = config
        #: None for a rail built without one -- a preview render, a test --
        #: which draws no network section at all rather than an empty one that
        #: is a different shape from the real thing.
        self._network = network
        self._places: list[tuple[str, str]] = []
        self._tokens: dict[str, str] = {}
        self._current = ""
        #: Every row that has text to elide, as (widget, full text). Rebuilt
        #: with the rail and walked on every resize.
        self._elidable: list[tuple[QPushButton, str]] = []
        self._collapsed = self._restore_collapsed()

        self.setProperty("role", "rail")
        self.setFrameShape(QFrame.NoFrame)
        self.setFocusPolicy(Qt.NoFocus)

        self._body = QWidget()
        self._body.setProperty("role", "railbody")
        self._body.setFocusPolicy(Qt.NoFocus)
        self._column = QVBoxLayout(self._body)
        self._column.setContentsMargins(6, 6, 6, 10)
        self._column.setSpacing(1)
        # The line the favorites bar was found by. Without it the widest row
        # in here becomes the rail's minimum width, and the splitter stops.
        self._column.setSizeConstraint(QLayout.SetNoConstraint)
        self._column.addStretch(1)

        self._scroll = QScrollArea()
        self._scroll.setWidget(self._body)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setFocusPolicy(Qt.NoFocus)
        # Never sideways. A rail that scrolls horizontally is a rail that is
        # too narrow, and the answer to that is elision, not a second bar.
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # The body is what the rows are laid out in, and it is resized by the
        # scroll area rather than by this widget -- so it is the body's own
        # resize that says how much room a row has, and this widget's may
        # arrive before the scroll area has passed the change on.
        self._body.installEventFilter(self)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.setSizeConstraint(QLayout.SetNoConstraint)
        outer.addWidget(self._scroll)

        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)

        favorites.changed.connect(self.rebuild)
        volumes.changed.connect(self.rebuild)
        capacity.changed.connect(self.rebuild)
        self.rebuild()

    # -------------------------------------------------------------- input

    def set_places(self, places) -> None:
        """The fixed places, as `(label, path)`. Worked out by `core.places`;
        this widget does no path arithmetic and no existence checking."""
        self._places = [(p.label, p.path) for p in places]
        self.rebuild()

    def set_current(self, path: str) -> None:
        """Mark the row the active pane is on, if one of them is it.

        Compared case-insensitively on the displayed form, which is what the
        rows carry. A pane somewhere no row names simply marks nothing.
        """
        wanted = (path or "").rstrip("\\").lower()
        if wanted == self._current:
            return
        self._current = wanted
        self.rebuild()

    def apply_tokens(self, tokens: dict[str, str]) -> None:
        """The colours the meters are painted from.

        Handed down from the window with the sheet they were rendered with,
        for the reason the rows and the chrome glyphs are: a widget that
        fetched its own would eventually be painted from a different render
        than the one it is styled by.
        """
        self._tokens = dict(tokens)
        self.rebuild()

    # ------------------------------------------------------------- building

    def rebuild(self) -> None:
        """Redraw the whole column.

        Rebuilt rather than diffed, which is the same call the favourites menu
        and the drive picker make: this is a few dozen small widgets and the
        alternative is a diff against a layout.
        """
        while self._column.count() > 1:          # keep the trailing stretch
            item = self._column.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._elidable = []

        if self._places:
            self._section(PLACES)
            for label, path in self._places:
                self._place_row(label, path)

        drives = list(getattr(self._volumes, "drives", []))
        if drives:
            self._section(DRIVES, menu=self._drives_menu)
            for drive in drives:
                self._drive_row(drive)

        # Only the ones Drives cannot show. A connection that *has* a letter
        # is already a row up there with its own meter and its own reconnect,
        # and listing it twice invites somebody to wonder which is the real
        # one. What is left is exactly the gap this section exists for: the
        # connections with no letter at all, and the locations somebody typed.
        locations = [item for item in getattr(self._network, "locations", [])
                     if not item.local] if self._network is not None else []
        if locations or self._network is not None:
            self._section(NETWORK, menu=self._network_menu)
            for location in locations:
                self._network_row(location)
            if not locations and NETWORK not in self._collapsed:
                # A heading with nothing under it, on purpose. The section is
                # where "add a network location" lives, and a section that
                # appears only once there is something in it is one nobody can
                # find the first time.
                #
                # And it says *why* it is empty when there is a why. "Nothing
                # here" and "the question could not be asked" look identical
                # and are not the same, which is the distinction this whole
                # section learned the hard way.
                trouble = getattr(self._network, "problem", "")
                self._empty_row(trouble or "Nothing without a drive letter")

        entries = list(self._favorites.entries) if self._favorites else []
        # Groups first, in the order the list mentions them, then whatever is
        # under no heading. Ungrouped last because a list nobody has grouped
        # then reads exactly as it did before groups existed, under one
        # heading rather than none.
        headings = [*self._favorites.groups(), UNGROUPED] if entries else []
        for heading in headings:
            members = [(position, entry) for position, entry in enumerate(entries)
                       if (entry.group or UNGROUPED) == heading]
            if not members:
                continue
            self._section(heading, menu=self._group_menu)
            for position, entry in members:
                self._favorite_row(position, entry)

        self._elide()

    def _section(self, title: str, *, menu=None) -> None:
        """A heading that folds what is under it.

        `menu` is what a right-click on the heading offers, or None for a
        heading with nothing to offer. It takes the heading's own widget so a
        menu pops where it was asked for.
        """
        head = QPushButton()
        head.setProperty("role", "railhead")
        head.setFocusPolicy(Qt.NoFocus)
        folded = title in self._collapsed
        head.setText(f"{FOLDED_MARK if folded else OPEN_MARK}  {title.upper()}")
        head.setToolTip(f"{title} - click to fold")
        head.clicked.connect(lambda _=False, name=title: self._toggle(name))
        if menu is not None:
            head.setContextMenuPolicy(Qt.CustomContextMenu)
            head.customContextMenuRequested.connect(
                lambda point, owner=head, fn=menu: fn(owner, point))
        self._column.insertWidget(self._column.count() - 1, head)

    def _place_row(self, label: str, path: str) -> None:
        if PLACES in self._collapsed:
            return
        self._row(label, path, tip=path, glyph="place")

    def _favorite_row(self, position: int, entry) -> None:
        heading = entry.group or UNGROUPED
        if heading in self._collapsed:
            return
        key = f"\nCtrl+{position + 1}" if position < 9 else ""
        button = self._row(entry.name, entry.path, tip=f"{entry.path}{key}",
                           glyph="star")
        button.setContextMenuPolicy(Qt.CustomContextMenu)
        button.customContextMenuRequested.connect(
            lambda point, owner=button, index=position:
            self._favorite_menu(owner, point, index))

    def _row(self, label: str, path: str, *, tip: str = "",
             glyph: str = "") -> QPushButton:
        button = QPushButton()
        button.setProperty("role", "railrow")
        if glyph and self._tokens:
            # 0.24: every row says what kind of place it is before it says
            # which. The current row's icon takes the accent with its text.
            current = bool(path) and path.rstrip("\\").lower() == self._current
            colour = self._tokens.get("accent_text" if current else "txt_2", "")
            button.setIcon(glyphs.icon(
                glyph, colour=colour, muted=self._tokens.get("txt_2", ""),
                ratio=float(self.devicePixelRatioF() or 1.0)))
            button.setIconSize(QSize(16, 16))
        # The path is carried on the widget rather than read back out of its
        # text or its tooltip: the text is elided and the tooltip has a
        # shortcut on the end of it, and both would be the wrong string.
        button.setProperty("target", path)
        button.setFocusPolicy(Qt.NoFocus)
        button.setToolTip(tip or path)
        button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        if path.rstrip("\\").lower() == self._current:
            button.setProperty("state", "current")
        button.clicked.connect(
            lambda _=False, target=path: self.chosen.emit(target, False))
        button.installEventFilter(self)
        self._column.insertWidget(self._column.count() - 1, button)
        self._elidable.append((button, label))
        button.setText(label)
        return button

    def _drive_row(self, drive: dict) -> None:
        if DRIVES in self._collapsed:
            return
        letter = drive.get("letter", "")
        row = DriveRow(letter, drive.get("unc") or "", drive.get("type", ""),
                       usage=self._capacity.usage(letter),
                       tokens=self._tokens,
                       ejectable=bool(drive.get("ejectable")))
        row.setProperty("state",
                        "current" if letter.lower() == self._current[:2] else "")
        row.chosen.connect(self.chosen)
        row.measureRequested.connect(self.measureRequested)
        row.reconnectRequested.connect(self.reconnectRequested)
        row.ejectRequested.connect(self.ejectRequested)
        self._column.insertWidget(self._column.count() - 1, row)

    def _network_row(self, location) -> None:
        if NETWORK in self._collapsed:
            return
        tip = location.remote if not location.local else \
            f"{location.remote}\n{location.local}"
        button = self._row(location.label, location.path, tip=tip,
                           glyph="network")
        button.setProperty(
            "state",
            "current" if location.path.lower() == self._current.lower()[:len(location.path)]
            else "")
        button.setContextMenuPolicy(Qt.CustomContextMenu)
        button.customContextMenuRequested.connect(
            lambda point, owner=button, where=location:
            self._location_menu(owner, point, where))

    def _empty_row(self, text: str) -> None:
        """A greyed line under a heading that has nothing in it."""
        button = self._row(text, "", tip="")
        button.setEnabled(False)

    def _network_menu(self, button, point) -> None:
        menu = QMenu(self)
        menu.addAction("Add a network location", self.addLocationRequested.emit)
        menu.addAction("Refresh", self.refreshNetworkRequested.emit)
        menu.exec(button.mapToGlobal(point))

    def _location_menu(self, button, point, location) -> None:
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        again = menu.addAction(
            "Reconnect",
            lambda: self.reconnectRequested.emit(location.path))
        again.setToolTip("Attach to this share again, for one that has stopped "
                         "answering. Windows uses this session's own account.")
        menu.addSeparator()
        menu.addAction("Add a network location", self.addLocationRequested.emit)
        forget = menu.addAction(
            "Remove from this list",
            lambda: self.forgetLocationRequested.emit(location.path))
        # Only the saved ones can be removed. A connection is there because
        # Windows says it is, and a menu entry that appears to remove it would
        # be one that does nothing the next time the list is read.
        forget.setEnabled(bool(getattr(location, "saved", False)))
        menu.exec(button.mapToGlobal(point))

    # ------------------------------------------------------------- folding

    def _restore_collapsed(self) -> set[str]:
        stored = self._config.get("rail.collapsed")
        if not isinstance(stored, list):
            return set()
        return {name for name in stored if isinstance(name, str) and name}

    def _toggle(self, title: str) -> None:
        if title in self._collapsed:
            self._collapsed.discard(title)
        else:
            self._collapsed.add(title)
        self._config.set("rail.collapsed", sorted(self._collapsed))
        self.rebuild()

    # ------------------------------------------------------------- gestures

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt naming
        """Middle click on a row opens it in a tab behind.

        The same gesture and the same meaning as a middle click on a folder in
        the listing or on a button in the favorites bar. Filtered rather than
        subclassed, for the reason the tab strip gives: a subclass would be a
        class to find, and this belongs with the rest of the rail's input.
        """
        if watched is self._body and event.type() == QEvent.Resize:
            self._elide(event.size().width())
            return False
        if event.type() == QEvent.MouseButtonRelease and \
                event.button() == Qt.MiddleButton:
            target = watched.property("target") if isinstance(watched, QPushButton) else None
            if target:
                self.chosen.emit(str(target), True)
                return True
        return super().eventFilter(watched, event)

    def _favorite_menu(self, button, point, index: int) -> None:
        entries = self._favorites.entries
        if not 0 <= index < len(entries):
            return
        entry = entries[index]
        menu = QMenu(self)
        menu.addAction("Go here", lambda: self.chosen.emit(entry.path, False))
        menu.addAction("Open in new tab",
                       lambda: self.chosen.emit(entry.path, True))
        menu.addSeparator()
        move = menu.addMenu("Move to group")
        for name in self._favorites.groups():
            if name == entry.group:
                continue
            move.addAction(name,
                           lambda _=False, g=name: self.groupRequested.emit(index, g))
        move.addAction("New group...",
                       lambda: self.groupRequested.emit(index, ""))
        if entry.group:
            move.addSeparator()
            move.addAction(f"Out of {entry.group}",
                           lambda: self.groupRequested.emit(index, UNGROUPED))
        menu.addSeparator()
        menu.addAction("Move up", lambda: self._move(index, -1))
        menu.addAction("Move down", lambda: self._move(index, 1))
        menu.addAction("Remove", lambda: self._favorites.remove(index))
        menu.addSeparator()
        menu.addAction("Add this folder", self.addFavoriteRequested.emit)
        menu.addAction("Manage favorites", self.manageFavoritesRequested.emit)
        menu.exec(button.mapToGlobal(point))

    def _group_menu(self, button, point) -> None:
        menu = QMenu(self)
        menu.addAction("Add this folder", self.addFavoriteRequested.emit)
        menu.addAction("Manage favorites", self.manageFavoritesRequested.emit)
        menu.exec(button.mapToGlobal(point))

    def _drives_menu(self, button, point) -> None:
        menu = QMenu(self)
        menu.addAction("Rescan drives", self.rescanRequested.emit)
        menu.exec(button.mapToGlobal(point))

    def _move(self, index: int, step: int) -> None:
        self._favorites.move(index, step)
        self._favorites.commit_order()

    # ------------------------------------------------------------- fitting

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        """Narrow enough to be dragged to. What does not fit is elided, not
        scrolled sideways and not pushed into the window's minimum."""
        return QSize(96, 0)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        return QSize(int(self._config.get("rail.width")), 0)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self._elide()

    def _elide(self, width: int | None = None) -> None:
        """Cut every row's text to the width it actually got.

        In the middle rather than at the end: the two ends of a favourite name
        are the parts that tell two of them apart, which is the same reasoning
        the breadcrumb drops its middle crumbs for.

        Measured against the *body's* width rather than this widget's, and the
        width is taken from the resize event where there is one. A pass run
        before the scroll area has been laid out measures against whatever the
        viewport happened to be, which is how a long favourite came back
        clipped by the edge of the rail rather than ending in an ellipsis --
        clipped and elided look nothing alike, and only one of them says there
        is more.
        """
        if width is None:
            width = self._body.width() or self._scroll.viewport().width()
        room = max(24, width - _ROW_INSET)
        for button, full in self._elidable:
            metrics = button.fontMetrics()
            button.setText(metrics.elidedText(full, Qt.ElideMiddle, room))


class DriveRow(QWidget):
    """One drive: its letter, what it is mapped to, and how full it is.

    Painted rather than assembled out of labels and a `QProgressBar`, because
    the meter is the thing a stylesheet cannot express -- the same reason
    `app/ui/rows.py` exists. It is also the cheaper answer: a drive row is
    three pieces of text and a bar, and three widgets each with their own
    stylesheet lookup is more machinery than one `paintEvent`.

    A drive with no measurement draws no meter at all, and that is deliberate.
    An empty bar and a bar that has not been asked look identical, and only
    one of them is a fact -- so an unmeasured drive shows its target and
    nothing else, and says how to ask in its tooltip.
    """

    chosen = Signal(str, bool)
    measureRequested = Signal(str)
    #: The UNC behind a mapped drive, for re-attaching to it. Only a remote
    #: drive has one, which is why the entry is only offered for those.
    reconnectRequested = Signal(str)
    #: 0.27: a USB drive's eject button, or Eject on its menu.
    ejectRequested = Signal(str)

    def __init__(self, letter: str, unc: str, kind: str, *, usage=None,
                 tokens=None, ejectable: bool = False,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._letter = letter
        self._ejectable = ejectable
        self._unc = unc
        self._kind = kind
        self._usage = usage
        self._tokens = dict(tokens or {})
        self._hover = False
        self._hover_eject = False
        self.setProperty("role", "raildrive")
        self.setFocusPolicy(Qt.NoFocus)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.setToolTip(self._tip())

    # ------------------------------------------------------------- geometry

    @property
    def path(self) -> str:
        """Where a click goes. The drive root, with its separator: `C:` on its
        own means the current directory on C:, which is not a place."""
        return self._letter.rstrip("\\") + "\\"

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        line = self.fontMetrics().height()
        extra = METER_HEIGHT + 6 if self._usage is not None else 0
        return QSize(0, line + 8 + extra)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        return QSize(0, self.sizeHint().height())

    # -------------------------------------------------------------- drawing

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        metrics = self.fontMetrics()
        rect = self.rect().adjusted(0, 1, 0, -1)

        if self._hover:
            band = QPainterPath()
            band.addRoundedRect(rect, 6, 6)
            painter.fillPath(band, self._colour("bg_3"))

        left = rect.left() + 8
        right = rect.right() - 8
        top = rect.top() + 3

        if self._tokens:
            current = self.property("state") == "current"
            glyph = glyphs.icon(
                "network" if self._unc else "drive",
                colour=self._tokens.get("accent_text" if current else "txt_2", ""),
                muted=self._tokens.get("txt_2", ""),
                ratio=float(self.devicePixelRatioF() or 1.0)).pixmap(16, 16)
            painter.drawPixmap(left, top + (metrics.height() - 16) // 2, glyph)
            left += 16 + 8

        painter.setPen(self._colour("txt_0"))
        font = painter.font()
        font.setBold(True)
        painter.setFont(font)
        letter_width = metrics.horizontalAdvance("W:") + 6
        painter.drawText(left, top, letter_width, metrics.height(),
                         Qt.AlignLeft | Qt.AlignVCenter, self._letter)
        font.setBold(False)
        painter.setFont(font)

        label_left = left + letter_width
        if self._ejectable and self._tokens:
            button = self.eject_rect()
            if self._hover_eject:
                shape = QPainterPath()
                shape.addRoundedRect(button, 5, 5)
                painter.fillPath(shape, self._colour("bg_4"))
            mark = glyphs.icon("eject", colour=self._tokens.get("txt_0", ""),
                               muted=self._tokens.get("txt_2", ""), size=14,
                               ratio=float(self.devicePixelRatioF() or 1.0)).pixmap(14, 14)
            painter.drawPixmap(int(button.center().x() - 7), int(button.center().y() - 7), mark)
            right = int(button.left()) - 6
        painter.setPen(self._colour("txt_2"))
        painter.drawText(
            label_left, top, max(0, right - label_left), metrics.height(),
            Qt.AlignLeft | Qt.AlignVCenter,
            metrics.elidedText(self._label(), Qt.ElideMiddle,
                               max(0, right - label_left)))

        if self._usage is None:
            painter.end()
            return

        bar_top = top + metrics.height() + 3
        width = max(0, right - left)
        track = QPainterPath()
        track.addRoundedRect(left, bar_top, width, METER_HEIGHT,
                             METER_RADIUS, METER_RADIUS)
        painter.fillPath(track, self._colour("bg_4"))
        filled = int(width * self._usage.share)
        if filled > 0:
            fill = QPainterPath()
            fill.addRoundedRect(left, bar_top, filled, METER_HEIGHT,
                                METER_RADIUS, METER_RADIUS)
            painter.fillPath(
                fill,
                self._colour("warn" if self._usage.share >= NEARLY_FULL
                             else "accent"))
        painter.end()

    def eject_rect(self) -> QRectF:
        """The eject button's square at the right of the first line."""
        line = self.fontMetrics().height()
        side = 22
        top = 1 + 3 + (line - side) / 2
        return QRectF(self.width() - 8 - side + 2, top, side, side)

    def _label(self) -> str:
        """The line beside the letter: what the drive is, in as few characters
        as say something. A mapping is named by its target, because that is
        the thing two mapped letters differ by; everything else is named by
        what it is, and a measured drive says how much is left instead."""
        if self._usage is not None and self._usage.total > 0:
            return f"{format_size(self._usage.free)} free"
        return self._unc or self._kind

    def _colour(self, name: str):
        return parse_colour(self._tokens.get(name))

    def _tip(self) -> str:
        parts = [self.path]
        if self._unc:
            parts.append(self._unc)
        if self._usage is not None and self._usage.total > 0:
            parts.append(f"{format_size(self._usage.free)} free of "
                         f"{format_size(self._usage.total)}")
        else:
            parts.append("right-click to measure")
        return "\n".join(parts)

    # ------------------------------------------------------------- gestures

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._hover = True
        self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._hover = False
        self._hover_eject = False
        self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt naming
        over = self._ejectable and self.eject_rect().contains(event.position())
        if over != self._hover_eject:
            self._hover_eject = over
            self.setToolTip(f"Eject {self._letter}" if over else self._tip())
            self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.button() == Qt.LeftButton and self._ejectable \
                and self.eject_rect().contains(event.position()):
            self.ejectRequested.emit(self._letter)
        elif event.button() == Qt.LeftButton:
            self.chosen.emit(self.path, False)
        elif event.button() == Qt.MiddleButton:
            self.chosen.emit(self.path, True)
        super().mouseReleaseEvent(event)

    def _menu(self, point) -> None:
        menu = QMenu(self)
        menu.addAction("Go here", lambda: self.chosen.emit(self.path, False))
        menu.addAction("Open in new tab", lambda: self.chosen.emit(self.path, True))
        menu.addSeparator()
        # The one entry that opens a volume, and the reason this is a menu
        # rather than something that happens by itself: on a mapped drive
        # whose server has gone, this is the call that takes the deadline.
        label = ("Measure again" if self._usage is not None
                 else "Measure how full it is")
        menu.addAction(label, lambda: self.measureRequested.emit(self._letter))
        if self._kind == "remote" and self._unc:
            # A letter can be present and dead at the same time -- the session
            # table still lists it while the server behind it has gone -- so
            # this is offered on every mapped drive rather than only on one
            # that has already failed. It attaches to the *UNC*, which is the
            # thing that still exists when the letter's session has died.
            menu.addSeparator()
            menu.addAction("Reconnect",
                           lambda: self.reconnectRequested.emit(self._unc))
        if self._ejectable:
            menu.addSeparator()
            menu.addAction("Eject", lambda: self.ejectRequested.emit(self._letter))
        menu.exec(self.mapToGlobal(point))

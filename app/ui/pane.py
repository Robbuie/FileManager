"""One pane: a drive picker, a tab strip, a path bar, the listing, and a status line.

Presentation and input, and nothing else. Every question this widget has about
the filesystem is asked of `core.Pane`, which asks a worker. There is no `os`,
no `pathlib`, and no path arithmetic here -- not even a `..`, which is why the
model carries the parent row and `core` decides what activating it means.

The active pane is drawn with the accent on its border. With two panes and one
keyboard, knowing where the next keystroke lands is not decoration.
"""

from __future__ import annotations

from PySide6.QtCore import (
    QItemSelection,
    QItemSelectionModel,
    QModelIndex,
    QPoint,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtCore import QEvent, QRect, QSize, QTimer
from PySide6.QtGui import (
    QAction,
    QGuiApplication,
    QFontMetrics,
    QIcon,
    QImage,
    QKeySequence,
    QPixmap,
)

from app.core.commands import normalise_shortcut
from app.core.icons import ROW_ICON
from app.core.listing import (
    HEADERS,
    Column,
    count_of,
    format_size,
    split_name,
)
from app.io import paths
from app.io.protocol import (
    MENU_COMMAND,
    MENU_SEPARATOR,
    MENU_SUBMENU,
    PREVIEW_BOX,
    MenuItem,
    Preview,
)
from app.ui import dialogs, glyphs
from app.ui.breadcrumb import Breadcrumb
from app.ui.favorites import FavoritesBar
from app.ui.grid import GridView
from app.ui.header import FolderHeader
from app.ui.preview import PANE_TEXT_BYTES, PreviewPanel
from app.ui.rows import RowDelegate
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QSplitter,
    QStackedWidget,
    QTabBar,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


#: Shell commands the pane already offers itself, by the shell's own name for
#: them rather than by their label. A verb is stable across languages and
#: across the wording Windows uses this year; the label is neither.
#:
#: The decision is here rather than in the shell host on purpose. The host
#: reads the menu Windows built and does not edit it -- what a menu ends up
#: showing is a question about this application's own verbs, and this is where
#: those are. Only the top level is filtered: a verb inside somebody's submenu
#: means what that extension says it means.
#:
#: Cut, Copy and Paste were deliberately *not* in here until 0.15, because
#: until 0.15 this application's Copy meant the other pane and the shell's
#: meant the clipboard -- two different commands that happened to share a
#: word. Now the pane has both, named apart ("Copy" and "Copy to other pane"),
#: so the shell's three are the same three and the menu was showing each of
#: them twice.
#:
#: Dropping the shell's Paste is the one worth stating plainly: it is not only
#: a duplicate, it is the wrong implementation. The shell's paste runs in the
#: menu host and copies the files itself, with no queue, no progress and no
#: cancel -- which over a share is exactly the wait this application exists to
#: escape, arriving through its own context menu.
SHELL_VERBS_WE_HAVE = frozenset({
    "open", "delete", "rename", "refresh", "cut", "copy", "paste",
})

#: Milliseconds of not typing before a quick search forgets what was typed.
#: Long enough to think about the next letter of a long name, short enough
#: that coming back to the keyboard starts a new search rather than extending
#: one nobody remembers making.
SEARCH_FORGETS_AFTER = 1500


def _leaf(path: str) -> str:
    """The name at the end of a path.

    The one piece of path arithmetic in this file, and it is here rather than in
    `core` because it is not arithmetic about the filesystem -- it is a label for
    a panel, taken from a string this widget was already handed. Anything that
    decides where a path *points* still goes through `app/io/paths.py`.
    """
    for separator in ("\\", "/"):
        if separator in path:
            path = path.rsplit(separator, 1)[-1]
    return path


#: What each column starts at before anybody drags one, in pixels. The name is
#: the exception: it is given whatever is left over, which is what a file
#: manager is for, and this number is only its floor while the pane has no
#: width yet.
#:
#: Modified is the one that cannot be trimmed -- it holds a fixed sixteen
#: characters, and a date cut off at the hour is worse than no date -- so the
#: other three give way to it.
DEFAULT_WIDTHS = {
    Column.NAME: 320,
    Column.EXT: 52,
    Column.SIZE: 92,
    Column.AGE: 46,
    Column.MODIFIED: 138,
    Column.LOCATION: 170,
}

#: Extra height on a row that starts a group in grouped flat view, where the
#: delegate draws the folder's heading above the row itself. One number, kept
#: in `app/ui/rows.py` beside the code that draws into it.
from app.ui.rows import GROUP_HEAD  # noqa: E402

#: The narrowest the name column is allowed to get itself down to while it is
#: working out its own width. A name at `MIN_COLUMN` is a column of first
#: letters, which is what a pane dragged narrow used to produce -- the other
#: four add up to 328 pixels of fixed width, so below about 380 of viewport
#: there was nothing left for the name and the table grew a horizontal
#: scrollbar instead. Below this the other columns give way; see `_widen_name`.
NAME_FLOOR = 150

#: The order the other columns give way in when there is not room for them all,
#: least useful first. Age duplicates what Modified says and goes first; Ext
#: repeats the end of the name; Modified is wide and degrades gracefully,
#: losing the time before the date. Size is last because a size column too
#: narrow for "1.2 M" is not a narrow size column, it is a missing one -- and
#: because deciding what to copy is what these columns are read for.
#:
#: A name cut to three letters is worse than any of them, which is why this
#: exists at all. Hiding a column outright is on the header's own menu and is
#: the better answer for somebody who works at that width.
GIVE_WAY = (Column.AGE, Column.EXT, Column.LOCATION, Column.MODIFIED, Column.SIZE)

#: The narrowest each giving-way column can be and still say what it says.
#: Below this a column is not narrow, it is broken -- "EX", "GE" and "MO" in
#: the header, an age chip drawn over the size -- which is what a pane with the
#: preview panel open used to show. So a column squeezed past this is hidden
#: for as long as the pane is that narrow, rather than drawn at `MIN_COLUMN`.
#: The hiding is not stored: widen the pane and it comes back.
READABLE = {
    Column.AGE: 40,
    Column.EXT: 40,
    Column.MODIFIED: 100,
    Column.SIZE: 64,
    Column.LOCATION: 90,
}

#: The narrowest a column may be dragged or fitted to. Not zero: a column
#: dragged to nothing is indistinguishable from one that is hidden, and the
#: header menu is where hiding belongs.
MIN_COLUMN = 28

#: Slack added when fitting a column to its contents: the cell's own padding
#: either side, and the room a sort indicator takes in the header.
CELL_PADDING = 12
HEADER_PADDING = 22

#: The gap between a row's icon and its text, which a text measurement knows
#: nothing about.
ICON_GAP = 8

#: The two buttons on the side of a mouse, and what they mean here. Every
#: browser and Explorer itself answer them with back and forward, so a file
#: manager that ignores them is one where the thumb does nothing -- which reads
#: as the application being unfinished rather than as a missing feature.
#:
#: Qt names them `BackButton` and `ForwardButton`; Windows calls them XBUTTON1
#: and XBUTTON2. They arrive as ordinary mouse events and no widget in Qt does
#: anything with them by default.
HISTORY_BUTTONS = {Qt.BackButton: -1, Qt.ForwardButton: 1}


def _drag_out(view: QAbstractItemView) -> None:
    """Let rows be dragged out of the window -- into an email, onto the
    desktop, into another program -- and never dropped in.

    `DragOnly` rather than `DragDrop`, because a drop here would be a copy or a
    move this application did not confirm, which `CLAUDE.md` rules out; copy as
    the only action for the reason `ListingModel.supportedDragActions` gives.
    Both views get it, because a second view that inherits the commands and
    not the gestures is the mistake 0.16 made with keys.
    """
    view.setDragEnabled(True)
    view.setDragDropMode(QAbstractItemView.DragOnly)
    view.setDefaultDropAction(Qt.CopyAction)


def refit_popup(menu: QWidget, anchor: QPoint, area: QRect) -> None:
    """Size a popup to everything now in it, then place it on the screen.

    **`resize(sizeHint())`, never `adjustSize()`.** `adjustSize` on a top-level
    widget caps it at two thirds of the screen, so a context menu grown past
    that by the shell's entries was drawn shorter than its contents -- the last
    entries, Properties among them, were cut off inside a menu that was itself
    on screen. A `QMenu`'s own size hint already accounts for the screen: past
    its height the entries wrap into another column.
    """
    size = menu.sizeHint()
    if menu.size() != size:
        menu.resize(size)
    where = fit_popup(anchor, size, area)
    if where != menu.pos():
        menu.move(where)


def fit_popup(anchor: QPoint, size: QSize, area: QRect) -> QPoint:
    """Where a popup of this size should sit so it stays on the screen.

    Pure, and separate from the menu for one reason: it is the part that can be
    checked. Placing a real popup needs a screen, a mouse grab and a window
    manager; deciding *where* is four comparisons, and those are where the bug
    was.

    The rules, in the order Explorer's own menus follow them:

      * below and to the right of the pointer, which is where a menu belongs;
      * too wide, and it goes to the left of the pointer instead;
      * too tall, and it **flips above** the pointer rather than sliding up --
        a menu that slides has its first entry somewhere new every time, and
        the first entry is the one being aimed at;
      * too tall to flip, and it sits as low as it can while still fitting,
        which for a menu taller than the screen means the top of the screen and
        Qt's own scrolling.
    """
    x = anchor.x()
    if x + size.width() > area.right():
        x = anchor.x() - size.width()
    x = max(area.left(), min(x, area.right() - size.width()))

    y = anchor.y()
    if y + size.height() > area.bottom():
        above = anchor.y() - size.height()
        y = above if above >= area.top() else area.bottom() - size.height()
    y = max(area.top(), y)
    return QPoint(x, y)


def shortcut_text(event) -> str:
    """What key was pressed, spelled the way the command table spells it.

    Qt's own `toString` does the work and `normalise_shortcut` settles the
    spelling, so there is one definition of what `Ctrl+F2` is rather than two
    that agree most of the time.

    A bare printable character answers "" deliberately. Typing a letter into
    the listing is the quick search, which is caught in the event filter before
    this is ever reached -- so a command bound to a bare letter would be a key
    that never fires, and returning it here would be this widget claiming a
    keystroke it does not get. `commands.shortcut_refusal` says the same thing
    in the editor, where somebody can still change it.
    """
    modifiers = event.modifiers()
    plain = not (modifiers & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier))
    if plain and event.text().isprintable() and event.text().strip():
        return ""
    try:
        return normalise_shortcut(QKeySequence(event.keyCombination()).toString())
    except (TypeError, ValueError):  # a key Qt has no sequence for
        return ""


class SortHeader(QHeaderView):
    """The listing's header, with a chevron on the column it is sorted by.

    0.24. The stylesheet sets Qt's own arrows to nothing, because the style's
    arrow is a platform bitmap in the platform's grey -- so for fourteen
    releases nothing on screen said which column was sorted, or which way.
    Painted from the same glyph set as the rest of the chrome, in the accent.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(Qt.Horizontal, parent)
        self._colour = "#4aa8ff"

    def set_colour(self, colour: str) -> None:
        self._colour = colour
        self.viewport().update()

    def paintSection(self, painter, rect, logical: int) -> None:  # noqa: N802
        painter.save()
        super().paintSection(painter, rect, logical)
        painter.restore()
        if not self.isSortIndicatorShown() or logical != self.sortIndicatorSection():
            return
        model = self.model()
        if model is None:
            return
        text = str(model.headerData(logical, Qt.Horizontal, Qt.DisplayRole) or "")
        font = self.font()
        font.setBold(True)
        width = QFontMetrics(font).horizontalAdvance(text.upper()) + 2
        size = 12
        name = ("sort_up" if self.sortIndicatorOrder() == Qt.AscendingOrder
                else "sort_down")
        pixmap = glyphs.icon(name, colour=self._colour, muted=self._colour,
                             size=size,
                             ratio=float(self.devicePixelRatioF() or 1.0)
                             ).pixmap(size, size)
        align = model.headerData(logical, Qt.Horizontal, Qt.TextAlignmentRole)
        right = bool(align is not None and int(align) & int(Qt.AlignRight))
        pad = 7
        if right:
            x = rect.right() - pad - width - size - 1
        else:
            x = rect.left() + pad + width + 3
        x = max(rect.left() + 1, min(x, rect.right() - size))
        y = rect.center().y() - size // 2
        painter.drawPixmap(x, y, pixmap)


class PaneWidget(QFrame):

    activated = Signal(object)          # this widget, when it takes focus
    transferRequested = Signal(str)     # "copy" or "move", from F5 and F6
    #: "copy", "cut" or "paste", from Ctrl+C, Ctrl+X and Ctrl+V. A signal
    #: rather than a call for the reason every other key here is one: the
    #: window owns the clipboard and the queue, and the pane owns the keys.
    clipboardRequested = Signal(str)
    addFavoriteRequested = Signal()     # from the favorites bar's own menu
    manageFavoritesRequested = Signal()
    #: F3 on a file. A signal rather than a call for the reason the clipboard
    #: keys are signals: the viewer is a window, and windows belong to the
    #: window. What the pane provides is the folder, the files in the order it
    #: has them, and which one the cursor is on.
    viewRequested = Signal(str, list, int)
    #: An external command, by its id in the table. The pane matches the
    #: keystroke because the pane is where a key is safe to act on; what to do
    #: with the id is the window's business.
    commandRequested = Signal(str)

    def __init__(self, pane, volumes, metrics: dict[str, int],
                 favorites=None, parent: QWidget | None = None):
        super().__init__(parent)
        self._pane = pane
        self._volumes = volumes
        self._favorites = favorites
        self._summary = ("", "idle")
        self.setProperty("pane", "true")
        self.setProperty("active", "false")
        self.setFrameShape(QFrame.NoFrame)

        self._tabs = QTabBar()
        self._tabs.setExpanding(False)
        # Not `setTabsClosable`: that draws the platform's close icon, which
        # follows the OS rather than the theme and lands as a bright red X in
        # the middle of a dark grey strip. A button of our own follows the
        # tokens like the rest of the chrome.
        self._tabs.setTabsClosable(False)
        self._tabs.setMovable(True)
        self._tabs.setDrawBase(False)
        # 0.24: a folder icon, the folder name cut at the end rather than the
        # tab growing, and a close button only where the pointer or the
        # current tab is. The width cap is in the sheet.
        self._tabs.setElideMode(Qt.ElideRight)
        self._tabs.setUsesScrollButtons(True)
        self._tabs.setMouseTracking(True)
        self._tab_hover = -1
        self._tabs.currentChanged.connect(self._pane.select_tab)
        self._tabs.currentChanged.connect(lambda _i: self._show_close_buttons())
        self._tabs.tabCloseRequested.connect(self._pane.close_tab)
        # Without this the strip's order and the pane's disagree after a drag,
        # and every index afterwards -- the one a click selects, the one a
        # close button reports -- names a different tab than the one under it.
        self._tabs.tabMoved.connect(self._pane.move_tab)
        self._tabs.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tabs.customContextMenuRequested.connect(self._on_tab_menu)
        # Middle click closes a tab and opens a folder in one behind. Both are
        # filtered rather than handled in a subclass: a QTabBar subclass would
        # be a class to find, and the behaviour belongs with the rest of this
        # widget's input.
        self._tabs.installEventFilter(self)

        # The drive picker. It lists what the session table says exists and
        # probes nothing, so it opens instantly even with a mapped server that
        # is down -- the failure only shows once a listing is asked for.
        self._drives = QComboBox()
        self._drives.setProperty("role", "drives")
        self._drives.setFocusPolicy(Qt.NoFocus)
        self._drives.setToolTip("Drive")
        self._drives.activated.connect(self._claim)
        self._drives.activated.connect(self._on_drive_chosen)
        # 0.24: the picker is no longer on screen. The drive is the first thing
        # in the path bar, as a button that drops the same list down -- one box
        # saying where you are instead of two. The combo stays as the list's
        # model, so nothing else that reads it changes.
        self._drive_button = QToolButton()
        self._drive_button.setProperty("role", "crumbdrive")
        self._drive_button.setToolTip("Drive")
        self._drive_button.setFocusPolicy(Qt.NoFocus)
        self._drive_button.setCursor(Qt.PointingHandCursor)
        self._drive_button.setIconSize(QSize(16, 16))
        self._drive_button.clicked.connect(self._claim)
        self._drive_button.clicked.connect(self._open_drive_menu)

        # Drawn icons, not text glyphs. Both follow the theme; only one of
        # them is the same weight and size as the other four, because a text
        # arrow is whatever the font that answered decided it was. See
        # `app/ui/glyphs.py`. The pictures are put on in `apply_tokens`, which
        # is also where they are replaced when the theme changes.
        self._back = self._nav("back", "Back (Alt+Left)", self._pane.go_back)
        self._forward = self._nav("forward", "Forward (Alt+Right)", self._pane.go_forward)
        self._up = self._nav("up", "Up (Backspace)", self._pane.go_up)
        self._reload = self._nav("refresh", "Refresh (Ctrl+R)", self._pane.refresh)
        self._sift = self._nav("filter", "Filter this folder (Ctrl+F)",
                               self.toggle_filter)

        # Two widgets for one slot. The breadcrumb is what is normally there;
        # the field is behind it, and Ctrl+L or a click on the bar's empty
        # space swaps them. Anybody who types paths keeps typing paths, and
        # everybody else gets every folder above this one as a target.
        self._crumbs = Breadcrumb()
        self._crumbs.navigate.connect(self._claim)
        self._crumbs.navigate.connect(self._pane.navigate)
        self._crumbs.editRequested.connect(self.focus_path)
        self._crumbs.set_lead(self._drive_button)
        # A chevron is a target now, and a target in a pane claims it -- the
        # same rule the nav buttons, the crumbs, the favourites and the drive
        # picker follow. Opening a dropdown in the other pane and choosing
        # from it must not walk this one.
        self._crumbs.siblingsWanted.connect(self._claim)
        self._crumbs.siblingsWanted.connect(self._ask_siblings)

        self._path = QLineEdit()
        self._path.setClearButtonEnabled(False)
        self._path.returnPressed.connect(self._on_path_entered)
        self._path.hide()

        # The filter is hidden until asked for. A filter box that is always
        # there is a box that eventually has something left in it, and a folder
        # that looks empty for a reason nobody can see is worse than no filter.
        self._filter = QLineEdit()
        self._filter.setProperty("role", "filter")
        self._filter.setPlaceholderText("Filter this folder  (Esc to clear)")
        self._filter.textChanged.connect(self._pane.set_filter)
        self._filter.hide()

        self._view = QTableView()
        self._header = SortHeader(self._view)
        # Clickable, said out loud. A header a view makes for itself is
        # clickable already, and one built here is not -- and `setSortingEnabled`
        # does not set it, it only shows the indicator and listens for it to
        # change. So from 0.24, when this header was added to draw the chevron,
        # until 0.29.9, clicking a column heading did nothing at all: the
        # dividers still dragged, which is what made it read as sorting being
        # broken rather than as the header not hearing the click.
        self._header.setSectionsClickable(True)
        self._view.setHorizontalHeader(self._header)
        self._view.setModel(self._pane.current.model)
        self._view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        _drag_out(self._view)
        self._view.setShowGrid(False)
        # Off. Stripes and a grid are two devices doing the job of row
        # spacing, and the pair of them is the strongest single signal of
        # software from another decade. What replaces them is air, a hover
        # that follows the mouse, and a selection with a shape.
        self._view.setAlternatingRowColors(False)
        # The hover needs the mouse's position between clicks, which a view
        # does not track by default.
        self._view.setMouseTracking(True)
        self._rows = RowDelegate(self._view)
        self._view.setItemDelegate(self._rows)
        self._view.entered.connect(self._on_row_entered)
        self._view.setWordWrap(False)
        self._view.setSortingEnabled(True)
        # Said rather than left to the style, which picks a size from the
        # platform and would leave the rows taller than the density asked for.
        # A larger pixmap on a scaled display still lands in this box: it
        # carries its own density and Qt draws it at the logical size.
        self._view.setIconSize(QSize(ROW_ICON, ROW_ICON))
        self._view.setTabKeyNavigation(False)  # Tab belongs to the window
        self._view.verticalHeader().setVisible(False)
        self._view.horizontalHeader().setStretchLastSection(False)
        # `activated` and nothing else. Qt emits it for both Enter and a
        # double click, so connecting `doubleClicked` as well opened everything
        # twice -- two copies of whatever the shell launched, from one gesture.
        # It also means single click opens where the user has told Windows that
        # is what a click does, which is the correct answer to that setting.
        self._view.activated.connect(self._on_activated)
        self._view.setContextMenuPolicy(Qt.CustomContextMenu)
        self._view.customContextMenuRequested.connect(self._on_context_menu)
        self._view.viewport().installEventFilter(self)
        # The view too, and for the keyboard. `QAbstractItemView` answers a
        # printable key with `keyboardSearch`, its own prefix jump, which never
        # reaches this widget and cannot say what it matched or that it matched
        # nothing. Intercepting before the view is the only place the search
        # can be this application's.
        self._view.installEventFilter(self)
        header = self._view.horizontalHeader()
        header.setSectionsMovable(False)
        header.setMinimumSectionSize(MIN_COLUMN)
        header.setContextMenuPolicy(Qt.CustomContextMenu)
        header.customContextMenuRequested.connect(self._on_header_menu)
        # Qt's own double click on a divider is `ResizeToContents`, which
        # measures every row. Taken over rather than left alone: on a folder of
        # 50,000 files the stock gesture is the exact cost this application is
        # built to avoid, and it is the gesture people already know.
        header.sectionHandleDoubleClicked.connect(self._fit_column)
        header.sectionResized.connect(self._on_section_resized)
        # Location is last in the model and belongs beside the name.
        header.moveSection(header.visualIndex(int(Column.LOCATION)), 1)
        self._grouped_rows: list[int] = []
        self._apply_columns()
        # A header defaults its indicator to *descending*, and enabling sorting
        # applies it, so a model that sorted itself ascending gets flipped the
        # moment it is shown. Said once, out loud, rather than left to a reader
        # to rediscover from a listing that starts at Z.
        self._view.sortByColumn(int(Column.NAME), Qt.AscendingOrder)

        # The same rows as cells. One model, two views, and -- once
        # `_share_selection` has run -- one selection model, which is what makes
        # switching view keep what was marked and lets every command in this
        # widget go on asking `self._view.selectionModel()` without caring which
        # view is in front.
        self._grid = GridView(self._pane.thumbnails)
        _drag_out(self._grid)
        self._grid.activated.connect(self._on_activated)
        self._grid.setContextMenuPolicy(Qt.CustomContextMenu)
        self._grid.customContextMenuRequested.connect(self._on_context_menu)
        self._grid.installEventFilter(self)
        self._grid.viewport().installEventFilter(self)
        self._views = QStackedWidget()
        self._views.addWidget(self._view)
        self._views.addWidget(self._grid)
        if self._pane.thumbnails is not None:
            self._grid.set_cell(int(pane.config.get("preview.thumb_size")))
            self._pane.thumbnails.changed.connect(self._grid.viewport().update)

        # The preview panel, beside the listing rather than under the window.
        # See `app/ui/preview.py` for why it is in the pane.
        self._preview = PreviewPanel()
        self._preview.activated.connect(self._claim)
        self._preview.setVisible(bool(pane.config.get("preview.pane")))
        self._body = QSplitter(Qt.Horizontal)
        self._body.setChildrenCollapsible(False)
        self._body.addWidget(self._views)
        self._body.addWidget(self._preview)
        # The listing takes the slack; the panel keeps the width it was dragged
        # to. Without this a window resize grows both, and a preview panel that
        # grows with the window ends up half the pane after a maximise -- the
        # same rule the navigation rail needed.
        self._body.setStretchFactor(0, 1)
        self._body.setStretchFactor(1, 0)
        self._body.splitterMoved.connect(self._remember_preview_width)

        self._status = QLabel("")
        self._status.setProperty("role", "status")
        self._space = QLabel("")
        self._space.setProperty("role", "space")
        self._space.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(4)
        self._drives.hide()
        for widget in (self._back, self._forward, self._up):
            controls.addWidget(widget)
        controls.addWidget(self._crumbs, 1)
        controls.addWidget(self._path, 1)
        controls.addWidget(self._reload)
        controls.addWidget(self._sift)

        # 0.27: the folder's name and what it is made of, between the path bar
        # and the listing. See `app/ui/header.py`.
        self._folder_header = FolderHeader()
        self._folder_header.setVisible(bool(pane.config.get("pane.header")))
        self._rows.badges = pane.config.get("icons.style") == "badges"
        transfers = getattr(pane, "transfers", None)
        if transfers is not None and hasattr(transfers, "row_progress"):
            # 0.29: rows fill as a transfer writes them. Repainted on the
            # queue's own ticks, and only while something is running.
            self._rows.progress = transfers.row_progress
            transfers.changed.connect(self._repaint_if_copying)

        # 0.27: the tab's history, from Back and Forward. Hold either, or
        # right-click it. Built when it opens, because the history is the
        # tab's and changes with every step.
        for button, direction in ((self._back, -1), (self._forward, 1)):
            menu = QMenu(button)
            menu.aboutToShow.connect(
                lambda m=menu, d=direction: self._fill_history(m, d))
            button.setMenu(menu)
            button.setPopupMode(QToolButton.DelayedPopup)
            button.setContextMenuPolicy(Qt.CustomContextMenu)
            button.customContextMenuRequested.connect(
                lambda _point, b=button: b.showMenu())

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(8)
        footer.addWidget(self._status, 1)
        footer.addWidget(self._space)

        # The bar goes in the pane rather than in the window because in a
        # dual-pane file manager the question is never only "where" but "which
        # side", and a bar inside a pane answers both in one click. Two of them
        # cost the height of one: the panes are side by side.
        self._bar = None
        if favorites is not None:
            self._bar = FavoritesBar(
                favorites, wanted=bool(pane.config.get("favorites.bar")))
            self._bar.chosen.connect(self._claim)
            self._bar.chosen.connect(self._on_favorite)
            self._bar.addRequested.connect(self.addFavoriteRequested)
            self._bar.manageRequested.connect(self.manageFavoritesRequested)

        # The tab strip is its own band, a step darker than the pane, with the
        # current tab drawn in the pane's own colour so it reads as the top of
        # the listing below it -- the way a browser does it.
        self._strip = QWidget()
        self._strip.setProperty("role", "tabstrip")
        self._strip.setAttribute(Qt.WA_StyledBackground, True)
        strip = QHBoxLayout(self._strip)
        strip.setContentsMargins(8, 5, 8, 0)
        strip.setSpacing(0)
        strip.addWidget(self._tabs, 1, Qt.AlignBottom)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._strip)
        layout = QVBoxLayout()
        layout.setContentsMargins(6, 6, 6, 4)
        layout.setSpacing(4)
        outer.addLayout(layout, 1)
        if self._bar is not None:
            layout.addWidget(self._bar)
        layout.addLayout(controls)
        layout.addWidget(self._folder_header)
        layout.addWidget(self._filter)
        layout.addWidget(self._body, 1)
        layout.addLayout(footer)

        self._pane.tabsChanged.connect(self._sync_tabs)
        self._pane.flatChanged.connect(self._on_flat_changed)
        self._pane.currentChanged.connect(self._sync_current)
        # The first listing is set on the view directly in the constructor, not
        # through `_sync_current`, so the header is pointed at it here.
        self._folder_header.follow(self._pane.current.model, self._header_title())
        self._pane.statusChanged.connect(self._sync_status)
        self._pane.pathChanged.connect(self._on_path_changed)
        self._pane.spaceChanged.connect(self._space.setText)
        self._pane.revealRequested.connect(self._reveal)
        self._volumes.changed.connect(self._sync_drives)
        if self._pane.icons is not None:
            # A repaint, not a model signal. The view asks the model for the
            # decoration of the rows it is about to draw and no others, so
            # telling it to draw again is both the cheapest way to show a
            # newly arrived icon and the only one that stays cheap at 50,000
            # rows -- a dataChanged over the whole model would have Qt build
            # an index per row to find out it is not on screen.
            self._pane.icons.changed.connect(self._view.viewport().update)

        if self._pane.overlays is not None:
            self._pane.overlays.changed.connect(self._view.viewport().update)
        if self._pane.file_icons is not None:
            self._pane.file_icons.changed.connect(self._view.viewport().update)
        if self._pane.sizes is not None:
            # A repaint rather than a model signal, for the reason the icons
            # give: the view asks the model about the rows it is drawing and
            # no others, which is what stays cheap at 50,000 rows.
            self._pane.sizes.changed.connect(self._view.viewport().update)
            self._pane.sizes.changed.connect(self._render_status)
        if self._pane.clipboard is not None:
            # The same repaint, for the same reason: a cut changes how a
            # handful of rows are drawn and nothing about what they contain,
            # and the rows it changes may well be in the other pane.
            self._pane.clipboard.changed.connect(self._view.viewport().update)
        if self._pane.menu is not None:
            self._pane.menu.ready.connect(self._on_shell_items)
            self._pane.menu.unavailable.connect(self._on_shell_unavailable)
        if self._pane.siblings is not None:
            # Both panes hear both answers, because there is one scan for the
            # window. The bar with no menu open drops what it is handed, which
            # is the same guard the shell menu needs and for the same reason.
            self._pane.siblings.ready.connect(self._on_siblings)
            self._pane.siblings.unavailable.connect(self._crumbs.sibling_problem)
        if self._pane.previews is not None:
            # Both panes hear both answers for the siblings' reason: there is
            # one decode outstanding for the window, so an answer arrives at the
            # pane that did not ask, and that pane's panel has to drop it. It
            # decides by comparing the path against the one it is waiting for.
            self._pane.previews.ready.connect(self._on_preview)
            self._pane.previews.unavailable.connect(self._on_preview_problem)

        #: The menu currently on screen, and what its shell entries mean. Both
        #: are None whenever no menu is open, which is what tells the replies
        #: arriving from the shell host that they are too late to be drawn.
        self._menu: QMenu | None = None
        self._menu_slot: QAction | None = None
        self._menu_commands: dict = {}
        #: Where the open menu was asked for, in screen coordinates. Kept
        #: because the menu grows after it is shown and the anchor is the only
        #: thing that can say where the grown one belongs.
        self._menu_anchor: QPoint | None = None

        #: True while the columns are being set from stored widths, so the
        #: `sectionResized` those calls emit does not store them straight back.
        self._laying_out = False

        #: Key -> external command id, handed down by the window. Empty until
        #: it is, so a pane built without a table -- a preview render, a test --
        #: answers its own keys and nothing else.
        self._command_keys: dict[str, str] = {}

        #: What has been typed into the listing so far, and the timer that
        #: forgets it. A quick search that never expires means the letters
        #: typed a minute ago are still narrowing the next one.
        self._search = ""
        #: The same term, kept past that timeout, so Ctrl+G has something to
        #: step. The timeout is about not extending a search nobody remembers
        #: typing; it is not a statement that the search is over. Cleared by
        #: Escape and by a change of folder, which are.
        self._last_search = ""
        self._search_state = "idle"
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(SEARCH_FORGETS_AFTER)
        self._search_timer.timeout.connect(self._clear_search)

        # Switching back to a tab must not connect its model a second time.
        self._watched: set = set()
        self._watch(self._pane.current.model)
        self._watch_selection()

        #: Whether the splitter has been shared out against a real width yet.
        #: See `showEvent`.
        self._restored = False
        self._share_selection()
        self.apply_metrics(metrics)
        self._sync_crumbs(self._pane.current.path)
        self._sync_tabs()
        self._sync_drives()
        self._sync_status(*self._current_status())
        self.set_view_mode(self._pane.view_mode)
        self._restore_preview_width()

    # ------------------------------------------------------------------ chrome

    def set_command_keys(self, keys: dict[str, str]) -> None:
        """The key -> command id map to match an unclaimed keystroke against.

        Handed down rather than read, so the pane needs to know nothing about
        the table, the settings or what a command is -- and so the window can
        hand both panes the same map the moment the editor changes it.
        """
        self._command_keys = dict(keys)

    def apply_metrics(self, metrics: dict[str, int]) -> None:
        """Take the numbers a stylesheet cannot set.

        Row height is the one that matters: it is the measurement being looked
        at 50,000 times, and Qt reads it from the vertical header rather than
        from the sheet.
        """
        header = self._view.verticalHeader()
        header.setDefaultSectionSize(metrics["row_h"])
        header.setMinimumSectionSize(metrics["row_h"])

    def apply_tokens(self, tokens: dict[str, str]) -> None:
        """Take the colours the sheet was just rendered from.

        Two things in a pane are painted rather than styled -- the chrome icons
        and the rows -- and both have to come from the same render as the sheet
        or the window ends up half in one theme. So the window hands down the
        tokens it applied rather than each of them asking `app.theme` again.
        """
        ratio = float(self.devicePixelRatioF() or 1.0)
        for button, name in ((self._back, "back"), (self._forward, "forward"),
                             (self._up, "up"), (self._reload, "refresh"),
                             (self._sift, "filter")):
            button.setIcon(glyphs.icon(
                name, colour=tokens["txt_1"], muted=tokens["txt_2"], ratio=ratio))
        self._drive_button.setIcon(glyphs.icon(
            "drive", colour=tokens["txt_1"], muted=tokens["txt_2"], ratio=ratio))
        self._tab_icon = glyphs.icon(
            "folder", colour=tokens["txt_1"], muted=tokens["txt_2"], ratio=ratio)
        for index in range(self._tabs.count()):
            self._tabs.setTabIcon(index, self._tab_icon)
        self._header.set_colour(tokens.get("accent", "#4aa8ff"))
        self._rows.apply_tokens(tokens)
        self._folder_header.apply_tokens(tokens)
        self._grid.apply_tokens(tokens)
        self._preview.apply_tokens(tokens)
        self._view.viewport().update()

    # ------------------------------------------------------------- view mode

    @property
    def showing_grid(self) -> bool:
        return self._views.currentWidget() is self._grid

    @property
    def listing(self):
        """Whichever view is in front.

        Only three things need this and they are the three that are about the
        widget rather than about the data: which view takes the keyboard, which
        one scrolls a row into sight, and which one a context menu came from.
        Everything else goes on asking `self._view.selectionModel()`, because
        both views share one -- see `_share_selection`.
        """
        return self._grid if self.showing_grid else self._view

    def set_view_mode(self, mode: str) -> None:
        """List or grid, keeping the cursor and the selection.

        Both are kept for free rather than by copying anything: the selection
        model is shared, so switching view is a `setCurrentWidget` and nothing
        else. What has to be done by hand is scrolling the cursor back into
        sight, because the two views have different scroll positions and a
        switch that left the cursor off screen would look like it had moved.
        """
        grid = mode == "grid"
        self._pane.set_view_mode("grid" if grid else "list")
        self._views.setCurrentWidget(self._grid if grid else self._view)
        current = self._view.currentIndex()
        if current.isValid():
            self.listing.scrollTo(current)
        if self.listing.hasFocus() or self._views.isVisible():
            self.listing.setFocus(Qt.OtherFocusReason)
        self._render_status()

    def set_cell_size(self, size: int) -> None:
        if self._pane.thumbnails is not None:
            self._pane.thumbnails.set_size(size)
        self._grid.set_cell(size)

    def _share_selection(self) -> None:
        """Give the grid the listing's selection model.

        The reason the rest of this widget did not have to change when the grid
        arrived. One selection model means the cursor and the marks are the same
        objects in both views, so every command already written -- F5, Del, the
        context menu, the group-selection keys -- acts on what is marked without
        knowing or asking which view is in front. It also means switching view
        cannot lose a selection, because there is only one.

        Called again after every `setModel`, because `setModel` makes a new
        selection model and drops the shared one on the floor.
        """
        picker = self._view.selectionModel()
        if picker is not None and self._grid.model() is not self._view.model():
            self._grid.setModel(self._view.model())
        if picker is not None:
            self._grid.setSelectionModel(picker)

    # ---------------------------------------------------------- preview panel

    @property
    def showing_preview(self) -> bool:
        return self._preview.isVisible()

    def show_preview(self, shown: bool) -> None:
        """Open or close the panel. Closing cancels what it was waiting for.

        Cancelled rather than left to finish, because a panel nobody can see is
        a read nobody is waiting for -- and the abandoned decode is still
        holding the volume the next listing wants.
        """
        self._pane.config.set("preview.pane", bool(shown))
        self._preview.setVisible(bool(shown))
        if shown:
            self._restore_preview_width()
            self._ask_preview()
        else:
            self._preview.clear()
            if self._pane.previews is not None:
                self._pane.previews.cancel()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """Share the splitter out once the pane is the width it will be.

        The same trap the navigation rail hit and the same fix. A `setSizes`
        called from `__init__` is dividing up a widget that has not been laid
        out yet -- its width is whatever Qt's initial guess was, usually a few
        hundred pixels -- so a panel asked for at 320 ends up taking most of the
        pane and the listing is squeezed to one column. Done on the first show,
        the numbers being divided are real ones.
        """
        super().showEvent(event)
        if not self._restored:
            self._restored = True
            self._restore_preview_width()
            # The name column for the splitter's reason: a width shared out in
            # `__init__` is dividing up a viewport Qt has only guessed at, so
            # the name would take its floor rather than the room it is meant
            # to have. Only when nothing has been dragged -- a stored width is
            # an answer and is not overruled by a resize.
            if not self._pane.columns:
                self._apply_columns()

    def _restore_preview_width(self) -> None:
        if not self._preview.isVisible():
            return
        total = self._body.width()
        if total <= 0:
            return          # not laid out yet; `showEvent` will come back to it
        # Never more than half the pane, whatever is stored. A preview panel
        # wider than the listing it belongs to is a listing that has stopped
        # being usable, and a stored width from a maximised window on a second
        # monitor is exactly how that happens.
        wanted = max(160, min(int(self._pane.config.get("preview.width")),
                              total // 2))
        self._body.setSizes([total - wanted, wanted])

    def _remember_preview_width(self, *_args) -> None:
        sizes = self._body.sizes()
        if len(sizes) == 2 and sizes[1] > 0:
            self._pane.config.set("preview.width", int(sizes[1]))

    def _ask_preview(self) -> None:
        """Show whatever the cursor is on, if the panel is open.

        The mtime and size come from the row rather than being looked up, which
        is the trick the icon caches use: the listing already carries them, and
        looking them up here would be a filesystem call from `app/ui/`.
        """
        previews = self._pane.previews
        if previews is None or not self._preview.isVisible():
            return
        row = self.current_row()
        model = self._pane.current.model
        if row < 0 or model.is_parent_row(row):
            self._preview.clear()
            previews.cancel()
            return
        entry = model.entry(row)
        path = self._pane.row_path(row)
        if entry is None or not path or entry.is_dir:
            # A folder is not previewed. Drawing the first four things inside it
            # would be a listing per cursor move, which is the cost this feature
            # is bounded against.
            self._preview.clear("" if entry is None else entry.name)
            previews.cancel()
            return
        self._preview.waiting(path, entry.name)
        previews.ask(path, box=PREVIEW_BOX, text_bytes=PANE_TEXT_BYTES,
                     mtime=entry.mtime, size=entry.size)

    def _on_preview(self, path: str, answer: Preview) -> None:
        if path != self._preview.path:
            return          # the other pane's file, or one this pane has left
        self._preview.show_preview(path, _leaf(path), answer)

    def _on_preview_problem(self, path: str, why: str) -> None:
        if path == self._preview.path:
            self._preview.problem(path, why)

    def set_active(self, active: bool) -> None:
        self.setProperty("active", "true" if active else "false")
        # The selection is painted, not styled, so the delegate has to be told
        # as well -- it draws the live pane's wash stronger than the other's.
        self._rows.set_live(active)
        self._folder_header.set_live(active)
        self._grid.set_live(active)
        self._view.viewport().update()
        # A property a stylesheet selects on only takes effect on a repolish.
        self.style().unpolish(self)
        self.style().polish(self)
        # Including the header, which a descendant selector reaches but a
        # repolish of this frame does not: Qt restyles a child only when the
        # child itself is repolished.
        header = self._view.horizontalHeader()
        header.style().unpolish(header)
        header.style().polish(header)
        header.viewport().update()
        self._grid.viewport().update()

    def _claim(self, *_ignored) -> None:
        """This pane was used, whether or not anything took focus.

        The window decides the active pane from `QApplication.focusChanged`,
        which is right for everything that can hold focus and blind to
        everything that cannot -- the nav buttons, the crumbs, the favourites,
        the drive picker. Each of those says so here instead.
        """
        self.activated.emit(self)

    def _history_button(self, button) -> bool:
        """Back or forward for the side buttons, or False for anything else.

        Claims the pane first, and that is the whole reason this is not a
        window-level shortcut. The buttons are pressed with the pointer over a
        pane, and the pane under the pointer is the one whose history the
        person means -- including when it is the *inactive* one, which the
        window cannot otherwise learn about, because a side button does not
        move the focus the way a click on a row does.

        The history is the tab's own, so this walks the tab in front of that
        pane and no other. A locked tab refuses, which `Tab.can_go_back`
        already decides.
        """
        step = HISTORY_BUTTONS.get(button)
        if step is None:
            return False
        self._claim()
        if step < 0:
            self._pane.go_back()
        else:
            self._pane.go_forward()
        return True

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """Swallow a side button on the way down, and act on the way up.

        Both halves matter. Acting on release is the middle click's rule -- a
        press that started here and finished somewhere else should do nothing.
        Swallowing the press is this one's own: left to itself
        `QAbstractItemView` treats an unknown button as a click on a row and
        clears the selection, so a thumb press would quietly unmark a selection
        somebody had just spent a minute building.
        """
        if event.button() in HISTORY_BUTTONS:
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if self._history_button(event.button()):
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _on_row_entered(self, index) -> None:
        """Move the hover highlight, repainting the two rows it moved between.

        Not the viewport. A hover changes two rows and the pointer crosses one
        every twenty-two pixels, so repainting everything here is the whole
        listing redrawn per row travelled -- a sixth of a second at full
        screen on a large display, which is a window permanently behind the
        mouse. The same reasoning as everywhere else in this file: the work
        belongs to what is on screen, and here it is smaller than that again.
        """
        was = self._rows.hovered_row
        self._rows.set_hovered_row(index)
        self._repaint_rows(was, self._rows.hovered_row)

    def _clear_hover(self) -> None:
        was = self._rows.hovered_row
        if was != -1:
            self._rows.set_hovered_row(-1)
            self._repaint_rows(was)

    def _repaint_rows(self, *rows: int) -> None:
        """Repaint whole rows by number, and nothing else.

        A row is the full width of the viewport whatever the columns are
        doing, so the rectangle is the row's height across everything rather
        than one cell.
        """
        model = self._view.model()
        if model is None:
            return
        viewport = self._view.viewport()
        width = viewport.width()
        for row in rows:
            if row is None or row < 0 or row >= model.rowCount():
                continue
            rect = self._view.visualRect(model.index(row, 0))
            if rect.isValid():
                viewport.update(0, rect.y(), width, rect.height())

    def toggle_filter(self) -> None:
        """The filter button. Shows the box, or clears and hides it again."""
        if self._filter.isVisible():
            self.clear_filter()
        else:
            self.focus_filter()

    def _sync_crumbs(self, text: str) -> None:
        """Rebuild the bar from what is being displayed.

        The split is `core.Pane`'s -- this widget does no path arithmetic -- and
        it is done on the displayed text rather than the resolved path, so the
        bar shows the letter or the UNC according to the tab's own preference.
        """
        self._crumbs.set_crumbs(self._pane.crumbs(text))

    def _show_crumbs(self) -> None:
        """Put the bar back after the field has had its turn."""
        self._path.hide()
        self._crumbs.show()

    def _ask_siblings(self, folder: str) -> None:
        """A chevron was clicked. Ask what is in the folder behind it."""
        if self._pane.siblings is not None:
            self._pane.siblings.ask(folder)

    def _on_siblings(self, folder: str, names, more: bool) -> None:
        """Names into places. The pane joins them; this widget does no path
        arithmetic, which is the same rule that keeps `..` out of the model."""
        self._crumbs.show_siblings(folder, self._pane.children(folder, names),
                                   bool(more))

    def focus_listing(self) -> None:
        self.listing.setFocus(Qt.OtherFocusReason)

    def focus_path(self) -> None:
        """Ctrl+L, and a click on the bar's empty space. Swap in the field."""
        self._crumbs.hide()
        self._path.show()
        self._path.setFocus(Qt.ShortcutFocusReason)
        self._path.selectAll()

    def focus_filter(self) -> None:
        self._filter.show()
        self._filter.setFocus(Qt.ShortcutFocusReason)
        self._filter.selectAll()

    def current_row(self) -> int:
        index = self._view.currentIndex()
        return index.row() if index.isValid() else -1

    def selected_names(self) -> list[str]:
        """What an operation acts on: the marked rows, or the row under the cursor.

        Falling back to the cursor is what every file manager does and what the
        keyboard expects -- pressing Delete with nothing marked deletes the
        thing being looked at, not nothing. The parent row is never in the
        answer, so `..` cannot be deleted by holding a key down.
        """
        rows = self._selected_rows()
        if not rows:
            row = self.current_row()
            if row < 0:
                return []
            rows = {row}
        return self._pane.names_for(rows)

    # ------------------------------------------------------------ operations

    def new_folder(self) -> None:
        name = dialogs.ask_name(
            self.window(), title="New folder",
            label=f"Create a folder in {self._pane.display()}",
            ok_text="Create",
        )
        if name:
            self._pane.make_folder(name)

    def rename_current(self) -> None:
        if self._pane.current.flat:
            self._pane.say("Rename works outside flat view -- Ctrl+B to leave it", "bad")
            return
        row = self.current_row()
        names = self._pane.names_for({row}) if row >= 0 else []
        if not names:
            return
        name = dialogs.ask_name(
            self.window(), title="Rename", label=f"Rename {names[0]} to",
            initial=names[0], ok_text="Rename", stem=True,
        )
        # Found again by name, never by the row it was on: the folder is live,
        # and a check that landed while the dialog was open can have moved
        # every row under it. Renaming whatever is on that row now would be
        # renaming the wrong file.
        row = self._pane.current.model.row_of(names[0])
        if name and name != names[0] and row >= 0:
            self._pane.rename(row, name)

    def duplicate_current(self) -> None:
        """Shift+F5: copy the row under the cursor beside itself, renamed.

        For the folder-per-day habit: yesterday's folder is duplicated, the copy
        is named for today, and yesterday's stays as the backup. The name
        offered carries today's date in the form the old one used, and a name
        already in the folder is refused in the dialog -- the engine refuses it
        again, because a duplicate that merged into an existing folder would
        mix two days together.
        """
        if self._pane.current.flat:
            self._pane.say("Duplicate works outside flat view -- Ctrl+B to leave it", "bad")
            return
        row = self.current_row()
        names = self._pane.names_for({row}) if row >= 0 else []
        if not names:
            return
        suggestion = self._pane.duplicate_suggestion(row) or names[0]
        name = dialogs.ask_name(
            self.window(), title="Duplicate",
            label=f"Duplicate {names[0]} here as", initial=suggestion,
            ok_text="Duplicate", taken=self._pane.name_taken,
        )
        row = self._pane.current.model.row_of(names[0])      # see rename_current
        if name and name != names[0] and row >= 0:
            self._pane.duplicate(row, name)

    def delete_selection(self, *, permanent: bool = False) -> None:
        names = self.selected_names()
        if not names:
            return
        if dialogs.confirm_delete(self.window(), names=names,
                                  folder=self._pane.display(), permanent=permanent):
            self._pane.delete(names, permanent=permanent)

    # ---------------------------------------------------------- context menu

    def _on_context_menu(self, point: QPoint) -> None:
        """The menu for what was right-clicked: this application's verbs, then
        Explorer's.

        The application's own first, and not as a matter of taste. They are the
        operations whose keys are in the user's fingers, they are the ones that
        run in a worker where a dead share cannot take the window down with it,
        and they are there whether or not the shell answers. Explorer's follow
        because the whole point of them is the entries this application will
        never have: TortoiseSVN, 7-Zip, whatever else is installed.

        Right-clicking a row that is not part of the selection moves to it
        first, the way Explorer does. Anything else means a menu whose title
        says one thing and whose commands act on another.
        """
        view = self.listing
        index = view.indexAt(point)
        on_row = index.isValid() and not self._pane.current.model.is_parent_row(index.row())
        if on_row and index.row() not in self._selected_rows():
            view.setCurrentIndex(index)
            view.selectionModel().clearSelection()
        names = self.selected_names() if on_row else []

        menu = QMenu(self)
        # The help line an extension supplies is worth showing, and Qt hides
        # action tooltips in a menu unless it is told not to.
        menu.setToolTipsVisible(True)
        self._menu = menu
        self._menu_commands = {}
        self._add_verbs(menu, names, on_row=on_row)
        self._menu_slot = None
        if self._pane.menu is not None and self._shell_wanted():
            menu.addSeparator()
            self._menu_slot = menu.addAction("Explorer commands")
            self._menu_slot.setEnabled(False)
            # Asked for after the menu is built rather than before it is
            # shown. The menu appears immediately with the verbs that are
            # always there, and the shell's entries land in it a moment later
            # -- a menu that waits for a shell extension to load is a menu
            # that is sometimes not there when the mouse button comes up.
            self._pane.context_menu(names, extended=self._extended())

        # Kept because the menu is about to grow. `exec` places it once, from
        # the entries it has at that instant, and the shell's arrive a moment
        # later -- so the point it was opened at is the only thing that can say
        # where the grown menu belongs. See `_place_menu`.
        self._menu_anchor = self.listing.viewport().mapToGlobal(point)
        chosen = menu.exec(self._menu_anchor)
        command = self._menu_commands.get(chosen)
        self._menu = None
        self._menu_slot = None
        self._menu_anchor = None
        self._menu_commands = {}
        if self._pane.menu is None:
            return
        if command is None:
            # Nothing of the shell's was chosen, so let go of it: a live
            # IContextMenu keeps somebody else's DLL loaded and, in a few
            # cases, holds the folder open.
            self._pane.menu.release()
            return
        token, item_id = command
        self._pane.menu.invoke(token, item_id)

    def _add_verbs(self, menu: QMenu, names: list[str], *, on_row: bool) -> None:
        """The application's own operations, in the words and keys they have
        everywhere else. The keys are shown, not claimed: they belong to the
        pane, which is what makes them safe to press in a text field.
        """
        if on_row:
            menu.addAction("Open\tEnter", self._open_current)
            row = self.current_row()
            entry = self._pane.current.model.entry(row) if row >= 0 else None
            if entry is not None and entry.is_dir:
                # By name for `rename_current`'s reason: a menu is open for as
                # long as somebody reads it, and the folder can change under it.
                menu.addAction("Open in new tab\tCtrl+Enter",
                               lambda name=entry.name: self._open_row_in_tab(
                                   self._pane.current.model.row_of(name),
                                   background=False))
            if entry is not None and not entry.is_dir:
                # Above Open rather than below, because for a drawing or a
                # photograph this is the entry somebody wants and Open hands the
                # file to whatever Windows has associated with it.
                menu.addAction("View\tF3", self.view_current)
            menu.addSeparator()
            menu.addAction("Copy\tCtrl+C",
                           lambda: self.clipboardRequested.emit("copy"))
            menu.addAction("Cut\tCtrl+X",
                           lambda: self.clipboardRequested.emit("cut"))
            menu.addAction("Copy to other pane\tF5",
                           lambda: self.transferRequested.emit("copy"))
            menu.addAction("Move to other pane\tF6",
                           lambda: self.transferRequested.emit("move"))
            menu.addAction("Duplicate\tShift+F5", self.duplicate_current)
            menu.addAction("Rename\tF2", self.rename_current)
            menu.addAction("Delete\tDel", self.delete_selection)
            menu.addAction("Delete permanently\tShift+Del",
                           lambda: self.delete_selection(permanent=True))
            menu.addSeparator()
        # Paste is offered whether or not a row was clicked: pasting into the
        # empty part of a listing is how a folder with nothing in it gets its
        # first file, and a menu that only offered it on top of an existing
        # row would be missing it exactly then.
        paste = menu.addAction("Paste\tCtrl+V",
                               lambda: self.clipboardRequested.emit("paste"))
        paste.setEnabled(self._pane.clipboard is not None
                         and self._pane.clipboard.has_files())
        menu.addSeparator()
        if on_row and self._pane.sizes is not None:
            counted = menu.addAction("Folder size\tSpace", self.measure_selection)
            counted.setToolTip("Walk what is under it and put the total in the "
                               "size column.")
            menu.addSeparator()
        menu.addAction("New folder\tF7", self.new_folder)
        menu.addAction("Refresh\tCtrl+R", self._pane.refresh)

    def _on_shell_items(self, token: int, items) -> None:
        """Put Explorer's entries into a menu that is already open.

        Both panes are connected to the one shell menu, because there is one
        shell host and one menu on screen at a time. So this fires on the pane
        that did not ask as well, and that pane must do *nothing*: releasing
        here would let go of the menu the other pane is about to draw, and
        every entry on it would then be dead when clicked. Whoever opened the
        menu releases it when it closes.
        """
        if self._menu is None or self._menu_slot is None:
            return
        slot, self._menu_slot = self._menu_slot, None
        self._menu.removeAction(slot)
        if not items:
            disabled = self._menu.addAction("No Explorer commands here")
            disabled.setEnabled(False)
            self._place_menu()
            return
        self._fill(self._menu, items, token, top=True)
        self._place_menu()

    def _place_menu(self) -> None:
        """Put the menu back on the screen after it has grown.

        A menu opened near the bottom of the screen is placed for the four or
        five entries it has when it opens, and then a dozen of Explorer's land
        in it -- and Qt grows a visible popup downwards from where it already
        is without looking at the screen again. Past the bottom edge the extra
        entries are simply not reachable, which is worse than them being slow
        to arrive, because nothing says they are there.

        So the placement is redone against the point the menu was opened at.
        Qt does this itself at `popup` time and has no reason to do it again;
        this application is the one that changes a menu after showing it.
        """
        menu = self._menu
        if menu is None or self._menu_anchor is None or not menu.isVisible():
            return
        screen = QGuiApplication.screenAt(self._menu_anchor) or menu.screen()
        if screen is None:
            return
        refit_popup(menu, self._menu_anchor, screen.availableGeometry())

    def _on_shell_unavailable(self, message: str) -> None:
        """Say why there are none, in the menu, without taking it over."""
        if self._menu is None or self._menu_slot is None:
            return
        self._menu_slot.setText(message)
        self._menu_slot.setEnabled(False)
        self._menu_slot = None

    def _fill(self, menu: QMenu, items, token: int, *, top: bool = False) -> None:
        """One level of the shell's menu, drawn with this application's look.

        Which is the whole reason the entries are walked in the shell host
        rather than shown by it: the same fonts, the same accent on the
        highlight, the same corner radius as everything else in the window.
        What it costs is the entries an extension paints itself rather than
        naming, which arrive labelled from their verb.
        """
        for item in _tidy(items, drop_verbs=SHELL_VERBS_WE_HAVE if top else frozenset()):
            if item.kind == MENU_SEPARATOR:
                menu.addSeparator()
                continue
            if item.kind == MENU_SUBMENU:
                child = menu.addMenu(item.text)
                child.setEnabled(item.enabled)
                icon = _menu_icon(item)
                if icon is not None:
                    child.setIcon(icon)
                self._fill(child, item.items, token)
                continue
            action = menu.addAction(item.text)
            action.setEnabled(item.enabled)
            if item.default:
                # What a double click would have done, drawn the way Explorer
                # draws it. The listing already opens on Enter, so this is the
                # menu agreeing with the keyboard rather than a new promise.
                font = action.font()
                font.setBold(True)
                action.setFont(font)
            if item.checked:
                action.setCheckable(True)
                action.setChecked(True)
            if item.help:
                action.setToolTip(item.help)
            icon = _menu_icon(item)
            if icon is not None:
                action.setIcon(icon)
            # The id means nothing without its token, and nothing at all after
            # this menu closes. Both travel together, back to the one process
            # that can still turn them into a command.
            self._menu_commands[action] = (token, item.id)

    def _shell_wanted(self) -> bool:
        return self._pane.menu is not None and not self._pane.menu.busy

    def _extended(self) -> bool:
        """Shift-right-click asks for the entries Explorer hides behind Shift."""
        from PySide6.QtWidgets import QApplication

        return bool(QApplication.keyboardModifiers() & Qt.ShiftModifier)

    def view_current(self) -> None:
        """F3: open the viewer on the file under the cursor.

        The list of files and the position in it are worked out here rather than
        in the viewer, because both are questions about what this pane is showing
        -- the sort order the header is in, the filter that is applied -- and the
        viewer has no business knowing about either.

        A cursor on a folder opens the viewer on the *first* file in the listing
        rather than doing nothing. Doing nothing is the response that reads as a
        broken key, and the folder the cursor is on is not a thing that can be
        viewed however the request is phrased.
        """
        model = self._pane.current.model
        names = self._pane.file_names()
        if not names:
            self._sync_status("nothing in this folder can be viewed", "idle")
            return
        row = self.current_row()
        entry = model.entry(row) if row >= 0 and not model.is_parent_row(row) else None
        at = names.index(entry.name) if (entry is not None
                                        and entry.name in names) else 0
        self.viewRequested.emit(self._pane.current.path, names, at)

    def reveal_name(self, name: str) -> None:
        """Put the cursor on a name. What the viewer's walk reports back.

        The listing follows the viewer rather than the other way round, so
        closing it leaves the cursor on the file that was last on screen -- which
        is where the eye already is, and the thing that makes stepping through a
        folder in the viewer and then acting on one of them work at all.
        """
        self._reveal(name)

    def _open_current(self) -> None:
        row = self.current_row()
        if row >= 0:
            self._pane.activate(row)

    def _selected_rows(self) -> set:
        picker = self._view.selectionModel()
        return {index.row() for index in picker.selectedRows()} if picker else set()

    def clear_filter(self) -> None:
        self._filter.clear()   # the model is told through textChanged
        self._filter.hide()
        self.focus_listing()

    # ------------------------------------------------------------------ events

    def focusInEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self.activated.emit(self)
        super().focusInEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """The keys, handled here rather than as window shortcuts.

        Deliberately: a window shortcut on Delete would take the key away from
        the path bar and the filter box, so backspacing over a typo would start
        deleting files. Handling them at the pane means a text field that wants
        a key keeps it, since a focused `QLineEdit` consumes what it uses and
        only the rest reaches this. The two fields that do not consume the
        function keys are checked for explicitly.
        """
        key = event.key()
        if key == Qt.Key_Escape and self._pane.stop_walk():
            return
        if key == Qt.Key_Escape and self._pane.sizes is not None \
                and self._pane.sizes.busy:
            # Before the filter, because a walk of a tree over SMB is the more
            # expensive thing to be stuck with and Escape is what a person
            # presses to stop something.
            self._pane.stop_measuring()
            return
        if key == Qt.Key_Escape and self._path.isVisible():
            # Out of the field and back to the bar, leaving the path alone.
            # Before the filter, because the field is the thing in front.
            self._path.setText(self._pane.current.path)
            self._show_crumbs()
            self._view.setFocus(Qt.OtherFocusReason)
            return
        if key == Qt.Key_Escape and self._filter.isVisible():
            self.clear_filter()
            return
        if key == Qt.Key_Backspace:
            self._pane.go_up()
            return
        if key == Qt.Key_Insert:
            self._mark_and_advance()
            return

        if key in (Qt.Key_Return, Qt.Key_Enter) and \
                event.modifiers() & Qt.ControlModifier and \
                not self._path.hasFocus() and not self._filter.hasFocus():
            self.open_in_new_tab(background=False)
            return

        if self._path.hasFocus() or self._filter.hasFocus():
            super().keyPressEvent(event)
            return
        # Ctrl+C, Ctrl+X and Ctrl+V, and only past the guard above -- the
        # path bar and the filter box need all three to mean what they mean
        # everywhere else, and a window shortcut would take them away. Shift
        # is excluded because Ctrl+Shift+C is the copy-path key, which is a
        # window shortcut and has to keep reaching it.
        if event.modifiers() & Qt.ControlModifier and \
                not event.modifiers() & Qt.ShiftModifier:
            if key == Qt.Key_C:
                self.clipboardRequested.emit("copy")
                return
            if key == Qt.Key_X:
                self.clipboardRequested.emit("cut")
                return
            if key == Qt.Key_V:
                self.clipboardRequested.emit("paste")
                return

        # The external commands, before the built-in function keys and after
        # the path bar and the filter box have had their say. Before, because a
        # table that could only take keys nothing else wanted would not be able
        # to put a compare tool on Ctrl+F2 while F2 stays rename -- and after
        # the guard, because a command key typed into the path bar is a
        # character like any other.
        identity = self._command_keys.get(shortcut_text(event))
        if identity:
            self.commandRequested.emit(identity)
            return

        shift = bool(event.modifiers() & Qt.ShiftModifier)
        if key == Qt.Key_F3:
            self.view_current()
            return
        if key == Qt.Key_F5 and shift:
            self.duplicate_current()
            return
        if key == Qt.Key_F5:
            self.transferRequested.emit("copy")
            return
        if key == Qt.Key_F6:
            self.transferRequested.emit("move")
            return
        if key == Qt.Key_F7:
            self.new_folder()
            return
        if key == Qt.Key_F2:
            self.rename_current()
            return
        if key in (Qt.Key_Delete, Qt.Key_F8):
            self.delete_selection(permanent=shift)
            return
        super().keyPressEvent(event)

    def _on_activated(self, index: QModelIndex) -> None:
        if not index.isValid():
            return
        if index.column() == int(Column.LOCATION) and self._pane.current.flat:
            # A double click on where a file is goes there, and ends flat view
            # on the way -- with the cursor put on that file.
            self._pane.go_to_location(index.row())
            return
        self._pane.activate(index.row())

    def _on_path_entered(self) -> None:
        text = self._path.text().strip()
        if text:
            self._pane.navigate(text)
        self._show_crumbs()
        self._view.setFocus(Qt.OtherFocusReason)

    def _header_title(self) -> str:
        shown = self._pane.display(self._pane.current.path)
        name = paths.leaf(shown) or shown
        return f"{name}  (flat)" if self._pane.current.flat else name

    def _fill_history(self, menu, direction: int) -> None:
        """The steps behind or ahead of this tab, nearest first."""
        self._claim()
        menu.clear()
        tab = self._pane.current
        if direction < 0:
            steps = range(tab.position - 1, -1, -1)
        else:
            steps = range(tab.position + 1, len(tab.history))
        shown = 0
        for index in steps:
            where = self._pane.display(tab.history[index])
            action = menu.addAction(paths.leaf(where) or where)
            action.setToolTip(where)
            action.triggered.connect(
                lambda _checked=False, i=index: self._pane.go_to_history(i))
            shown += 1
            if shown >= 20:
                break
        if not shown:
            empty = menu.addAction("Nothing " + ("back" if direction < 0 else "ahead"))
            empty.setEnabled(False)

    def _repaint_if_copying(self) -> None:
        transfers = self._pane.transfers
        if transfers.active or getattr(self, "_was_copying", False):
            self._view.viewport().update()
        self._was_copying = bool(transfers.active)

    def set_header_shown(self, shown: bool) -> None:
        self._folder_header.setVisible(shown)

    def set_badges(self, on: bool) -> None:
        self._rows.badges = on
        self._view.viewport().update()

    def _arrive(self) -> None:
        """0.29: a new folder fades in over a sixth of a second.

        An opacity effect renders the listing offscreen while it is on, which
        is exactly the cost the glow avoided -- so it is on only for the length
        of the fade and taken off at the end, and never while a listing is
        being scrolled. Off entirely when View > Animations is off.
        """
        if not bool(self._pane.config.get("look.motion")) or not self.isVisible():
            return
        from PySide6.QtCore import QEasingCurve, QVariantAnimation
        from PySide6.QtWidgets import QGraphicsOpacityEffect

        effect = QGraphicsOpacityEffect(self._views)
        effect.setOpacity(0.25)
        self._views.setGraphicsEffect(effect)
        fade = QVariantAnimation(self)
        fade.setDuration(170)
        fade.setStartValue(0.25)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.OutCubic)
        fade.valueChanged.connect(lambda value: effect.setOpacity(float(value)))
        fade.finished.connect(lambda: self._views.setGraphicsEffect(None))
        fade.start(QVariantAnimation.DeleteWhenStopped)

    def _on_path_changed(self, text: str) -> None:
        if text != self._path.text():
            self._arrive()
        self._path.setText(text)
        self._folder_header.set_title(self._header_title())
        self._sync_crumbs(text)
        # Navigating from anywhere else -- a crumb, a favourite, a double
        # click -- puts the bar back, so the field is never left open showing
        # somewhere the pane has already left.
        if self._path.isVisible() and not self._path.hasFocus():
            self._show_crumbs()
        self._clear_hover()
        # A search is about the rows on screen, and these are about to be
        # different rows -- so the term goes too, not just the narrowing. A
        # Ctrl+G in a new folder stepping a name typed in the last one would be
        # the cursor jumping for a reason nothing on screen explains.
        self._clear_search(forget=True)
        # Navigating clears the model's filter; the box has to agree with it.
        if self._filter.text():
            self._filter.blockSignals(True)
            self._filter.clear()
            self._filter.blockSignals(False)
        self._filter.hide()
        self._sync_drives()

    def _on_drive_chosen(self, index: int) -> None:
        letter = self._drives.itemData(index)
        if letter:
            self._pane.navigate(letter + "\\")
            self.focus_listing()

    def _mark_and_advance(self) -> None:
        """Insert marks the row and moves down, the way a file manager does.

        Marking without moving means holding Insert does nothing after the
        first press, which is not what anybody's fingers expect.
        """
        model = self._view.model()
        index = self._view.currentIndex()
        if model is None or not index.isValid():
            return
        row = index.row()
        selection = QItemSelection(model.index(row, 0),
                                   model.index(row, model.columnCount() - 1))
        picker = self._view.selectionModel()
        picker.select(selection, QItemSelectionModel.Toggle | QItemSelectionModel.Rows)
        if row + 1 < model.rowCount():
            picker.setCurrentIndex(model.index(row + 1, 0),
                                   QItemSelectionModel.NoUpdate)
            self.listing.scrollTo(model.index(row + 1, 0))

    # ------------------------------------------------------------------- sync

    def _current_status(self) -> tuple[str, str]:
        tab = self._pane.current
        return tab.status_text, tab.status_state

    def _sync_tabs(self) -> None:
        blocked = self._tabs.blockSignals(True)
        try:
            while self._tabs.count() > len(self._pane.tabs):
                self._tabs.removeTab(self._tabs.count() - 1)
            while self._tabs.count() < len(self._pane.tabs):
                self._tabs.addTab("")
            closable = len(self._pane.tabs) > 1
            for index, tab in enumerate(self._pane.tabs):
                # Brackets rather than an icon for the lock. Status in this
                # application is text and colour, and a bracketed name reads as
                # held in place at any density without a bitmap to scale.
                label = f"{tab.label} (flat)" if tab.flat else tab.label
                self._tabs.setTabText(index,
                                      f"[{label}]" if tab.locked else label)
                tip = self._pane.display(tab.path)
                if tab.locked:
                    tip += "\nLocked. Opening a folder here opens a new tab."
                self._tabs.setTabToolTip(index, tip)
                existing = self._tabs.tabButton(index, QTabBar.RightSide)
                if tab.locked and existing is not None:
                    # A locked tab refuses to close, so it does not offer to.
                    self._tabs.setTabButton(index, QTabBar.RightSide, None)
                    existing = None
                if tab.locked:
                    continue
                if closable and existing is None:
                    self._tabs.setTabButton(index, QTabBar.RightSide,
                                            self._close_button())
                elif not closable and existing is not None:
                    self._tabs.setTabButton(index, QTabBar.RightSide, None)
            self._tabs.setCurrentIndex(self._pane.index)
            icon = getattr(self, "_tab_icon", None)
            if icon is not None:
                for index in range(self._tabs.count()):
                    self._tabs.setTabIcon(index, icon)
        finally:
            self._tabs.blockSignals(blocked)
        self._show_close_buttons()

    def _show_close_buttons(self) -> None:
        """A close button on the current tab and the one under the pointer.

        Hidden rather than removed, so a tab does not change width as the
        pointer crosses it -- a strip whose tabs jump sideways under the mouse
        is a strip nobody can click on.
        """
        current = self._tabs.currentIndex()
        for index in range(self._tabs.count()):
            button = self._tabs.tabButton(index, QTabBar.RightSide)
            if button is None:
                continue
            shown = index in (current, self._tab_hover)
            button.setProperty("shown", "true" if shown else "false")
            button.setEnabled(shown)
            button.style().unpolish(button)
            button.style().polish(button)

    def _open_drive_menu(self) -> None:
        menu = QMenu(self)
        for row in range(self._drives.count()):
            action = menu.addAction(self._drives.itemText(row))
            tip = self._drives.itemData(row, Qt.ToolTipRole)
            if tip:
                action.setText(str(tip))
            action.setCheckable(True)
            action.setChecked(row == self._drives.currentIndex())
            action.triggered.connect(
                lambda _=False, r=row: (self._drives.setCurrentIndex(r),
                                        self._on_drive_chosen(r)))
        button = self._drive_button
        menu.exec(button.mapToGlobal(QPoint(0, button.height())))

    def _sync_drives(self) -> None:
        """Rebuild the picker and put it on the drive this tab is looking at.

        Rebuilt rather than patched, because the list is a dozen items and the
        alternative is a diff against a combo box. A path on no listed letter
        -- a UNC typed straight in -- gets an entry of its own rather than
        leaving the picker showing a drive the pane is not on.
        """
        current = self._pane.display()
        blocked = self._drives.blockSignals(True)
        try:
            self._drives.clear()
            for drive in self._volumes.drives:
                letter = drive["letter"]
                self._drives.addItem(letter, letter)
                index = self._drives.count() - 1
                target = drive.get("unc")
                self._drives.setItemData(
                    index,
                    f"{letter}  {target}" if target else f"{letter}  {drive['type']}",
                    Qt.ToolTipRole,
                )
            letter = self._volumes.letter_for(current)
            if letter is None:
                self._drives.addItem(_root_label(current), None)
                self._drives.setCurrentIndex(self._drives.count() - 1)
            else:
                self._drives.setCurrentIndex(self._drives.findData(letter))
        finally:
            self._drives.blockSignals(blocked)

    def _layout_columns(self) -> None:
        """Set the columns to what this pane was left at, or to the defaults.

        Every section is `Interactive`, including the name, and that is the
        whole of what changed in 0.18. It used to be `Stretch`, which is not a
        column somebody can drag -- Qt gives a stretched section no usable
        handle and recomputes it on every resize -- so the one column people
        actually want wider was the one column that could not be touched. The
        other four could be dragged and were reset on the next tab switch,
        because this was called again from `_sync_current`. Between the two,
        the answer to "can I change the column widths" was no.

        Nothing here is `ResizeToContents`: it measures every row, which at
        50,000 rows is the one thing this application is built to avoid.
        `_fit_column` is the bounded version and is what the double click and
        the header menu use.
        """
        header = self._view.horizontalHeader()
        stored = self._pane.columns
        for column, fallback in DEFAULT_WIDTHS.items():
            header.setSectionResizeMode(int(column), QHeaderView.Interactive)
            width = 0
            if len(stored) > int(column):
                width = int(stored[int(column)])
            header.resizeSection(int(column), width or fallback)
        for column in range(len(HEADERS)):
            self._view.setColumnHidden(
                column, column in self._hidden_set()
                and column != int(Column.NAME))
        if not stored:
            # Nothing has been dragged yet, so the name gets whatever is left.
            # Only now: once there are stored widths they are the answer, and
            # a name that re-widened itself on every resize would be a column
            # that will not stay where it is put.
            self._widen_name()

    def _widen_name(self) -> None:
        """Give the name column the slack, and make room for it if there is none.

        Two jobs, and the second one is a fault that predates this release. The
        other four columns are fixed and add up to 328 pixels, so a pane dragged
        below about 380 of viewport had nothing left for the name: it collapsed
        to a column of first letters and the table grew a horizontal scrollbar,
        which is the one place a file listing should never need one. Squeezing
        the *name* is exactly backwards -- it is the column being read.

        So below `NAME_FLOOR` the others give way, in `GIVE_WAY` order, each
        down to `MIN_COLUMN`. This runs only while nothing has been dragged; a
        pane whose widths are somebody's own decision is left alone, scrollbar
        or not, because the alternative is undoing what they set every time the
        window changes size.
        """
        header = self._view.horizontalHeader()
        room = self._view.viewport().width()
        if room <= 0:
            return              # not laid out yet; `showEvent` comes back to it

        # Start from what the person chose to see; anything squeezed out on
        # an earlier, narrower pass comes back before this one decides again.
        chosen = self._hidden_set()
        visible = []
        for column in GIVE_WAY:
            hide = int(column) in chosen
            if self._view.isColumnHidden(int(column)) != hide:
                self._view.setColumnHidden(int(column), hide)
            if not hide:
                visible.append(column)
                header.resizeSection(int(column), DEFAULT_WIDTHS[column])

        def others() -> int:
            return sum(header.sectionSize(int(column)) for column in visible)

        # Squeeze each to what it can still be read at, in order, and only then
        # take whole columns away, in the same order.
        short = NAME_FLOOR - (room - others())
        for column in list(visible):
            if short <= 0:
                break
            give = min(short, header.sectionSize(int(column)) - READABLE[column])
            if give > 0:
                header.resizeSection(int(column),
                                     header.sectionSize(int(column)) - give)
                short -= give
        for column in list(visible):
            if short <= 0:
                break
            short -= header.sectionSize(int(column))
            self._view.setColumnHidden(int(column), True)
            visible.remove(column)
        header.resizeSection(int(Column.NAME), max(MIN_COLUMN, room - others()))

    def _on_section_resized(self, *_args) -> None:
        """Remember the drag. Written to the settings in memory, not to disk.

        `Config.save` happens when the window closes, which is the same deal
        every other pane setting gets -- and the alternative, a file write per
        pixel of a drag, is not one.
        """
        if self._laying_out:
            return
        header = self._view.horizontalHeader()
        self._pane.set_columns([header.sectionSize(column)
                                for column in range(len(HEADERS))])

    def _fit_column(self, column: int) -> None:
        """Widen one column to fit the rows **on screen**, and no others.

        The bounded answer to `ResizeToContents`, which measures every row in
        the model: on a folder of 50,000 files that is 50,000 string
        measurements for a double click, and this application does not do that
        anywhere else either. A screenful is what somebody is looking at when
        they ask, and it is a few dozen measurements.

        The honest consequence, which is why the menu entry says "on screen":
        scroll down to longer names and ask again, and it gets wider again.
        That is better than the alternative on a big folder, and it is
        predictable once it has been seen once.
        """
        model = self._view.model()
        if model is None or self._view.isColumnHidden(column):
            return
        metrics = QFontMetrics(self._view.font())
        header = self._view.horizontalHeader()
        widest = metrics.horizontalAdvance(HEADERS[column]) + HEADER_PADDING
        first = max(0, self._view.rowAt(0))
        last = self._view.rowAt(self._view.viewport().height() - 1)
        if last < 0:
            last = model.rowCount() - 1
        for row in range(first, min(last + 1, model.rowCount())):
            text = model.data(model.index(row, column), Qt.DisplayRole)
            if text:
                widest = max(widest, metrics.horizontalAdvance(str(text)))
        # The name column carries an icon and the gap beside it, which the
        # text measurement knows nothing about.
        if column == int(Column.NAME):
            widest += ROW_ICON + ICON_GAP
        header.resizeSection(column, max(MIN_COLUMN, widest + CELL_PADDING))

    def fit_columns(self) -> None:
        """Every visible column, to what is on screen."""
        for column in range(len(HEADERS)):
            self._fit_column(column)

    def reset_columns(self) -> None:
        """Back to the shipped widths, and the name takes the slack again."""
        self._pane.set_columns([])
        self._apply_columns()

    def _apply_columns(self) -> None:
        """`_layout_columns` with the saving suppressed while it runs.

        Qt emits `sectionResized` for every section this touches, and without
        the guard applying the stored widths would immediately store them
        again -- harmless, until the pane is not laid out yet and what gets
        stored is Qt's initial guess at the width of a widget with no size.
        """
        self._laying_out = True
        try:
            self._layout_columns()
        finally:
            self._laying_out = False

    def _hidden_set(self) -> set[int]:
        """The columns not to draw: the ones hidden by choice, and Location
        everywhere except a flat view laid out with a Location column."""
        hidden = {int(column) for column in self._pane.hidden_columns}
        tab = self._pane.current
        if tab.flat and self._pane.flat_layout == "column":
            hidden.discard(int(Column.LOCATION))
        else:
            hidden.add(int(Column.LOCATION))
        return hidden

    def _apply_hidden(self) -> None:
        hidden = self._hidden_set()
        for column in range(len(HEADERS)):
            self._view.setColumnHidden(
                column, column in hidden and column != int(Column.NAME))

    def _set_column_shown(self, column: int, shown: bool) -> None:
        """Hide or show one column, and give its room to the name.

        The room matters. Every section is `Interactive` and the last one does
        not stretch, so hiding a column without moving its width somewhere
        leaves a gap exactly where it was -- which reads as the column still
        being there and empty rather than gone. The name is where the room
        goes, for the same reason the name gets the slack in the first place.

        Only when there are stored widths. Without them `_widen_name` is about
        to work the name out from scratch and would undo this.
        """
        if column == int(Column.NAME) and not shown:
            return              # a listing with no names is not a listing
        header = self._view.horizontalHeader()
        # Measured while the column is *visible*, whichever direction this is
        # going. A hidden section answers zero for its size, so asking before
        # showing one -- or after hiding one -- gives nothing to move.
        room = 0 if shown else header.sectionSize(column)
        hidden = set(self._pane.hidden_columns)
        if shown:
            hidden.discard(column)
        else:
            hidden.add(column)
        self._pane.set_hidden_columns(hidden)
        self._apply_columns()
        if shown:
            room = -header.sectionSize(column)
        if self._pane.columns:
            header.resizeSection(
                int(Column.NAME),
                max(MIN_COLUMN, header.sectionSize(int(Column.NAME)) + room))

    def _on_header_menu(self, point: QPoint) -> None:
        """Right-click on the header: the sizes, and which columns are drawn.

        The two belong together because they are the same question asked twice
        -- a column somebody keeps dragging to nothing is a column they want
        gone -- and because the header is where a hand already is.
        """
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        fit = menu.addAction("Size columns to what is on screen",
                             self.fit_columns)
        fit.setToolTip("Measures the rows you can see rather than all of them. "
                       "On a folder of 50,000 files, measuring every row is the "
                       "cost this application exists to avoid.")
        menu.addAction("Reset column widths", self.reset_columns)
        menu.addSeparator()
        for column in range(len(HEADERS)):
            if column in (int(Column.NAME), int(Column.LOCATION)):
                # No names is not a listing; Location follows the flat view
                # layout in the View menu rather than a tick here.
                continue
            action = menu.addAction(HEADERS[column])
            action.setCheckable(True)
            # What was chosen, not what is drawn: a column the pane squeezed out
            # for want of room is still one the person asked to see.
            action.setChecked(column not in self._pane.hidden_columns)
            action.toggled.connect(
                lambda shown, c=column: self._set_column_shown(c, shown))
        menu.exec(self._view.horizontalHeader().mapToGlobal(point))

    def _watch(self, model) -> None:
        if model not in self._watched:
            model.modelReset.connect(self._on_rows_settled)
            model.modelReset.connect(self._regroup)
            # A refresh or a live check reconciles rather than resets, and a
            # row that went can take a mark with it: the count has to follow.
            model.layoutChanged.connect(self._on_rows_settled)
            self._watched.add(model)

    def _watch_selection(self) -> None:
        """`setModel` replaces the selection model, so this is reconnected."""
        picker = self._view.selectionModel()
        if picker is not None:
            picker.selectionChanged.connect(self._render_status)
            # The cursor, not the selection. A preview follows where the
            # keyboard is, which is `currentChanged` -- `selectionChanged` does
            # not fire when the cursor moves without marking anything, which is
            # what the arrow keys do and is exactly the case this is for.
            picker.currentChanged.connect(self._on_cursor_moved)

    def _on_rows_settled(self) -> None:
        """Put the cursor on the first row so the keyboard has somewhere to be.

        The cursor, not a selection. A first row that arrives already selected
        reads as a mark the user made, and marks are what an operation will act
        on -- a file manager that starts every folder with something selected
        is a file manager that eventually copies something nobody chose.
        """
        model = self._view.model()
        picker = self._view.selectionModel()
        if (model is not None and picker is not None and model.rowCount()
                and not self._view.currentIndex().isValid()):
            picker.setCurrentIndex(model.index(0, 0), QItemSelectionModel.NoUpdate)
        self._render_status()

    def _sync_current(self) -> None:
        self._clear_search(forget=True)
        model = self._pane.current.model
        self._view.setModel(model)
        self._grid.setModel(model)
        # Deliberately *not* laying the columns out again. Widths belong to the
        # view rather than to the model, and calling this here is what used to
        # throw away every drag on the next tab switch -- half of the reason
        # the columns felt unchangeable. What does have to be reapplied is
        # which of them are hidden, because `setModel` brings them all back.
        self._apply_hidden()
        self._view.horizontalHeader().setSortIndicator(
            int(model.sort_column), model.sort_order,
        )
        self._watch(model)
        # Before `_watch_selection`, because sharing is what decides which
        # selection model the connections below are made on. The other way round
        # connects to one that is about to be replaced.
        self._share_selection()
        self._watch_selection()
        self._sync_tabs()
        self._sync_drives()
        self._ask_preview()
        self._grouped_rows = []     # `setModel` put every row back to one height
        self._regroup()
        self._folder_header.follow(model, self._header_title())

    def _on_flat_changed(self) -> None:
        # Laid out again rather than only re-hidden: the Location column
        # arriving or leaving changes what the name can have.
        self._apply_columns()
        self._sync_tabs()
        self._regroup()
        self._view.viewport().update()

    def _regroup(self) -> None:
        """Make room above each row that starts a group, in grouped flat view.

        The heading is drawn by the delegate inside the taller row rather than
        being a row of its own, which is what keeps every row number in the
        model meaning a file -- the selection, the marks and every operation
        go on counting rows as they always have.
        """
        model = self._view.model()
        header = self._view.verticalHeader()
        base = header.defaultSectionSize()
        count = model.rowCount() if model is not None else 0
        for row in self._grouped_rows:
            if row < count:
                header.resizeSection(row, base)
        self._grouped_rows = []
        if model is None or not getattr(model, "grouped", False):
            return
        rows = [row for row in range(count) if model.group_heading(row) is not None]
        for row in rows:
            header.resizeSection(row, base + GROUP_HEAD)
        self._grouped_rows = rows

    def _on_cursor_moved(self, *_args) -> None:
        """The keyboard moved to another row.

        Only the preview cares, and only when it is open -- `_ask_preview`
        returns immediately otherwise, which is what keeps this connection free
        for everybody who never opens the panel.
        """
        self._ask_preview()

    def _reveal(self, name: str) -> None:
        """Put the cursor on a name once its listing has arrived.

        The cursor, not a selection, for the same reason the first row of a
        folder is not selected: what is marked is what an operation will act
        on, and the user marked nothing by creating a folder.
        """
        model = self._pane.current.model
        row = model.row_of(name)
        picker = self._view.selectionModel()
        if row < 0 or picker is None:
            return
        index = model.index(row, 0)
        picker.setCurrentIndex(index, QItemSelectionModel.NoUpdate)
        self.listing.scrollTo(index)

    def _sync_status(self, text: str, state: str) -> None:
        self._summary = (text, state)
        self._render_status()
        self._back.setEnabled(self._pane.current.can_go_back)
        self._forward.setEnabled(self._pane.current.can_go_forward)

    def _render_status(self, *_args) -> None:
        """What the folder holds, with what is selected in front of it.

        The selection goes first because it is the part that changes as the
        user works; the folder totals sit behind it and stay put.
        """
        text, state = self._summary
        sizes = self._pane.sizes
        if sizes is not None and sizes.busy:
            outstanding = sizes.busy
            text = (f"counting {outstanding:,} folder(s)  ·  {text}"
                    if outstanding > 1 else f"counting  ·  {text}")
        if self._search:
            # In front of everything, because it is the thing that changes as
            # the user types and the thing they are looking at the line for.
            found = "" if self._search_state != "bad" else "  (no match)"
            text = f"search: {self._search}{found}  ·  {text}"
            state = self._search_state if self._search_state == "bad" else state
        picker = self._view.selectionModel()
        rows = {index.row() for index in picker.selectedRows()} if picker else set()
        model = self._pane.current.model
        if rows:
            folders, files, total = model.selection(rows)
            parts = [count_of(folders, "folder"), count_of(files, "file")]
            marked = ", ".join(part for part in parts if part)
            if marked:
                text = f"{marked} selected, {format_size(total)}  ·  {text}"
        self._status.setText(text)
        self._status.setProperty("state", state)
        self._status.style().unpolish(self._status)
        self._status.style().polish(self._status)

    def _close_button(self) -> QToolButton:
        """Close the tab this button ends up on, wherever it ends up.

        The index is looked up at click time rather than captured, because tabs
        are movable and a captured index closes the wrong one after a drag.
        """
        button = QToolButton()
        button.setText("×")
        button.setProperty("role", "tabclose")
        button.setToolTip("Close tab (Ctrl+W)")
        button.setFocusPolicy(Qt.NoFocus)
        button.setCursor(Qt.ArrowCursor)

        def close() -> None:
            for index in range(self._tabs.count()):
                if self._tabs.tabButton(index, QTabBar.RightSide) is button:
                    self._pane.close_tab(index)
                    return

        button.clicked.connect(close)
        return button

    # -------------------------------------------------------------- selecting

    def _on_selection_key(self, event) -> bool:
        """Answer a key on behalf of the selection commands. True means ours.

        Taken here rather than as window shortcuts for two reasons. Ctrl+A as
        a window shortcut takes select-all away from the path bar and the
        filter box, which is the failure the function keys are kept off the
        window for. And a bare `+` is either the keypad key that selects a
        group or a character somebody is typing into a quick search -- only
        the widget holding the search can tell those apart, and it does it by
        insisting the keypad ones carry `KeypadModifier`.
        """
        key = event.key()
        modifiers = event.modifiers()
        control = bool(modifiers & Qt.ControlModifier)
        shift = bool(modifiers & Qt.ShiftModifier)
        alt = bool(modifiers & Qt.AltModifier)
        pad = bool(modifiers & Qt.KeypadModifier)

        if key == Qt.Key_A and control and not alt:
            self.select_all(on=not shift)
            return True

        if pad and not control and not alt:
            # The Norton keys, on the pad and bare.
            if key == Qt.Key_Plus:
                self.ask_and_select(on=True)
                return True
            if key == Qt.Key_Minus:
                self.ask_and_select(on=False)
                return True
            if key == Qt.Key_Asterisk:
                self.invert_selection()
                return True
            return False

        if pad and alt and not control:
            # The rest of the files of the kind under the cursor.
            if key in (Qt.Key_Plus, Qt.Key_Minus):
                self.select_same_extension(on=key == Qt.Key_Plus)
                return True
            return False

        if control and not alt:
            # A keyboard with no numeric pad. Ctrl+8 is where the asterisk
            # lives on the row above, which is what makes it the invert.
            if key in (Qt.Key_Plus, Qt.Key_Equal):
                self.ask_and_select(on=True)
                return True
            if key == Qt.Key_Minus:
                self.ask_and_select(on=False)
                return True
            if key in (Qt.Key_Asterisk, Qt.Key_8):
                self.invert_selection()
                return True
        return False

    def select_matching(self, pattern: str, *, on: bool = True) -> None:
        """Mark, or unmark, every row whose name answers to a pattern."""
        if not pattern:
            return
        self._apply_selection(self._pane.current.model.rows_matching(pattern), on=on)

    def select_same_extension(self, *, on: bool = True) -> None:
        """The rest of the files of the kind under the cursor.

        The row under the cursor rather than the marked rows, because this is
        the command for "and all the other drawings" and the cursor is what
        names the kind. A folder has no extension, so it does nothing.
        """
        model = self._pane.current.model
        entry = model.entry(self.current_row())
        if entry is None or entry.is_dir:
            return
        suffix = split_name(entry)[1]
        if not suffix:
            return
        self._apply_selection(model.rows_with_extension(suffix), on=on)

    def select_names(self, names, *, on: bool = True) -> None:
        """Mark, or unmark, a set of rows by name.

        What a comparison of the two panes leaves behind. By name rather than
        by row because the answer was worked out against the *other* listing,
        which has its own row numbers and its own sort.
        """
        rows = self._pane.current.model.rows_named(names)
        if rows:
            self._apply_selection(rows, on=on)

    def select_all(self, *, on: bool = True) -> None:
        self._apply_selection(self._pane.current.model.all_rows(), on=on)

    def invert_selection(self) -> None:
        model = self._pane.current.model
        marked = self._selected_rows()
        rows = model.all_rows()
        self._apply_selection([row for row in rows if row in marked], on=False)
        self._apply_selection([row for row in rows if row not in marked], on=True)

    def ask_and_select(self, *, on: bool = True) -> None:
        """The pattern dialog behind the two group commands."""
        pattern = dialogs.ask_pattern(
            self.window(),
            title="Select" if on else "Unselect",
            label=("Mark everything matching" if on
                   else "Unmark everything matching"),
            ok_text="Select" if on else "Unselect",
        )
        if pattern:
            self.select_matching(pattern, on=on)

    def _apply_selection(self, rows, *, on: bool) -> None:
        """Mark or unmark a set of rows in as few calls as Qt will take.

        Coalesced into runs rather than sent one row at a time. At 50,000 rows
        a selection command that emits a range per row is seconds of the view
        rebuilding its selection, and this is the folder size this application
        is built around.
        """
        picker = self._view.selectionModel()
        model = self._view.model()
        if picker is None or model is None or not rows:
            return
        flag = QItemSelectionModel.Select if on else QItemSelectionModel.Deselect
        span = QItemSelection()
        last = model.columnCount() - 1
        for start, end in _runs(sorted(rows)):
            span.select(model.index(start, 0), model.index(end, last))
        picker.select(span, flag | QItemSelectionModel.Rows)
        self._render_status()

    # ----------------------------------------------------------- folder sizes

    def measure_selection(self) -> None:
        """Count what is under the marked folders, or the one under the cursor.

        The same fallback the operations use, and for the same reason:
        pressing a key with nothing marked means the thing being looked at.
        """
        names = self.selected_names()
        if names:
            self._pane.measure(names)

    def measure_all(self) -> None:
        self._pane.measure_all()

    # ------------------------------------------------------------ quick search

    def _on_search_key(self, event) -> bool:
        """Answer a key on behalf of the quick search. True means it was ours.

        Only the keys the search actually uses are taken. Everything else --
        the arrows, Enter, the function keys, Delete -- reaches the view and
        then this widget exactly as before, so a live search does not quietly
        change what the rest of the keyboard does.
        """
        key = event.key()
        if key == Qt.Key_G and event.modifiers() & Qt.ControlModifier:
            # Ctrl+G, not F3. F3 was find-next until 0.16 and is the viewer
            # now, which is what Double Commander does with it and what these
            # fingers already expect -- and a viewer is worth a bare function
            # key in a way that stepping a quick search is not. Ctrl+G is what
            # every editor uses for the same thing, so the move is to a key that
            # was already the second guess.
            #
            # It steps the *last* term as well as a live one, which the F3 it
            # replaced did not, and the difference matters more than it sounds.
            # A quick search stops narrowing after `SEARCH_FORGETS_AFTER` --
            # that timeout is about not extending a search nobody remembers
            # typing, and it has nothing to do with finding the next match. So
            # without this, find-next only worked within a second and a half of
            # the last keystroke, and pressing it at any other time did nothing
            # at all. Which is not a key that reads as unavailable; it reads as
            # a key that is broken.
            if not self._search and not self._last_search:
                return False
            if not self._search:
                self._revive_search()
            self._step_search(-1 if event.modifiers() & Qt.ShiftModifier else 1)
            return True
        if key in (Qt.Key_Return, Qt.Key_Enter) and self._search \
                and not event.modifiers() & Qt.ControlModifier:
            # And Enter while a search is live, which is the key a hand already
            # on the letters actually reaches for. Only while one is live: with
            # no search, Enter opens what the cursor is on and must keep doing
            # so. Ctrl+Enter is excluded because it opens a folder in a new tab.
            self._step_search(-1 if event.modifiers() & Qt.ShiftModifier else 1)
            return True
        if not self._search:
            if not self._is_search_key(event):
                return False
            self._set_search(event.text())
            return True
        if key == Qt.Key_Escape:
            self._clear_search(forget=True)
            return True
        if key == Qt.Key_Backspace:
            # Shortens the search rather than leaving the folder. Leaving is
            # what Backspace does the rest of the time, and losing the folder
            # to one mistyped letter is not what anybody meant by it.
            self._set_search(self._search[:-1])
            return True
        if self._is_search_key(event):
            self._set_search(self._search + event.text())
            return True
        return False

    def _is_search_key(self, event) -> bool:
        """Whether a keystroke is somebody typing a name.

        A printable character with no Ctrl or Alt held. Shift is allowed
        through because it is how capitals and most punctuation are typed, and
        the search is case-insensitive anyway. Space is deliberately excluded:
        it is the folder-size key, and a name with a space in it is reachable
        by typing past it.
        """
        text = event.text()
        if not text or not text.isprintable() or text == " ":
            return False
        return not event.modifiers() & (Qt.ControlModifier | Qt.AltModifier)

    def _set_search(self, text: str) -> None:
        if not text:
            self._clear_search()
            return
        self._search = text
        # Remembered separately, and this is the copy the timeout does not
        # touch. `_search` is "what is being typed right now" and expires;
        # `_last_search` is "what was looked for", and it lives until somebody
        # presses Escape or leaves the folder.
        self._last_search = text
        self._search_timer.start()
        model = self._pane.current.model
        # From the row the cursor is on, so a second letter narrows the answer
        # rather than restarting the walk.
        row = model.find(text, start=max(0, self.current_row()))
        self._search_state = "idle" if row >= 0 else "bad"
        if row >= 0:
            self._go_to(row)
        self._render_status()

    def _step_search(self, direction: int) -> None:
        """The next match, or the previous one. Wraps, because the model does."""
        model = self._pane.current.model
        start = self.current_row() + direction
        row = model.find(self._search, start=max(0, start), forward=direction > 0)
        self._search_timer.start()
        self._search_state = "idle" if row >= 0 else "bad"
        if row >= 0:
            self._go_to(row)
        self._render_status()

    def _revive_search(self) -> None:
        """Put the last term back, without moving the cursor.

        Without moving it, which is the whole point: `_set_search` jumps to the
        first match from where the cursor is, and reviving is always followed by
        a step -- so going through `_set_search` would make one Ctrl+G move two
        matches. This only makes the term live again; `_step_search` does the
        moving, from wherever the cursor actually is now.
        """
        self._search = self._last_search
        self._search_state = "idle"
        self._search_timer.start()

    def _clear_search(self, *, forget: bool = False) -> None:
        """Stop narrowing. `forget` also drops the term Ctrl+G would step.

        Two callers and two meanings. The timer means "somebody stopped typing",
        which ends the accumulation and nothing else -- the term is still what
        they were looking for. Escape and a change of folder mean "done with
        this", and those forget it.
        """
        if forget:
            self._last_search = ""
        if not self._search:
            self._render_status()
            return
        self._search = ""
        self._search_state = "idle"
        self._search_timer.stop()
        self._render_status()

    def _go_to(self, row: int) -> None:
        """Put the cursor on a row without marking it.

        The cursor, not a selection, for the reason the first row of a folder
        is not selected: what is marked is what an operation acts on, and
        typing three letters is not a decision to act on anything.
        """
        model = self._view.model()
        picker = self._view.selectionModel()
        if model is None or picker is None or not 0 <= row < model.rowCount():
            return
        index = model.index(row, 0)
        picker.setCurrentIndex(index, QItemSelectionModel.NoUpdate)
        self.listing.scrollTo(index)

    # ---------------------------------------------------------- the favorites

    def _on_favorite(self, path: str, new_tab: bool) -> None:
        """A place from the bar. The pane decides what going there means.

        Including the case that makes locked tabs worth having: `navigate` on
        a locked tab opens a new one, so a favourite clicked from a pinned tab
        does not take the pin with it.
        """
        self.activated.emit(self)
        if new_tab:
            self._pane.open_tab(path)
        else:
            self._pane.navigate(path)
        self.focus_listing()

    def go_to_favorite(self, position: int) -> None:
        """The nth saved place, for Ctrl+1 through Ctrl+9."""
        if self._favorites is None:
            return
        entries = self._favorites.entries
        if 0 <= position < len(entries):
            self._on_favorite(entries[position].path, False)

    def show_favorites_bar(self, shown: bool) -> None:
        """Draw the bar, or do not. It stays hidden while the list is empty
        whichever way this is set -- a row of chrome with nothing in it is
        worse than no row."""
        if self._bar is not None:
            self._bar.set_wanted(shown)

    # --------------------------------------------------------------- the tabs

    def _on_tab_menu(self, point: QPoint) -> None:
        """The menu for one tab, or for the empty part of the strip.

        Which tab was clicked is asked of the bar rather than remembered,
        because tabs are movable and every command here takes an index.
        """
        index = self._tabs.tabAt(point)
        menu = QMenu(self)
        menu.addAction("New tab\tCtrl+T", self._pane.open_tab)
        if index >= 0:
            tab = self._pane.tabs[index]
            menu.addAction("Duplicate tab\tCtrl+Shift+T",
                           lambda: self._pane.duplicate_tab(index))
            menu.addSeparator()
            lock = menu.addAction("Locked", lambda: self._pane.toggle_lock(index))
            lock.setCheckable(True)
            lock.setChecked(tab.locked)
            lock.setToolTip("A locked tab keeps its folder. Opening one from "
                            "here opens a new tab instead.")
            menu.addSeparator()
            close = menu.addAction("Close tab\tCtrl+W",
                                   lambda: self._pane.close_tab(index))
            close.setEnabled(not tab.locked and len(self._pane.tabs) > 1)
            others = menu.addAction("Close other tabs",
                                    lambda: self._pane.close_others(index))
            right = menu.addAction("Close tabs to the right",
                                   lambda: self._pane.close_to_right(index))
            others.setEnabled(self._closable_besides(index))
            right.setEnabled(any(not t.locked
                                 for t in self._pane.tabs[index + 1:]))
        menu.setToolTipsVisible(True)
        menu.exec(self._tabs.mapToGlobal(point))

    def _closable_besides(self, index: int) -> bool:
        return any(position != index and not tab.locked
                   for position, tab in enumerate(self._pane.tabs))

    def open_in_new_tab(self, *, background: bool = True) -> None:
        """The folder under the cursor, in a tab of its own.

        A file has no folder to open, so it is passed over rather than
        refused with a message -- the gesture is a middle click, and a middle
        click that produces a dialog is a middle click nobody makes twice.
        """
        row = self.current_row()
        if row < 0:
            return
        self._open_row_in_tab(row, background=background)

    def _open_row_in_tab(self, row: int, *, background: bool) -> None:
        model = self._pane.current.model
        if model.is_parent_row(row):
            above = self._pane.parent_path()
            if above:
                self._pane.open_tab(above, background=background)
            return
        entry = model.entry(row)
        if entry is None or not entry.is_dir:
            return
        path = self._pane.row_path(row)
        if path:
            self._pane.open_tab(path, background=background)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt naming
        """The two gestures the widgets underneath would otherwise answer
        themselves: a middle click, and typing in the listing.

        Middle click is taken on release rather than press, so one that
        started on one row and finished on another does nothing -- the same
        rule a browser follows, and for the same reason.
        """
        # Both views, not just the listing. `self._grid` was added in 0.16 and
        # this line was left naming one widget, which is the whole of what a
        # second view costs here: in grid view the keys below reached
        # `QListView` instead of this filter, so Space marked a cell rather
        # than counting a folder, the group keys did nothing, and typing a name
        # went to Qt's own `keyboardSearch` -- which jumps without saying what
        # it matched or that it matched nothing.
        #
        # Everything under `self._view.selectionModel()` further down kept
        # working in both views because the selection model is shared. That is
        # exactly why this was invisible: the commands were all fine, and only
        # the keys that have to be caught *before* a view were not.
        # The name column follows the pane's width -- but only while nobody
        # has dragged anything. Once there are stored widths they are the
        # answer, and a column that re-widened itself on every resize would be
        # one that will not stay where it is put. This is what keeps a pane
        # nobody has touched looking exactly as it did before 0.18.
        if watched is self._view.viewport() and event.type() == QEvent.Resize \
                and not self._pane.columns:
            self._apply_columns()

        # The side buttons, first and for every widget this filter watches.
        # First because the two views answer a mouse button before this widget
        # ever sees it -- the same rule the keys below sit under -- and for
        # every widget because a thumb press means back wherever in the pane
        # the pointer happens to be resting.
        if event.type() in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease,
                            QEvent.MouseButtonDblClick) \
                and event.button() in HISTORY_BUTTONS:
            if event.type() == QEvent.MouseButtonRelease:
                self._history_button(event.button())
            return True

        if watched in (self._view, self._grid) and event.type() == QEvent.KeyPress:
            # Space, before the view: `QAbstractItemView` answers it by
            # toggling the selection, so a key press that never reaches this
            # widget would mark a row instead of counting it.
            if event.key() == Qt.Key_Space and not event.modifiers():
                self.measure_selection()
                return True
            if self._on_selection_key(event):
                return True
            if self._on_search_key(event):
                return True
        if watched is self._tabs and event.type() in (QEvent.MouseMove,
                                                       QEvent.Leave):
            hover = (self._tabs.tabAt(event.position().toPoint())
                     if event.type() == QEvent.MouseMove else -1)
            if hover != self._tab_hover:
                self._tab_hover = hover
                self._show_close_buttons()
        if watched is self._tabs and event.type() == QEvent.MouseButtonDblClick \
                and event.button() == Qt.LeftButton:
            # Empty strip only. Double-clicking a tab is how a lot of people
            # rename one, and opening a tab instead would be a surprise on the
            # gesture most likely to be made by accident.
            if self._tabs.tabAt(event.position().toPoint()) < 0:
                self._pane.open_tab()
                return True
        if event.type() == QEvent.MouseButtonRelease and \
                event.button() == Qt.MiddleButton:
            if watched is self._tabs:
                index = self._tabs.tabAt(event.position().toPoint())
                if index >= 0:
                    self._pane.close_tab(index)
                    return True
            elif watched is self._view.viewport():
                index = self._view.indexAt(event.position().toPoint())
                if index.isValid():
                    self._open_row_in_tab(index.row(), background=True)
                    return True
        return super().eventFilter(watched, event)

    def _button(self, text: str, tip: str, slot) -> QToolButton:
        button = QToolButton()
        button.setText(text)
        button.setToolTip(tip)
        button.setFocusPolicy(Qt.NoFocus)  # the listing keeps the focus
        button.clicked.connect(slot)
        return button

    def _nav(self, glyph: str, tip: str, slot) -> QToolButton:
        """A borderless chrome button. The picture arrives with the tokens.

        Claims the pane before it does anything. These take no focus -- the
        listing keeps it, which is the whole point of `NoFocus` here -- and
        the window works out the active pane from where the focus went. So a
        control that never takes focus is a control the window cannot see
        being used, and clicking Up in the pane that is not active would walk
        that pane while every keystroke still went to the other one. That is
        the 0.10 bug with a different first cause, and every borderless
        control in a pane has to answer it.
        """
        button = QToolButton()
        button.setProperty("role", "nav")
        button.setToolTip(tip)
        button.setIconSize(QSize(16, 16))
        button.setFocusPolicy(Qt.NoFocus)
        button.clicked.connect(self._claim)
        button.clicked.connect(slot)
        return button


def _runs(rows) -> list[tuple[int, int]]:
    """Consecutive row numbers, as `(first, last)` pairs.

    A sorted list in, contiguous blocks out. Selecting 40,000 rows is then a
    handful of ranges rather than 40,000 of them, which is the difference
    between a command that feels instant and one that redraws for seconds.
    """
    blocks: list[tuple[int, int]] = []
    for row in rows:
        if blocks and row == blocks[-1][1] + 1:
            blocks[-1] = (blocks[-1][0], row)
        else:
            blocks.append((row, row))
    return blocks


def _tidy(items, *, drop_verbs) -> list[MenuItem]:
    """The entries to draw: the shell's, less the ones this pane already has,
    with the gaps that leaves closed up.

    Dropping an entry leaves its separator behind, and two separators with
    nothing between them read as a menu that failed to draw rather than as one
    that was tidied. So separators are collapsed afterwards rather than
    decided at the same time -- which also means a menu that turns out to be
    entirely duplicates comes back empty instead of coming back as a row of
    lines.
    """
    kept: list[MenuItem] = []
    for item in items:
        if not isinstance(item, MenuItem):
            continue
        if item.kind == MENU_COMMAND and item.verb.lower() in drop_verbs:
            continue
        if item.kind == MENU_SEPARATOR and (not kept or kept[-1].kind == MENU_SEPARATOR):
            continue
        kept.append(item)
    while kept and kept[-1].kind == MENU_SEPARATOR:
        kept.pop()
    return kept


def _menu_icon(item: MenuItem) -> QIcon | None:
    """A menu entry's own picture, if it came with one.

    The same premultiplied BGRA the row icons arrive as, for the same reason:
    a bitmap handle does not cross a process boundary. An entry without one
    draws without one, which is what most of them do.
    """
    if not item.icon or not item.icon_size:
        return None
    size = int(item.icon_size)
    if len(item.icon) != size * size * 4:
        return None
    image = QImage(bytes(item.icon), size, size, size * 4,
                   QImage.Format_ARGB32_Premultiplied).copy()
    if image.isNull():
        return None
    pixmap = QPixmap.fromImage(image)
    return None if pixmap.isNull() else QIcon(pixmap)


def _root_label(path: str) -> str:
    """A short name for a place the drive list does not cover.

    String work on something already displayed rather than path arithmetic:
    the picker only needs something to show, and getting it wrong costs a
    label, not a navigation.
    """
    text = (path or "").rstrip("\\")
    if text.startswith("\\\\"):
        parts = text.split("\\")
        server = parts[2] if len(parts) > 2 else ""
        share = parts[3] if len(parts) > 3 else ""
        return f"\\\\{server}\\{share}" if share else f"\\\\{server}"
    return text[:2] if text[1:2] == ":" else (text or "?")

"""The grid: the same listing, drawn as cells with pictures in them.

The same model. Not a second model, not a proxy, not a copy of the rows -- the
`ListingModel` a pane already has, shown through a second view. Everything that
follows from that is the reason it is built this way: the sort the header set
still applies, the filter still applies, the selection is the same selection, so
switching view mid-task does not lose what was marked, and a folder of 50,000
rows costs nothing extra to show this way because the rows were already there.

`QListView` in `IconMode` rather than a `QTableView` with tall rows, because
what is wanted is reflow -- cells wrapping to the width of the pane and
rewrapping when the splitter moves -- and that is the one thing a table cannot
do at all.

The cell is painted rather than styled, which makes this the fourth thing in the
application that paints: the rows, the queue's job rows, the drive meters, and
now this. The reason is the same every time. A cell is a picture, a name on two
lines, and a selection band with a shape, and QSS can express exactly one of
those.
"""

from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QListView, QStyle, QStyledItemDelegate

from app.core.listing import Column
from app.ui.rows import CUT_OPACITY, IDLE_OPACITY, parse_colour, parse_px

#: Air around a cell's picture, and between the picture and the name. Small
#: numbers that decide whether a grid reads as a grid or as a wall.
CELL_PAD = 8
LABEL_GAP = 5

#: Lines of the name under a cell. Two, which fits most real filenames and is
#: what Explorer settled on; one truncates almost everything in a folder of
#: `DSC_0431-edited-final.jpg`, and three makes the cells tall enough that
#: fewer fit on screen than the pictures are worth.
LABEL_LINES = 2

#: How much wider a cell is than its picture. A cell narrower than its name is
#: a cell of ellipses, and the extra width costs one column across a pane.
CELL_EXTRA = 26


class CellDelegate(QStyledItemDelegate):
    """One cell: a picture or an icon, a name under it, and a band when marked."""

    def __init__(self, thumbnails, parent=None) -> None:
        super().__init__(parent)
        self._thumbnails = thumbnails
        self._cell = 128
        self._live = True
        self._tokens: dict[str, str] = {}

    def apply_tokens(self, tokens: dict[str, str]) -> None:
        self._tokens = dict(tokens)

    def set_cell(self, size: int) -> None:
        self._cell = max(16, int(size))

    def sizeHint(self, option, index) -> QSize:  # noqa: N802 - Qt naming
        del option, index
        metrics = QFontMetrics(self._font())
        label = metrics.height() * LABEL_LINES
        return QSize(self._cell + CELL_EXTRA,
                     self._cell + LABEL_GAP + label + CELL_PAD * 2)

    def paint(self, painter: QPainter, option, index) -> None:
        from app.core.listing import ListingModel

        model = index.model()
        if model is None:
            return
        painter.save()
        painter.setClipRect(option.rect)

        # `QStyle` by name rather than off the option's own enum class. The
        # latter works and is one attribute lookup away from silently being
        # `int`, at which point every state reads as False and the selection
        # stops drawing -- with nothing to see in a diff.
        selected = bool(option.state & QStyle.State_Selected)
        current = bool(option.state & QStyle.State_HasFocus)
        hovered = bool(option.state & QStyle.State_MouseOver) and not selected
        self._band(painter, option.rect, selected=selected, current=current,
                   hovered=hovered)

        entry = model.data(index, ListingModel.EntryRole)
        is_dir = bool(model.data(index, ListingModel.IsDirRole))
        cut = bool(model.data(index, ListingModel.CutRole))
        # The same fades the listing uses, for the same reasons and by the
        # same numbers: a cut row and a cut cell are the same fact, and so are
        # an idle pane's rows and its cells.
        fade = (1.0 if self._live else IDLE_OPACITY) * (CUT_OPACITY if cut else 1.0)
        if fade < 1.0:
            painter.setOpacity(fade)

        box = QRect(option.rect)
        box.adjust(CELL_PAD, CELL_PAD, -CELL_PAD, -CELL_PAD)
        picture_box = QRect(box.left(), box.top(), box.width(), self._cell)

        drawn = False
        if entry is not None and self._thumbnails is not None:
            picture = self._thumbnails.picture(model.folder, entry)
            if picture is not None and not picture.isNull():
                size = picture.size()
                scale = min(picture_box.width() / max(1, size.width()),
                            picture_box.height() / max(1, size.height()), 1.0)
                target = QRect(0, 0, max(1, round(size.width() * scale)),
                               max(1, round(size.height() * scale)))
                target.moveCenter(picture_box.center())
                painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
                painter.drawPixmap(target, picture)
                drawn = True

        if not drawn:
            # The icon for the kind, drawn large. Which is not a placeholder: a
            # folder of drawings in grid view is a grid of the application's
            # own icon at 64 pixels, and that is a legible way to look at a
            # folder of drawings. Nothing about a cell says "waiting".
            icon = model.data(index, Qt.DecorationRole)
            if icon is not None:
                # Two thirds, so an icon does not pretend to be a photograph
                # filling its cell.
                edge = max(16, int(self._cell * 0.62))
                target = QRect(0, 0, edge, edge)
                target.moveCenter(picture_box.center())
                icon.paint(painter, target, Qt.AlignCenter)

        self._label(painter, box, index, is_dir=is_dir)
        painter.restore()

    # -------------------------------------------------------------- internals

    def _band(self, painter: QPainter, rect: QRect, *, selected: bool,
              current: bool, hovered: bool = False) -> None:
        """The marked state, as a rounded shape rather than a filled rectangle.

        `app/ui/rows.py` makes the same argument for the listing: a band with a
        radius reads as a shape on a surface, and a rectangle painted edge to
        edge reads as a table cell from another decade. Here it is the whole
        cell rather than an inset stripe, because a cell is already an object
        with air around it.
        """
        if not selected and not current and not hovered:
            return
        box = QRect(rect).adjusted(2, 2, -2, -2)
        radius = parse_px(self._tokens.get("radius"), 7)
        if hovered:
            painter.setPen(Qt.NoPen)
            painter.setBrush(parse_colour(self._tokens.get("bg_3", "")))
            painter.drawRoundedRect(box, radius, radius)
        if selected:
            painter.setPen(Qt.NoPen)
            key = "accent_row" if self._live else "accent_row_idle"
            painter.setBrush(parse_colour(self._tokens.get(key, "")))
            painter.drawRoundedRect(box, radius, radius)
        if current:
            pen = QPen(parse_colour(self._tokens.get("accent_line", "")))
            pen.setWidth(1)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(box, radius, radius)

    def _label(self, painter: QPainter, box: QRect, index, *,
               is_dir: bool) -> None:
        """The name, wrapped to two lines and elided in the middle of the second.

        Elided in the middle rather than at the end, and it is the one thing in
        this cell worth arguing about. Every interesting part of a real filename
        is at one end or the other -- a job number at the front, a revision and
        an extension at the back -- and cutting the tail off throws away the
        half that says which version this is.
        """
        painter.setPen(QPen(parse_colour(self._tokens.get("txt_0", ""))))
        painter.setFont(self._font())
        metrics = QFontMetrics(self._font())
        text = str(index.model().data(index.model().index(
            index.row(), int(Column.NAME)), Qt.DisplayRole) or "")
        if not is_dir:
            # The extension back on. `Column.NAME` is the stem alone because the
            # listing has a column of its own for the suffix, and a cell has
            # no columns -- so a grid without this would show `plan` and `plan`
            # for a drawing and its PDF.
            suffix = str(index.model().data(index.model().index(
                index.row(), int(Column.EXT)), Qt.DisplayRole) or "")
            if suffix:
                text = f"{text}.{suffix}"

        label = QRect(box.left(), box.top() + self._cell + LABEL_GAP,
                      box.width(), metrics.height() * LABEL_LINES)
        first, rest = _wrap(text, metrics, label.width())
        painter.drawText(QRect(label.left(), label.top(), label.width(),
                              metrics.height()),
                         int(Qt.AlignHCenter | Qt.AlignVCenter), first)
        if rest:
            painter.drawText(
                QRect(label.left(), label.top() + metrics.height(),
                      label.width(), metrics.height()),
                int(Qt.AlignHCenter | Qt.AlignVCenter),
                metrics.elidedText(rest, Qt.ElideMiddle, label.width()))

    def set_live(self, live: bool) -> None:
        """Whether this pane has the keyboard.

        The selection in the inactive pane is drawn weaker, exactly as the
        listing's is -- `accent_row_idle` against `accent_row`. With two panes
        and one keyboard, two equally bright selections is the ambiguity the
        accent border exists to remove, and a grid full of cells makes it worse
        than a listing does.
        """
        if live != self._live:
            self._live = live

    def _font(self):
        from PySide6.QtWidgets import QApplication

        return QApplication.font()


class GridView(QListView):
    """The listing as cells. One model, two views, one selection.

    `setSelectionModel` is what makes that last part true and it is done by the
    pane rather than here, because the pane owns both views and the model. What
    this class adds is the icon mode, the reflow, and a size hint that follows
    the cell size.
    """

    def __init__(self, thumbnails, parent=None) -> None:
        super().__init__(parent)
        self._cells = CellDelegate(thumbnails, self)
        self.setItemDelegate(self._cells)
        self.setProperty("role", "grid")
        self.setViewMode(QListView.IconMode)
        # Adjust, not Fixed: the whole reason this is a list view is that cells
        # rewrap when the splitter moves, and Fixed would leave a column of
        # empty space after every drag.
        self.setResizeMode(QListView.Adjust)
        self.setMovement(QListView.Static)
        self.setWrapping(True)
        self.setUniformItemSizes(True)
        self.setSelectionBehavior(QListView.SelectRows)
        self.setSelectionMode(QListView.ExtendedSelection)
        self.setEditTriggers(QListView.NoEditTriggers)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setMouseTracking(True)
        self.setTabKeyNavigation(False)      # Tab belongs to the window
        # The model's first column, because a cell draws one thing. The other
        # columns are still there and still sorted; they are simply not what a
        # grid shows.
        self.setModelColumn(int(Column.NAME))

    def apply_tokens(self, tokens: dict[str, str]) -> None:
        self._cells.apply_tokens(tokens)
        self.viewport().update()

    def set_live(self, live: bool) -> None:
        self._cells.set_live(live)
        self.viewport().update()

    def set_cell(self, size: int) -> None:
        self._cells.set_cell(size)
        # `reset` rather than a repaint: the size hint changed, and a view that
        # is only told to repaint keeps laying the cells out on the old one.
        self.setGridSize(self._cells.sizeHint(None, None))
        self.reset()


def _wrap(text: str, metrics: QFontMetrics, width: int) -> tuple[str, str]:
    """Split a name into what fits on the first line and what is left.

    Split by measuring rather than by words, because a filename is frequently
    one word forty characters long and a word-wrap would put the whole of it on
    the second line and elide it all.
    """
    if metrics.horizontalAdvance(text) <= width:
        return text, ""
    at = len(text)
    while at > 1 and metrics.horizontalAdvance(text[:at]) > width:
        at -= 1
    return text[:at], text[at:]



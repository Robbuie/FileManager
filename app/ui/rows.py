"""How a row is drawn: the selection, the size bar and the age chip.

These three are here rather than in the stylesheet because QSS cannot express
any of them. `QTableView::item` takes no `border-radius`, so a selected row
drawn from the sheet is a hard rectangle across the pane; and there is no way
at all to ask a sheet for a second shape inside a cell, which is what a bar and
a chip are.

What it draws, and why each one is worth a delegate:

  * **The selected row** is a rounded band inset from the edges of the
    listing, rather than a rectangle bleeding into them. Rounded because the
    pane it sits in is, and a square selection inside a rounded pane is the
    detail that makes a themed window look like a themed window with a table
    dropped into it.
  * **The size bar** is two pixels under the figure, as wide as that file is
    against the largest file in the listing. Finding what is big in a folder
    stops being arithmetic done by eye over a column of numbers.
  * **The age chip** is how long ago, tinted in three steps. The Modified
    column says exactly when, which is six digits to read; this says how long
    ago, which is the question actually being asked.

Two rules hold the accessibility line. The chip always carries its text, so the
tint is a second signal and never the only one -- which is what keeps it honest
in the high contrast theme and for anyone who cannot separate the greens. And
every colour comes from the token set handed in by `apply_tokens`, so all of it
follows the theme, the accent and the density like the rest of the chrome. A
literal here would be exactly the spot that stops following the picker.

The paint path runs per visible cell, so it holds to what the rest of the
application holds to: it asks the model for values it already has, computes
nothing over the whole list, and touches no filesystem. The one number that is
about the listing rather than the row -- the largest file, for the bar -- is
the model's own `size_scale`, computed once per change and cached there.
"""

from __future__ import annotations

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPalette
from PySide6.QtWidgets import QApplication, QStyle, QStyledItemDelegate, QStyleOptionViewItem

from app.core.listing import Column, ListingModel
from app.io import paths

#: The heading drawn above the first row of each folder in grouped flat view.
GROUP_HEAD = 26

#: Height of the size bar, and how far its baseline sits off the bottom of the
#: row. Small numbers, but they are the difference between a bar that reads as
#: part of the figure above it and one that reads as a stray line.
BAR_HEIGHT = 2
BAR_LIFT = 3

#: Inset of the selection band from the left and right edges of the listing.
#: The band is a shape floating on the pane, not a stripe painted across it.
BAND_INSET = 3

#: Vertical air above and below the band, so consecutive selected rows read as
#: a run of rows rather than one tall block.
BAND_GAP = 1

#: How faded a row cut to the clipboard is drawn. Explorer's own figure is
#: close to this. Lower and the name stops being readable on the dark themes,
#: which turns "this is going somewhere" into "this row is broken".
CUT_OPACITY = 0.45


def tabular(font: QFont) -> QFont:
    """The same font with every digit the same width, so a column of sizes or
    dates lines up digit under digit. Qt 6.7 and later; a font without the
    feature, or an older Qt, just draws as it did."""
    copy = QFont(font)
    try:
        copy.setFeature(QFont.Tag("tnum"), 1)
    except (AttributeError, TypeError):
        pass
    return copy

#: How the pane that is not taking keystrokes draws its rows. 0.23: the accent
#: bar down the active pane's edge was the only way to tell which side a key
#: would land in, and it is two pixels wide. Opacity rather than a second set
#: of muted tokens, for the reason the cut fade gives -- it has to reach the
#: icon, the size bar and the age chip as well as the text. Multiplied with
#: the cut fade, so a cut row in the idle pane is still fainter than its
#: neighbours.
IDLE_OPACITY = 0.6


def parse_colour(value: str | None) -> QColor:
    """A token into a `QColor`, taking both forms the token set produces.

    Solid tokens are `#rrggbb`, which `QColor` reads itself. Derived tints are
    written as `rgba(r, g, b, a)` for QSS, and that form `QColor` does not
    read -- it returns an invalid colour and paints nothing, silently. Hence
    this, and hence a test on it.
    """
    if not value:
        return QColor()
    text = value.strip()
    if text.startswith("rgba(") and text.endswith(")"):
        parts = [p.strip() for p in text[5:-1].split(",")]
        if len(parts) != 4:
            return QColor()
        try:
            r, g, b = (int(float(p)) for p in parts[:3])
            alpha = float(parts[3])
        except ValueError:
            return QColor()
        colour = QColor(r, g, b)
        colour.setAlphaF(max(0.0, min(1.0, alpha)))
        return colour
    return QColor(text)


def parse_px(value: str | None, fallback: int) -> int:
    """`10px` to 10. A density or a shape token, as a number to paint with."""
    if not value:
        return fallback
    try:
        return int(float(str(value).rstrip("px").strip()))
    except ValueError:
        return fallback


def _one_step_smaller(base: QFont) -> QFont:
    """The same font, one unit down, in whichever unit it was set in.

    A font carries a size in points *or* in pixels, never both, and asking a
    pixel-sized font for its point size gets -1 rather than a conversion.
    Every font in this application is pixel-sized, because the sheet writes
    `font-size: 13px` and Qt honours that as pixels -- so a step measured in
    points here would ignore the density entirely and draw the same small
    chip at every setting. This asks the font which unit it is in and steps
    in that one.

    The floors are there so the chip stays legible if a later density goes
    smaller than any of the three today.
    """
    font = QFont(base)
    pixels = base.pixelSize()
    if pixels > 0:
        font.setPixelSize(max(8, pixels - 1))
    else:
        font.setPointSizeF(max(7.0, base.pointSizeF() - 1.0))
    return font


class RowDelegate(QStyledItemDelegate):
    """Draws the listing's rows. Give it tokens before it paints anything."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._t: dict[str, str] = {}
        self._radius = 6
        self._live = True
        self._hovered = -1
        self._chip_font: QFont | None = None

    # ------------------------------------------------------------- the state

    def apply_tokens(self, tokens: dict[str, str]) -> None:
        """Take the colours the sheet was rendered from.

        Handed in rather than read from `app.theme` here, so that what the
        rows are painted with and what the chrome is styled with cannot come
        from two different renders of the token set.
        """
        self._t = dict(tokens)
        self._radius = max(3, parse_px(tokens.get("radius_sm"), 5) + 1)
        self._chip_font = None

    def set_live(self, live: bool) -> None:
        """Whether this pane is the one taking keystrokes.

        Two panes with two selections on screen and only one of them live is
        the situation the idle wash exists for: the pane that would act on a
        key reads stronger than the one that would not.
        """
        if live != self._live:
            self._live = live

    def set_hovered_row(self, row) -> None:
        """The row under the mouse, or -1. Takes a `QModelIndex` or an int."""
        value = row.row() if hasattr(row, "row") else int(row)
        if value != self._hovered:
            self._hovered = value

    @property
    def hovered_row(self) -> int:
        return self._hovered

    # ------------------------------------------------------------- painting

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        model = index.model()
        heading = (model.group_heading(index.row())
                   if hasattr(model, "group_heading") else None)
        if heading is not None:
            # Grouped flat view: this row was made taller, and the top of it is
            # the folder's heading. Everything below paints into what is left,
            # so the band, the hover and the text sit where a row always does.
            option = QStyleOptionViewItem(option)
            full = QRect(option.rect)
            self._heading(painter, QRect(full.left(), full.top(), full.width(),
                                         GROUP_HEAD), index, heading)
            full.setTop(full.top() + GROUP_HEAD)
            option.rect = full
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)

        selected = bool(opt.state & QStyle.State_Selected)
        hovered = index.row() == self._hovered and not selected

        # The background is this delegate's job from here on. Qt would
        # otherwise paint the palette's highlight as a hard rectangle
        # underneath everything drawn below, which is the thing being
        # replaced -- and the focus rectangle on top of it.
        opt.state &= ~QStyle.State_Selected
        opt.state &= ~QStyle.State_HasFocus
        opt.backgroundBrush = Qt.NoBrush

        painter.save()
        if selected or hovered:
            self._band(painter, option, index, selected)

        # A cut row is faded, which is what Explorer does and therefore what
        # these eyes already read as "this is going somewhere". Opacity rather
        # than a muted colour: it has to work on the icon as well as the text,
        # and a second set of greyed tokens per theme is five more numbers to
        # keep in step for no gain. The band underneath keeps its strength --
        # a cut row that is also the selected row still has to look selected.
        fade = 1.0 if self._live else IDLE_OPACITY
        if index.data(ListingModel.CutRole):
            fade *= CUT_OPACITY
        if fade < 1.0:
            painter.setOpacity(fade)

        if index.column() in (Column.SIZE, Column.AGE, Column.MODIFIED):
            opt.font = tabular(opt.font)
        ext = self._inline_ext(opt, index)
        if index.column() == Column.AGE:
            self._age(painter, opt, index)
        elif ext:
            # Not `super().paint`: that runs `initStyleOption` again and puts
            # the text straight back, so the stem is drawn twice, a pixel apart.
            # The style draws the icon and the background; this draws the text.
            stem = opt.text
            opt.text = ""
            style = opt.widget.style() if opt.widget is not None else QApplication.style()
            style.drawControl(QStyle.CE_ItemViewItem, opt, painter, opt.widget)
            opt.text = stem
            self._name_and_ext(painter, opt, stem, ext)
        else:
            super().paint(painter, opt, index)
            if index.column() == Column.SIZE:
                self._bar(painter, option, index)
        painter.restore()

    def _heading(self, painter: QPainter, rect: QRect, index, heading) -> None:
        """A folder's heading across the top of the row that starts it: its
        path under the flattened folder and how many files are in it, in the
        name column, and a rule under it across every column."""
        where, count = heading
        painter.save()
        line = parse_colour(self._t.get("line_soft"))
        if line.isValid():
            painter.fillRect(QRect(rect.left(), rect.bottom() - 1, rect.width(), 1), line)
        if index.column() == int(Column.NAME):
            model = index.model()
            label = where or (paths.leaf(model.folder) if model.folder else "")
            font = QFont(painter.font())
            font.setBold(True)
            painter.setFont(font)
            metrics = QFontMetrics(font)
            ink = parse_colour(self._t.get("txt_1"))
            painter.setPen(ink if ink.isValid() else QColor(Qt.gray))
            text_rect = rect.adjusted(8, 4, -4, -2)
            tail = f"   {count:,} file{'s' if count != 1 else ''}"
            tail_w = QFontMetrics(painter.font()).horizontalAdvance(tail)
            shown = metrics.elidedText(label, Qt.ElideMiddle,
                                       max(0, text_rect.width() - tail_w))
            painter.drawText(text_rect, int(Qt.AlignLeft | Qt.AlignVCenter), shown)
            font.setBold(False)
            painter.setFont(font)
            muted = parse_colour(self._t.get("txt_2"))
            painter.setPen(muted if muted.isValid() else QColor(Qt.gray))
            painter.drawText(text_rect.adjusted(metrics.horizontalAdvance(shown), 0, 0, 0),
                             int(Qt.AlignLeft | Qt.AlignVCenter), tail)
        painter.restore()

    @staticmethod
    def _inline_ext(opt: QStyleOptionViewItem, index) -> str:
        """The extension to draw after the name, or "" when the Ext column is
        on screen to say it instead.

        0.24 hides that column by default and puts the extension back on the
        name in the muted grey -- and a pane squeezed narrow enough to lose the
        column gets the same, which is when it matters most.
        """
        if index.column() != Column.NAME:
            return ""
        view = opt.widget
        if view is None or not hasattr(view, "isColumnHidden") \
                or not view.isColumnHidden(int(Column.EXT)):
            return ""
        return str(index.siblingAtColumn(int(Column.EXT)).data(Qt.DisplayRole) or "")

    def _name_and_ext(self, painter: QPainter, opt: QStyleOptionViewItem,
                      stem: str, ext: str) -> None:
        """The stem in the row's text colour and `.ext` after it, muted.

        The stem is what gets elided when the two do not fit. The extension is
        the part that says which of two same-named files this is, so it is the
        part kept whole.
        """
        style = opt.widget.style() if opt.widget is not None else None
        if style is None:
            return
        rect = style.subElementRect(QStyle.SE_ItemViewItemText, opt, opt.widget)
        rect = rect.adjusted(3, 0, -2, 0)
        metrics = QFontMetrics(opt.font)
        tail = "." + ext
        tail_w = metrics.horizontalAdvance(tail)
        shown = metrics.elidedText(stem, Qt.ElideRight, max(0, rect.width() - tail_w))
        stem_w = metrics.horizontalAdvance(shown)
        painter.save()
        painter.setFont(opt.font)
        painter.setPen(opt.palette.color(QPalette.Text))
        painter.drawText(rect.adjusted(0, 0, 0, 0), int(Qt.AlignLeft | Qt.AlignVCenter), shown)
        muted = parse_colour(self._t.get("txt_2"))
        painter.setPen(muted if muted.isValid() else QColor(Qt.gray))
        painter.drawText(rect.adjusted(stem_w, 0, 0, 0),
                         int(Qt.AlignLeft | Qt.AlignVCenter), tail)
        painter.restore()

    def _band(self, painter: QPainter, option: QStyleOptionViewItem,
              index, selected: bool) -> None:
        """The row's background, rounded at the ends of the row only.

        Each cell paints its own slice. A slice that is not at an end of the
        row is extended past that edge by the radius and clipped back to the
        cell, which leaves it square there and rounded at the end it actually
        is -- so the row reads as one band rather than as five rounded cells.
        """
        if selected:
            key = "accent_row" if self._live else "accent_row_idle"
        else:
            key = "bg_3"
        colour = parse_colour(self._t.get(key))
        if not colour.isValid():
            return

        model = index.model()
        last = (model.columnCount() - 1) if model is not None else index.column()
        first_cell = index.column() == 0
        last_cell = index.column() == last

        rect = QRectF(option.rect)
        rect.adjust(0, BAND_GAP, 0, -BAND_GAP)
        if first_cell:
            rect.adjust(BAND_INSET, 0, 0, 0)
        else:
            rect.adjust(-self._radius * 2, 0, 0, 0)
        if last_cell:
            rect.adjust(0, 0, -BAND_INSET, 0)
        else:
            rect.adjust(0, 0, self._radius * 2, 0)

        painter.save()
        painter.setClipRect(option.rect)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(colour)
        painter.drawRoundedRect(rect, self._radius, self._radius)
        painter.restore()

    def _bar(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        """A file's size against the largest file in this listing.

        Nothing is drawn for a folder, including one that has been measured:
        a folder total can be orders of magnitude past anything in the folder,
        and on the same scale every real file becomes no bar at all. The
        number is still there; the comparison is the thing that would be a lie.
        """
        share = index.data(ListingModel.SizeShareRole)
        if not share:
            return
        colour = parse_colour(self._t.get("bg_4"))
        if not colour.isValid():
            return

        rect = option.rect
        room = max(0, rect.width() - 12)
        width = max(1, int(room * max(0.0, min(1.0, float(share)))))
        bar = QRectF(
            rect.right() - 6 - width,
            rect.bottom() - BAR_LIFT - BAR_HEIGHT,
            width,
            BAR_HEIGHT,
        )
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(colour)
        painter.drawRoundedRect(bar, 1, 1)
        painter.restore()

    def _age(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        """How long ago, as a tinted chip -- or as plain muted text past a month.

        The text is drawn whether or not there is a tint behind it. That is the
        rule that keeps this readable in the high contrast theme and for anyone
        who cannot tell the greens apart: the colour is a second way of saying
        what the characters already say, never the only way.
        """
        text = index.data(Qt.DisplayRole)
        if not text:
            return
        step = index.data(ListingModel.AgeStepRole)
        tint = parse_colour(self._t.get(f"age_{step}")) if step else QColor()
        ink = parse_colour(self._t.get("age_text" if step else "txt_2"))

        font = self._chip_font
        if font is None:
            font = _one_step_smaller(option.font)
            self._chip_font = font

        metrics = option.fontMetrics
        painter.save()
        painter.setFont(font)
        width = max(painter.fontMetrics().horizontalAdvance(str(text)) + 12, 30)
        height = min(option.rect.height() - 4, metrics.height() + 2)
        chip = QRectF(
            option.rect.right() - 6 - width,
            option.rect.center().y() - height / 2.0,
            width,
            height,
        )
        if tint.isValid() and tint.alpha():
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setPen(Qt.NoPen)
            painter.setBrush(tint)
            painter.drawRoundedRect(chip, 4, 4)
        painter.setPen(ink if ink.isValid() else QColor(Qt.gray))
        painter.drawText(chip, int(Qt.AlignCenter), str(text))
        painter.restore()

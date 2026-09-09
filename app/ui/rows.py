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

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

from app.core.listing import Column, ListingModel

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

        if index.column() == Column.AGE:
            self._age(painter, opt, index)
        else:
            super().paint(painter, opt, index)
            if index.column() == Column.SIZE:
                self._bar(painter, option, index)
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
            font = QFont(option.font)
            font.setPointSizeF(max(7.0, option.font.pointSizeF() - 1.0))
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

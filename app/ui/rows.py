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
  * **The size bar** is as wide as that file is against the largest file in
    the listing. Finding what is big in a folder stops being arithmetic done
    by eye over a column of numbers. Since 0.47 it is a soft block *behind*
    the figure by default; the two-pixel line under it ran into the digits.
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

import time

from PySide6.QtCore import QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPainter, QPainterPath, QPalette, QPen
from PySide6.QtWidgets import QApplication, QStyle, QStyledItemDelegate, QStyleOptionViewItem

from app.core import filetypes, when
from app.core.listing import ATTRIBUTE_HIDDEN, Column, ListingModel
from app.io import paths
from app.ui import glyphs

#: 0.27: the type badge that stands where the icon was. Wide enough for four
#: capitals in the small mono face; the listing's name column moves over by the
#: difference, which is the price of being able to read the type at a glance.
BADGE_W = 32
BADGE_H = 16

#: The heading drawn above the first row of each folder in grouped flat view.
GROUP_HEAD = 26

#: Height of the size bar, and how far its baseline sits off the bottom of the
#: row. Small numbers, but they are the difference between a bar that reads as
#: part of the figure above it and one that reads as a stray line.
BAR_HEIGHT = 2
BAR_LIFT = 3

#: 0.47: the narrowest bar drawn behind a figure.
BEHIND_MIN = 8

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

#: 0.33: a hidden file, when hidden files are shown. Not as faint as a cut
#: row: a cut row is about to go somewhere, a hidden one is merely not meant
#: to be looked at, and it still has to be readable when somebody is looking.
HIDDEN_OPACITY = 0.55

#: 0.34: a file older than `fade_days`. Fainter than a hidden one: the point is
#: that the eye skips it, and it is still one click from being read.
OLD_OPACITY = 0.5

#: 0.38: the width a label's dot or a note's mark takes at the end of a name.
LABEL_ROOM = 14


def size_bar_rect(mode: str, cell: QRectF, share: float) -> QRectF | None:
    """Where a size bar goes in its cell, or None for no bar.

    0.47. "under" is the 0.11 bar: two pixels a few above the bottom of the
    row, right under the figure -- and at compact density that is the bottom
    of the digits, which is what was reported as the line making the size hard
    to read. "behind" is the replacement: the whole height of the row inside
    the selection band's air, so the figure sits *on* the bar rather than
    having one cross it, and nothing in it is near a glyph's edge. A plain
    function so the geometry is tested without a painter.
    """
    if mode not in ("behind", "under") or not share or share <= 0:
        return None
    share = min(1.0, float(share))
    if mode == "under":
        room = max(0.0, cell.width() - 12)
        width = max(1.0, float(int(room * share)))
        return QRectF(cell.right() - 6 - width,
                      cell.bottom() - BAR_LIFT - BAR_HEIGHT, width, BAR_HEIGHT)
    room = max(0.0, cell.width() - 6)
    width = round(room * share)
    if width < BEHIND_MIN:
        # A few pixels behind the last digit read as a cursor or a stray
        # divider rather than as a bar, so a file that small against the
        # largest gets none: "no bar" already says "not one of the big ones".
        return None
    top = cell.top() + BAND_GAP + 2
    height = max(2.0, cell.height() - 2 * (BAND_GAP + 2))
    return QRectF(cell.right() - 3 - width, top, width, height)


def visible_position(view, column: int) -> tuple[int, bool]:
    """`column`'s place among the columns on screen, and whether it is last.

    For the banded and ruled styles: shading every other *visible* column,
    and drawing no line after the last one, both need to skip the hidden
    columns -- Ext starts hidden and Location is hidden outside flat view, so
    counting by logical index would shade two neighbours alike and draw a
    line at the right edge of the listing.
    """
    if view is None:
        return 0, False
    header = view.horizontalHeader() if hasattr(view, "horizontalHeader") else view
    if not hasattr(header, "visualIndex"):
        return 0, False
    own = header.visualIndex(column)
    place = 0
    last = True
    for visual in range(header.count()):
        logical = header.logicalIndex(visual)
        if header.isSectionHidden(logical):
            continue
        if visual < own:
            place += 1
        elif visual > own:
            last = False
    return place, last


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
        self._badge_font: QFont | None = None
        #: Badges in place of icons. Off keeps the shell's pictures, which is
        #: the pre-0.27 listing exactly.
        self.badges = False
        #: 0.29: `(folder, name) -> fraction or None`, for a row a transfer is
        #: writing. Set by the pane when there is a queue to ask.
        self.progress = None
        #: 0.34: "off", "chip" or "glow" -- see `listing.recency`.
        self.recency = "glow"
        #: 0.34: files older than this many days are drawn faded; 0 is never.
        self.fade_days = 0.0
        self._today = 0.0
        self._today_checked = 0.0
        #: 0.47: "off", "header", "ruled" or "banded" -- see
        #: `listing.column_edges`. The rows draw the last two; the header
        #: draws its own dividers for all but "off".
        self.edges = "header"
        #: 0.47: "behind", "under" or "off" -- see `listing.size_bar`.
        self.size_bar = "behind"

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
        self._badge_font = None

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
        entry = None
        if self.badges and index.column() == Column.NAME:
            entry = index.data(ListingModel.EntryRole)
            if entry is not None:
                # Keep the room an icon would take, draw nothing in it, and put
                # the badge there afterwards.
                opt.icon = QIcon()
                opt.features |= QStyleOptionViewItem.HasDecoration
                opt.decorationSize = QSize(BADGE_W, BADGE_H)

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
        place, last = (visible_position(option.widget, index.column())
                       if self.edges in ("banded", "ruled") else (0, False))
        if self.edges == "banded" and place % 2 == 1:
            shade = parse_colour(self._t.get("band"))
            if shade.isValid():
                painter.fillRect(option.rect, shade)
        if selected or hovered:
            self._band(painter, option, index, selected)
        if self.progress is not None:
            copying = entry if entry is not None else index.data(ListingModel.EntryRole)
            folder = getattr(model, "folder", "")
            fraction = (self.progress(folder, copying.name)
                        if copying is not None and folder else None)
            if fraction is not None:
                self._filling(painter, option, fraction)

        # A cut row is faded, which is what Explorer does and therefore what
        # these eyes already read as "this is going somewhere". Opacity rather
        # than a muted colour: it has to work on the icon as well as the text,
        # and a second set of greyed tokens per theme is five more numbers to
        # keep in step for no gain. The band underneath keeps its strength --
        # a cut row that is also the selected row still has to look selected.
        row_entry = entry if entry is not None else index.data(ListingModel.EntryRole)
        if self.recency == "glow" and row_entry is not None \
                and row_entry.mtime >= self._start_of_today():
            self._glow(painter, option, index)

        fade = 1.0 if self._live else IDLE_OPACITY
        if index.data(ListingModel.CutRole):
            fade *= CUT_OPACITY
        if row_entry is not None and row_entry.attributes & ATTRIBUTE_HIDDEN:
            # 0.33: shown, because the setting says so, and dimmed, because
            # a hidden file listed at full strength is indistinguishable from
            # one somebody meant to be there.
            fade *= HIDDEN_OPACITY
        if self.fade_days > 0 and row_entry is not None and not row_entry.is_dir \
                and row_entry.mtime \
                and time.time() - row_entry.mtime > self.fade_days * 86400:
            # 0.34: an old file steps back, so this week's work stands out.
            # Files only: a folder's date moves whenever anything in it does,
            # so an old folder date is rarer and says less.
            fade *= OLD_OPACITY
        if fade < 1.0:
            painter.setOpacity(fade)

        marker = None
        if index.column() == Column.NAME:
            label = index.data(ListingModel.LabelRole) or (0, "")
            git = index.data(ListingModel.GitRole) or ""
            if label[0] or label[1] or git:
                marker = (label[0], label[1], git)
        if marker:
            # 0.38: room at the end of the name for git's letter, the label's
            # dot and the note's mark, taken from the text so a long name
            # elides short of them instead of running under them.
            room = sum(LABEL_ROOM for part in marker if part) + (4 if marker[2] else 0)
            opt.rect = opt.rect.adjusted(0, 0, -room, 0)
        if index.column() in (Column.SIZE, Column.AGE, Column.MODIFIED):
            opt.font = tabular(opt.font)
        ext = self._inline_ext(opt, index)
        if index.column() == Column.AGE:
            self._age(painter, opt, index)
        elif ext or entry is not None:
            # Not `super().paint`: that runs `initStyleOption` again and puts
            # the text straight back, so the stem is drawn twice, a pixel apart.
            # The style draws the icon and the background; this draws the text.
            stem = opt.text
            if ext:
                opt.text = ""
            style = opt.widget.style() if opt.widget is not None else QApplication.style()
            style.drawControl(QStyle.CE_ItemViewItem, opt, painter, opt.widget)
            if entry is not None:
                self._badge(painter, opt, style, entry)
            if ext:
                opt.text = stem
                self._name_and_ext(painter, opt, stem, ext)
        else:
            sized = index.column() == Column.SIZE
            if sized and self.size_bar == "behind":
                self._bar(painter, option, index)
            super().paint(painter, opt, index)
            if sized and self.size_bar == "under":
                self._bar(painter, option, index)
        if marker:
            self._label(painter, option, marker)
        painter.restore()
        if self.edges == "ruled" and not last:
            # After the restore, so the line is not faded with the row: it is
            # part of the listing's structure, not of the file.
            line = parse_colour(self._t.get("rule_soft"))
            if line.isValid():
                x = option.rect.right()
                painter.fillRect(QRectF(x, option.rect.top(), 1,
                                        option.rect.height()), line)

    def _label(self, painter: QPainter, option: QStyleOptionViewItem, marker) -> None:
        """Git's letter, a colour label's dot and a note's mark, at the end of
        the name, right to left in that order of importance."""
        colour, note, git = marker
        right = option.rect.right() - 6
        middle = option.rect.center().y()
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        if colour:
            fill = parse_colour(self._t.get(f"label_{colour}"))
            if fill.isValid():
                painter.setPen(Qt.NoPen)
                painter.setBrush(fill)
                painter.drawEllipse(QRectF(right - 8, middle - 4, 8, 8))
            right -= LABEL_ROOM
        if note:
            ink = parse_colour(self._t.get("warn"))
            if ink.isValid():
                pen = QPen(ink, 1.3)
                painter.setPen(pen)
                painter.setBrush(Qt.NoBrush)
                shape = QPainterPath()
                shape.moveTo(right - 9, middle - 4)
                shape.lineTo(right, middle - 4)
                shape.lineTo(right, middle + 2)
                shape.lineTo(right - 5, middle + 2)
                shape.lineTo(right - 8, middle + 5)
                shape.lineTo(right - 8, middle + 2)
                shape.lineTo(right - 9, middle + 2)
                shape.closeSubpath()
                painter.drawPath(shape)
            right -= LABEL_ROOM
        if git:
            self._git_chip(painter, right, middle, git)
        painter.restore()

    def _git_chip(self, painter: QPainter, right: float, middle: float, code: str) -> None:
        """Git's mark as a small chip: amber for a change, green for something
        new, the muted grey for untracked, and a dot for a folder with changes
        somewhere under it."""
        tone = {"M": "warn", "R": "warn", "U": "down", "D": "down", "A": "good",
                "?": "txt_2", "*": "warn"}.get(code, "txt_2")
        ink = parse_colour(self._t.get(tone))
        if not ink.isValid():
            return
        if code == "*":
            painter.setPen(Qt.NoPen)
            painter.setBrush(ink)
            painter.drawEllipse(QRectF(right - 7, middle - 3, 6, 6))
            return
        box = QRectF(right - 13, middle - 7, 13, 14)
        fill = QColor(ink)
        fill.setAlphaF(0.18)
        painter.setPen(Qt.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(box, 3, 3)
        if self._badge_font is None:
            font = QFont(painter.font())
            font.setFamilies(["Cascadia Mono", "Consolas", "monospace"])
            font.setPixelSize(9)
            font.setBold(True)
            self._badge_font = font
        painter.setFont(self._badge_font)
        painter.setPen(ink)
        painter.drawText(box, int(Qt.AlignCenter), code)

    def _start_of_today(self) -> float:
        """Local midnight, worked out at most once a minute rather than per cell."""
        now = time.time()
        if now - self._today_checked > 60:
            self._today = when.window("today", now)[0] or 0.0
            self._today_checked = now
        return self._today

    def _glow(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        """A row changed today: a faint green wash across it and a lit edge.

        0.34. Green because it is the age chip's colour -- "fresh" already
        means green in this listing -- and not the accent, because the accent
        means "selected" and a row that is both has to read as both.
        """
        wash = parse_colour(self._t.get("age_row"))
        cell = QRectF(option.rect).adjusted(0, BAND_GAP, 0, -BAND_GAP)
        if wash.isValid():
            painter.fillRect(cell, wash)
        if index.column() != int(Column.NAME):
            return
        edge = parse_colour(self._t.get("good"))
        halo = parse_colour(self._t.get("age_glow"))
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        top = cell.top() + 4
        height = max(4.0, cell.height() - 8)
        if halo.isValid():
            painter.setBrush(halo)
            painter.drawRoundedRect(QRectF(cell.left() + BAND_INSET - 2, top - 2,
                                           7, height + 4), 3.5, 3.5)
        if edge.isValid():
            painter.setBrush(edge)
            painter.drawRoundedRect(QRectF(cell.left() + BAND_INSET, top, 3, height),
                                    1.5, 1.5)
        painter.restore()

    def _filling(self, painter: QPainter, option: QStyleOptionViewItem,
                 fraction: float) -> None:
        """The part of the row a transfer has written, as a wash across every
        column with a line under it -- one bar that happens to be cut into
        cells, so each cell paints only its own slice of it."""
        view = option.widget
        width = view.viewport().width() if view is not None and hasattr(view, "viewport") \
            else option.rect.right()
        start = BAND_INSET
        end = start + (width - 2 * BAND_INSET) * max(0.0, min(1.0, fraction))
        cell = option.rect
        left = max(cell.left(), start)
        right = min(cell.right() + 1, end)
        if right <= left:
            return
        wash = parse_colour(self._t.get("accent_wash"))
        line = parse_colour(self._t.get("accent"))
        top = cell.top() + BAND_GAP
        height = cell.height() - 2 * BAND_GAP
        if wash.isValid():
            painter.fillRect(QRectF(left, top, right - left, height), wash)
        if line.isValid():
            painter.fillRect(QRectF(left, top + height - 2, right - left, 2), line)

    def _badge(self, painter: QPainter, opt: QStyleOptionViewItem, style, entry) -> None:
        """The family-coloured tag where the icon would have been."""
        area = style.subElementRect(QStyle.SE_ItemViewItemDecoration, opt, opt.widget)
        height = min(BADGE_H, max(10, opt.rect.height() - 4))
        box = QRectF(area.left(), opt.rect.top() + (opt.rect.height() - height) / 2,
                     BADGE_W, height)
        kind = filetypes.family(entry.name, entry.is_dir)
        fill = parse_colour(self._t.get(f"kind_{kind}_fill"))
        ink = parse_colour(self._t.get(f"kind_{kind}_text"))
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        shape = QPainterPath()
        shape.addRoundedRect(box, 4, 4)
        if fill.isValid():
            painter.fillPath(shape, fill)
        if entry.is_dir:
            size = int(height - 4)
            picture = glyphs.icon("folder", colour=self._t.get("txt_1", ""),
                                  muted=self._t.get("txt_2", ""), size=size,
                                  ratio=float(painter.device().devicePixelRatioF()
                                              if painter.device() else 1.0))
            painter.drawPixmap(int(box.center().x() - size / 2),
                               int(box.center().y() - size / 2), picture.pixmap(size, size))
        else:
            if self._badge_font is None:
                font = QFont(opt.font)
                font.setFamilies(["Cascadia Mono", "Consolas", "monospace"])
                font.setPixelSize(9)
                font.setBold(True)
                self._badge_font = font
            painter.setFont(self._badge_font)
            painter.setPen(ink if ink.isValid() else opt.palette.color(QPalette.Text))
            painter.drawText(box, int(Qt.AlignCenter), filetypes.tag(entry.name))
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
        bar = size_bar_rect(self.size_bar, QRectF(option.rect),
                            float(share) if share else 0.0)
        if bar is None:
            return
        # A folder's bar is on a scale of its own -- the largest counted
        # folder -- so it is drawn in the accent to keep the two scales from
        # being read as one.
        folder = bool(index.data(ListingModel.IsDirRole))
        if self.size_bar == "behind":
            key = "size_fill_dir" if folder else "size_fill"
        else:
            key = "accent_dim" if folder else "bg_4"
        colour = parse_colour(self._t.get(key))
        if not colour.isValid():
            return
        radius = 1 if self.size_bar == "under" else max(2, self._radius - 2)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(colour)
        painter.drawRoundedRect(bar, radius, radius)
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
        step = index.data(ListingModel.AgeStepRole) if self.recency != "off" else None
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

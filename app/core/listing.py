"""The item model behind a pane, fed by the workers a batch at a time.

`QFileSystemModel` is not used, and this is what replaces it. The stock model
does its own filesystem access on its own terms, which is the network hang
being escaped; this one holds a plain Python list and is told what is in it.

The list is the reason 50,000 rows is unremarkable. Sorting and filtering are a
`list.sort` over data already in memory, which is well under 100ms at that
size. A proxy model would re-query, and re-querying is the expensive thing.

Rows arrive while the view is already showing them: `begin`, then `add` per
batch, then `finish`. Sorting happens at `finish` rather than per batch, since
sorting a list that is still growing is work thrown away.

The filter is held here rather than in a proxy for the same reason the list is:
filtering is a pass over data already in memory. Everything that arrived is
kept in `_all` and what passes the filter is in `_rows`, so clearing the filter
costs nothing and never re-lists the folder.
"""

from __future__ import annotations

import fnmatch
import time
from enum import IntEnum
from typing import Sequence

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt

from app.io.protocol import Entry

#: The `..` row. Not a real entry, so it is kept out of the list and handled at
#: index 0 -- putting a fake `Entry` in the list means every count, sort and
#: total has to remember to skip it, and one of them eventually will not.
PARENT_NAME = ".."


class Column(IntEnum):
    NAME = 0
    EXT = 1
    SIZE = 2
    AGE = 3
    MODIFIED = 4


HEADERS = ("Name", "Ext", "Size", "Age", "Modified")


def format_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    value = float(size)
    for unit in ("K", "M", "G", "T"):
        value /= 1024.0
        if value < 1024.0:
            return f"{value:,.1f} {unit}"
    return f"{value:,.1f} P"


#: The multipliers `format_size` writes, for reading one of its strings back.
_UNITS = {"B": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3,
          "T": 1024 ** 4, "P": 1024 ** 5}


def parse_size(text: str | None) -> int:
    """Bytes from something `format_size` produced, or 0.

    Approximate by construction -- `4.2 G` lost its exact value when it was
    formatted -- and that is fine for the one thing it is for, which is putting
    folders in order by size. Anything that needs the real number asks for it.
    """
    if not text:
        return 0
    body = text.rstrip("+").strip().replace(",", "")
    number, _, unit = body.partition(" ")
    try:
        return int(float(number) * _UNITS[unit.strip() or "B"])
    except (ValueError, KeyError):
        return 0


def count_of(value: int, noun: str) -> str:
    """`2 files`, `1 file`, and nothing at all for none of them.

    Small, and here rather than in the widget, because the status line and the
    model's own summary have to say the same thing the same way.
    """
    if not value:
        return ""
    return f"{value:,} {noun}" if value == 1 else f"{value:,} {noun}s"


def format_time(mtime: float) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime))
    except (ValueError, OSError, OverflowError):
        return ""


#: Seconds behind each step of the age chip, and what that step is called.
#: Past the last one a file gets no chip at all -- a fourth colour would say
#: what the text already says, and in most folders most rows are past a month.
#:
#: The names are the other half of `app.theme.tokens.AGE_ALPHA`. The rule about
#: time is here because it is a rule about files; the tint is there because it
#: is a rule about colour. Neither layer should have to import the other, so
#: what holds them together is a test.
AGE_STEPS: tuple[tuple[float, str], ...] = (
    (60 * 60 * 24, "fresh"),
    (60 * 60 * 24 * 7, "recent"),
    (60 * 60 * 24 * 30, "month"),
)


def format_age(mtime: float, now: float | None = None) -> str:
    """How long ago, in three characters.

    The Modified column already says exactly when. This says how long ago,
    which is the question actually being asked of a folder after a build or a
    sync -- and it is answerable at a glance in a way six digits are not.

    A file dated in the future is a share whose clock disagrees with this
    machine, which happens, so it is reported as `now` rather than as a
    negative age. The date column still shows what the share claims.
    """
    if not mtime:
        return ""
    seconds = max(0.0, (time.time() if now is None else now) - mtime)
    if seconds < 60:
        return "now"
    minutes = seconds / 60
    if minutes < 60:
        return f"{int(minutes)}m"
    hours = minutes / 60
    if hours < 24:
        return f"{int(hours)}h"
    days = hours / 24
    if days < 30:
        return f"{int(days)}d"
    if days < 365:
        return f"{int(days / 30)}M"
    return f"{int(days / 365)}y"


def age_step(mtime: float, now: float | None = None) -> str | None:
    """Which strength of the age chip this file gets, or None for no chip.

    Three steps and then nothing. Past a month the colour would be a fourth
    grey saying what the text already says, and in most folders most rows are
    past a month -- a chip on all of them is a chip on none of them.
    """
    if not mtime:
        return None
    seconds = max(0.0, (time.time() if now is None else now) - mtime)
    for limit, name in AGE_STEPS:
        if seconds < limit:
            return name
    return None


#: What separates one pattern from the next in a selection or filter string.
PATTERN_SEPARATOR = ";"


def matches(name: str, pattern: str) -> bool:
    """Whether a name answers to a pattern, the way somebody typing means it.

    A pattern containing `*` or `?` is a glob; anything else is a substring.
    Both are what a person typing into a file list means, and which one they
    meant is legible from what they typed rather than from a mode they had to
    set first.

    Several patterns can be given at once, separated by `;`, which is what
    makes `*.dwg;*.dxf` a single answer to "select the drawings".
    """
    name = name.lower()
    for part in pattern.lower().split(PATTERN_SEPARATOR):
        part = part.strip()
        if not part:
            continue
        found = (fnmatch.fnmatch(name, part) if "*" in part or "?" in part
                 else part in name)
        if found:
            return True
    return False


def split_name(entry: Entry) -> tuple[str, str]:
    """Name and extension as separate columns, the way a file list wants them.

    A folder has no extension however many dots are in it, and a leading dot is
    part of the name rather than an extension.
    """
    if entry.is_dir:
        return entry.name, ""
    stem, dot, suffix = entry.name.rpartition(".")
    if not dot or not stem:
        return entry.name, ""
    return stem, suffix


class ListingModel(QAbstractTableModel):

    IsDirRole = Qt.UserRole + 1
    EntryRole = Qt.UserRole + 2
    #: Which age strength this row gets, or None. Read by the delegate rather
    #: than recomputed there, so the rule lives in one place.
    AgeStepRole = Qt.UserRole + 3
    #: This file's size as a fraction of the largest file in the listing, or
    #: None for a folder and for a listing with nothing to scale against.
    SizeShareRole = Qt.UserRole + 4
    #: Whether this row is on the clipboard as a cut, which the delegate draws
    #: faded. A fact about the clipboard rather than about the listing, so it
    #: is asked of a provider rather than stored on the entry.
    CutRole = Qt.UserRole + 5

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._all: list[Entry] = []      # everything that arrived
        self._rows: list[Entry] = []     # what the filter lets through
        self._icons = None               # set by the pane; None draws no icons
        self._overlays = None            # the same, for the badges on them
        self._file_icons = None          # and for the few files carrying one
        self._sizes = None               # recursive folder sizes, once asked for
        self._cut = None                 # the clipboard, for the faded rows
        self._folder = ""                # what the per-path requests ask about
        self._has_parent = False
        self._sort_column = Column.NAME
        self._sort_order = Qt.AscendingOrder
        self._filter = ""
        #: The largest file in `_rows`, for the size bars. Computed on demand
        #: and thrown away whenever the list changes -- one pass over a list
        #: already in memory is cheap, and doing it per batch during a
        #: streaming listing would not be.
        self._scale: int | None = None

    def set_icons(self, provider) -> None:
        """Where the decoration comes from, or None for a model without one.

        Injected rather than imported so the model stays something that can be
        built and checked without a worker behind it, which is what its tests
        do. `provider.icon(entry)` is called during a paint and must answer
        from what it already has.
        """
        self._icons = provider

    def set_overlays(self, provider) -> None:
        """Where the badges come from, or None for a listing without them.

        Separate from `set_icons` because they are separate requests with
        separate costs: an icon is a fact about a type and is free after the
        first row of its kind, an overlay is a fact about a file and is a
        lookup per row on screen.
        """
        self._overlays = provider

    def set_file_icons(self, provider) -> None:
        """Where an executable's own icon comes from, or None to draw every
        row as its kind.

        A third provider rather than something the icon cache does, because
        the cost is a third thing again: this one opens the file. Keeping it
        separate is what lets it be turned off on its own, and what stops a
        change to the by-kind cache quietly making a request per row.
        `provider.icon(folder, entry)` is called during a paint and answers
        from what it already has.
        """
        self._file_icons = provider

    def set_cut(self, provider) -> None:
        """Where "this row was cut" comes from, or None to fade nothing.

        A fourth provider, and the cheapest of them: `provider.cut_names(folder)`
        answers with a set it already has, so a row costs a lookup. It is asked
        per row for the same reason the others are -- the model does not know
        when the clipboard changed, and the widget above it does.
        """
        self._cut = provider

    def set_sizes(self, provider) -> None:
        """Where a folder's recursive size comes from, or None for a model
        that only ever says `<DIR>`.

        Injected like the other two, and for the same reason: the model stays
        something that can be built and checked without a worker behind it.
        `provider.known(folder, name)` is called during a paint and answers
        from what it already has or not at all.
        """
        self._sizes = provider

    def set_folder(self, path: str) -> None:
        """Which folder these rows are in.

        The model does not otherwise know or care -- rows are names. The two
        per-path requests are the exception: an overlay and a file's own icon
        are both asked about a file, so the name has to be put back together
        with the folder it is in, and the folder is told to the model rather
        than worked out from anything here.
        """
        self._folder = path or ""

    @property
    def folder(self) -> str:
        return self._folder

    @property
    def size_scale(self) -> int:
        """The largest file in the listing, or 0 if there is nothing to scale.

        Files only. A folder measured with Space can be orders of magnitude
        larger than anything in the folder, and putting it on the same scale
        would draw every real file as no bar at all -- which is why a measured
        folder keeps its total and gets no bar.
        """
        if self._scale is None:
            self._scale = max(
                (e.size for e in self._rows if not e.is_dir), default=0)
        return self._scale

    @property
    def sort_column(self) -> "Column":
        return self._sort_column

    @property
    def sort_order(self):
        return self._sort_order

    @property
    def filter_text(self) -> str:
        return self._filter

    # ------------------------------------------------------------ filling it

    def begin(self, *, has_parent: bool) -> None:
        """A new folder. The filter does not survive it.

        Carrying a filter into the next folder means arriving somewhere that
        looks empty for a reason that is one line of chrome away from the eye.
        """
        self.beginResetModel()
        self._all = []
        self._rows = []
        self._filter = ""
        self._has_parent = has_parent
        self._scale = None
        self.endResetModel()

    def add(self, entries: Sequence[Entry]) -> None:
        if not entries:
            return
        self._all.extend(entries)
        visible = [e for e in entries if self._passes(e)] if self._filter else list(entries)
        if not visible:
            return
        start = len(self._rows) + self._offset
        self.beginInsertRows(QModelIndex(), start, start + len(visible) - 1)
        self._rows.extend(visible)
        self._scale = None
        self.endInsertRows()

    def finish(self) -> None:
        """Sort what arrived. The only point at which the order is settled."""
        self.beginResetModel()
        self._sort_rows()
        self.endResetModel()

    def set_filter(self, text: str) -> None:
        """Show only the rows whose name matches.

        `matches` decides what a pattern means: a glob if it has `*` or `?` in
        it, a substring otherwise, and several of them separated by `;`.
        """
        text = (text or "").strip()
        if text == self._filter:
            return
        self.beginResetModel()
        self._filter = text
        self._apply_filter()
        self.endResetModel()

    # -------------------------------------------------------------- reading it

    @property
    def _offset(self) -> int:
        return 1 if self._has_parent else 0

    def entry(self, row: int) -> Entry | None:
        index = row - self._offset
        if 0 <= index < len(self._rows):
            return self._rows[index]
        return None

    def row_of(self, name: str) -> int:
        """The row a name is on, or -1.

        Case-insensitive, because Windows is: after renaming `Notes.txt` to
        `notes.txt` the row to put the cursor back on is the one that is
        obviously the same file.
        """
        wanted = name.lower()
        for index, entry in enumerate(self._rows):
            if entry.name.lower() == wanted:
                return index + self._offset
        return -1

    def entry_named(self, name: str) -> Entry | None:
        """The entry a name belongs to, or None.

        Case-insensitive like `row_of`, and for the same reason: Windows is.
        """
        wanted = name.lower()
        for entry in self._rows:
            if entry.name.lower() == wanted:
                return entry
        return None

    def rows_matching(self, pattern: str, *, files_only: bool = False) -> list[int]:
        """The rows a selection command should act on.

        Rows rather than names, because a selection is rows -- and view rows,
        with the parent row's offset already in them, because that is what the
        widget hands to Qt. `..` is never among them: it is not an entry, and
        a selection that included it would offer `..` to the next operation.
        """
        found = []
        for index, entry in enumerate(self._rows):
            if files_only and entry.is_dir:
                continue
            if matches(entry.name, pattern):
                found.append(index + self._offset)
        return found

    def rows_with_extension(self, suffix: str) -> list[int]:
        """Every file sharing an extension, for "the rest of these".

        Folders are never included however many dots are in their names, which
        is the same rule the Ext column follows.
        """
        wanted = (suffix or "").lower()
        return [index + self._offset for index, entry in enumerate(self._rows)
                if not entry.is_dir and split_name(entry)[1].lower() == wanted]

    def all_rows(self) -> list[int]:
        """Every row a selection may hold, the parent row excluded."""
        return [index + self._offset for index in range(len(self._rows))]

    def names(self) -> list[str]:
        """Every name in the folder, filtered out or not.

        What a new name has to avoid. The filter hides rows from view, not from
        the disk, so checking a name against `_rows` would offer one that is
        already taken by something the filter happens to be hiding.
        """
        return [entry.name for entry in self._all]

    def entries(self) -> list:
        """What the filter lets through, as the rows themselves.

        Handed out rather than copied per row because the one caller that
        wants them -- comparing the two panes -- wants every row at once and
        reads nothing but the fields `os.scandir` already delivered. A list
        rather than the internal one, so nobody sorts it in place.
        """
        return list(self._rows)

    def rows_named(self, names) -> list[int]:
        """The rows for a set of names, in the order the listing holds them.

        Case-insensitive for `row_of`'s reason: Windows is, and a name that
        came back from a comparison may be spelled the way the *other* pane
        spells it.
        """
        wanted = {str(name).lower() for name in names}
        return [index + self._offset for index, entry in enumerate(self._rows)
                if entry.name.lower() in wanted]

    def folder_names(self) -> list[str]:
        """Every folder on screen. What the filter lets through, not what
        arrived -- a command aimed at the listing acts on the listing."""
        return [entry.name for entry in self._rows if entry.is_dir]

    def is_parent_row(self, row: int) -> bool:
        return self._has_parent and row == 0

    def find(self, text: str, *, start: int = 0, forward: bool = True) -> int:
        """The row a quick search lands on, or -1.

        Two passes, in this order: names that *start* with what was typed,
        then names that merely contain it. Prefix first because that is what
        typing three letters into a file list means -- and separated into two
        passes rather than ranked in one, so a folder called `drawings` is
        never passed over in favour of one called `old-drawings` that happens
        to sit above it.

        The search wraps, and `start` is where it begins rather than where it
        stops: typing continues from where the cursor already is, which is what
        makes a second keystroke narrow the answer instead of restarting it.
        """
        wanted = (text or "").lower()
        if not wanted or not self._rows:
            return -1
        step = 1 if forward else -1
        count = len(self._rows)
        first = max(0, min(count - 1, start - self._offset))
        order = [(first + step * n) % count for n in range(count)]
        for match in (str.startswith, str.__contains__):
            for index in order:
                if match(self._rows[index].name.lower(), wanted):
                    return index + self._offset
        return -1

    def summary(self) -> str:
        folders = sum(1 for e in self._all if e.is_dir)
        files = len(self._all) - folders
        total = sum(e.size for e in self._all if not e.is_dir)
        counted = ", ".join(part for part in
                            (count_of(folders, "folder"), count_of(files, "file")) if part)
        text = f"{counted or 'empty'}, {format_size(total)}" if counted else "empty folder"
        if self._filter:
            text += f"  ·  {len(self._rows):,} shown"
        return text

    def selection(self, rows) -> tuple[int, int, int]:
        """`(folders, files, bytes)` for a set of row numbers.

        The parent row and any row that is no longer there are ignored rather
        than counted as nothing, so a stale selection cannot report a total
        that quietly excludes rows the user can see highlighted.
        """
        folders = files = total = 0
        for row in rows:
            entry = self.entry(row)
            if entry is None:
                continue
            if entry.is_dir:
                folders += 1
            else:
                files += 1
                total += entry.size
        return folders, files, total

    # ------------------------------------------------------- the model itself

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows) + self._offset

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(HEADERS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation != Qt.Horizontal:
            return None
        if role == Qt.TextAlignmentRole:
            # A header that does not sit over its own column reads as a
            # different column, which on a size column is actively misleading.
            align = (Qt.AlignRight if section in (Column.SIZE, Column.AGE)
                     else Qt.AlignLeft)
            return int(align | Qt.AlignVCenter)
        if role != Qt.DisplayRole:
            return None
        # Upper case here rather than in HEADERS, which is the name of the
        # column and is what a menu or a settings file would want to say. This
        # is only how the strip above the rows is set.
        return HEADERS[section].upper()

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row, column = index.row(), index.column()

        if self.is_parent_row(row):
            if role == Qt.DisplayRole and column == Column.NAME:
                return PARENT_NAME
            if role == Qt.DecorationRole and column == Column.NAME:
                return self._icons.folder_icon() if self._icons is not None else None
            if role == self.IsDirRole:
                return True
            return None

        entry = self.entry(row)
        if entry is None:
            return None

        if role == self.IsDirRole:
            return entry.is_dir
        if role == self.EntryRole:
            return entry
        if role == self.CutRole:
            if self._cut is None or not self._folder:
                return False
            return entry.name in self._cut.cut_names(self._folder)
        if role == Qt.DecorationRole:
            # Only the name column: an icon in every column is four pictures
            # of the same file on one row.
            if column != Column.NAME or self._icons is None:
                return None
            if self._overlays is not None and self._folder:
                # The badged picture is the file's own icon with the overlay
                # already on it -- the shell composites them, because where a
                # badge sits on an icon is its business. So it replaces the
                # icon rather than being drawn over it.
                badged = self._overlays.icon(self._folder, entry.name)
                if badged is not None:
                    return badged
            if self._file_icons is not None and self._folder:
                # Under the badge and over the kind. A badged picture already
                # has this file's own icon underneath it -- the shell drew it
                # from the real path -- so asking for both would be the same
                # read twice for a picture that is already correct.
                own = self._file_icons.icon(self._folder, entry)
                if own is not None:
                    return own
            return self._icons.icon(entry)
        if role == self.AgeStepRole:
            return age_step(entry.mtime) if column == Column.AGE else None
        if role == self.SizeShareRole:
            if column != Column.SIZE or entry.is_dir or not entry.size:
                return None
            largest = self.size_scale
            return (entry.size / largest) if largest else None
        if role == Qt.TextAlignmentRole and column in (Column.SIZE, Column.AGE):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.ToolTipRole and column == Column.SIZE and not entry.is_dir:
            return f"{entry.size:,} bytes"
        if role != Qt.DisplayRole:
            return None

        if column == Column.NAME:
            return split_name(entry)[0]
        if column == Column.EXT:
            return split_name(entry)[1]
        if column == Column.SIZE:
            # A folder's size is a separate, lazy request. Showing a blank
            # would read as zero bytes, which is worse than saying nothing.
            if not entry.is_dir:
                return format_size(entry.size)
            if self._sizes is not None and self._folder:
                counted = self._sizes.known(self._folder, entry.name)
                if counted is not None:
                    return counted
            return "<DIR>"
        if column == Column.AGE:
            return format_age(entry.mtime)
        if column == Column.MODIFIED:
            return format_time(entry.mtime)
        return None

    def sort(self, column: int, order=Qt.AscendingOrder) -> None:
        self._sort_column = Column(column)
        self._sort_order = order
        self.finish()

    def _sort_rows(self) -> None:
        """Folders first, always, then the column. A sort that mixes them makes
        a folder list unusable and no file manager does it.
        """
        column = self._sort_column
        reverse = self._sort_order == Qt.DescendingOrder

        def key(entry: Entry):
            if column == Column.SIZE:
                # A folder that has been counted sorts by what it holds. One
                # that has not sorts as nothing, which keeps the uncounted
                # ones together instead of scattering them through the
                # answer at whatever their directory entry claims.
                if entry.is_dir:
                    return self._counted(entry.name)
                return entry.size
            if column == Column.MODIFIED:
                return entry.mtime
            if column == Column.AGE:
                # Age ascending is the newest first, which is mtime
                # descending. Sorting age the same way as the date would put
                # the oldest thing in the folder at the top of a column
                # labelled "how long ago", which is backwards.
                return -entry.mtime
            if column == Column.EXT:
                return split_name(entry)[1].lower()
            return entry.name.lower()

        self._all.sort(key=key, reverse=reverse)
        self._all.sort(key=lambda e: not e.is_dir)
        self._apply_filter()

    def _counted(self, name: str) -> int:
        """The bytes behind a folder's size cell, for sorting. 0 if unknown.

        Parsed back out of the text rather than held twice. The alternative is
        a second dictionary that has to be kept in step with the first, and a
        sort key is not worth that.
        """
        if self._sizes is None or not self._folder:
            return 0
        return parse_size(self._sizes.known(self._folder, name))

    # ---------------------------------------------------------------- filter

    def _passes(self, entry: Entry) -> bool:
        return matches(entry.name, self._filter)

    def _apply_filter(self) -> None:
        """Recompute the visible list. Callers own the reset around it."""
        # A copy, not the same list: `add` appends to both, and aliasing them
        # would append every batch twice the moment no filter is set.
        self._rows = list(self._all) if not self._filter else [
            e for e in self._all if self._passes(e)
        ]
        self._scale = None

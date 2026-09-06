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
    MODIFIED = 3


HEADERS = ("Name", "Ext", "Size", "Modified")


def format_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    value = float(size)
    for unit in ("K", "M", "G", "T"):
        value /= 1024.0
        if value < 1024.0:
            return f"{value:,.1f} {unit}"
    return f"{value:,.1f} P"


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

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._all: list[Entry] = []      # everything that arrived
        self._rows: list[Entry] = []     # what the filter lets through
        self._icons = None               # set by the pane; None draws no icons
        self._has_parent = False
        self._sort_column = Column.NAME
        self._sort_order = Qt.AscendingOrder
        self._filter = ""

    def set_icons(self, provider) -> None:
        """Where the decoration comes from, or None for a model without one.

        Injected rather than imported so the model stays something that can be
        built and checked without a worker behind it, which is what its tests
        do. `provider.icon(entry)` is called during a paint and must answer
        from what it already has.
        """
        self._icons = provider

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
        self.endInsertRows()

    def finish(self) -> None:
        """Sort what arrived. The only point at which the order is settled."""
        self.beginResetModel()
        self._sort_rows()
        self.endResetModel()

    def set_filter(self, text: str) -> None:
        """Show only the rows whose name matches.

        A pattern containing `*` or `?` is matched as a glob, anything else as
        a substring. Both are what someone typing into a file list means, and
        which one they meant is legible from what they typed.
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

    def is_parent_row(self, row: int) -> bool:
        return self._has_parent and row == 0

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
            align = Qt.AlignRight if section == Column.SIZE else Qt.AlignLeft
            return int(align | Qt.AlignVCenter)
        if role != Qt.DisplayRole:
            return None
        return HEADERS[section]

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
        if role == Qt.DecorationRole:
            # Only the name column: an icon in every column is four pictures
            # of the same file on one row.
            if column != Column.NAME or self._icons is None:
                return None
            return self._icons.icon(entry)
        if role == Qt.TextAlignmentRole and column == Column.SIZE:
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
            return "<DIR>" if entry.is_dir else format_size(entry.size)
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
                return entry.size
            if column == Column.MODIFIED:
                return entry.mtime
            if column == Column.EXT:
                return split_name(entry)[1].lower()
            return entry.name.lower()

        self._all.sort(key=key, reverse=reverse)
        self._all.sort(key=lambda e: not e.is_dir)
        self._apply_filter()

    # ---------------------------------------------------------------- filter

    def _passes(self, entry: Entry) -> bool:
        pattern = self._filter.lower()
        name = entry.name.lower()
        if "*" in pattern or "?" in pattern:
            return fnmatch.fnmatch(name, pattern)
        return pattern in name

    def _apply_filter(self) -> None:
        """Recompute the visible list. Callers own the reset around it."""
        # A copy, not the same list: `add` appends to both, and aliasing them
        # would append every batch twice the moment no filter is set.
        self._rows = list(self._all) if not self._filter else [
            e for e in self._all if self._passes(e)
        ]

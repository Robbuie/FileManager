"""The last good listing of the folders a pane has been in on a share.

What lets a pane that goes back to a folder on a share that has stopped
answering show what was there, marked as out of date, rather than an empty
listing and an error. Somebody looking for a file name, or checking whether
last night's export arrived, can often answer that from a listing made ten
minutes ago -- and a blank pane answers nothing.

Held in memory and nowhere else. A listing written to disk would be a copy of
the server's folder structure on this machine, which is a decision about data
rather than about convenience, and it would also outlive the session it was
true in.

Only network volumes are kept. A local disk that stops answering has bigger
problems than its listing, and its folders are the ones most often opened, so
remembering them would spend the budget on the folders that never need it.

Bounded by rows rather than by folders, because a folder is anywhere from one
row to fifty thousand: the budget is `ROWS` rows across at most `FOLDERS`
folders, and the least recently listed go first.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import Sequence

ROWS = 150_000
FOLDERS = 40


class Remembered:
    def __init__(self, rows: int = ROWS, folders: int = FOLDERS) -> None:
        self._limit_rows = rows
        self._limit_folders = folders
        self._folders: "OrderedDict[str, tuple[float, list]]" = OrderedDict()
        self._rows = 0

    def keep(self, path: str, entries: Sequence, when: float | None = None) -> None:
        """The listing of `path` just succeeded. A folder too big for the whole
        budget is not kept at all rather than evicting everything else."""
        key = path.lower()
        self.forget(path)
        entries = list(entries)
        if len(entries) > self._limit_rows:
            return
        self._folders[key] = (time.time() if when is None else when, entries)
        self._rows += len(entries)
        while self._rows > self._limit_rows or len(self._folders) > self._limit_folders:
            _key, (_when, dropped) = self._folders.popitem(last=False)
            self._rows -= len(dropped)

    def recall(self, path: str) -> tuple[float, list] | None:
        """(when it was listed, the rows), or None if it was never kept."""
        found = self._folders.get(path.lower())
        if found is not None:
            self._folders.move_to_end(path.lower())
        return found

    def forget(self, path: str) -> None:
        found = self._folders.pop(path.lower(), None)
        if found is not None:
            self._rows -= len(found[1])

    @property
    def rows(self) -> int:
        return self._rows

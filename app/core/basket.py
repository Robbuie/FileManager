"""The basket: files gathered from several folders, to be copied or moved as one
(0.38).

Marks belong to a listing and are lost the moment the pane goes somewhere
else, so collecting "these two exports from Backups, that PDF from Drawings
and the I/O list from the job folder" used to mean three copies, three
prompts and three chances to send one of them to the wrong place. Alt+Ins
puts the marked files (or the one under the cursor) into the basket and
leaves the listing alone; the pane can then go anywhere, and one Copy here or
Move here takes the lot, through the same prompt F5 uses.

Paths only, in the order they were added, each once. Nothing here checks that
a path still exists -- that is a filesystem question and the copy engine
answers it per file, as a failure the queue can retry -- and nothing is kept
across a restart: a basket is a gesture in progress, not a list to maintain.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from app.io import paths


class Basket(QObject):
    changed = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._paths: list[str] = []

    @property
    def paths(self) -> list[str]:
        return list(self._paths)

    def __len__(self) -> int:
        return len(self._paths)

    def names(self) -> list[str]:
        return [paths.leaf(path) for path in self._paths]

    def add(self, items) -> int:
        """Add paths not already here. Returns how many were new.

        Compared case-insensitively, for Windows' reason, so the same file
        reached through a differently spelled path is still one entry.
        """
        known = {path.lower() for path in self._paths}
        added = 0
        for path in items:
            if path and path.lower() not in known:
                self._paths.append(path)
                known.add(path.lower())
                added += 1
        if added:
            self.changed.emit()
        return added

    def remove(self, path: str) -> None:
        before = len(self._paths)
        self._paths = [known for known in self._paths if known.lower() != path.lower()]
        if len(self._paths) != before:
            self.changed.emit()

    def clear(self) -> None:
        if self._paths:
            self._paths = []
            self.changed.emit()

    def folders(self) -> int:
        """How many folders the contents came from, for the tray's line."""
        return len({paths.parent(path).lower() for path in self._paths
                    if paths.parent(path)})

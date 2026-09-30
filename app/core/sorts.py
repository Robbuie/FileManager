"""Which folders were sorted by something other than the tab's usual order (0.40).

Double Commander keeps a sort per folder, and the folder-per-day habit is the
case for it here: the day folders are read newest first, the job folders by
name, and re-clicking Modified every morning is the chore this removes.

The rule is small on purpose, so it can be predicted without reading this:

  * Clicking a column heading remembers that order for that folder.
  * Going into a folder that has one puts it back.
  * Going into a folder that has none uses the order last clicked in this tab,
    which is how the tab behaved before 0.40.

Kept in the settings by folder path, compared without case the way Windows
compares names, and capped: the oldest entry goes once there are `LIMIT`, so
a year of day folders does not grow the settings file without bound.

No Qt and no filesystem. An order is stored as 0 ascending, 1 descending.
"""

from __future__ import annotations

from typing import Any

#: How many folders' orders are kept before the oldest is forgotten.
LIMIT = 500


def key(path: str) -> str:
    return path.rstrip("\\/").lower() or path.lower()


class SortMemory:
    """A view over `config["listing.sorts"]`."""

    def __init__(self, config) -> None:
        self._config = config

    @property
    def enabled(self) -> bool:
        return bool(self._config.get("listing.remember_sort"))

    def _table(self) -> dict[str, Any]:
        table = self._config.get("listing.sorts")
        return dict(table) if isinstance(table, dict) else {}

    def get(self, path: str) -> tuple[int, int] | None:
        if not self.enabled:
            return None
        value = self._table().get(key(path))
        if (isinstance(value, (list, tuple)) and len(value) == 2
                and all(isinstance(part, int) for part in value)):
            return int(value[0]), 1 if value[1] else 0
        return None

    def put(self, path: str, column: int, order: int) -> None:
        if not self.enabled:
            return
        table = self._table()
        name = key(path)
        # Re-inserted rather than updated, so the dict's order stays the
        # order folders were last sorted in and the cap drops the stalest.
        table.pop(name, None)
        table[name] = [int(column), 1 if order else 0]
        while len(table) > LIMIT:
            table.pop(next(iter(table)))
        self._config.set("listing.sorts", table)

    def forget(self, path: str) -> bool:
        table = self._table()
        if table.pop(key(path), None) is None:
            return False
        self._config.set("listing.sorts", table)
        return True

    def has(self, path: str) -> bool:
        return self.enabled and key(path) in self._table()

"""Workspaces: both panes' tabs, saved under a name and put back in one step
(0.38).

A commissioning job, the office, this repository: each is a set of folders
open in a particular arrangement, and rebuilding that arrangement by hand --
six tabs, two sides, the right one in front on each -- is the chore this
removes. A workspace is the same record the window already writes on close
for each side (`left.tabs`, `left.tab`), kept under a name.

Nothing about the folders is checked when one is saved or opened. Opening one
is the same as opening those tabs by hand: each lists when it is looked at,
and a share that has gone away says so in its own tab.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, Signal

#: The most a list keeps. A workspace menu of forty entries is a second
#: favourites list, which is not what this is for.
LIMIT = 20


def capture(name: str, left, right) -> dict[str, Any]:
    """The record for the two panes as they are now."""
    return {"name": name,
            "left": {"tabs": left.session(), "tab": int(left.index)},
            "right": {"tabs": right.session(), "tab": int(right.index)}}


def side(entry: dict, which: str) -> tuple[list[dict], int]:
    """`(tabs, index)` for one side of a record, cleaned of anything malformed.

    A settings file is not a schema: a hand-edited or half-written entry costs
    the tabs it got wrong, not the workspace.
    """
    part = entry.get(which) if isinstance(entry, dict) else None
    if not isinstance(part, dict):
        return [], 0
    tabs = []
    for item in part.get("tabs") or []:
        if isinstance(item, str):
            item = {"path": item}
        if isinstance(item, dict) and isinstance(item.get("path"), str) and item["path"]:
            tabs.append({"path": item["path"], "locked": bool(item.get("locked"))})
    try:
        index = int(part.get("tab") or 0)
    except (TypeError, ValueError):
        index = 0
    return tabs, max(0, min(index, len(tabs) - 1)) if tabs else 0


class Workspaces(QObject):
    changed = Signal()

    def __init__(self, config, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._config = config

    @property
    def entries(self) -> list[dict]:
        stored = self._config.get("workspaces")
        return [entry for entry in stored if isinstance(entry, dict)
                and isinstance(entry.get("name"), str) and entry["name"]] \
            if isinstance(stored, list) else []

    def names(self) -> list[str]:
        return [entry["name"] for entry in self.entries]

    def named(self, name: str) -> dict | None:
        for entry in self.entries:
            if entry["name"].lower() == name.lower():
                return entry
        return None

    def save(self, name: str, left, right) -> None:
        """Keep the panes under `name`, replacing one of the same name."""
        name = name.strip()
        if not name:
            return
        record = capture(name, left, right)
        entries = [entry for entry in self.entries
                   if entry["name"].lower() != name.lower()]
        entries.append(record)
        self._config.set("workspaces", entries[-LIMIT:])
        self.changed.emit()

    def remove(self, name: str) -> None:
        entries = [entry for entry in self.entries
                   if entry["name"].lower() != name.lower()]
        self._config.set("workspaces", entries)
        self.changed.emit()

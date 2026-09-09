"""Saved locations, and the one list both panes read from.

A favourite is a place rather than a side of the window, so there is one list
and both panes navigate to entries in it. It lives in the settings file next to
everything else the window remembers.

The one thing here that is not obvious is that a change writes the settings
file straight away, rather than waiting for the window to close like the pane
paths and the window size do. A favourite is something the user deliberately
made -- typed a name for, chose a folder for -- and losing one to a crash is a
different kind of loss from losing a window size. The write is a few hundred
bytes of JSON into `%APPDATA%`, which is local by definition and is the same
call `closeEvent` already makes; it is not the kind of filesystem access the
rest of this application keeps off the UI thread, and it stays here rather than
spreading to other settings.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal

from app.io import paths

#: How many favourites the list will hold. A menu longer than this stops being
#: a way of getting somewhere quickly, which is the only thing it is for.
MAX_FAVORITES = 60


#: What the rail calls the favourites that are in no group. Not stored: an
#: empty group is the absence of one, and writing a name for it would make
#: "Places" a group somebody could then rename or empty.
UNGROUPED = "Saved"


@dataclass(frozen=True, slots=True)
class Favorite:
    """A name, where it goes, and which heading it sits under.

    The group is a plain string and defaults to empty, which is what every
    favourite saved before 0.12 has. Empty means the entry belongs to no group
    rather than to one called "": the rail draws those under `UNGROUPED` and
    the settings file does not carry the key at all, so a list made by an
    earlier version reads back byte for byte the same.
    """

    name: str
    path: str
    group: str = ""


class Favorites(QObject):
    """The saved locations, in the order the user put them in."""

    changed = Signal()

    def __init__(self, config, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._config = config
        self._entries: list[Favorite] = self._restore()

    # ----------------------------------------------------------------- state

    @property
    def entries(self) -> list[Favorite]:
        """A copy. The list is edited through this object or not at all."""
        return list(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def index_of(self, path: str) -> int:
        """Where a path already sits in the list, or -1.

        Compared on the normalized form and case-insensitively, because
        Windows is: `S:\\Jobs` and `s:\\jobs\\` are one favourite, and offering
        to add the second while the first is there is how a list ends up with
        six spellings of one folder.
        """
        wanted = paths.normalize(path).lower()
        for position, entry in enumerate(self._entries):
            if paths.normalize(entry.path).lower() == wanted:
                return position
        return -1

    def contains(self, path: str) -> bool:
        return self.index_of(path) >= 0

    def suggested_name(self, path: str) -> str:
        """What to fill the name field in with. The folder's own name."""
        return paths.leaf(paths.normalize(path))

    # --------------------------------------------------------------- editing

    def groups(self) -> list[str]:
        """The group names in use, in the order the list first mentions them.

        Order of first appearance rather than alphabetical, for the reason
        `move` gives: the order is the user's. A group is created by putting
        something in it and disappears when the last entry leaves, so there is
        no separate list of groups to fall out of step with this one.
        """
        out: list[str] = []
        for entry in self._entries:
            if entry.group and entry.group not in out:
                out.append(entry.group)
        return out

    def add(self, name: str, path: str, group: str = "") -> bool:
        """Put a folder in the list, or rename the entry already on it.

        Adding a path that is already a favourite is a rename rather than a
        second entry: a list with the same folder twice under two names is a
        list nobody trusts to be the whole list. Its group is kept in that
        case -- renaming something is not a reason to move it.
        """
        name, path = name.strip(), paths.normalize(path)
        if not name or not path:
            return False
        existing = self.index_of(path)
        if existing >= 0:
            keep = self._entries[existing].group
            self._entries[existing] = Favorite(name, path, group.strip() or keep)
            self._commit()
            return True
        if len(self._entries) >= MAX_FAVORITES:
            return False
        self._entries.append(Favorite(name, path, group.strip()))
        self._commit()
        return True

    def set_group(self, index: int, group: str) -> None:
        """Move one entry under a heading, or out from under all of them.

        An empty name is what takes it out, which is why this is one call
        rather than a move and a remove: a group with nothing in it is not a
        state this list can be in.
        """
        if 0 <= index < len(self._entries):
            entry = self._entries[index]
            self._entries[index] = Favorite(entry.name, entry.path, group.strip())
            self._commit()

    def rename_group(self, old: str, new: str) -> None:
        """Rename a heading, taking everything under it with it."""
        old, new = old.strip(), new.strip()
        if not old or old == new:
            return
        changed = False
        for position, entry in enumerate(self._entries):
            if entry.group == old:
                self._entries[position] = Favorite(entry.name, entry.path, new)
                changed = True
        if changed:
            self._commit()

    def remove(self, index: int) -> None:
        if 0 <= index < len(self._entries):
            del self._entries[index]
            self._commit()

    def remove_path(self, path: str) -> None:
        self.remove(self.index_of(path))

    def rename(self, index: int, name: str) -> None:
        name = name.strip()
        if name and 0 <= index < len(self._entries):
            self._entries[index] = Favorite(name, self._entries[index].path)
            self._commit()

    def move(self, index: int, step: int) -> int:
        """Shift one entry up or down. Returns where it ended up.

        The order is the user's, so it is theirs to change: a menu whose
        entries sort themselves is a menu whose entries move about.
        """
        if not 0 <= index < len(self._entries):
            return index
        target = max(0, min(len(self._entries) - 1, index + step))
        if target != index:
            self._entries.insert(target, self._entries.pop(index))
        return target

    def commit_order(self) -> None:
        """Write out an order that `move` has been building up.

        Separate from `move` so that dragging an entry up five places is one
        write rather than five.
        """
        self._commit()

    # -------------------------------------------------------------- internals

    def _restore(self) -> list[Favorite]:
        """Read the stored list, dropping anything malformed.

        Dropped rather than repaired, for the reason the tab session gives:
        a settings file is not a schema, and a pane that fails to build
        because somebody hand-edited their config is worse than a favourite
        that does not come back.
        """
        stored = self._config.get("favorites")
        entries: list[Favorite] = []
        if not isinstance(stored, list):
            return entries
        for item in stored[:MAX_FAVORITES]:
            if not isinstance(item, dict):
                continue
            name, path = item.get("name"), item.get("path")
            if not isinstance(path, str) or not path:
                continue
            if not isinstance(name, str) or not name.strip():
                name = paths.leaf(paths.normalize(path))
            group = item.get("group")
            if not isinstance(group, str):
                group = ""
            entries.append(Favorite(name.strip(), paths.normalize(path),
                                    group.strip()))
        return entries

    def _commit(self) -> None:
        # The group key is written only when there is one. A list nobody has
        # grouped then reads back exactly as an earlier version wrote it,
        # which is what makes this addition invisible to somebody who never
        # uses it -- and what lets 0.11 read a 0.12 settings file.
        self._config.set("favorites", [
            ({"name": e.name, "path": e.path, "group": e.group} if e.group
             else {"name": e.name, "path": e.path})
            for e in self._entries
        ])
        self._config.save()
        self.changed.emit()

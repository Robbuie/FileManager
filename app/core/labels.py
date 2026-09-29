"""Colour labels and notes on files and folders (0.38).

A red dot on the job that is overdue, a note on a drawing set saying it went
to the customer on the 12th. Both live here, in the settings file beside
everything else, keyed on the full path -- not in the files themselves.

The mockup had them in an NTFS alternate data stream on each file, so a label
would travel with it. That was dropped on inspection, for two reasons worth
recording. Reading a stream is opening the file, and a listing that asked
every row for its label would be the 50,000 round trips the whole architecture
exists to avoid. And writing a stream changes the file's modified time on the
file servers this is used against, which would make a labelled drawing look
newer to the sync, to the Age column and to the recency glow. A label is a
fact about how *this person* sees a file, so it is kept where this person's
settings are.

The consequence, said plainly in the dialog: a label follows a path. Renaming
or moving the file outside this application leaves the label behind on the old
name. Renames and moves made here carry it (see `moved`).

Nothing here touches the filesystem, and the lookup is one dictionary read per
row painted.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, Signal

from app.io import paths

#: The six colours, in the order the menu offers them. The names are what the
#: menu says; the numbers are what is stored, and index the tokens `label_1`
#: to `label_6`, which are semantic and follow neither theme nor accent.
COLOURS: tuple[str, ...] = ("Red", "Orange", "Yellow", "Green", "Blue", "Purple")


def key(folder: str, name: str) -> str:
    return paths.join(folder, name).lower() if folder else name.lower()


class Labels(QObject):
    changed = Signal()

    def __init__(self, config, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._config = config
        stored = config.get("labels")
        self._store: dict[str, dict[str, Any]] = {
            k: v for k, v in (stored.items() if isinstance(stored, dict) else [])
            if isinstance(k, str) and isinstance(v, dict)}

    def label(self, folder: str, name: str) -> tuple[int, str] | None:
        """`(colour 0-6, note)` for a row, or None when it has neither."""
        found = self._store.get(key(folder, name))
        if not found:
            return None
        return int(found.get("c") or 0), str(found.get("n") or "")

    def set_colour(self, folder: str, names, colour: int) -> None:
        for name in names:
            self._update(key(folder, name), c=max(0, min(len(COLOURS), int(colour))))
        self._save()

    def set_note(self, folder: str, name: str, note: str) -> None:
        self._update(key(folder, name), n=(note or "").strip())
        self._save()

    def moved(self, old_folder: str, old_name: str, new_folder: str,
              new_name: str) -> None:
        """A rename or move made here: the label goes with the file."""
        entry = self._store.pop(key(old_folder, old_name), None)
        if entry:
            self._store[key(new_folder, new_name)] = entry
            self._save()

    def _update(self, where: str, **fields) -> None:
        entry = dict(self._store.get(where) or {})
        entry.update(fields)
        if not entry.get("c") and not entry.get("n"):
            self._store.pop(where, None)
        else:
            self._store[where] = {k: v for k, v in entry.items() if v}

    def _save(self) -> None:
        self._config.set("labels", dict(self._store))
        self.changed.emit()

    def __len__(self) -> int:
        return len(self._store)

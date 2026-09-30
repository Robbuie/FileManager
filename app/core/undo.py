"""Undo (0.44): the last few things this window did that can be taken back.

What can be undone, and how:

  * a rename, or a Rename several  -- renamed back, as one plan (so a swap
    goes through temporary names again, see `core/renamer.py`);
  * a new folder                    -- to the Recycle Bin;
  * a copy, a paste, a drop, a duplicate or an extraction -- the copies to
    the Recycle Bin;
  * a move                          -- moved back where each item came from.

What cannot, and why: a delete already has its undo -- the Recycle Bin, where
Windows can put things back better than this application could; attribute and
date changes do not record what they replaced; and a copy or move that met a
file already at the destination is not offered at all. That last one is the
rule that matters. After an overwrite, or an "ask" answered with skip or
rename, the names at the destination are no longer all copies this
application made, and recycling them would take somebody's own files with
them. So only a job that finished whole -- nothing skipped, failed or
cancelled, no conflict met, rule "ask" -- is recorded.

Every undo is itself an ordinary operation, through the worker or the queue,
behind the same confirmation the forward operation had; nothing here writes.
The stack is in memory, for this window, `DEPTH` deep, and an undo that has
run is gone -- there is no redo.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QObject, Signal

DEPTH = 20

RENAME = "rename"       # data: folder, moves [(old, new)]
MKDIR = "mkdir"         # data: folder, name
COPY = "copy"           # data: targets [path]
MOVE = "move"           # data: moves [(source it came from, where it is now)]


@dataclass(frozen=True)
class Action:
    kind: str
    label: str
    folder: str = ""
    moves: tuple[tuple[str, str], ...] = ()
    targets: tuple[str, ...] = ()
    name: str = ""
    extra: dict = field(default_factory=dict, compare=False)


def leaf(path: str) -> str:
    return path.replace("/", "\\").rstrip("\\").rpartition("\\")[2] or path


def parent(path: str) -> str:
    text = path.replace("/", "\\").rstrip("\\")
    head, sep, _tail = text.rpartition("\\")
    if not sep:
        return ""
    return head + "\\" if head.endswith(":") else head


def for_rename(folder: str, moves: list[tuple[str, str]]) -> Action | None:
    moves = [(old, new) for old, new in moves if old != new]
    if not moves:
        return None
    label = (f"rename of {moves[0][1]}" if len(moves) == 1
             else f"rename of {len(moves)} items")
    return Action(RENAME, label, folder=folder, moves=tuple(moves))


def for_mkdir(folder: str, name: str) -> Action:
    return Action(MKDIR, f"new folder {name}", folder=folder, name=name)


def for_job(job) -> Action | None:
    """The undo for a finished transfer job, or None when it is not safe.

    `job` is a `core.transfers.JobState`, read for `kind`, `sources`,
    `destination`, `rename`, `into`, `conflict`, `conflicts`, the totals and
    whether it was cancelled or refused.
    """
    kind = getattr(getattr(job, "kind", None), "value", getattr(job, "kind", ""))
    if kind not in ("copy", "move"):
        return None
    if (job.cancelled or job.refused or job.skipped or job.failed
            or getattr(job, "conflicts", 0) or getattr(job, "conflict", "ask") != "ask"
            or not job.sources):
        return None
    into = list(getattr(job, "into", ()) or ())
    rename = getattr(job, "rename", "") or ""
    pairs = []
    for index, source in enumerate(job.sources):
        folder = into[index] if index < len(into) and into[index] else job.destination
        if not folder:
            return None
        name = rename or leaf(source)
        if not name:
            return None
        target = folder.rstrip("\\/") + "\\" + name
        pairs.append((source, target))
    count = len(pairs)
    what = leaf(pairs[0][1]) if count == 1 else f"{count} items"
    if kind == "copy":
        return Action(COPY, f"copy of {what}", targets=tuple(target for _s, target in pairs))
    return Action(MOVE, f"move of {what}", moves=tuple(pairs))


class UndoStack(QObject):
    changed = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._actions: list[Action] = []

    def push(self, action: Action | None) -> None:
        if action is None:
            return
        self._actions.append(action)
        del self._actions[:-DEPTH]
        self.changed.emit()

    def peek(self) -> Action | None:
        return self._actions[-1] if self._actions else None

    def pop(self) -> Action | None:
        if not self._actions:
            return None
        action = self._actions.pop()
        self.changed.emit()
        return action

    def __len__(self) -> int:
        return len(self._actions)

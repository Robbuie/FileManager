"""Make one folder match another, one way, after showing what that will do.

The pane compare (`core/compare.py`) answers "what is different here" for two
listings already on screen and reads nothing. This is the other half: it walks
both trees, works out what would have to be copied -- and, in mirror mode,
removed -- for the target to match the source, puts that in front of somebody,
and only then hands it to the queue. It never writes anything itself; a sync is
an ordinary copy job and, for a mirror, an ordinary recycle job, with the
queue's pause, cancel, conflict rule, retry and history.

Why a plan rather than a copy with "newer only" on the whole tree:

  * **Somebody sees it first.** A mirror removes files. A command that removes
    files without first saying which ones is the kind of convenience this
    application has refused everywhere else, and a list of forty deletions is
    exactly what somebody needs to see to notice the one that is wrong.
  * **The walk is done once and read both ways.** Both trees are listed, and
    direction and mode are decided afterwards against the same rows -- so
    flipping the arrow in the dialog is instant rather than a second walk over
    a share.

The rules, all of them conservative on purpose:

  * **Newer means newer by more than the tolerance**, for `compare.py`'s FAT
    reason. A file that is newer on the target is not overwritten: it is
    listed as a conflict and left alone. So is a file with the same time and a
    different size, because neither timestamp says which one is right.
  * **The copy runs with the queue's "newer only" rule**, so a target file
    that changed between the preview and the copy is still not overwritten by
    an older one. The plan is a statement about the moment of the walk; the
    rule is checked again at the moment of the write.
  * **A folder missing on one side is one action**, not one per file inside
    it: copied whole, or removed whole. That is also what makes the copy
    possible at all, because a job's destination folders must already exist.
  * **Links and junctions are never walked through, copied or removed.** They
    are listed so somebody can see they were left out.
  * **Mirror needs a complete picture.** A walk that stopped at its limit, or
    could not read a folder, has not seen everything, and a file it did not
    see on the source side would look like one to delete on the target side.
    So an incomplete walk offers update only, and says why.
"""

from __future__ import annotations

import ntpath
from dataclasses import dataclass, field
from typing import Iterable

from PySide6.QtCore import QObject, Signal

from app.io import paths
from app.io.protocol import Op, Reply, Status

#: What an action does, in the order the preview lists them.
NEW_FOLDER = "new folder"
NEW_FILE = "new file"
NEWER = "newer"
EXTRA_FOLDER = "extra folder"
EXTRA_FILE = "extra file"
TARGET_NEWER = "target newer"
DIFFERENT = "different"
CLASH = "clash"
LINK = "link"

#: Actions that write to the target.
COPIES = (NEW_FOLDER, NEW_FILE, NEWER)
#: Actions that remove from the target -- only ever in mirror mode.
REMOVALS = (EXTRA_FOLDER, EXTRA_FILE)
#: Things found and deliberately left alone.
LEFT_ALONE = (TARGET_NEWER, DIFFERENT, CLASH, LINK)

TOLERANCE = 2.0


@dataclass(frozen=True)
class Action:
    kind: str
    path: str            # relative to both roots, backslash-separated
    size: int = 0        # what would be written, for copies
    source_mtime: float = 0.0
    target_mtime: float = 0.0
    files: int = 1       # files inside, for a folder copied or removed whole


@dataclass
class Plan:
    mirror: bool
    actions: list[Action] = field(default_factory=list)

    def of(self, *kinds: str) -> list[Action]:
        return [action for action in self.actions if action.kind in kinds]

    @property
    def copies(self) -> list[Action]:
        return self.of(*COPIES)

    @property
    def removals(self) -> list[Action]:
        return self.of(*REMOVALS) if self.mirror else []

    @property
    def bytes(self) -> int:
        return sum(action.size for action in self.copies)

    @property
    def empty(self) -> bool:
        return not self.copies and not self.removals


@dataclass
class Scan:
    """Both trees as the walks found them, whichever way round they are used."""

    left: str
    right: str
    left_rows: list = field(default_factory=list)
    right_rows: list = field(default_factory=list)
    #: Why the picture is incomplete, per side: a limit hit, folders unread.
    left_gaps: list[str] = field(default_factory=list)
    right_gaps: list[str] = field(default_factory=list)
    #: Set when a walk failed outright; nothing else is meaningful then.
    error: str = ""

    @property
    def complete(self) -> bool:
        return not self.error and not self.left_gaps and not self.right_gaps


def _index(rows: Iterable) -> dict[str, object]:
    return {row.name.lower(): row for row in rows}


def _parents(path: str) -> list[str]:
    """Every folder above a relative path, nearest last: a\\b\\c -> [a, a\\b]."""
    parts = path.split("\\")
    return ["\\".join(parts[:i]) for i in range(1, len(parts))]


def plan(source_rows: Iterable, target_rows: Iterable, *, mirror: bool,
         tolerance: float = TOLERANCE) -> Plan:
    """What would make the target match the source. Rows are walk entries,
    names relative to their roots; folders included (`Op.WALK` with
    `folders`)."""
    source = _index(source_rows)
    target = _index(target_rows)
    result = Plan(mirror=mirror)

    # Folders handled whole, and everything under them therefore not listed
    # again: a missing folder, a clash, a link. Keys are lowercase.
    covered_source: set[str] = set()
    covered_target: set[str] = set()

    def under(key: str, covered: set[str]) -> bool:
        return any(parent in covered for parent in _parents(key))

    # Files and bytes under every folder, counted once per file per level
    # rather than by searching the whole tree for each folder -- a new tree of
    # a thousand folders is otherwise a thousand passes over 200,000 rows.
    def totals(rows: dict) -> dict[str, tuple[int, int]]:
        found: dict[str, tuple[int, int]] = {}
        for name, row in rows.items():
            if row.is_dir:
                continue
            for parent in _parents(name):
                files, size = found.get(parent, (0, 0))
                found[parent] = (files + 1, size + row.size)
        return found

    under_source, under_target = totals(source), totals(target)

    def inside(key: str, rows: dict) -> tuple[int, int]:
        return (under_source if rows is source else under_target).get(key, (0, 0))

    for key in sorted(source):
        if under(key, covered_source):
            continue
        here = source[key]
        there = target.get(key)
        if here.is_link or (there is not None and there.is_link):
            result.actions.append(Action(LINK, here.name))
            covered_source.add(key)
            covered_target.add(key)
            continue
        if there is None:
            if here.is_dir:
                files, size = inside(key, source)
                result.actions.append(Action(NEW_FOLDER, here.name, size=size,
                                             source_mtime=here.mtime, files=files))
                covered_source.add(key)
            else:
                result.actions.append(Action(NEW_FILE, here.name, size=here.size,
                                             source_mtime=here.mtime))
            continue
        if here.is_dir != there.is_dir:
            result.actions.append(Action(CLASH, here.name))
            covered_source.add(key)
            covered_target.add(key)
            continue
        if here.is_dir:
            continue                         # both have it; its contents decide
        if abs(here.mtime - there.mtime) <= tolerance:
            if here.size != there.size:
                result.actions.append(Action(DIFFERENT, here.name,
                                             source_mtime=here.mtime,
                                             target_mtime=there.mtime))
            continue
        if here.mtime > there.mtime:
            result.actions.append(Action(NEWER, here.name, size=here.size,
                                         source_mtime=here.mtime,
                                         target_mtime=there.mtime))
        else:
            result.actions.append(Action(TARGET_NEWER, here.name,
                                         source_mtime=here.mtime,
                                         target_mtime=there.mtime))

    for key in sorted(target):
        if key in source or under(key, covered_target):
            continue
        there = target[key]
        if there.is_link:
            result.actions.append(Action(LINK, there.name))
            covered_target.add(key)
            continue
        if there.is_dir:
            files, _size = inside(key, target)
            result.actions.append(Action(EXTRA_FOLDER, there.name,
                                         target_mtime=there.mtime, files=files))
            covered_target.add(key)
        else:
            result.actions.append(Action(EXTRA_FILE, there.name,
                                         target_mtime=there.mtime))
    order = {kind: index for index, kind in enumerate(
        COPIES + REMOVALS + LEFT_ALONE)}
    result.actions.sort(key=lambda action: (order[action.kind], action.path.lower()))
    return result


def copy_request(chosen: Iterable[Action], source_root: str,
                 target_root: str) -> tuple[list[str], list[str]]:
    """(sources, the folder each goes into) for one copy job."""
    sources: list[str] = []
    into: list[str] = []
    for action in chosen:
        if action.kind not in COPIES:
            continue
        sources.append(ntpath.join(source_root, action.path))
        into.append(ntpath.join(target_root, ntpath.dirname(action.path))
                    if ntpath.dirname(action.path) else target_root)
    return sources, into


def removal_request(chosen: Iterable[Action], target_root: str) -> list[str]:
    """The paths one recycle job removes from the target."""
    return [ntpath.join(target_root, action.path) for action in chosen
            if action.kind in REMOVALS]


def refusal(source: str, target: str) -> str:
    """Why these two folders cannot be synced, or empty when they can.

    One inside the other is the case that matters: the walk of the outer one
    includes the inner one, so a sync would copy the target into itself, and
    again on the next run. Compared resolved, so `S:\\Jobs` and the UNC path
    of the same share are recognised as the same place.
    """
    a = paths.resolve(source).rstrip("\\").lower()
    b = paths.resolve(target).rstrip("\\").lower()
    if a == b:
        return "both panes are showing the same folder"
    if b.startswith(a + "\\"):
        return "the target is inside the source"
    if a.startswith(b + "\\"):
        return "the source is inside the target"
    return ""


def on_a_share(path: str) -> bool:
    """Whether removing from here skips the Recycle Bin, as Windows does on a
    network folder."""
    return paths.volume_key(path) != paths.LOCAL_VOLUME_KEY


def gaps(reply: Reply) -> list[str]:
    """Why a finished walk has not seen everything, in words."""
    words = (reply.message or "").split()
    found: list[str] = []
    if "limit" in words:
        found.append("stopped at the walk limit")
    for word in words:
        if word.startswith("skipped="):
            count = int(word.partition("=")[2] or 0)
            if count:
                found.append(f"{count:,} folder{'s' if count != 1 else ''} "
                             "could not be read")
    return found


class SyncScan(QObject):
    """Walks both folders, through the volumes' own workers, and says when done.

    Two requests, one per volume, in flight at once -- a share on the left and
    a local disk on the right are different workers, so neither waits for the
    other. The UI thread only ever sees the rows arrive.
    """

    progressed = Signal(int, int)   # rows found so far, left and right
    finished = Signal(object)       # a Scan

    def __init__(self, bridge, config, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._config = config
        self._ids: dict[int, str] = {}
        self._scan: Scan | None = None
        self._open = 0

    @property
    def busy(self) -> bool:
        return bool(self._ids)

    def start(self, left: str, right: str) -> None:
        self.cancel()
        self._scan = Scan(left=left, right=right)
        self._open = 2
        for side, path in (("left", left), ("right", right)):
            request_id = self._bridge.submit(
                Op.WALK, path,
                timeout=float(self._config.get("timeout.listing")),
                on_reply=self._handle,
                args={"limit": int(self._config.get("sync.limit")),
                      "folders": True},
            )
            self._ids[request_id] = side

    def cancel(self) -> None:
        for request_id in list(self._ids):
            self._bridge.cancel(request_id)
            self._bridge.forget(request_id)
        self._ids.clear()
        self._scan = None

    def _handle(self, reply: Reply) -> None:
        side = self._ids.get(reply.id)
        scan = self._scan
        if side is None or scan is None:
            return
        rows = scan.left_rows if side == "left" else scan.right_rows
        rows.extend(reply.payload or [])
        if reply.status is Status.PARTIAL:
            self.progressed.emit(len(scan.left_rows), len(scan.right_rows))
            return
        del self._ids[reply.id]
        if reply.status is Status.OK:
            (scan.left_gaps if side == "left" else scan.right_gaps).extend(gaps(reply))
        elif not scan.error:
            where = scan.left if side == "left" else scan.right
            scan.error = f"{where}: {reply.message or reply.status.value}"
            self.cancel()
            self.finished.emit(scan)
            return
        self._open -= 1
        if self._open == 0:
            self._scan = None
            self.finished.emit(scan)

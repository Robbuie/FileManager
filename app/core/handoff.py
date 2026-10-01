"""Jobs another application asks this queue to run (0.46, File Compare).

File Compare's folder view finds what differs between two trees and can say
what would make one match the other; copying and removing is this
application's job, and File Manager has one engine for it. So File Compare
does not copy anything. It writes the jobs it wants to a small JSON file and
starts `FileManager.exe --queue <file>`; a running window gets the path over
the single-instance pipe (`io/instance.py`), a new one from its own command
line. Either way the jobs arrive here, are checked, and become ordinary queue
jobs -- the same pause, cancel, conflict rule, retry, history and undo as a
copy made with F5.

Somebody has already seen the plan: File Compare shows every action with a
box beside it before it writes the file, the way the sync dialog does here.
This module's job is to refuse anything that is not what that preview could
have produced (0.46.1 tightened every one of these after a review showed a
request could recycle any path at all):

  * **Only from File Compare's own folder.** The request file has to be in
    `%LOCALAPPDATA%\\FileCompare\\handoff`; a path anywhere else is refused
    before it is opened.
  * **Two roots, and nothing outside them.** A request names the folder it
    copies from and the folder it changes. Every copy source is strictly
    inside the first, every copy lands inside the second, and every removal
    is strictly inside the second -- never a root itself, never a drive or a
    share. Neither root may be inside the other.
  * **Only copy and recycle.** No move, no permanent erase: a request that
    asks for either is refused whole, not trimmed.
  * **Absolute Windows paths only**, a drive or a UNC share, with no `..`.
  * **Conflict rules are the queue's own**, spelled the way `Conflict` spells
    them.

And the window asks once more itself before a request that removes or
replaces anything (`MainWindow.queue_from_outside`): the preview was File
Compare's, and this is the application that does the removing.

Every request is answered with a file beside it: `<name>.taken.json` when it
is queued, then `<name>.result.json` when the last of its jobs ends -- or at
once, saying why, when it is refused, and when the window closes with its
jobs unfinished. File Compare waits for that file and walks the two folders
again, so its tree shows what the queue did rather than what it planned.

Pure apart from `read` and `write_result`, which touch one small local file
each and are only ever called off the UI thread.
"""

from __future__ import annotations

import json
import ntpath
import os
from dataclasses import dataclass, field

from app.io.protocol import Conflict

VERSION = 1
#: A request larger than this is not one File Compare wrote.
LIMIT = 16 * 1024 * 1024
#: Items one request may name, across its jobs.
MAX_ITEMS = 250_000

COPY = "copy"
RECYCLE = "recycle"
KINDS = (COPY, RECYCLE)


class Refused(ValueError):
    """The request is not one this queue will run. The message says why."""


@dataclass(frozen=True)
class Job:
    kind: str
    sources: tuple[str, ...]
    destination: str = ""
    into: tuple[str, ...] = ()
    conflict: Conflict = Conflict.ASK


@dataclass
class Request:
    path: str                  # the file it was read from
    sender: str = ""           # "File Compare"
    title: str = ""            # what to call it in the status bar
    jobs: list[Job] = field(default_factory=list)
    source_root: str = ""      # the folder copies come from
    target_root: str = ""      # the folder the request changes

    @property
    def replaces(self) -> bool:
        """Whether any copy overwrites whatever is there, newer or not."""
        return any(job.kind == COPY and job.conflict == Conflict.OVERWRITE
                   for job in self.jobs)

    @property
    def copies(self) -> int:
        return sum(len(job.sources) for job in self.jobs if job.kind == COPY)

    @property
    def removals(self) -> int:
        return sum(len(job.sources) for job in self.jobs if job.kind == RECYCLE)

    def summary(self) -> str:
        parts = []
        if self.copies:
            parts.append(f"copy {self.copies:,} item{'s' if self.copies != 1 else ''}")
        if self.removals:
            parts.append(f"remove {self.removals:,} item{'s' if self.removals != 1 else ''}")
        what = " and ".join(parts) or "nothing"
        who = self.sender or "another application"
        return f"{who}: {what} queued" + (f" ({self.title})" if self.title else "")


def result_path(request_path: str) -> str:
    """Where the outcome goes: beside the request, never somewhere it names."""
    root, _ext = os.path.splitext(request_path)
    return root + ".result.json"


def taken_path(request_path: str) -> str:
    root, _ext = os.path.splitext(request_path)
    return root + ".taken.json"


def folder() -> str:
    """Where File Compare writes its requests; the only place one is read."""
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"),
                                                          ".local", "share")
    return os.path.join(base, "FileCompare", "handoff")


def _same_folder(path: str, where: str) -> bool:
    parent = os.path.dirname(os.path.normpath(path))
    return os.path.normcase(parent) == os.path.normcase(os.path.normpath(where))


def answerable(path: str) -> bool:
    """A request path this module will read, and write an answer beside: a
    `.json` file directly in File Compare's handoff folder."""
    if not isinstance(path, str) or not path or "\x00" in path:
        return False
    lowered = path.lower()
    if not lowered.endswith(".json") or lowered.endswith((".result.json", ".taken.json")):
        return False
    return _same_folder(path, folder())


def absolute(path: str) -> bool:
    """A drive path (`C:\\...`) or a UNC path (`\\\\server\\share\\...`)."""
    if not isinstance(path, str) or not path or "\x00" in path:
        return False
    if path.startswith("\\\\?\\") or path.startswith("\\\\.\\"):
        return False           # the engine adds the prefix itself
    if path.startswith("\\\\"):
        parts = path[2:].split("\\")
        return len(parts) >= 2 and all(parts[:2])
    return len(path) >= 3 and path[0].isalpha() and path[1] == ":" and path[2] in "\\/"


def _norm(path: str) -> str:
    return ntpath.normpath(path).rstrip("\\").lower()


def _inside(path: str, root: str) -> bool:
    a, b = _norm(path), _norm(root)
    return a == b or a.startswith(b + "\\")


def _strictly_inside(path: str, root: str) -> bool:
    a, b = _norm(path), _norm(root)
    return a != b and a.startswith(b + "\\")


def _a_root(path: str) -> bool:
    """A drive (`D:\\`) or a share (`\\\\server\\share`): never removed, never a
    folder a request may change as a whole."""
    norm = _norm(path)
    if len(norm) <= 2 and norm[1:2] == ":":
        return True
    if norm.startswith("\\\\"):
        return len([p for p in norm[2:].split("\\") if p]) <= 2
    return False


def _dotted(path: str) -> bool:
    """`..` anywhere: normpath would fold it away, which is exactly how a
    path that looks inside a folder gets out of it."""
    return any(part == ".." for part in path.replace("/", "\\").split("\\"))


def parse(data: bytes, path: str) -> Request:
    """A request from the bytes of its file. Raises `Refused`."""
    if len(data) > LIMIT:
        raise Refused("the request is larger than any File Compare writes")
    try:
        message = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        raise Refused("the request is not readable JSON") from None
    if not isinstance(message, dict):
        raise Refused("the request is not an object")
    if message.get("version") != VERSION:
        raise Refused(f"the request is version {message.get('version')!r}; "
                      f"this File Manager reads version {VERSION}")
    jobs_in = message.get("jobs")
    if not isinstance(jobs_in, list) or not jobs_in:
        raise Refused("the request has no jobs")
    request = Request(path=path, sender=str(message.get("from", ""))[:60],
                      title=str(message.get("title", ""))[:200])
    source_root = message.get("source_root")
    target_root = message.get("target_root")
    for name, root in (("source", source_root), ("target", target_root)):
        if not absolute(root) or _dotted(root):
            raise Refused(f"the request does not name its {name} folder")
    if _inside(source_root, target_root) or _inside(target_root, source_root):
        raise Refused("one folder is inside the other")
    request.source_root, request.target_root = source_root, target_root
    items = 0
    for number, raw in enumerate(jobs_in, 1):
        if not isinstance(raw, dict):
            raise Refused(f"job {number} is not an object")
        kind = raw.get("kind")
        if kind not in KINDS:
            raise Refused(f"job {number} asks to {kind!r}; only copy and recycle are run")
        sources = raw.get("sources")
        if not isinstance(sources, list) or not sources:
            raise Refused(f"job {number} names nothing")
        for source in sources:
            if not absolute(source) or _dotted(source):
                raise Refused(f"job {number}: {source!r} is not a full path")
        items += len(sources)
        if items > MAX_ITEMS:
            raise Refused("the request names more items than one request may")
        if kind == RECYCLE:
            for source in sources:
                if not _strictly_inside(source, target_root) or _a_root(source):
                    raise Refused(f"job {number}: {source} is not inside {target_root}")
            request.jobs.append(Job(RECYCLE, tuple(sources)))
            continue
        for source in sources:
            if not _strictly_inside(source, source_root):
                raise Refused(f"job {number}: {source} is not inside {source_root}")
        destination = raw.get("destination")
        if not absolute(destination) or _dotted(destination):
            raise Refused(f"job {number}: the destination is not a full path")
        if _norm(destination) != _norm(target_root):
            raise Refused(f"job {number}: the destination is not {target_root}")
        into = raw.get("into") or [destination] * len(sources)
        if not isinstance(into, list) or len(into) != len(sources):
            raise Refused(f"job {number}: one folder per source is needed")
        for folder in into:
            if not absolute(folder) or _dotted(folder) or not _inside(folder, destination):
                raise Refused(f"job {number}: {folder!r} is not under {destination}")
        for source, folder in zip(sources, into):
            if _inside(folder, source):
                raise Refused(f"job {number}: {source} would be copied into itself")
        try:
            conflict = Conflict(raw.get("conflict", Conflict.ASK.value))
        except ValueError:
            raise Refused(f"job {number}: {raw.get('conflict')!r} is not a "
                          "rule for names already taken") from None
        request.jobs.append(Job(COPY, tuple(sources), destination, tuple(into), conflict))
    return request


def read(path: str) -> Request:
    """Read and check a request file. Off the UI thread: it is a file call.

    The path is checked before it is opened -- a `.json` file directly in
    File Compare's handoff folder -- because it arrives over the pipe from
    whoever wrote to it.
    """
    if not answerable(path):
        raise Refused("the request is not a .json file in File Compare's handoff folder")
    try:
        with open(path, "rb") as handle:
            data = handle.read(LIMIT + 1)
    except OSError as exc:
        raise Refused(f"could not read {path}: {exc.strerror or exc}") from None
    return parse(data, path)


def outcome(request: Request, states: list) -> dict:
    """What happened to a request's jobs, for File Compare to read. `states`
    are the queue's `JobState`s, finished."""
    copied = sum(getattr(s, "copied", 0) for s in states)
    skipped = sum(getattr(s, "skipped", 0) for s in states)
    failed = sum(getattr(s, "failed", 0) for s in states)
    cancelled = any(getattr(s, "cancelled", False) for s in states)
    refused = [s.refused for s in states if getattr(s, "refused", "")]
    problems: list[str] = []
    for state in states:
        problems.extend(getattr(state, "problems", [])[:20])
    return {
        "version": VERSION,
        "copied": copied,
        "skipped": skipped,
        "failed": failed,
        "cancelled": cancelled,
        "refused": refused,
        "problems": problems[:50],
    }


def refused(reason: str) -> dict:
    return {"version": VERSION, "refused": [reason]}


def cancelled() -> dict:
    return {"version": VERSION, "copied": 0, "cancelled": True,
            "refused": ["File Manager closed before the jobs finished"]}


def write_taken(request_path: str) -> None:
    """`<name>.taken.json`: the request is in the queue."""
    if answerable(request_path):
        _write(taken_path(request_path), {"version": VERSION})


def write_result(request_path: str, result: dict) -> None:
    """Beside the request, written whole and then renamed, so a reader never
    sees half of it. Off the UI thread -- except at exit, the settings file's
    exception for the settings file's reason."""
    if not answerable(request_path):
        return
    _write(result_path(request_path), result)


def _write(target: str, result: dict) -> None:
    partial = target + ".partial"
    try:
        with open(partial, "w", encoding="utf-8") as handle:
            json.dump(result, handle)
        os.replace(partial, target)
    except OSError:
        pass                    # File Compare times out and says so

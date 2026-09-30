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
have produced:

  * **Only copy and recycle.** No move, no permanent erase: a request that
    asks for either is refused whole, not trimmed.
  * **Absolute Windows paths only**, a drive or a UNC share. A relative path
    would be relative to wherever this process happened to start.
  * **Every copy source goes into a folder under the job's destination**, so
    a request cannot spray files across a disk while the queue reports one
    destination.
  * **Conflict rules are the queue's own**, spelled the way `Conflict` spells
    them.

When every job from one request has finished, the outcome is written beside
the request file as `<name>.result.json`. File Compare waits for that file and
walks the two folders again, so its tree shows what the queue did rather than
what it planned.

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


def _inside(path: str, root: str) -> bool:
    a = ntpath.normpath(path).rstrip("\\").lower()
    b = ntpath.normpath(root).rstrip("\\").lower()
    return a == b or a.startswith(b + "\\")


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
            request.jobs.append(Job(RECYCLE, tuple(sources)))
            continue
        destination = raw.get("destination")
        if not absolute(destination) or _dotted(destination):
            raise Refused(f"job {number}: the destination is not a full path")
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

    The path is checked before it is opened -- it has to be a `.json` file
    named by a full path -- because it arrives over the pipe from whoever
    wrote to it.
    """
    if not absolute(path) and not os.path.isabs(path):
        raise Refused("the request path is not a full path")
    if not path.lower().endswith(".json") or path.lower().endswith(".result.json"):
        raise Refused("the request is not a .json file")
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


def write_result(request_path: str, result: dict) -> None:
    """Beside the request, written whole and then renamed, so a reader never
    sees half of it. Off the UI thread."""
    target = result_path(request_path)
    partial = target + ".partial"
    try:
        with open(partial, "w", encoding="utf-8") as handle:
            json.dump(result, handle)
        os.replace(partial, target)
    except OSError:
        pass                    # File Compare times out and says so

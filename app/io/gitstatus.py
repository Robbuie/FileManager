"""What git says about the folder on screen, for the badges in the listing
(0.38).

Asked of `git` itself rather than by reading `.git` here: the index format has
versions, a split index, sparse checkouts and filesystem monitors, and the one
program that reads all of them correctly is git. So this finds the repository
the folder is in, runs `git status --porcelain=v1 -z -b` there once, and turns
the answer into one mark per name in *this* folder -- a file's own state, or
a dot on a subfolder with something changed somewhere under it.

The rules that keep it cheap are the caller's (local volumes only, one
request per folder, answered from a cache while the folder is on screen) and
this module's own: finding the repository is at most one `isdir` per folder
above, git is run under the request's deadline, and a machine without git is
an empty answer with a note rather than a failure. It runs in the volume's
worker, never in the window: it is a read of the disk and a program started.
"""

from __future__ import annotations

import os
import subprocess

from app.io import paths

#: How far up the tree to look for `.git` before deciding this is not a repo.
MAX_DEPTH = 40


def find_root(folder: str) -> str | None:
    """The working tree's top folder, or None if `folder` is not in one."""
    here = folder.rstrip("\\/")
    for _ in range(MAX_DEPTH):
        if os.path.exists(paths.api(os.path.join(here, ".git"))):
            return here
        parent = os.path.dirname(here)
        if not parent or parent == here:
            return None
        here = parent
    return None


def _code(xy: str) -> str:
    """The one letter a row shows, from git's two-column code."""
    if xy == "??":
        return "?"
    if "U" in xy or xy in ("AA", "DD"):
        return "U"
    for letter in ("A", "R", "M", "D"):
        if letter in xy:
            return letter
    return "M"


def parse(output: bytes, root: str, folder: str) -> dict:
    """The porcelain answer as marks for the names directly in `folder`.

    `-z` output is NUL-separated `XY path` records, a rename carrying its old
    path as an extra record, and a leading `## branch...upstream [ahead n]`.
    """
    records = output.decode("utf-8", "replace").split("\0")
    branch, ahead, behind = "", 0, 0
    marks: dict[str, str] = {}
    prefix = os.path.relpath(folder, root).replace("\\", "/")
    prefix = "" if prefix in (".", "") else prefix.rstrip("/") + "/"
    skip = False
    #: Git reports a wholly untracked folder once, as `?? sub/`, and nothing
    #: inside it. Standing inside such a folder, everything here is untracked.
    untracked = False
    for record in records:
        if skip:
            skip = False
            continue
        if not record:
            continue
        if record.startswith("## "):
            head = record[3:]
            name, _, rest = head.partition("...")
            branch = name.replace("No commits yet on ", "")
            if "[" in rest:
                detail = rest[rest.index("[") + 1:rest.rindex("]")]
                for part in detail.split(","):
                    part = part.strip()
                    if part.startswith("ahead "):
                        ahead = int(part[6:])
                    elif part.startswith("behind "):
                        behind = int(part[7:])
            continue
        xy, path = record[:2], record[3:]
        if xy[0] in "RC":
            skip = True           # the next record is the old name
        if xy == "??" and path.endswith("/") and prefix.startswith(path):
            untracked = True
            continue
        if not path.startswith(prefix):
            continue
        rest = path[len(prefix):]
        if not rest:
            continue
        top, slash, _below = rest.partition("/")
        code = "*" if slash and _below else _code(xy)
        if slash and not _below:
            code = _code(xy)      # an untracked folder reported as "name/"
        key = top.lower()
        # A folder's dot never replaces a file's own letter, and a letter
        # never replaces a dot: they are different rows.
        marks.setdefault(key, code)
    return {"root": root, "branch": branch, "ahead": ahead, "behind": behind,
            "marks": marks, "untracked": untracked}


def status(folder: str, timeout: float) -> dict:
    """The marks for one folder, or `{"marks": {}}` with a note saying why not."""
    root = find_root(folder)
    if root is None:
        return {"marks": {}, "note": "not in a repository"}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        done = subprocess.run(
            ["git", "-C", root, "status", "--porcelain=v1", "-z", "-b",
             "--untracked-files=normal"],
            capture_output=True, timeout=max(1.0, timeout), creationflags=flags)
    except FileNotFoundError:
        return {"marks": {}, "note": "git is not installed"}
    except subprocess.TimeoutExpired:
        return {"marks": {}, "note": "git took too long"}
    except OSError as exc:
        return {"marks": {}, "note": str(exc)}
    if done.returncode != 0:
        return {"marks": {}, "note": done.stderr.decode("utf-8", "replace").strip()[:200]}
    return parse(done.stdout, root, folder)

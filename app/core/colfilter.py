"""Filtering by a column as well as by name, in the one filter box.

0.49. The filter box (Ctrl+F) matched names only. Typing a column's name and a
colon now narrows by that column instead:

    ext:acd,l5x          files with either extension
    size:>10mb  size:<1k  bigger or smaller than a size (k, mb, g; bytes bare)
    modified:week        today, yesterday, week, month, older -- `when.WINDOWS`
    kind:folder          folder or file

Several terms are all required. Whatever is left over is the name pattern it
always was, so a filter typed before this release means what it meant.

One box rather than a filter per heading: the pane already has a filter, it
already says it is filtering, and Esc already clears it. The header's menu
offers "Filter by Ext" and the rest, which put the column's word into the box
for you.

Pure and Qt-free, so the parsing -- which is where a filter that quietly hides
rows would go wrong -- is tested on its own.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from app.core import when

#: What each column is called in the box. The header's own words, lower case,
#: plus the short forms people type.
FIELDS = {
    "ext": "ext", "type": "ext",
    "size": "size",
    "modified": "modified", "age": "modified", "date": "modified",
    "kind": "kind",
}

#: The word the header menu puts into the box for each column heading.
HEADING_FIELDS = {"Ext": "ext", "Size": "size", "Age": "modified",
                  "Modified": "modified"}

_TERM = re.compile(r"(?i)\b(ext|type|size|modified|age|date|kind):(\S*)")
_SIZE = re.compile(r"(?i)^(<=|>=|<|>|=)?\s*([\d.,]+)\s*([kmgt]?)i?b?$")
_SCALE = {"": 1, "k": 1024, "m": 1024 ** 2, "g": 1024 ** 3, "t": 1024 ** 4}
_WINDOWS = {key for key, _label in when.WINDOWS}


@dataclass(frozen=True)
class Spec:
    """A filter box's text, read."""

    name: str = ""
    exts: frozenset[str] = frozenset()
    sizes: tuple[tuple[str, int], ...] = ()
    modified: str = ""
    kind: str = ""
    #: Terms that did not read, so the pane can say so rather than hide rows
    #: for a reason nobody can see.
    problems: tuple[str, ...] = field(default_factory=tuple)

    @property
    def by_column(self) -> bool:
        return bool(self.exts or self.sizes or self.modified or self.kind)


def parse(text: str) -> Spec:
    text = text or ""
    exts: set[str] = set()
    sizes: list[tuple[str, int]] = []
    modified = ""
    kind = ""
    problems: list[str] = []
    for match in _TERM.finditer(text):
        word, value = FIELDS[match.group(1).lower()], match.group(2).strip().lower()
        if not value:
            continue        # still being typed: "size:" alone filters nothing yet
        if word == "ext":
            exts.update(part.strip().lstrip(".") for part in value.split(",")
                        if part.strip().lstrip("."))
        elif word == "size":
            read = _size(value)
            if read is None:
                problems.append(match.group(0))
            else:
                sizes.append(read)
        elif word == "modified":
            if value in _WINDOWS:
                modified = value
            else:
                problems.append(match.group(0))
        elif word == "kind":
            if value in ("folder", "folders", "dir"):
                kind = "folder"
            elif value in ("file", "files"):
                kind = "file"
            else:
                problems.append(match.group(0))
    name = " ".join(_TERM.sub(" ", text).split())
    return Spec(name=name, exts=frozenset(exts), sizes=tuple(sizes),
                modified=modified, kind=kind, problems=tuple(problems))


def _size(value: str) -> tuple[str, int] | None:
    found = _SIZE.match(value)
    if found is None:
        return None
    op = found.group(1) or ">="
    try:
        number = float(found.group(2).replace(",", ""))
    except ValueError:
        return None
    return op, int(number * _SCALE[found.group(3).lower()])


def passes(spec: Spec, *, is_dir: bool, ext: str, size: int, mtime: float,
           now: float | None = None) -> bool:
    """Whether a row with these values answers to the column terms.

    Only the column terms: the name pattern is the model's own `matches`, so
    the rule for a name stays in one place. A folder has no extension and no
    size of its own, so an `ext:` or `size:` term leaves folders out -- which is
    what somebody narrowing to "the big drawings" means.
    """
    if spec.kind == "folder" and not is_dir:
        return False
    if spec.kind == "file" and is_dir:
        return False
    if spec.exts and (is_dir or ext.lower() not in spec.exts):
        return False
    for op, limit in spec.sizes:
        if is_dir:
            return False
        if not _compare(size, op, limit):
            return False
    if spec.modified:
        start, before = when.window(spec.modified, time.time() if now is None else now)
        if start is not None and mtime < start:
            return False
        if before is not None and mtime >= before:
            return False
    return True


def _compare(value: int, op: str, limit: int) -> bool:
    return {"<": value < limit, "<=": value <= limit, ">": value > limit,
            ">=": value >= limit, "=": value == limit}[op]

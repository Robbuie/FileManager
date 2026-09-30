"""What a search keeps (0.42): name patterns, dates, sizes and contents.

A search is a walk -- `Op.WALK`, the one flat view already uses -- with a
filter in the worker, so the rows that come back are only the matches and they
stream into a tab like any other walk: marked, copied, deleted, previewed. The
filter is built here from the request's `args["search"]`, a plain dict:

    names     "*.L5X; *.acd"   wildcards, `;` or `,` between them, matched
                               against the file's own name without case;
                               blank for every name
    text      "Pump_101"       content to find; blank for none
    case      False            whether `text` is matched with case
    regex     False            whether `text` is a regular expression
    folders   False            whether folders whose names match are results
                               too (never when `text` is given: a folder has
                               no contents)
    after     0.0              modified at or after this time (epoch seconds)
    before    0.0              modified before this time
    min_size  0                at least this many bytes
    max_size  0                at most this many bytes, 0 for no limit
    max_read  64 MiB           a file larger than this is not read for `text`

The content test reads in 1 MiB blocks with an overlap the length of what is
being looked for, so a match across a block boundary is still found, and stops
at the first match. It looks for the text as UTF-8 and as UTF-16 -- the two
encodings a Windows text export is in -- and case-insensitive matching folds
ASCII only, which covers tag names and addresses. A regular expression is
matched against the block decoded as UTF-8.

`beat` is called between blocks: it is how a long read keeps the pool's
watchdog from mistaking a busy worker for a stuck one, and how a cancel
reaches the middle of a large file.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from typing import Any, Callable, IO

BLOCK = 1 << 20
MAX_READ = 64 * 1024 * 1024


@dataclass(frozen=True)
class Spec:
    names: tuple[str, ...] = ()
    text: str = ""
    case: bool = False
    regex: bool = False
    folders: bool = False
    after: float = 0.0
    before: float = 0.0
    min_size: int = 0
    max_size: int = 0
    max_read: int = MAX_READ

    @property
    def reads(self) -> bool:
        return bool(self.text)


def patterns(text: str) -> tuple[str, ...]:
    """`"*.L5X; *.acd"` as its patterns. A bare word is a part of a name."""
    out = []
    for part in re.split(r"[;,]", text or ""):
        part = part.strip()
        if not part:
            continue
        if not any(ch in part for ch in "*?["):
            part = f"*{part}*"
        out.append(part.lower())
    return tuple(out)


def spec_from(args: dict[str, Any] | None) -> Spec | None:
    """The request's search, or None when it has none. Refuses nothing:
    anything malformed is read as its default."""
    if not isinstance(args, dict):
        return None

    def number(key: str, kind=float):
        try:
            return kind(args.get(key) or 0)
        except (TypeError, ValueError):
            return kind(0)

    return Spec(
        names=patterns(str(args.get("names") or "")),
        text=str(args.get("text") or ""),
        case=bool(args.get("case")),
        regex=bool(args.get("regex")),
        folders=bool(args.get("folders")),
        after=number("after"), before=number("before"),
        min_size=number("min_size", int), max_size=number("max_size", int),
        max_read=number("max_read", int) or MAX_READ,
    )


def check(spec: Spec) -> str:
    """Why a search cannot run, or "" -- a regular expression that does not
    compile is the only such reason."""
    if spec.text and spec.regex:
        try:
            re.compile(spec.text)
        except re.error as exc:
            return f"the pattern does not work: {exc}"
    return ""


def name_matches(spec: Spec, name: str) -> bool:
    if not spec.names:
        return True
    lowered = name.lower()
    return any(fnmatch.fnmatchcase(lowered, pattern) for pattern in spec.names)


def cheap_matches(spec: Spec, name: str, is_dir: bool, size: int, mtime: float) -> bool:
    """Everything but the contents: what a walk already knows about a row."""
    if is_dir:
        if not spec.folders or spec.reads or not spec.names:
            return False
        return name_matches(spec, name)
    if not name_matches(spec, name):
        return False
    if spec.after and mtime < spec.after:
        return False
    if spec.before and mtime >= spec.before:
        return False
    if spec.min_size and size < spec.min_size:
        return False
    if spec.max_size and size > spec.max_size:
        return False
    if spec.reads and size > spec.max_read:
        return False
    return True


class Needle:
    """The text to find, prepared once for every file."""

    def __init__(self, spec: Spec) -> None:
        self.regex = re.compile(spec.text, 0 if spec.case else re.IGNORECASE) \
            if spec.regex else None
        self.case = spec.case
        text = spec.text if spec.case else spec.text.lower()
        self.forms = [] if self.regex else [
            form for form in {text.encode("utf-8"), text.encode("utf-16-le")} if form]
        self.overlap = max((len(form) for form in self.forms), default=0) - 1
        if self.regex is not None:
            self.overlap = 4096

    def found_in(self, stream: IO[bytes], beat: Callable[[], None] | None = None) -> bool:
        tail = b""
        while True:
            if beat is not None:
                beat()
            block = stream.read(BLOCK)
            if not block:
                return False
            data = tail + block
            if self.regex is not None:
                if self.regex.search(data.decode("utf-8", "ignore")):
                    return True
            else:
                haystack = data if self.case else data.lower()
                if any(form in haystack for form in self.forms):
                    return True
            tail = data[-self.overlap:] if self.overlap > 0 else b""


def matcher(spec: Spec) -> Callable[[str, Any, Callable[[], IO[bytes]] | None,
                                     Callable[[], None] | None], bool]:
    """One function the walk calls per row: `(relative name, Entry, opener,
    beat)` -> keep it. `opener` gives a binary stream for the contents, and is
    only called when the name, the dates and the size have already passed."""
    needle = Needle(spec) if spec.reads else None

    def keep(relative: str, entry, opener, beat=None) -> bool:
        leaf = re.split(r"[\\/]", relative)[-1]
        if not cheap_matches(spec, leaf, entry.is_dir, entry.size, entry.mtime):
            return False
        if needle is None:
            return True
        if opener is None:
            return False
        try:
            with opener() as stream:
                return needle.found_in(stream, beat)
        except OSError:
            return False

    return keep

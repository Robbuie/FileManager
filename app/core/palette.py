"""What the command palette offers, and how a typed word ranks it. No Qt.

0.28. Ctrl+K opens one box that reaches everything: every menu command, every
saved folder, the folders both panes have recently been in, the folders inside
the one on screen, and a path typed out in full. A person who half remembers
where something is, or what a command is called, types a few letters of it.

The ranking is deliberately simple and deliberately predictable, because a
palette that reorders itself for reasons nobody can see gets read rather than
used. In order: the whole query at the start of the label; at the start of a
word in it; anywhere in it; then its letters in order with the gaps counted
against it. Ties keep the order the sources were given in, which puts commands
before folders unless a prefix says otherwise.

Prefixes narrow the sources: `>` commands, `@` saved folders, `#` recent
folders, `/` folders here. A query that looks like a path offers going there.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

KINDS = ("command", "favorite", "recent", "here", "path")

PREFIXES = {">": ("command",), "@": ("favorite",), "#": ("recent",), "/": ("here",)}

#: How many rows the palette shows. More is a list to read, not a choice.
LIMIT = 12

_PATHLIKE = re.compile(r"^(?:[A-Za-z]:(?:\\|/|$)|\\\\[^\\]+)")


@dataclass
class Item:
    kind: str
    label: str
    detail: str = ""          # a path, or the menu it lives in
    shortcut: str = ""
    target: Any = None        # a QAction, or a path
    checked: bool | None = None
    score: int = field(default=0, compare=False)


def split_prefix(query: str) -> tuple[tuple[str, ...] | None, str]:
    text = query.lstrip()
    if text[:1] in PREFIXES:
        return PREFIXES[text[:1]], text[1:].strip()
    return None, text.strip()


def score(query: str, text: str) -> int | None:
    """Higher is better; None is no match. Case-insensitive."""
    if not query:
        return 0
    q = query.lower()
    t = text.lower()
    if t.startswith(q):
        return 4000 - len(t)
    at = t.find(q)
    if at >= 0:
        word_start = at == 0 or not t[at - 1].isalnum()
        return (3000 if word_start else 2000) - at
    # Letters in order. Each gap costs, so "dtd" finds "Duplicate as today"
    # but ranks below anything that contains it outright.
    position = -1
    gaps = 0
    for char in q:
        found = t.find(char, position + 1)
        if found < 0:
            return None
        if position >= 0:
            gaps += found - position - 1
        position = found
    return max(1, 1000 - gaps * 10)


def looks_like_path(text: str) -> bool:
    return bool(_PATHLIKE.match(text.strip()))


def rank(query: str, items: list[Item], limit: int = LIMIT) -> list[Item]:
    kinds, word = split_prefix(query)
    ranked: list[tuple[int, int, Item]] = []
    for order, item in enumerate(items):
        if kinds is not None and item.kind not in kinds:
            continue
        points = score(word, item.label)
        if points is None and item.detail and item.kind != "command":
            # A folder can be found by its path as well as its name, slightly
            # below a match on the name.
            by_path = score(word, item.detail)
            points = None if by_path is None else by_path - 500
        if points is None:
            continue
        item.score = points
        ranked.append((-points, order, item))
    ranked.sort(key=lambda row: (row[0], row[1]))
    out = [row[2] for row in ranked[:limit]]
    if kinds is None and looks_like_path(word):
        out.insert(0, Item("path", f"Go to {word}", target=word))
    return out[:limit]


def clean_label(text: str) -> tuple[str, str]:
    """A menu entry's text as a label and its key: `&Copy\\tF5` -> ("Copy", "F5")."""
    label, _, key = text.partition("\t")
    label = re.sub(r"&(.)", r"\1", label).replace("…", "").rstrip(".").strip()
    return label, key.strip()


def unique_recent(histories: list[list[str]], current: set[str], limit: int = 20) -> list[str]:
    """Folders from several tabs' histories, newest first, each once, leaving
    out the ones already on screen. Case-insensitive, as Windows is."""
    seen = {path.lower() for path in current}
    out: list[str] = []
    longest = max((len(history) for history in histories), default=0)
    for back in range(1, longest + 1):
        for history in histories:
            if back > len(history):
                continue
            path = history[-back]
            if path.lower() in seen:
                continue
            seen.add(path.lower())
            out.append(path)
            if len(out) >= limit:
                return out
    return out

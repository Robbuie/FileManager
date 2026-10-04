"""A ladder rung's neutral text, read into something that can be drawn (0.55).

Logix writes every rung of ladder as one line of what Rockwell calls neutral
text: `XIC(Start)XIO(Fault)[OTE(Run),OTL(Latched)];` -- instructions one
after another are in series, square brackets hold branches separated by
commas, and the semicolon ends the rung. That is a small grammar and this is
its parser, kept pure and away from Qt so the drawing is the only thing the
browser has to get right on screen.

Three shapes come out:

  * `Instr(name, args)` -- one instruction and its operands as written.
  * `Branch([series, series, ...])` -- parallel paths, each a list of nodes.
  * a rung is a list of nodes in series.

Anything the parser does not understand returns None, and the browser shows
the rung's text instead. A drawing that guessed would be worse than the text
it came from.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Instructions drawn as a contact on the wire: one operand, a symbol.
CONTACTS = {"XIC": "] [", "XIO": "]/[", "ONS": "[ONS]"}
#: Drawn as a coil at the end of a path.
COILS = {"OTE": "( )", "OTL": "(L)", "OTU": "(U)"}


@dataclass
class Instr:
    name: str
    args: list[str] = field(default_factory=list)

    @property
    def symbol(self) -> str | None:
        return CONTACTS.get(self.name) or COILS.get(self.name)


@dataclass
class Branch:
    paths: list[list] = field(default_factory=list)


class _Bad(Exception):
    pass


def parse(text: str) -> list | None:
    """The rung as nodes in series, or None if it is not ladder this reads."""
    source = (text or "").strip()
    if source.endswith(";"):
        source = source[:-1]
    if not source:
        return []
    try:
        nodes, at = _series(source, 0, stop="")
    except _Bad:
        return None
    if at != len(source):
        return None
    return nodes


def _skip(source: str, at: int) -> int:
    while at < len(source) and source[at] in " \t\r\n":
        at += 1
    return at


def _series(source: str, at: int, *, stop: str) -> tuple[list, int]:
    """Nodes until one of the `stop` characters (not consumed) or the end."""
    nodes: list = []
    while True:
        at = _skip(source, at)
        if at >= len(source) or source[at] in stop:
            return nodes, at
        char = source[at]
        if char == "[":
            branch, at = _branch(source, at + 1)
            nodes.append(branch)
        elif char.isalpha() or char == "_":
            instr, at = _instr(source, at)
            nodes.append(instr)
        else:
            raise _Bad(f"unexpected {char!r}")


def _branch(source: str, at: int) -> tuple[Branch, int]:
    branch = Branch()
    while True:
        path, at = _series(source, at, stop=",]")
        branch.paths.append(path)
        if at >= len(source):
            raise _Bad("unclosed branch")
        if source[at] == "]":
            return branch, at + 1
        at += 1        # the comma


def _instr(source: str, at: int) -> tuple[Instr, int]:
    start = at
    while at < len(source) and (source[at].isalnum() or source[at] == "_"):
        at += 1
    name = source[start:at]
    at = _skip(source, at)
    if at >= len(source) or source[at] != "(":
        # NOP-style instructions are still written with parentheses; a bare
        # word is not something this grammar has.
        raise _Bad(f"{name} without operands")
    depth = 0
    args: list[str] = []
    current = []
    at += 1
    while at < len(source):
        char = source[at]
        if char in "([":
            depth += 1
        elif char in ")]":
            if depth == 0:
                if char == "]":
                    raise _Bad("bracket inside operands")
                args.append("".join(current).strip())
                if args == [""]:
                    args = []
                return Instr(name, args), at + 1
            depth -= 1
        elif char == "," and depth == 0:
            args.append("".join(current).strip())
            current = []
            at += 1
            continue
        current.append(char)
        at += 1
    raise _Bad(f"{name} not closed")


def tags_in(node) -> list[str]:
    """Every operand in a node, for search highlighting and tests."""
    if isinstance(node, Instr):
        return list(node.args)
    if isinstance(node, Branch):
        return [tag for path in node.paths for item in path for tag in tags_in(item)]
    return [tag for item in node for tag in tags_in(item)]

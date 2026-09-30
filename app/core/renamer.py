"""Renaming many files at once (0.41): the rule, the preview and the plan.

Double Commander's multi-rename is Ctrl+M and a mask, and that is the shape
kept here because it is the one the user's hands know. A rule is a name mask
and an extension mask with tokens in them, then an optional find and replace,
then a case change:

    [N]   the old name without its extension      [E]  the old extension
    [N2-5] characters 2 to 5 of the old name       [N3-] from the 3rd on
    [C]   a counter: start, step and width set beside the mask
    [D]   the file's modified date, 2026-09-29     [P]  the folder's name
    [[    a literal [

Everything in this module is pure: no Qt, no filesystem. What it is given is
names and modified times from the listing already on screen, and what it gives
back is a preview -- every row with its new name or the reason it cannot have
one -- and, when nothing is wrong, the steps to run.

The steps are where the care goes. Renaming `a -> b` and `b -> a` in that
order fails at the first step, and so does any rename onto a name another row
is about to give up. So a name that is both a source and a target goes through
a temporary name first: every such source is moved aside, then everything is
renamed to its final name. The worker runs the steps in order and stops at the
first failure, and the steps already done are listed in the answer, so a
half-finished run says exactly which files moved.

Refused, per row, before anything runs:
  * a new name that is empty, `.` or `..`, ends in a space or a dot, or holds
    one of `\\ / : * ? " < > |` -- what Windows would refuse or mangle;
  * two rows given the same new name (without regard to case);
  * a new name that is already the name of a file in the folder that is not
    itself being renamed away.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field

BAD_CHARACTERS = set('\\/:*?"<>|')

#: How the case of the new name is changed, after the masks and the replace.
CASES = (("keep", "Leave as is"), ("lower", "lowercase"), ("upper", "UPPERCASE"),
         ("title", "First Letter Of Each Word"), ("first", "First letter only"))

_TOKEN = re.compile(r"\[\[|\[(N|E)(\d+)?(-)?(\d+)?\]|\[(C|D|P)\]")


@dataclass(frozen=True)
class Rule:
    name: str = "[N]"
    extension: str = "[E]"
    find: str = ""
    replace: str = ""
    regex: bool = False
    #: Whether the find and replace looks at the extension as well.
    whole: bool = False
    case: str = "keep"
    start: int = 1
    step: int = 1
    width: int = 1


@dataclass(frozen=True)
class Item:
    """One row being renamed: its current name and modified time."""
    name: str
    mtime: float = 0.0
    is_dir: bool = False


@dataclass
class Row:
    old: str
    new: str
    problem: str = ""

    @property
    def changes(self) -> bool:
        return not self.problem and self.new != self.old


@dataclass
class Preview:
    rows: list[Row] = field(default_factory=list)
    #: The rule itself is unusable -- a regular expression that does not
    #: compile -- so no row has a name to show.
    error: str = ""

    @property
    def problems(self) -> int:
        return sum(1 for row in self.rows if row.problem)

    @property
    def changing(self) -> int:
        return sum(1 for row in self.rows if row.changes)

    @property
    def ready(self) -> bool:
        return not self.error and not self.problems and self.changing > 0


def split(name: str, is_dir: bool = False) -> tuple[str, str]:
    """A name as stem and extension, the way Explorer splits it.

    A folder has no extension -- `2026-09-29.backup` is a folder name, not a
    `.backup` -- and neither does a name whose only dot is its first character.
    """
    if is_dir:
        return name, ""
    stem, dot, extension = name.rpartition(".")
    if not dot or not stem:
        return name, ""
    return stem, extension


def _slice(text: str, first: str | None, dash: str | None, last: str | None) -> str:
    if first is None:
        return text
    begin = max(int(first) - 1, 0)
    if dash is None:
        return text[begin:begin + 1]
    if last is None:
        return text[begin:]
    return text[begin:int(last)]


def _expand(mask: str, stem: str, extension: str, counter: str, date: str,
            folder: str) -> str:
    def one(match: re.Match) -> str:
        whole = match.group(0)
        if whole == "[[":
            return "["
        kind = match.group(1)
        if kind == "N":
            return _slice(stem, match.group(2), match.group(3), match.group(4))
        if kind == "E":
            return _slice(extension, match.group(2), match.group(3), match.group(4))
        other = match.group(5)
        if other == "C":
            return counter
        if other == "D":
            return date
        return folder
    return _TOKEN.sub(one, mask)


def _case(text: str, how: str) -> str:
    if how == "lower":
        return text.lower()
    if how == "upper":
        return text.upper()
    if how == "title":
        return re.sub(r"[A-Za-z][^\s_\-.]*",
                      lambda m: m.group(0)[0].upper() + m.group(0)[1:].lower(), text)
    if how == "first":
        return text[:1].upper() + text[1:].lower()
    return text


def _refusal(name: str) -> str:
    if not name or not name.strip():
        return "the new name is empty"
    if name in (".", ".."):
        return "not a usable name"
    bad = sorted(BAD_CHARACTERS & set(name))
    if bad:
        return "a name cannot contain " + " ".join(bad)
    if name[-1] in " .":
        return "a name cannot end in a space or a dot"
    return ""


def new_name(item: Item, index: int, rule: Rule, folder: str = "",
             pattern: re.Pattern | None = None) -> str:
    stem, extension = split(item.name, item.is_dir)
    counter = str(rule.start + index * rule.step).zfill(max(1, int(rule.width)))
    date = ""
    if item.mtime:
        date = datetime.datetime.fromtimestamp(item.mtime).strftime("%Y-%m-%d")
    new_stem = _expand(rule.name, stem, extension, counter, date, folder)
    new_extension = _expand(rule.extension, stem, extension, counter, date, folder)
    if item.is_dir:
        new_extension = ""
    result = f"{new_stem}.{new_extension}" if new_extension else new_stem
    if rule.find:
        if rule.whole:
            result = _replace(result, rule, pattern)
        else:
            head, dot, tail = result.rpartition(".") if new_extension else (result, "", "")
            head = _replace(head, rule, pattern)
            result = f"{head}{dot}{tail}"
    if rule.case in ("lower", "upper") or not new_extension:
        return _case(result, rule.case)
    # Title and first-letter are about words in the name; an extension is not
    # one, and `First letter only` applied to `pump.L5X` would make it `.l5x`.
    head, dot, tail = result.rpartition(".")
    return f"{_case(head, rule.case)}{dot}{tail}"


def _replace(text: str, rule: Rule, pattern: re.Pattern | None) -> str:
    if pattern is not None:
        return pattern.sub(rule.replace, text)
    return text.replace(rule.find, rule.replace)


def preview(items: list[Item], rule: Rule, existing: list[str], folder: str = "") -> Preview:
    """Every row's new name, or why it cannot have one."""
    pattern = None
    if rule.find and rule.regex:
        try:
            pattern = re.compile(rule.find)
            # Checked once against a string, so a bad back-reference in the
            # replacement is reported here rather than on the first row.
            pattern.sub(rule.replace, "")
        except (re.error, IndexError) as exc:
            return Preview(error=f"the pattern does not work: {exc}")
    rows: list[Row] = []
    for index, item in enumerate(items):
        try:
            name = new_name(item, index, rule, folder, pattern)
        except (re.error, IndexError) as exc:
            return Preview(error=f"the replacement does not work: {exc}")
        rows.append(Row(item.name, name, _refusal(name)))

    # Collisions, without regard to case: Windows' own comparison.
    leaving = {row.old.lower() for row in rows if row.new.lower() != row.old.lower()}
    staying = {name.lower() for name in existing} - leaving
    targets: dict[str, int] = {}
    for row in rows:
        targets[row.new.lower()] = targets.get(row.new.lower(), 0) + 1
    for row in rows:
        if row.problem or row.new == row.old:
            continue
        lowered = row.new.lower()
        if targets[lowered] > 1:
            row.problem = "same name as another row"
        elif lowered in staying and lowered != row.old.lower():
            row.problem = "already in this folder"
    return Preview(rows=rows)


def plan(result: Preview, temp_tag: str = "fm-rename") -> list[tuple[str, str]]:
    """The renames to run, in order, for a preview that is ready.

    Sources whose name another row wants go through a temporary name first.
    A rename that only changes case is also sent through one: Windows accepts
    it in one step, but a share served from something else may not.
    """
    if not result.ready:
        return []
    moving = [row for row in result.rows if row.changes]
    taken = {row.old.lower() for row in moving}
    wanted = {row.new.lower() for row in moving}
    first: list[tuple[str, str]] = []
    second: list[tuple[str, str]] = []
    for number, row in enumerate(moving):
        if row.old.lower() in wanted or row.new.lower() == row.old.lower():
            aside = f"~{temp_tag}-{number}-{row.old}"
            while aside.lower() in taken or aside.lower() in wanted:
                aside = "~" + aside
            first.append((row.old, aside))
            second.append((aside, row.new))
        else:
            second.append((row.old, row.new))
    return first + second

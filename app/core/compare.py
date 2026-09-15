"""What is different between the two panes, decided without opening a file.

The external compare tool in `commands.py` answers "what changed inside this
file". This answers the other question, the one asked far more often: which of
these two folders has the newer copy, and what is in one and not the other. It
is worth having both because they cost completely different amounts -- this
reads nothing at all.

That is the whole design. Both panes already hold a listing, and a listing
already carries size and mtime because `os.scandir` hands them over with the
name. So a comparison of two folders of 50,000 rows is a dictionary lookup per
row against data that is already in memory: no request, no worker, no deadline,
and nothing to cancel. The moment this reached into a file to checksum it, it
would become a job for the queue and would need every piece of machinery the
transfers have -- which is the argument for leaving that to the tool that
already does it well.

The result is marks in the two panes, not a window of its own. A file manager
that reports a difference in a dialog has told somebody something; a file
manager that marks the differing files has put them in the state where F5
copies exactly those. The second is the feature.

Two decisions inside worth knowing about:

  * **Names match case-insensitively**, because Windows does. Two files whose
    names differ only in case cannot be in one folder, so folding them is safe
    here in a way it would not be on a case-sensitive filesystem -- and the
    alternative, a folder that differs from its own copy because something
    wrote `README.TXT`, is a false difference somebody has to work out.
  * **Timestamps match within two seconds.** FAT stores mtime to two seconds,
    and a copy from NTFS to a USB stick or an SMB share can land on either side
    of a rounding. An exact comparison calls half of those files newer, every
    time, which trains people to ignore the answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

#: The same file on both sides, as far as size and time can say.
SAME = "same"
#: Here and not on the other side.
ONLY = "only"
#: Both sides have it; this one was written later.
NEWER = "newer"
#: Both sides have it; the other one was written later.
OLDER = "older"
#: Both sides have it, written at the same time, and they are not the same
#: size. Which means one of them is wrong, and neither timestamp says which.
DIFFERENT = "different"
#: A folder on one side and a file of that name on the other. Rare, confusing,
#: and worth its own word rather than being folded into `different`.
CLASH = "clash"
#: A folder that exists on both sides. Not looked into: see `Comparison`.
FOLDER = "folder"

#: Seconds two timestamps may differ by and still count as the same moment.
TOLERANCE = 2.0


@dataclass(frozen=True)
class Comparison:
    """What the two listings hold, name by name.

    `left` and `right` map every name in that pane to one of the verdicts
    above. Both sides are kept rather than one side and an inverse, because the
    two are not mirror images: `ONLY` on the left and `ONLY` on the right are
    different files, and the status line wants to say how many of each.

    Folders are paired by name and **not compared through**. Walking into them
    is a recursive scan of two trees over SMB, which is the queue's kind of
    work rather than a keystroke's, and a comparison that sometimes takes four
    minutes is one nobody presses. What this does instead is say which folders
    exist on one side only, which is the part that needs no walk.
    """

    left: dict[str, str] = field(default_factory=dict)
    right: dict[str, str] = field(default_factory=dict)

    def counts(self, side: str = "left") -> dict[str, int]:
        verdicts = self.left if side == "left" else self.right
        totals: dict[str, int] = {}
        for verdict in verdicts.values():
            totals[verdict] = totals.get(verdict, 0) + 1
        return totals

    @property
    def identical(self) -> bool:
        """True when nothing on either side differs at all."""
        return not any(
            verdict not in (SAME, FOLDER)
            for verdict in list(self.left.values()) + list(self.right.values()))


def _key(name: str) -> str:
    return name.lower()


def _verdict(here, there, tolerance: float) -> str:
    if here.is_dir != there.is_dir:
        return CLASH
    if here.is_dir:
        return FOLDER
    if abs(here.mtime - there.mtime) <= tolerance:
        return SAME if here.size == there.size else DIFFERENT
    return NEWER if here.mtime > there.mtime else OLDER


def compare(left: Iterable, right: Iterable,
            *, tolerance: float = TOLERANCE) -> Comparison:
    """Compare two listings. `left` and `right` are sequences of `Entry`.

    Takes entries rather than paths, and that is the point of the whole module:
    the rows are already in memory, so this is arithmetic rather than I/O and
    can be called from wherever the marks are about to be set.
    """
    left_rows = list(left)
    right_rows = list(right)
    other = {_key(row.name): row for row in right_rows}
    mine = {_key(row.name): row for row in left_rows}

    here: dict[str, str] = {}
    for row in left_rows:
        match = other.get(_key(row.name))
        here[row.name] = ONLY if match is None else _verdict(row, match, tolerance)

    there: dict[str, str] = {}
    for row in right_rows:
        match = mine.get(_key(row.name))
        if match is None:
            there[row.name] = ONLY
        else:
            there[row.name] = _verdict(row, match, tolerance)
    return Comparison(left=here, right=there)


#: The verdicts worth marking on a side: what that side has that the other one
#: does not, in the sense that matters for copying. `OLDER` is deliberately not
#: here -- it is the same file, and the copy that would fix it starts from the
#: other pane, where it is marked as `NEWER`.
MARKED = (ONLY, NEWER, DIFFERENT, CLASH)


def marks(verdicts: Mapping[str, str],
          *, wanted: Sequence[str] = MARKED) -> list[str]:
    """The names to select, in the order the listing holds them.

    What makes this the useful end of the feature: after it runs, the marked
    rows in each pane are exactly the ones F5 would need to copy to make the
    other side match. The comparison is a statement; the marks are something to
    act on.
    """
    return [name for name, verdict in verdicts.items() if verdict in wanted]


def summary(comparison: Comparison) -> str:
    """One line for the status bar, in the words a person would use.

    Says nothing about folders unless one is missing: a folder on both sides
    was not looked into, and reporting it as "the same" would be a claim this
    module has not earned.
    """
    here = comparison.counts("left")
    there = comparison.counts("right")
    pieces: list[str] = []
    if here.get(NEWER):
        pieces.append(f"{here[NEWER]} newer here")
    if there.get(NEWER):
        pieces.append(f"{there[NEWER]} newer there")
    if here.get(ONLY):
        pieces.append(f"{here[ONLY]} only here")
    if there.get(ONLY):
        pieces.append(f"{there[ONLY]} only there")
    if here.get(DIFFERENT):
        pieces.append(f"{here[DIFFERENT]} the same age and a different size")
    if here.get(CLASH):
        pieces.append(f"{here[CLASH]} a folder on one side and a file on the other")
    if not pieces:
        return "No differences, as far as size and time can tell"
    return "  ·  ".join(pieces)

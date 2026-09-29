"""Where the ticks on the listing's scrollbar go (0.34).

The scrollbar of a 50,000-row folder says where the view is and nothing else.
The map adds three facts about the rows that are *not* on screen: which of them
are marked, which were changed today, and which match the name being looked
for. A mark three thousand rows down is a mark somebody forgot about, and an
F5 that copies it is the surprise this exists to prevent.

Pure arithmetic, kept out of the widget for the reason `fit_popup` is: drawing
needs a screen and deciding where needs division, and the division is where a
bug would be. The rows come in as row numbers from the model; what goes out is
fractions of the listing, and then pixels, deduplicated -- 50,000 marked rows
on a 600-pixel scrollbar are at most 600 ticks, and drawing the other 49,400
would be the paint path doing a per-row loop it has no business doing.
"""

from __future__ import annotations

from typing import Iterable

#: The three kinds of tick, in the order they are drawn -- the last on top.
#: Search hits under the marks, because a marked row that also matches is
#: first of all marked: that is the one an operation will act on.
KINDS: tuple[str, ...] = ("today", "hits", "marked")


def fractions(rows: Iterable[int], total: int) -> list[float]:
    """Row numbers as positions down the listing, 0.0 at the top.

    The centre of each row rather than its top, so the last row's tick sits
    inside the groove rather than on its bottom edge.
    """
    if total <= 0:
        return []
    return sorted((row + 0.5) / total for row in rows if 0 <= row < total)


def pixels(positions: Iterable[float], top: int, height: int) -> list[int]:
    """Fractions to the y of each tick in a groove, one per pixel at most."""
    if height <= 0:
        return []
    seen: set[int] = set()
    for position in positions:
        y = top + int(max(0.0, min(1.0, position)) * (height - 1))
        seen.add(y)
    return sorted(seen)


def build(total: int, *, marked: Iterable[int] = (), today: Iterable[int] = (),
          hits: Iterable[int] = ()) -> dict[str, list[float]]:
    """Everything the scrollbar draws, by kind. Empty kinds are left out."""
    out: dict[str, list[float]] = {}
    for kind, rows in (("today", today), ("hits", hits), ("marked", marked)):
        placed = fractions(rows, total)
        if placed:
            out[kind] = placed
    return out

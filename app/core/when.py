"""Date windows for selecting by when something was modified.

Kept out of `listing` so the arithmetic can be tested without Qt, and because
the rule it states -- what "today" means -- is about the calendar rather than
about a model. Days are local days, from midnight, because that is what "the
files I changed today" means to the person asking; a rolling twenty-four hours
would take in last night's work at nine in the morning and call it today.

A share whose clock disagrees with this machine can date a file in the future.
Such a file counts as today rather than as nothing: the Age column already
calls it `now`, and a selection that disagreed with the column would read as a
bug.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

#: The windows the Select menu offers, in the order it offers them.
WINDOWS: tuple[tuple[str, str], ...] = (
    ("today", "Modified today"),
    ("yesterday", "Modified yesterday"),
    ("week", "Modified in the last 7 days"),
    ("month", "Modified in the last 30 days"),
    ("older", "Not modified for 30 days"),
)


def _midnight(now: float, days_back: int = 0) -> float:
    day = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0,
                                              microsecond=0)
    return (day - timedelta(days=days_back)).timestamp()


def window(key: str, now: float | None = None) -> tuple[float | None, float | None]:
    """(from, before) in epoch seconds for a named window; None is open-ended.

    `week` and `month` count today as one of the days, so "the last 7 days" is
    today and the six before it, which is how a calendar reads it.
    """
    now = time.time() if now is None else now
    if key == "today":
        return _midnight(now), None
    if key == "yesterday":
        return _midnight(now, 1), _midnight(now)
    if key == "week":
        return _midnight(now, 6), None
    if key == "month":
        return _midnight(now, 29), None
    if key == "older":
        return None, _midnight(now, 29)
    raise KeyError(key)


def same_day(mtime: float) -> tuple[float, float]:
    """The local day holding a moment, for "modified the same day as this"."""
    start = _midnight(mtime)
    return start, _midnight(start + 36 * 3600)


def inside(mtime: float, span: tuple[float | None, float | None]) -> bool:
    """Whether a modification time falls in a window. No time is never inside."""
    if not mtime:
        return False
    start, before = span
    if start is not None and mtime < start:
        return False
    if before is not None and mtime >= before:
        return False
    return True

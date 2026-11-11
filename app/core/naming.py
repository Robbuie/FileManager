"""The name a duplicated folder is offered, worked out without the filesystem.

Asked for by somebody who keeps a folder per day of PLC work: yesterday's
folder is copied, the copy is named for today, and yesterday's stays behind as
the backup. So the useful default is not "Copy of" -- it is the same name with
today's date in it, **written the way the folder already writes dates**, so a
row of `Line 3 2026-09-15` folders gets `Line 3 2026-09-16` and not a second
naming scheme.

Pure on purpose. The names already in the folder come from the listing the
pane is showing, and today comes from the caller, which is what makes every
rule here checkable without a clock or a disk. The listing can be stale by the
time the copy runs; the engine refuses a duplicate whose name has since been
taken, so this is a suggestion and never the check.
"""

from __future__ import annotations

import datetime as _dt
from typing import Iterable

# Re-exported: the listing's date chip reads `naming.date_span`, and the
# reading itself moved below `core` in 0.59 -- see `app/io/dates.py`.
from app.io.dates import date_span, with_date  # noqa: F401


def duplicate_name(name: str, taken: Iterable[str], today: _dt.date,
                   *, is_dir: bool = True) -> str:
    """What a duplicate of `name` should be called, given what is in the folder.

    Today's date in place of the old one when the name has a date and that name
    is free. Otherwise `name - Copy`, then `name - Copy (2)` and on -- with a
    file's extension kept at the end, because `plan.dwg - Copy` is a file
    Windows no longer knows how to open.

    Case-insensitive, because Windows is: `Line3` and `LINE3` are one folder.
    """
    existing = {item.lower() for item in taken}
    dated = with_date(name, today)
    if dated and dated != name and dated.lower() not in existing:
        return dated

    stem, ext = name, ""
    if not is_dir:
        head, dot, tail = name.rpartition(".")
        if dot and head:
            stem, ext = head, "." + tail
    candidate = f"{stem} - Copy{ext}"
    number = 2
    while candidate.lower() in existing:
        candidate = f"{stem} - Copy ({number}){ext}"
        number += 1
    return candidate

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
import re
from dataclasses import dataclass
from typing import Callable, Iterable

#: Years a date in a name is believed to be in. A run of eight digits in a
#: name is as likely a part number as a date, and requiring a plausible year as
#: well as a real month and day is what keeps `Pump 12345678` from becoming a
#: date nobody wrote.
_YEARS = range(1990, 2100)


@dataclass(frozen=True)
class _Form:
    pattern: re.Pattern[str]
    read: Callable[[re.Match[str]], _dt.date]
    write: Callable[[re.Match[str], _dt.date], str]


def _ymd(match: re.Match[str]) -> _dt.date:
    return _dt.date(int(match["y"]), int(match["m"]), int(match["d"]))


def _write_ymd(match: re.Match[str], day: _dt.date) -> str:
    sep = match["s"] or ""
    return f"{day.year:04d}{sep}{day.month:02d}{sep}{day.day:02d}"


def _write_mdy(match: re.Match[str], day: _dt.date) -> str:
    sep = match["s"]
    return f"{day.month:02d}{sep}{day.day:02d}{sep}{day.year:04d}"


def _write_ymd_short(match: re.Match[str], day: _dt.date) -> str:
    return f"{day.year % 100:02d}{day.month:02d}{day.day:02d}"


#: Tried in this order, and the order is the ambiguity decided. A separated
#: year-first date is unambiguous, so it goes first; month-first is the
#: American form and the only four-digit-year-last one recognised, because
#: `03-04-2026` cannot be read both ways and this user writes month first;
#: eight bare digits next; six bare digits last and only as year-month-day.
_FORMS = (
    _Form(re.compile(r"(?<!\d)(?P<y>\d{4})(?P<s>[-_.])(?P<m>\d{1,2})(?P=s)(?P<d>\d{1,2})(?!\d)"),
          _ymd, _write_ymd),
    _Form(re.compile(r"(?<!\d)(?P<m>\d{1,2})(?P<s>[-_.])(?P<d>\d{1,2})(?P=s)(?P<y>\d{4})(?!\d)"),
          _ymd, _write_mdy),
    _Form(re.compile(r"(?<!\d)(?P<y>\d{4})(?P<s>)(?P<m>\d{2})(?P<d>\d{2})(?!\d)"),
          _ymd, _write_ymd),
    _Form(re.compile(r"(?<!\d)(?P<y>\d{2})(?P<m>\d{2})(?P<d>\d{2})(?!\d)"),
          lambda m: _dt.date(2000 + int(m["y"]), int(m["m"]), int(m["d"])),
          _write_ymd_short),
)


def _first_date(name: str):
    for form in _FORMS:
        for match in form.pattern.finditer(name):
            try:
                day = form.read(match)
            except ValueError:
                continue
            if day.year in _YEARS:
                return match, day, form
    return None


def with_date(name: str, day: _dt.date) -> str | None:
    """The name with its date replaced by `day`, in the same form, or None
    when the name carries no date."""
    found = _first_date(name)
    if found is None:
        return None
    match, _, form = found
    return name[:match.start()] + form.write(match, day) + name[match.end():]


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

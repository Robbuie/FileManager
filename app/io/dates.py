"""Dates written into names: finding one, and writing another in its place.

Moved out of `app/core/naming.py` in 0.59 so the copy engine can use it as well
as the window. Duplicate as today reads the date in a folder's name to offer
today's; Duplicate Logix (0.59) also has to rename the files *inside* the copy
that carry the same date, and that walk happens in the ops process, which does
not import anything from `core`. So the reading lives here, below both, and
`naming` keeps its names for the callers it already had.

Pure: no filesystem, no clock. Today is always the caller's to pass in.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass
from typing import Callable

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


def date_span(name: str) -> tuple[int, int, _dt.date] | None:
    """0.49: where the date in a name is, and which day it says, or None.

    The same reading Duplicate uses, so a name the listing draws a date chip
    in is exactly a name Duplicate would move to today.
    """
    found = _first_date(name)
    if found is None:
        return None
    match, day, _form = found
    return match.start(), match.end(), day


def with_date(name: str, day: _dt.date) -> str | None:
    """The name with its date replaced by `day`, in the same form, or None
    when the name carries no date."""
    found = _first_date(name)
    if found is None:
        return None
    match, _, form = found
    return name[:match.start()] + form.write(match, day) + name[match.end():]


def redated(name: str, old: _dt.date, new: _dt.date) -> str:
    """0.59: `name` with `old` replaced by `new`, or `name` unchanged.

    Only when the first date in the name *is* `old`. A file in yesterday's
    folder dated some other day -- a reference copy from last month, a
    drawing with its own revision date -- is somebody else's date and keeps
    it. Written in the form the name already used, so `Line3_20261110.ACD`
    becomes `Line3_20261111.ACD` and not `Line3_2026-11-11.ACD`.
    """
    found = _first_date(name)
    if found is None:
        return name
    match, day, form = found
    if day != old:
        return name
    return name[:match.start()] + form.write(match, new) + name[match.end():]

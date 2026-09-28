"""What "today" and "the last 7 days" mean to the Select menu."""

from __future__ import annotations

from datetime import datetime

import pytest

from app.core import when


def at(*parts) -> float:
    return datetime(*parts).timestamp()


NOW = at(2026, 9, 27, 9, 30)


def test_today_starts_at_midnight_not_a_day_ago():
    span = when.window("today", NOW)
    assert when.inside(at(2026, 9, 27, 0, 1), span)
    assert not when.inside(at(2026, 9, 26, 23, 59), span), "last night is not today"


def test_yesterday_is_the_whole_day_before_and_nothing_else():
    span = when.window("yesterday", NOW)
    assert when.inside(at(2026, 9, 26, 0, 0), span)
    assert when.inside(at(2026, 9, 26, 23, 59), span)
    assert not when.inside(at(2026, 9, 27, 0, 0), span)
    assert not when.inside(at(2026, 9, 25, 23, 59), span)


def test_the_last_seven_days_count_today_as_one():
    span = when.window("week", NOW)
    assert when.inside(at(2026, 9, 21, 0, 0), span)
    assert not when.inside(at(2026, 9, 20, 23, 59), span)


def test_older_and_the_last_thirty_days_do_not_overlap():
    recent, older = when.window("month", NOW), when.window("older", NOW)
    for moment in (at(2026, 8, 29, 0, 0), at(2026, 8, 28, 23, 59), at(2026, 1, 1)):
        assert when.inside(moment, recent) != when.inside(moment, older)


def test_a_future_date_from_a_share_counts_as_today():
    assert when.inside(at(2026, 9, 28, 12, 0), when.window("today", NOW))


def test_no_time_is_never_inside():
    assert not when.inside(0.0, when.window("older", NOW))


def test_the_same_day_as_a_moment():
    span = when.same_day(at(2026, 3, 8, 14, 0))
    assert when.inside(at(2026, 3, 8, 0, 0), span)
    assert when.inside(at(2026, 3, 8, 23, 59), span)
    assert not when.inside(at(2026, 3, 9, 0, 0), span)


def test_every_offered_window_is_answerable():
    for key, _label in when.WINDOWS:
        when.window(key, NOW)
    with pytest.raises(KeyError):
        when.window("fortnight", NOW)

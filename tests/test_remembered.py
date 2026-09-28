"""Going back to a folder on a share that has stopped answering.

What was there, marked as out of date, rather than an empty pane and an error.
The rows come from memory only, and only for network volumes.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from app.core.config import Config  # noqa: E402
from app.core.pane import Pane  # noqa: E402
from app.core.remembered import Remembered  # noqa: E402
from app.io.protocol import Status  # noqa: E402
from tests.test_live import FakeBridge, row  # noqa: E402

JOBS = r"\\server\share\Jobs"
OTHER = r"\\server\share\Other"


# ------------------------------------------------------------------ the memory


def test_the_oldest_folders_go_first_when_the_rows_run_out():
    memory = Remembered(rows=5, folders=10)
    memory.keep("a", [1, 2, 3], when=1)
    memory.keep("b", [1, 2], when=2)
    memory.recall("a")                      # a is now the most recent
    memory.keep("c", [1, 2], when=3)
    assert memory.recall("b") is None
    assert memory.recall("a") is not None and memory.recall("c") is not None
    assert memory.rows == 5


def test_a_folder_bigger_than_the_budget_is_not_kept_at_all():
    memory = Remembered(rows=5)
    memory.keep("small", [1], when=1)
    memory.keep("huge", list(range(6)), when=2)
    assert memory.recall("huge") is None
    assert memory.recall("small") is not None


def test_keeping_a_folder_again_replaces_it_rather_than_counting_twice():
    memory = Remembered()
    memory.keep("A", [1, 2], when=1)
    memory.keep("a", [1, 2, 3], when=2)
    assert memory.rows == 3
    assert memory.recall("A") == (2, [1, 2, 3])


# -------------------------------------------------------------------- the pane


@pytest.fixture
def pane():
    bridge = FakeBridge()
    core = Pane(bridge, Config({"left.path": JOBS}), "left")
    core.refresh()
    bridge.answer(bridge.listings()[-1], [row("a.dwg"), row("b.dwg")])
    return core, bridge


def names(core):
    return sorted(entry.name for entry in core.current.model.everything())


def test_going_back_to_a_dead_share_shows_what_was_there(pane):
    core, bridge = pane
    core.navigate(OTHER)
    bridge.answer(bridge.listings()[-1], [row("x")])
    core.navigate(JOBS)
    bridge.answer(bridge.listings()[-1], status=Status.GONE)
    assert names(core) == ["a.dwg", "b.dwg"]
    assert core.current.stale
    assert "showing the listing from" in core.current.status_text


def test_a_folder_never_seen_is_still_an_error(pane):
    core, bridge = pane
    core.navigate(r"\\server\share\Never")
    bridge.answer(bridge.listings()[-1], status=Status.GONE)
    assert names(core) == []
    assert not core.current.stale
    assert "not reachable" in core.current.status_text


def test_a_refused_folder_is_not_papered_over(pane):
    """Access denied is an answer from a share that is there. Showing old rows
    would hide that somebody's rights changed."""
    core, bridge = pane
    core.navigate(OTHER)
    bridge.answer(bridge.listings()[-1], [row("x")])
    core.navigate(JOBS)
    bridge.answer(bridge.listings()[-1], status=Status.DENIED)
    assert names(core) == [] and not core.current.stale


def test_a_live_check_that_finds_the_share_gone_says_so(pane):
    core, bridge = pane
    assert core.check(now=core.current.check_after + 1)
    bridge.answer(bridge.listings()[-1], status=Status.TIMEOUT)
    assert core.current.stale
    assert names(core) == ["a.dwg", "b.dwg"]


def test_a_good_listing_clears_it(pane):
    core, bridge = pane
    core.refresh()
    bridge.answer(bridge.listings()[-1], status=Status.GONE)
    assert core.current.stale
    core.retry()
    bridge.answer(bridge.listings()[-1], [row("a.dwg")])
    assert not core.current.stale
    assert names(core) == ["a.dwg"]


def test_local_folders_are_not_remembered():
    bridge = FakeBridge()
    core = Pane(bridge, Config({"left.path": "C:\\Jobs"}), "left")
    core.refresh()
    bridge.answer(bridge.listings()[-1], [row("a")])
    assert core.remembered.recall("C:\\Jobs") is None

"""Checks on folder sizes: the lazy request with the longest reach.

A folder walk holds a volume's worker for as long as it runs, which makes this
the one request in the application that can starve everything else. What the
tests are here to protect is the bookkeeping that stops it:

  * one walk at a time, however many folders were selected;
  * a walk that is left behind is withdrawn at the worker, not merely dropped;
  * an answer lives exactly as long as the listing it was shown against.
"""

from __future__ import annotations

import pytest

from app.core.config import Config
from app.core.listing import parse_size
from app.core.sizes import WORKING, FolderSizes
from app.io.protocol import Op, Reply, Status


class FakeBridge:
    def __init__(self):
        self.sent = []
        self.cancelled = []
        self.forgotten = []

    def submit(self, op, path, *, timeout, on_reply, args=None):
        self.sent.append({"op": op, "path": path, "handler": on_reply})
        return len(self.sent)

    def cancel(self, request_id):
        self.cancelled.append(request_id)

    def forget(self, request_id):
        self.forgotten.append(request_id)

    def answer(self, index, *, status=Status.OK, payload=None):
        entry = self.sent[index]
        entry["handler"](Reply(index + 1, status, payload=payload))


@pytest.fixture
def sizes():
    bridge = FakeBridge()
    return FolderSizes(bridge, Config({})), bridge


def total(value):
    return {"bytes": value, "files": 1, "folders": 1}


def test_twenty_selected_folders_are_a_queue_not_twenty_requests(sizes):
    """The whole reason this is a queue. Twenty walks in flight is the tab
    next door waiting behind all of them for its listing.
    """
    provider, bridge = sizes
    provider.request("C:\\Jobs", [f"job{n}" for n in range(20)])
    assert len(bridge.sent) == 1
    assert bridge.sent[0]["op"] is Op.DIR_SIZE
    assert bridge.sent[0]["path"] == "C:\\Jobs\\job0"


def test_the_next_walk_starts_when_the_one_before_it_lands(sizes):
    provider, bridge = sizes
    provider.request("C:\\Jobs", ["a", "b", "c"])
    bridge.answer(0, payload=total(1024))
    assert [entry["path"] for entry in bridge.sent] == \
        ["C:\\Jobs\\a", "C:\\Jobs\\b"]
    bridge.answer(1, payload=total(2048))
    assert len(bridge.sent) == 3


def test_asking_twice_about_one_folder_walks_it_once(sizes):
    provider, bridge = sizes
    provider.request("C:\\Jobs", ["a"])
    provider.request("C:\\Jobs", ["a"])
    assert len(bridge.sent) == 1


def test_a_row_reads_as_working_until_its_answer_lands(sizes):
    provider, bridge = sizes
    provider.request("C:\\Jobs", ["a"])
    assert provider.known("C:\\Jobs", "a") == WORKING
    bridge.answer(0, payload=total(5 * 1024 * 1024))
    assert provider.known("C:\\Jobs", "a") == "5.0 M"


def test_a_folder_nobody_asked_about_has_no_size(sizes):
    provider, _ = sizes
    assert provider.known("C:\\Jobs", "untouched") is None


def test_a_walk_that_ran_out_of_time_keeps_what_it_counted(sizes):
    """A floor is more use to somebody deciding what to copy than `<DIR>`."""
    provider, bridge = sizes
    provider.request("C:\\Jobs", ["big"])
    bridge.answer(0, status=Status.TIMEOUT, payload=total(3 * 1024 ** 3))
    assert provider.known("C:\\Jobs", "big") == "3.0 G+"


def test_a_refused_folder_says_so_in_one_word(sizes):
    provider, bridge = sizes
    provider.request("C:\\Windows", ["System Volume Information"])
    bridge.answer(0, status=Status.DENIED)
    assert provider.known("C:\\Windows", "System Volume Information") == "denied"


def test_cancelling_withdraws_the_running_walk_at_the_worker(sizes):
    """Dropping the handler would leave a worker walking a tree over SMB for
    a folder nobody is looking at."""
    provider, bridge = sizes
    provider.request("C:\\Jobs", ["a", "b"])
    provider.cancel()
    assert bridge.cancelled == [1]
    assert provider.busy == 0


def test_a_cancelled_row_goes_back_to_being_unasked(sizes):
    provider, _ = sizes
    provider.request("C:\\Jobs", ["a"])
    provider.cancel()
    assert provider.known("C:\\Jobs", "a") is None


def test_listing_a_folder_again_forgets_its_sizes(sizes):
    """A size is precisely what changes while the folder itself does not."""
    provider, bridge = sizes
    provider.request("C:\\Jobs", ["a"])
    bridge.answer(0, payload=total(1024))
    provider.forget("C:\\Jobs")
    assert provider.known("C:\\Jobs", "a") is None


def test_forgetting_one_folder_leaves_another_alone(sizes):
    provider, bridge = sizes
    provider.request("C:\\Jobs", ["a"])
    bridge.answer(0, payload=total(1024))
    provider.request("D:\\Other", ["a"])
    bridge.answer(1, payload=total(2048))
    provider.forget("C:\\Jobs")
    assert provider.known("D:\\Other", "a") == "2.0 K"


def test_forgetting_a_folder_leaves_what_is_deeper_in_it_alone(sizes):
    """`C:\\Jobs` being listed again says nothing about a size counted for
    something inside `C:\\Jobs\\2026`."""
    provider, bridge = sizes
    provider.request("C:\\Jobs\\2026", ["drawings"])
    bridge.answer(0, payload=total(1024))
    provider.forget("C:\\Jobs")
    assert provider.known("C:\\Jobs\\2026", "drawings") == "1.0 K"


def test_busy_counts_the_queue_and_the_one_running(sizes):
    provider, bridge = sizes
    provider.request("C:\\Jobs", ["a", "b", "c"])
    assert provider.busy == 3
    bridge.answer(0, payload=total(1))
    assert provider.busy == 2


# ------------------------------------------------------------------- sorting

@pytest.mark.parametrize("text, expected", [
    ("512 B", 512),
    ("1.0 K", 1024),
    ("2.5 M", int(2.5 * 1024 ** 2)),
    ("3.0 G+", 3 * 1024 ** 3),
    ("1,024.0 G", 1024 * 1024 ** 3),
    ("<DIR>", 0),
    ("denied", 0),
    ("...", 0),
    (None, 0),
    ("", 0),
])
def test_a_size_cell_reads_back_as_a_number_to_sort_on(text, expected):
    assert parse_size(text) == expected

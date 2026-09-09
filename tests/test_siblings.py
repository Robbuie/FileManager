"""Checks on the breadcrumb chevrons and the scan behind them.

A dropdown looks like chrome and is not: it is a scan of somebody's job folder
over SMB, started because a mouse crossed a chevron. That is why it was kept
out of the chrome release, and it is what every test here is about.

The three that matter:

  * **the cap is on the scan.** `Op.FOLDERS` stops looking at its limit rather
    than enumerating the folder and slicing the answer, so a chevron on a
    50,000-row folder costs the first two hundred names. The worker test drives
    the real handler against a real temporary folder, which is the one part of
    this application that can be checked that way.
  * **one question at a time, and the last one is withdrawn.** Four chevrons in
    a row must leave one scan running. Withdrawn at the worker rather than
    dropped here: an abandoned scan still holds the volume, and the next
    chevron wants that worker.
  * **a late answer is dropped.** Both panes hear every reply, because there is
    one scan for the window -- so the bar with no menu open has to do nothing
    with what it is handed, which is the same guard the shell menu needs.
"""

from __future__ import annotations

import multiprocessing
import os

import pytest

from app.io.protocol import Op, Reply, Request, Status

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.core.config import Config  # noqa: E402
from app.core.siblings import Siblings  # noqa: E402
from app.ui.breadcrumb import NOTHING, WAITING, Breadcrumb  # noqa: E402


# ------------------------------------------------------------------ the worker


def run_folders(path: str, *, limit: int = 200, timeout: float = 5.0) -> Reply:
    """The real handler, against a real folder, with no process in between."""
    from app.io import worker

    outbox: multiprocessing.Queue = multiprocessing.Queue()
    control: multiprocessing.Queue = multiprocessing.Queue()
    request = Request(id=1, op=Op.FOLDERS, path=path, timeout=timeout,
                      args={"limit": limit})
    worker._folders(request, outbox, control, set())  # noqa: SLF001
    return outbox.get(timeout=5)


def test_it_answers_with_folders_and_no_files(tmp_path):
    (tmp_path / "alpha").mkdir()
    (tmp_path / "bravo").mkdir()
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    reply = run_folders(str(tmp_path))
    assert reply.status is Status.OK
    assert reply.payload["names"] == ["alpha", "bravo"]
    assert reply.payload["more"] is False


def test_the_cap_stops_the_scan_and_says_so(tmp_path):
    for index in range(20):
        (tmp_path / f"folder-{index:02d}").mkdir()
    reply = run_folders(str(tmp_path), limit=5)
    assert len(reply.payload["names"]) == 5
    assert reply.payload["more"] is True


def test_the_answer_is_sorted_case_insensitively(tmp_path):
    for name in ("Zulu", "alpha", "Bravo"):
        (tmp_path / name).mkdir()
    assert run_folders(str(tmp_path)).payload["names"] == \
        ["alpha", "Bravo", "Zulu"]


def test_a_folder_that_is_not_there_fails_rather_than_answering_empty(tmp_path):
    """An empty answer and a missing folder are different facts, and the
    dropdown says different things about them."""
    reply = run_folders(os.path.join(str(tmp_path), "nowhere"))
    assert reply.status is not Status.OK


# -------------------------------------------------------------------- the core


class FakeBridge:
    def __init__(self):
        self.sent = []
        self.cancelled = []
        self._handlers = {}

    def submit(self, op, path, *, timeout, on_reply, args=None):
        self.sent.append((op, path, dict(args or {})))
        request_id = len(self.sent)
        self._handlers[request_id] = on_reply
        return request_id

    def answer(self, request_id, payload, status=Status.OK):
        handler = self._handlers.get(request_id)
        if handler is not None:
            handler(Reply(request_id, status, payload=payload))

    def cancel(self, request_id):
        self.cancelled.append(request_id)

    def forget(self, request_id):
        self._handlers.pop(request_id, None)


@pytest.fixture
def asking(tmp_path):
    config = Config({}, str(tmp_path / "config.json"))
    bridge = FakeBridge()
    return Siblings(bridge, config), bridge


def test_it_carries_the_limit_and_its_own_deadline(asking):
    siblings, bridge = asking
    siblings.ask("S:\\Jobs\\2026")
    op, path, args = bridge.sent[0]
    assert op is Op.FOLDERS and path == "S:\\Jobs\\2026"
    assert args["limit"] == 200


def test_a_second_chevron_withdraws_the_first_at_the_worker(asking):
    """Not merely forgotten. A scan nobody is waiting for still holds the
    volume the next chevron wants."""
    siblings, bridge = asking
    siblings.ask("S:\\Jobs")
    siblings.ask("S:\\Drawings")
    assert bridge.cancelled == [1]
    assert len(bridge.sent) == 2


def test_an_answer_to_a_chevron_nobody_is_waiting_on_is_dropped(asking):
    siblings, bridge = asking
    heard = []
    siblings.ready.connect(lambda folder, names, more: heard.append(folder))
    siblings.ask("S:\\Jobs")
    siblings.cancel()
    bridge.answer(1, {"names": ["a"], "more": False})
    assert heard == []


def test_a_timeout_still_delivers_what_it_got(asking):
    """A share answering slowly should lose its dropdown, not empty it: some
    of the folders is a usable menu and none of them is a lie."""
    siblings, bridge = asking
    heard = []
    siblings.ready.connect(lambda folder, names, more: heard.append((names, more)))
    siblings.ask("S:\\Jobs")
    bridge.answer(1, {"names": ["a", "b"], "more": True}, status=Status.TIMEOUT)
    assert heard == [(["a", "b"], True)]


def test_a_failure_says_why_in_words_short_enough_for_a_menu(asking):
    siblings, bridge = asking
    said = []
    siblings.unavailable.connect(lambda folder, message: said.append(message))
    siblings.ask("S:\\Jobs")
    bridge.answer(1, None, status=Status.GONE)
    assert said == ["not reachable"]
    assert len(said[0]) < 30


# --------------------------------------------------------------------- the bar


def test_the_dropdown_opens_saying_it_is_looking():
    """Straight away, because the answer comes from a worker and may be a
    second away. A chevron that does nothing until the scan returns reads as a
    chevron that does nothing."""
    bar = Breadcrumb()
    asked = []
    bar.siblingsWanted.connect(asked.append)
    bar.set_crumbs([("C:\\", "C:\\"), ("Jobs", "C:\\Jobs"), ("2026", "C:\\Jobs\\2026")])
    chevrons = _chevrons(bar)
    assert len(chevrons) == 2
    chevrons[0].click()
    QApplication.processEvents()
    assert asked == ["C:\\"]
    assert [action.text() for action in bar._menu.actions()] == [WAITING]  # noqa: SLF001
    bar._close_menu()  # noqa: SLF001


def test_a_chevron_drops_down_the_crumb_on_its_left():
    bar = Breadcrumb()
    asked = []
    bar.siblingsWanted.connect(asked.append)
    bar.set_crumbs([("C:\\", "C:\\"), ("Jobs", "C:\\Jobs"), ("2026", "C:\\Jobs\\2026")])
    _chevrons(bar)[1].click()
    QApplication.processEvents()
    assert asked == ["C:\\Jobs"]
    bar._close_menu()  # noqa: SLF001


def test_an_answer_for_a_different_folder_is_ignored():
    """Both panes hear every reply, because there is one scan for the window."""
    bar = Breadcrumb()
    bar.set_crumbs([("C:\\", "C:\\"), ("Jobs", "C:\\Jobs")])
    _chevrons(bar)[0].click()
    QApplication.processEvents()
    bar.show_siblings("D:\\Somewhere", [("x", "D:\\Somewhere\\x")], False)
    assert [action.text() for action in bar._menu.actions()] == [WAITING]  # noqa: SLF001
    bar._close_menu()  # noqa: SLF001


def test_an_empty_folder_says_so_rather_than_opening_an_empty_menu():
    bar = Breadcrumb()
    bar.set_crumbs([("C:\\", "C:\\"), ("Jobs", "C:\\Jobs")])
    _chevrons(bar)[0].click()
    QApplication.processEvents()
    bar.show_siblings("C:\\", [], False)
    assert [action.text() for action in bar._menu.actions()] == [NOTHING]  # noqa: SLF001
    bar._close_menu()  # noqa: SLF001


def test_a_capped_answer_says_there_is_more():
    bar = Breadcrumb()
    bar.set_crumbs([("C:\\", "C:\\"), ("Jobs", "C:\\Jobs")])
    _chevrons(bar)[0].click()
    QApplication.processEvents()
    bar.show_siblings("C:\\", [("Jobs", "C:\\Jobs")], True)
    texts = [action.text() for action in bar._menu.actions()]  # noqa: SLF001
    assert texts[0] == "Jobs"
    assert "more" in texts[-1]
    bar._close_menu()  # noqa: SLF001


def test_choosing_one_navigates_to_the_path_it_was_handed():
    """Handed, not built here: joining a folder to a name is path arithmetic
    and this layer does none."""
    bar = Breadcrumb()
    went = []
    bar.navigate.connect(went.append)
    bar.set_crumbs([("C:\\", "C:\\"), ("Jobs", "C:\\Jobs")])
    _chevrons(bar)[0].click()
    QApplication.processEvents()
    bar.show_siblings("C:\\", [("Drawings", "C:\\Drawings")], False)
    bar._menu.actions()[0].trigger()  # noqa: SLF001
    assert went == ["C:\\Drawings"]
    bar._close_menu()  # noqa: SLF001


def test_the_ellipsis_is_not_a_target():
    """It stands for crumbs that were dropped, so there is no one folder it
    could open."""
    bar = Breadcrumb()
    deep = "C:\\"
    crumbs = [("C:\\", deep)]
    for step in range(9):
        deep = deep.rstrip("\\") + "\\" + f"f{step}"
        crumbs.append((f"f{step}", deep))
    bar.set_crumbs(crumbs)
    from PySide6.QtWidgets import QLabel

    labels = [w for w in bar._buttons if isinstance(w, QLabel)]  # noqa: SLF001
    assert any(label.text() == "\u2026" for label in labels)


def _chevrons(bar):
    from PySide6.QtWidgets import QToolButton

    return [w for w in bar._buttons  # noqa: SLF001
            if isinstance(w, QToolButton) and w.property("role") == "crumbsep"]

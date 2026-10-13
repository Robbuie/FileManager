"""0.56.5: a worker ends with the window's process, however that ends."""

from __future__ import annotations

import multiprocessing
import os
import time

from app.io import orphans


def test_outside_a_worker_there_is_nothing_to_follow() -> None:
    assert orphans.follow_parent() is False


def _child(answers) -> None:
    answers.put(orphans.follow_parent())


def test_inside_a_worker_the_parent_is_followed() -> None:
    ctx = multiprocessing.get_context("spawn")
    answers = ctx.Queue()
    process = ctx.Process(target=_child, args=(answers,))
    process.start()
    try:
        assert answers.get(timeout=60) is True
    finally:
        process.join(timeout=30)


def _grandchild(pids) -> None:
    pids.put(os.getpid())
    orphans.follow_parent()
    while True:            # a worker with nothing to do, waiting forever
        time.sleep(0.2)


def _middle(pids) -> None:
    ctx = multiprocessing.get_context("spawn")
    ctx.Process(target=_grandchild, args=(pids,), daemon=False).start()
    # Gone without exiting: no atexit, no daemon cleanup. A crash, as far as
    # the grandchild can tell.
    time.sleep(1.0)
    os._exit(1)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    if os.name != "nt":
        # A zombie still answers kill(0); one that has exited is not running.
        try:
            with open(f"/proc/{pid}/stat") as stat:
                return stat.read().split()[2] != "Z"
        except OSError:
            return False
    return True


def test_a_worker_whose_parent_died_ends_too() -> None:
    if os.name == "nt":  # os.kill(pid, 0) terminates on Windows
        import pytest

        pytest.skip("checked with a POSIX liveness probe")
    ctx = multiprocessing.get_context("spawn")
    pids = ctx.Queue()
    middle = ctx.Process(target=_middle, args=(pids,))
    middle.start()
    worker = pids.get(timeout=60)
    middle.join(timeout=60)
    deadline = time.monotonic() + 20
    while _alive(worker) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not _alive(worker)

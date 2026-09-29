"""Checks on share health (0.37): what gets measured, and how often.

The rule these hold is the one that makes pinging acceptable at all: a share
nobody has opened is never asked anything, and a share that has stopped
answering has one question outstanding, not one per tick.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from app.core import health  # noqa: E402
from app.core.config import Config  # noqa: E402
from app.io.protocol import Op, Reply, Status  # noqa: E402

DRIVES = [{"letter": "S:", "unc": "\\\\fs01\\projects"}, {"letter": "C:", "unc": ""}]


class Bridge:
    def __init__(self):
        self.sent = []
        self.cancelled = []

    def submit(self, op, path, *, timeout, on_reply, args=None):
        self.sent.append((op, path, on_reply))
        return len(self.sent)

    def cancel(self, request_id):
        self.cancelled.append(request_id)

    def forget(self, request_id):
        pass


def test_keys():
    assert health.key_for("\\\\FS01\\Projects\\Jobs", DRIVES) == "\\\\fs01\\projects"
    assert health.key_for("S:\\Jobs", DRIVES) == "S:"
    assert health.key_for("C:\\Users", DRIVES) is None


@pytest.fixture
def made():
    bridge = Bridge()
    return health.ShareHealth(bridge, Config({})), bridge


def test_a_local_folder_is_never_measured(made):
    watcher, bridge = made
    watcher.watch("C:\\Users", DRIVES)
    watcher.tick()
    assert bridge.sent == []


def test_a_share_is_stat_once_at_once_and_not_again_while_out(made):
    watcher, bridge = made
    watcher.watch("S:\\Jobs\\1234", DRIVES)
    assert [(op, path) for op, path, _ in bridge.sent] == [(Op.STAT, "S:\\")]
    watcher.tick()
    watcher.tick()
    assert len(bridge.sent) == 1
    bridge.sent[0][2](Reply(1, Status.OK))
    reading = watcher.reading("S:")
    assert reading.latest is not None
    watcher.tick()
    assert len(bridge.sent) == 2


def test_no_answer_is_down(made):
    watcher, bridge = made
    watcher.watch("\\\\fs01\\projects\\x", DRIVES)
    bridge.sent[0][2](Reply(1, Status.TIMEOUT))
    reading = watcher.reading("\\\\fs01\\projects")
    assert reading.state(100.0) == health.DOWN
    assert reading.last_down is not None


def test_turning_it_off_stops_the_timer_and_cancels(made):
    watcher, bridge = made
    watcher.watch("S:\\Jobs", DRIVES)
    watcher._config.set("network.ping", False)
    watcher.configure()
    assert not watcher.enabled
    assert bridge.cancelled == [1]
    watcher.tick()
    assert len(bridge.sent) == 1


def test_usual_is_the_median_of_answers():
    reading = health.Reading()
    for value in (10.0, None, 30.0, 20.0):
        reading.history.append(value)
    assert reading.usual() == 20.0
    assert reading.state(100.0) == health.GOOD

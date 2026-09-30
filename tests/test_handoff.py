"""Jobs File Compare hands to the queue (0.46): the request, the pipe
message, the command line, and the window running them."""

from __future__ import annotations

import json
import time

import pytest

from app.core import handoff
from app.io import instance
from app.io.protocol import Conflict


def request(**overrides):
    body = {
        "version": 1,
        "from": "File Compare",
        "title": "S:\\Jobs -> D:\\Jobs",
        "jobs": [
            {"kind": "copy", "sources": ["S:\\Jobs\\a.txt", "S:\\Jobs\\sub\\b.txt"],
             "destination": "D:\\Jobs", "into": ["D:\\Jobs", "D:\\Jobs\\sub"],
             "conflict": "overwrite"},
            {"kind": "recycle", "sources": ["D:\\Jobs\\old.txt"]},
        ],
    }
    body.update(overrides)
    return json.dumps(body).encode("utf-8")


def test_a_request_becomes_one_copy_and_one_recycle():
    got = handoff.parse(request(), "C:\\x\\r.json")
    assert [job.kind for job in got.jobs] == ["copy", "recycle"]
    copy = got.jobs[0]
    assert copy.into == ("D:\\Jobs", "D:\\Jobs\\sub")
    assert copy.conflict is Conflict.OVERWRITE
    assert got.copies == 2 and got.removals == 1
    assert "File Compare: copy 2 items and remove 1 item queued" in got.summary()


@pytest.mark.parametrize("change, why", [
    ({"version": 2}, "version"),
    ({"jobs": []}, "no jobs"),
    ({"jobs": [{"kind": "move", "sources": ["C:\\a"], "destination": "D:\\"}]}, "move"),
    ({"jobs": [{"kind": "erase", "sources": ["C:\\a"]}]}, "erase"),
    ({"jobs": [{"kind": "recycle", "sources": ["a.txt"]}]}, "full path"),
    ({"jobs": [{"kind": "recycle", "sources": ["C:\\a\\..\\..\\Windows"]}]}, "full path"),
    ({"jobs": [{"kind": "recycle", "sources": ["\\\\?\\C:\\a"]}]}, "full path"),
    ({"jobs": [{"kind": "copy", "sources": ["C:\\a"], "destination": "D:\\x",
                "into": ["E:\\y"]}]}, "not under"),
    ({"jobs": [{"kind": "copy", "sources": ["C:\\a"], "destination": "D:\\x",
                "into": ["D:\\x", "D:\\x"]}]}, "one folder per source"),
    ({"jobs": [{"kind": "copy", "sources": ["C:\\a"], "destination": "C:\\a\\in"}]},
     "into itself"),
    ({"jobs": [{"kind": "copy", "sources": ["C:\\a"], "destination": "D:\\x",
                "conflict": "clobber"}]}, "rule"),
])
def test_anything_the_preview_could_not_have_made_is_refused_whole(change, why):
    with pytest.raises(handoff.Refused, match=why):
        handoff.parse(request(**change), "C:\\x\\r.json")


def test_junk_is_refused():
    for data in (b"\xff\xfe", b"[1]", b"not json"):
        with pytest.raises(handoff.Refused):
            handoff.parse(data, "C:\\r.json")


def test_unc_paths_are_full_paths():
    assert handoff.absolute("\\\\server\\share\\Jobs")
    assert not handoff.absolute("\\\\server")
    assert handoff.absolute("S:\\")
    assert not handoff.absolute("S:")


def test_the_file_is_read_and_the_result_written_beside_it(tmp_path):
    path = tmp_path / "plan-1.json"
    path.write_bytes(request())
    got = handoff.read(str(path))
    assert got.path == str(path)
    handoff.write_result(str(path), {"version": 1, "copied": 2})
    assert json.loads((tmp_path / "plan-1.result.json").read_text()) == {
        "version": 1, "copied": 2}
    with pytest.raises(handoff.Refused, match="json"):
        handoff.read(str(tmp_path / "plan-1.txt"))
    with pytest.raises(handoff.Refused, match="could not read"):
        handoff.read(str(tmp_path / "missing.json"))


def test_the_outcome_adds_up_the_jobs():
    class State:
        def __init__(self, **kw):
            self.copied = kw.get("copied", 0)
            self.skipped = kw.get("skipped", 0)
            self.failed = kw.get("failed", 0)
            self.cancelled = kw.get("cancelled", False)
            self.refused = kw.get("refused", "")
            self.problems = kw.get("problems", [])

    got = handoff.outcome(None, [State(copied=3, skipped=1),
                                 State(failed=1, problems=["x: locked"])])
    assert got["copied"] == 3 and got["skipped"] == 1 and got["failed"] == 1
    assert got["problems"] == ["x: locked"] and not got["cancelled"]


def test_the_pipe_carries_a_queue_message_and_old_folder_messages_still_read():
    assert instance.decode_message(instance.encode_queue("C:\\r.json")) == {
        "queue": "C:\\r.json"}
    assert instance.decode_message(instance.encode("S:\\Jobs")) == {"open": "S:\\Jobs"}
    assert instance.decode(instance.encode_queue("C:\\r.json")) == ""
    assert instance.decode_message(b'{"queue": 5}') is None
    assert instance.decode_message(b'{"queue": ""}') is None


def test_the_command_line_takes_the_queue_file_out_of_the_folders():
    from app.__main__ import _queue_argument

    assert _queue_argument(["--queue", '"C:\\r.json"']) == ([], "C:\\r.json")
    assert _queue_argument(["S:\\Jobs", "--queue=C:\\r.json"]) == (["S:\\Jobs"],
                                                                   "C:\\r.json")
    assert _queue_argument(["S:\\Jobs"]) == (["S:\\Jobs"], "")


# ---------------------------------------------------------------- the window

@pytest.fixture
def window(tmp_path):
    from PySide6.QtWidgets import QApplication

    from app.core.capacity import Capacity
    from app.core.config import Config
    from app.core.favorites import Favorites
    from app.core.pane import Pane
    from app.core.transfers import TransferQueue
    from app.ui.window import MainWindow
    from tests.test_columns import FakeBridge
    from tests.test_window import FakeVolumes

    config = Config({"left.path": "C:\\Jobs", "right.path": "D:\\Archive"},
                    str(tmp_path / "config.json"))
    bridge = FakeBridge()
    bridge.retry = lambda path: None
    queue = TransferQueue()
    made = MainWindow(config, Pane(bridge, config, "left"), Pane(bridge, config, "right"),
                      FakeVolumes(), queue, None, Favorites(config),
                      Capacity(bridge, config))
    made.show()
    QApplication.processEvents()
    yield made, queue
    made.hide()


def test_the_window_queues_the_jobs_and_writes_the_outcome_when_they_end(
        window, tmp_path, monkeypatch):
    made, queue = window
    submitted = []

    def submit(kind, sources, destination, **extra):
        submitted.append((kind.value, list(sources), destination, extra))
        return len(submitted) + 100

    monkeypatch.setattr(queue._transfers, "submit", submit)  # noqa: SLF001
    path = tmp_path / "plan-2.json"
    path.write_bytes(request())
    got = handoff.read(str(path))
    made.queue_from_outside(got)
    assert [s[0] for s in submitted] == ["copy", "recycle"]
    assert submitted[0][3]["into"] == ("D:\\Jobs", "D:\\Jobs\\sub")
    assert submitted[0][3]["conflict"] is Conflict.OVERWRITE
    assert "File Compare" in made.statusBar().currentMessage()

    for job_id in list(queue.jobs):
        job = queue.jobs[job_id]
        job.copied = 2 if job.kind.value == "copy" else 0
        job.state = "done"
        made._on_transfer_finished(job)  # noqa: SLF001
    result = tmp_path / "plan-2.result.json"
    for _ in range(100):
        if result.exists():
            break
        time.sleep(0.02)
    assert json.loads(result.read_text())["copied"] == 2


def test_a_refused_request_says_why_and_queues_nothing(window, monkeypatch):
    made, queue = window
    monkeypatch.setattr(queue._transfers, "submit",  # noqa: SLF001
                        lambda *a, **k: pytest.fail("nothing is queued"))
    made.queue_from_outside(handoff.Refused("the request has no jobs"))
    assert "refused: the request has no jobs" in made.statusBar().currentMessage()

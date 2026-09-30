"""Undo (0.44): what is recorded, what is refused, and what undoing sends."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from app.core import undo
from app.io.protocol import JobKind


@dataclass
class Job:
    kind: JobKind
    sources: tuple
    destination: str
    rename: str = ""
    into: tuple = ()
    conflict: str = "ask"
    conflicts: int = 0
    cancelled: bool = False
    refused: str = ""
    skipped: int = 0
    failed: int = 0
    copied: int = 1
    problems: list = field(default_factory=list)


def test_a_clean_copy_is_undone_by_recycling_the_copies():
    action = undo.for_job(Job(JobKind.COPY, ("C:\\a\\x.txt", "C:\\a\\d"), "D:\\out"))
    assert action.kind == undo.COPY
    assert action.targets == ("D:\\out\\x.txt", "D:\\out\\d")
    assert action.label == "copy of 2 items"


def test_a_duplicate_and_a_sync_style_copy_use_their_own_names_and_folders():
    duplicate = undo.for_job(Job(JobKind.COPY, ("C:\\a\\2026-09-29",), "C:\\a",
                                 rename="2026-09-30"))
    assert duplicate.targets == ("C:\\a\\2026-09-30",)
    spread = undo.for_job(Job(JobKind.COPY, ("C:\\s\\x", "C:\\s\\y\\z"), "D:\\t",
                              into=("D:\\t", "D:\\t\\y")))
    assert spread.targets == ("D:\\t\\x", "D:\\t\\y\\z")


def test_a_move_is_undone_by_moving_back():
    action = undo.for_job(Job(JobKind.MOVE, ("C:\\a\\x.txt",), "D:\\out"))
    assert action.kind == undo.MOVE and action.moves == (("C:\\a\\x.txt", "D:\\out\\x.txt"),)


@pytest.mark.parametrize("change", [
    {"skipped": 1}, {"failed": 1}, {"cancelled": True}, {"refused": "no room"},
    {"conflicts": 1}, {"conflict": "overwrite"}, {"conflict": "rename"},
])
def test_a_job_that_met_anything_already_there_is_not_offered(change):
    job = Job(JobKind.COPY, ("C:\\a\\x.txt",), "D:\\out")
    for key, value in change.items():
        setattr(job, key, value)
    assert undo.for_job(job) is None


def test_deletes_are_not_offered():
    assert undo.for_job(Job(JobKind.RECYCLE, ("C:\\a\\x",), "")) is None


def test_renames_record_only_real_changes():
    assert undo.for_rename("C:\\a", [("x", "x")]) is None
    action = undo.for_rename("C:\\a", [("a.txt", "b.txt")])
    assert action.label == "rename of b.txt"


def test_the_stack_is_bounded_and_newest_first():
    stack = undo.UndoStack()
    for n in range(undo.DEPTH + 5):
        stack.push(undo.for_mkdir("C:\\a", f"f{n}"))
    assert len(stack) == undo.DEPTH
    assert stack.pop().name == f"f{undo.DEPTH + 4}"
    stack.push(None)
    assert len(stack) == undo.DEPTH - 1


def test_parent_and_leaf():
    assert undo.parent("C:\\a\\b.txt") == "C:\\a"
    assert undo.parent("C:\\b.txt") == "C:\\"
    assert undo.leaf("\\\\srv\\share\\x\\") == "x"


# ----------------------------------------------------------- queue and window

def test_a_job_remembers_what_undo_needs(monkeypatch):
    from app.core.transfers import TransferQueue
    from app.io.protocol import Event, Progress

    queue_ = TransferQueue()
    monkeypatch.setattr(queue_._transfers, "submit",  # noqa: SLF001
                        lambda kind, sources, destination, **extra: 41)
    job = queue_.jobs[queue_.duplicate("C:\\a\\x", "C:\\a", "y")]
    assert job.rename == "y" and job.conflict == "ask" and job.conflicts == 0
    queue_._deliver(Event(41, Progress.CONFLICT, {"name": "y"}))  # noqa: SLF001
    assert job.conflicts == 1
    moved = queue_.jobs[queue_.move_into(["D:\\x"], "C:\\a", ["C:\\a"])]
    assert moved.into == ("C:\\a",)


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
    made = MainWindow(config, Pane(bridge, config, "left"), Pane(bridge, config, "right"),
                      FakeVolumes(), TransferQueue(), None, Favorites(config),
                      Capacity(bridge, config))
    made.show()
    QApplication.processEvents()
    yield made, bridge
    made.hide()


def test_the_window_undoes_a_rename(window, monkeypatch):
    from app.io.protocol import Op
    from app.ui import dialogs

    made, bridge = window
    assert not made._undo_action.isEnabled()  # noqa: SLF001
    made._panes[0].undoable.emit(undo.for_rename("C:\\a", [("a.txt", "b.txt")]))  # noqa: SLF001
    assert made._undo_action.isEnabled()  # noqa: SLF001
    assert "rename of b.txt" in made._undo_action.text()  # noqa: SLF001
    monkeypatch.setattr(dialogs, "confirm", lambda *a, **k: True)
    made._undo_last()  # noqa: SLF001
    sent = [item for item in _sent(bridge) if item[0] is Op.RENAME_MANY][-1]
    assert sent[1] == "C:\\a" and sent[2]["steps"] == [["b.txt", "a.txt"]]
    assert len(made._undo) == 0  # noqa: SLF001


def test_a_declined_undo_stays_on_the_stack(window, monkeypatch):
    from app.ui import dialogs

    made, _bridge = window
    made._undo.push(undo.for_mkdir("C:\\a", "New"))  # noqa: SLF001
    monkeypatch.setattr(dialogs, "confirm_delete", lambda *a, **k: False)
    made._undo_last()  # noqa: SLF001
    assert len(made._undo) == 1  # noqa: SLF001


def _sent(bridge):
    """(op, path, args) for everything a test bridge was asked, whatever it keeps."""
    out = []
    for item in getattr(bridge, "sent", []):
        if isinstance(item, dict):
            out.append((item["op"], item["path"], item.get("args") or {}))
        else:
            op, path = item[0], item[1]
            args = item[2] if len(item) > 2 and isinstance(item[2], dict) else {}
            out.append((op, path, args))
    return out


# ------------------------------------------------------------ split and join

def test_split_then_join_gives_back_the_same_bytes(tmp_path):
    from app.io.protocol import JobKind, Progress
    from tests.test_ops import final

    data = bytes(range(256)) * 4000 + b"tail"
    source = tmp_path / "big.acd"
    source.write_bytes(data)
    parts = tmp_path / "parts"
    parts.mkdir()
    events = _run(JobKind.SPLIT, [str(source)], str(parts), part_size=300_000)
    assert final(events).payload["copied"] == 4 and final(events).payload["failed"] == 0
    assert sorted(p.name for p in parts.iterdir()) == [
        "big.acd.001", "big.acd.002", "big.acd.003", "big.acd.004"]
    out = tmp_path / "out"
    out.mkdir()
    events = _run(JobKind.JOIN, [str(parts / "big.acd.001")], str(out))
    assert final(events).payload["copied"] == 1
    assert (out / "big.acd").read_bytes() == data
    assert not any(p.name.endswith(".fm-part") for p in out.iterdir())


def test_split_and_join_refuse_to_overwrite(tmp_path):
    from app.io.protocol import JobKind
    from tests.test_ops import final

    source = tmp_path / "f.bin"
    source.write_bytes(b"x" * 1000)
    (tmp_path / "f.bin.002").write_bytes(b"mine")
    events = _run(JobKind.SPLIT, [str(source)], str(tmp_path), part_size=400)
    assert final(events).payload["failed"] == 1
    assert (tmp_path / "f.bin.002").read_bytes() == b"mine"
    assert not (tmp_path / "f.bin.001").exists()
    events = _run(JobKind.JOIN, [str(tmp_path / "f.bin.002")], str(tmp_path))
    assert any("first part" in (e.message or "") for e in events)


def test_the_dialog_counts_parts():
    from app.ui.split import MB, SplitDialog, part_count

    assert part_count(10, 4) == 3 and part_count(8, 4) == 2
    dialog = SplitDialog(None, name="big.acd", size=250 * MB, destination="D:\\\\out")
    assert dialog.part_size() == 100 * MB and dialog._ok.isEnabled()  # noqa: SLF001
    dialog._sizes.setCurrentIndex(dialog._sizes.count() - 1)  # noqa: SLF001 - Other
    dialog._custom.setValue(300)  # noqa: SLF001
    assert dialog.part_size() == 300 * MB and not dialog._ok.isEnabled()  # noqa: SLF001


def _run(kind, sources, destination, **extra):
    import queue as queue_module
    import time

    from app.io.ops import Transfers
    from app.io.protocol import Progress

    events: "queue_module.Queue" = queue_module.Queue()
    transfers = Transfers(events.put)
    collected = []
    try:
        transfers.submit(kind, sources, destination, **extra)
        deadline = time.monotonic() + 30
        while True:
            event = events.get(timeout=max(0.1, deadline - time.monotonic()))
            collected.append(event)
            if event.kind is Progress.DONE:
                return collected
    finally:
        transfers.shutdown()

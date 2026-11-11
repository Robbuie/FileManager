"""Shift+F5, the duplicate: the key, the name offered, and what reaches the queue.

Asked for by somebody who keeps a folder per day of PLC work and makes today's
by duplicating yesterday's. The engine half -- a whole copy, and never a merge
into a name already taken -- is in `test_ops.py`. This is the window half,
built on a real window for `test_tools.py`'s reason: a key is only covered by a
test that presses it, in both views.
"""

from __future__ import annotations

import datetime

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QObject, Qt, Signal  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.core.config import Config  # noqa: E402
from app.io.protocol import Entry, Status  # noqa: E402


class FakeBridge:
    def __init__(self):
        self.sent = []

    def submit(self, op, path, *, timeout, on_reply, args=None):
        self.sent.append(op)
        return len(self.sent)

    def cancel(self, request_id):
        pass

    def forget(self, request_id):
        pass


class FakeQueue:
    def __init__(self):
        self.duplicates = []
        self.rules = []
        self.cleans = []

    def duplicate(self, source, folder, name, **rules):
        self.duplicates.append((source, folder, name))
        self.rules.append(rules)
        return 1

    def clean(self, folders, patterns):
        self.cleans.append((list(folders), tuple(patterns)))
        return 2


def entry(name, *, is_dir=True):
    return Entry(name=name, is_dir=is_dir, size=0, mtime=100.0, attributes=0)


@pytest.fixture
def window(tmp_path):
    from app.core.capacity import Capacity
    from app.core.commands import Commands
    from app.core.favorites import Favorites
    from app.core.pane import Pane
    from app.core.transfers import TransferQueue
    from app.ui.window import MainWindow

    class Volumes(QObject):
        changed = Signal()
        drives = []

        def letter_for(self, path):
            return None

        def refresh(self, *, rescan=False):
            pass

    config = Config({"left.path": "C:\\PLC", "right.path": "D:\\Archive"},
                    str(tmp_path / "config.json"))
    bridge = FakeBridge()
    made = MainWindow(
        config,
        Pane(bridge, config, "left"),
        Pane(bridge, config, "right"),
        Volumes(), TransferQueue(), None, Favorites(config),
        Capacity(bridge, config), Commands(bridge, config))
    made.resize(1200, 600)
    made.show()
    QApplication.processEvents()
    yield made
    made.hide()


def prepare(window, monkeypatch, rows, *, cursor, answer):
    """A pane on `rows` with the cursor on one, a queue that records, and a
    dialog that answers `answer` and remembers what it was offered."""
    from app.ui import dialogs

    widget = window._widgets[0]
    pane = widget._pane
    model = pane.current.model
    model.begin(has_parent=False)
    model.add(list(rows))
    model.finish()
    queue = FakeQueue()
    pane.transfers = queue
    names = [row.name for row in rows]
    widget._view.setCurrentIndex(model.index(names.index(cursor), 0))

    offered = {}

    def ask_name(parent, **kwargs):
        offered.update(kwargs)
        return answer if answer is not None else kwargs["initial"]

    monkeypatch.setattr(dialogs, "ask_name", ask_name)
    return widget, queue, offered


def press(target, key, modifiers=Qt.NoModifier):
    QApplication.sendEvent(target, QKeyEvent(QKeyEvent.KeyPress, key, modifiers, ""))


def test_shift_f5_offers_today_and_queues_the_copy(window, monkeypatch) -> None:
    widget, queue, offered = prepare(
        window, monkeypatch, [entry("Line3 2020-01-01"), entry("Line3 2020-01-02")],
        cursor="Line3 2020-01-02", answer=None)
    today = datetime.date.today().isoformat()

    press(widget._view, Qt.Key_F5, Qt.ShiftModifier)

    assert offered["initial"] == f"Line3 {today}"
    assert len(queue.duplicates) == 1
    source, folder, name = queue.duplicates[0]
    assert name == f"Line3 {today}"
    assert source.endswith("Line3 2020-01-02")
    assert folder == widget._pane.current.path


def test_shift_f5_in_the_grid_does_the_same(window, monkeypatch) -> None:
    widget, queue, _ = prepare(window, monkeypatch, [entry("Job")],
                               cursor="Job", answer="Job two")
    press(widget._grid, Qt.Key_F5, Qt.ShiftModifier)
    assert [d[2] for d in queue.duplicates] == ["Job two"]


def test_plain_f5_is_still_the_copy_to_the_other_pane(window, monkeypatch) -> None:
    widget, queue, _ = prepare(window, monkeypatch, [entry("Job")],
                               cursor="Job", answer="Job two")
    seen = []
    # The window's own handler puts a modal prompt up, which offscreen is a
    # test that never ends; what is being checked is only which request it is.
    widget.transferRequested.disconnect()
    widget.transferRequested.connect(seen.append)
    press(widget._view, Qt.Key_F5)
    assert seen == ["copy"]
    assert queue.duplicates == []


def test_the_dialog_is_told_which_names_are_taken(window, monkeypatch) -> None:
    widget, _, offered = prepare(window, monkeypatch,
                                 [entry("Job"), entry("job - Copy")],
                                 cursor="Job", answer="")
    press(widget._view, Qt.Key_F5, Qt.ShiftModifier)
    taken = offered["taken"]
    assert taken("JOB - COPY") and taken("Job")
    assert not taken("Job - Copy (2)")


def test_a_cancelled_dialog_queues_nothing(window, monkeypatch) -> None:
    widget, queue, _ = prepare(window, monkeypatch, [entry("Job")],
                               cursor="Job", answer="")
    press(widget._view, Qt.Key_F5, Qt.ShiftModifier)
    assert queue.duplicates == []


def test_the_name_prompt_refuses_a_name_in_use() -> None:
    from app.ui.dialogs import NamePrompt

    prompt = NamePrompt(None, title="Duplicate", label="as", initial="Job",
                        taken=lambda name: name.lower() == "job")
    assert not prompt._ok.isEnabled()
    prompt._field.setText("Job 2")
    assert prompt._ok.isEnabled()
    prompt.deleteLater()


def test_ctrl_shift_f5_is_duplicate_logix(window, monkeypatch) -> None:
    """0.59: the day in the old name and the new go to the engine, with the
    backups to leave out; plain Shift+F5 sends neither."""
    widget, queue, offered = prepare(window, monkeypatch, [entry("2026-11-10")],
                                     cursor="2026-11-10", answer="2026-11-11")
    press(widget._view, Qt.Key_F5, Qt.ShiftModifier | Qt.ControlModifier)

    assert offered["title"] == "Duplicate Logix"
    assert [d[2] for d in queue.duplicates] == ["2026-11-11"]
    assert queue.rules == [{"redate": ("2026-11-10", "2026-11-11"),
                            "skip": ("*.BAK*.acd", "*.Sem", "*.Wrk")}]

    press(widget._view, Qt.Key_F5, Qt.ShiftModifier)
    assert queue.rules[-1] == {}


def test_duplicate_logix_without_a_date_still_leaves_the_backups(window, monkeypatch) -> None:
    widget, queue, _ = prepare(window, monkeypatch, [entry("Line 3")],
                               cursor="Line 3", answer="Line 3 copy")
    press(widget._view, Qt.Key_F5, Qt.ShiftModifier | Qt.ControlModifier)
    assert queue.rules == [{"skip": ("*.BAK*.acd", "*.Sem", "*.Wrk")}]


def test_duplicate_logix_clears_the_backups_from_the_original(window, monkeypatch) -> None:
    """0.60: queued after the copy, on the folder copied from."""
    widget, queue, _ = prepare(window, monkeypatch, [entry("2026-11-10")],
                               cursor="2026-11-10", answer="2026-11-11")
    press(widget._view, Qt.Key_F5, Qt.ShiftModifier | Qt.ControlModifier)
    source = queue.duplicates[0][0]
    assert queue.cleans == [([source], ("*.BAK*.acd", "*.Sem", "*.Wrk"))]

    press(widget._view, Qt.Key_F5, Qt.ShiftModifier)
    assert len(queue.cleans) == 1, "plain Duplicate cleaned something"


def test_the_original_is_left_alone_with_the_option_off(window, monkeypatch) -> None:
    widget, queue, _ = prepare(window, monkeypatch, [entry("2026-11-10")],
                               cursor="2026-11-10", answer="2026-11-11")
    widget._pane._config.set("duplicate.logix_clean_original", False)
    press(widget._view, Qt.Key_F5, Qt.ShiftModifier | Qt.ControlModifier)
    assert len(queue.duplicates) == 1
    assert queue.cleans == []


def test_clean_up_works_on_the_marked_folder_or_this_one(window, monkeypatch) -> None:
    from app.ui import dialogs

    widget, queue, _ = prepare(window, monkeypatch,
                               [entry("2026-11-10"), entry("plan.ACD", is_dir=False)],
                               cursor="2026-11-10", answer=None)
    asked = []
    monkeypatch.setattr(dialogs, "confirm", lambda parent, **kw: asked.append(kw) or True)
    pane = widget._pane

    widget.clean_backups()
    assert queue.cleans[-1][0] == [pane.row_path(pane.current.model.row_of("2026-11-10"))]

    widget._view.setCurrentIndex(pane.current.model.index(
        pane.current.model.row_of("plan.ACD"), 0))
    widget.clean_backups()
    assert queue.cleans[-1][0] == [pane.current.path]
    assert len(asked) == 2 and asked[0]["action"] == "Clean up"


def test_a_declined_clean_up_queues_nothing(window, monkeypatch) -> None:
    from app.ui import dialogs

    widget, queue, _ = prepare(window, monkeypatch, [entry("2026-11-10")],
                               cursor="2026-11-10", answer=None)
    monkeypatch.setattr(dialogs, "confirm", lambda parent, **kw: False)
    widget.clean_backups()
    assert queue.cleans == []

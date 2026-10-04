"""The F3 Logix browser (0.55): the rung parser, the export's logic read
whole, the worker's op, and the browser over a stand-in for the worker."""

from __future__ import annotations

import queue
import time

import pytest

from app.core import ladder
from app.core.ladder import Branch, Instr
from app.io import logix, worker
from app.io.protocol import Op, Request, Status
from tests.test_logix import EXPORT, L5K


# ------------------------------------------------------------------ parser

def test_series_and_symbols():
    nodes = ladder.parse("XIC(Start)XIO(Fault)OTE(Run);")
    assert nodes == [Instr("XIC", ["Start"]), Instr("XIO", ["Fault"]), Instr("OTE", ["Run"])]
    assert [n.symbol for n in nodes] == ["] [", "]/[", "( )"]


def test_branches_nest_and_operands_keep_their_brackets():
    nodes = ladder.parse("[XIC(A) ,XIC(B)[XIC(C),XIO(D)] ]MOV(Tag[3],Out[i])CPT(X,A+(B*C));")
    branch = nodes[0]
    assert isinstance(branch, Branch) and len(branch.paths) == 2
    assert isinstance(branch.paths[1][1], Branch)
    assert nodes[1] == Instr("MOV", ["Tag[3]", "Out[i]"])
    assert nodes[2] == Instr("CPT", ["X", "A+(B*C)"])
    assert ladder.tags_in(nodes)[:4] == ["A", "B", "C", "D"]


def test_what_it_cannot_read_is_none_not_a_guess():
    for text in ("XIC(A", "XIC(A)]", "XIC A;", "XIC(A);OTE(B);"):
        assert ladder.parse(text) is None, text
    assert ladder.parse("") == [] and ladder.parse("NOP();") == [Instr("NOP", [])]


# ------------------------------------------------------------------ reading

@pytest.fixture
def export(tmp_path):
    logix._loaded.clear()
    path = tmp_path / "RPS_Main.L5X"
    path.write_text(EXPORT, encoding="utf-8")
    return str(path)


@pytest.fixture
def l5k(tmp_path):
    logix._loaded.clear()
    path = tmp_path / "RPS_Main.L5K"
    path.write_text(L5K, encoding="utf-8")
    return str(path)


def test_the_outline_lists_programs_then_aois(export):
    outline = logix.load(export, time.monotonic() + 10).outline()
    assert outline["controller"] == "RPS_Main"
    assert [(c["name"], c["kind"]) for c in outline["containers"]] == [
        ("MainProgram", "program"), ("PID_Loops", "program"), ("Motor", "aoi")]
    assert outline["containers"][0]["routines"][0]["count"] == 2


def test_a_routine_comes_back_with_its_rungs(export):
    project = logix.load(export, time.monotonic() + 10)
    routine = project.routine("MainProgram", "Main")
    assert routine["kind"] == "RLL"
    assert routine["rungs"][0] == [0, "", "XIC(A)OTE(B);"]
    assert project.routine("Nowhere", "Main")["missing"]


def test_search_finds_every_place(export):
    found = logix.load(export, time.monotonic() + 10).search("otE(b")
    assert found["hits"] == [{"program": "MainProgram", "routine": "Main", "at": 0}]
    assert logix.load(export, time.monotonic() + 10).search("  ")["hits"] == []


def test_the_export_is_read_once_and_kept(export, monkeypatch):
    first = logix.load(export, time.monotonic() + 10)
    monkeypatch.setattr(logix, "_load_l5x", lambda *a: pytest.fail("read again"))
    assert logix.load(export, time.monotonic() + 10) is first


def test_an_l5k_reads_the_same_way(l5k):
    project = logix.load(l5k, time.monotonic() + 10)
    routine = project.routine("MainProgram", "Main")
    assert [r[2] for r in routine["rungs"]] == ["XIC(A)OTE(B);", "NOP();"]
    assert routine["rungs"][0][1] == "First rung"


def test_something_else_is_refused(tmp_path):
    logix._loaded.clear()
    path = tmp_path / "x.L5X"
    path.write_text("<other/>", encoding="utf-8")
    with pytest.raises(logix.NotAnExport):
        logix.load(str(path), time.monotonic() + 10)


def _ask(path, **args):
    outbox: queue.Queue = queue.Queue()
    worker._handle(Request(1, Op.LOGIX, str(path), 10.0, args), outbox, queue.Queue(), set())
    return outbox.get()


def test_the_worker_answers_all_three_questions(export):
    assert _ask(export, what="outline").payload["controller"] == "RPS_Main"
    assert _ask(export, what="routine", program="MainProgram",
                routine="Main").payload["rungs"][1][2] == ""
    assert len(_ask(export, what="search", text="XIC").payload["hits"]) == 1


def test_inside_an_archive_it_says_no(tmp_path):
    import zipfile

    archive = tmp_path / "x.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("a.L5X", EXPORT)
    reply = _ask(f"{archive}\\a.L5X", what="outline")
    assert reply.status is Status.ERROR


# ------------------------------------------------------------------ browser

def test_the_browser_walks_outline_routine_and_hits(export):
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QApplication

    from app.ui.logixbrowser import LogixBrowser

    app = QApplication.instance() or QApplication([])

    class Fake(QObject):
        outlineReady = Signal(str, object)
        routineReady = Signal(str, object)
        hitsReady = Signal(str, object)
        failed = Signal(str, str)

        def project(self):
            return logix.load(export, time.monotonic() + 10)

        def outline(self, path):
            self.outlineReady.emit(path, self.project().outline())

        def routine(self, path, program, routine):
            self.routineReady.emit(path, self.project().routine(program, routine))

        def search(self, path, text):
            self.hitsReady.emit(path, self.project().search(text))

        def cancel(self):
            pass

    browser = LogixBrowser(Fake(), {})
    browser.open(export, logix.summarise(export, time.monotonic() + 10))
    assert ("MainProgram", "Main") in browser._items
    browser._find.setText("OTE(B)")
    browser._on_find()
    app.processEvents()
    assert browser._showing == ("MainProgram", "Main")
    assert browser._right.currentWidget() is browser._ladder_area
    assert "1 of 1" in browser._found.text()
    browser._as_text.setChecked(True)
    assert "XIC(A)OTE(B);" in browser._text.toPlainText()


def test_the_logix_op_is_asked_through_the_side_lane():
    from app.io.pool import SIDE_OPS

    assert Op.LOGIX in SIDE_OPS


def test_f3_on_an_export_opens_the_browser_and_asks_for_the_outline(export, tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from app.core.config import Config
    from app.core.previews import Previews
    from app.io import decode
    from app.ui.viewer import Viewer
    from tests.test_previews import FakeBridge

    QApplication.instance() or QApplication([])
    bridge = FakeBridge()
    viewer = Viewer(Previews(bridge, Config({}, str(tmp_path / "c.json"))), {})
    import os

    folder, name = os.path.split(export)
    viewer._folder, viewer._names, viewer._at = folder, [name], 0
    answer = decode.preview(export, box=256, deadline=time.monotonic() + 10, logix=True)
    viewer._on_ready(viewer.path, answer)
    assert viewer._stack.currentWidget() is viewer._browser
    asked = [sent for sent in bridge.sent if sent["op"] is Op.LOGIX]
    assert asked and asked[-1]["args"]["what"] == "outline"

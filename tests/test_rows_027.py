"""0.27: type badges, the folder header, the history dropdown and USB eject.

The eject call itself is Windows-only and is tried by hand. Everything that
decides whether it has a chance -- which panes move, which transfers refuse
it, the sentence a veto becomes -- is checked here.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QObject, QPointF, Qt, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.core import eject as core_eject  # noqa: E402
from app.core import filetypes  # noqa: E402
from app.core.config import Config  # noqa: E402
from app.core.pane import Pane  # noqa: E402
from app.io.eject import veto_sentence  # noqa: E402
from app.io.protocol import Entry, Op, Reply, Status  # noqa: E402
from app.theme import qss, sheet  # noqa: E402
from app.ui.header import FolderHeader  # noqa: E402
from app.ui.rail import DriveRow  # noqa: E402

from tests.test_window import FakeBridge  # noqa: E402


def _entry(name, size=0, is_dir=False):
    return Entry(name=name, is_dir=is_dir, size=size, mtime=0.0, attributes=0)


# ------------------------------------------------------------------ families

def test_plc_hmi_and_cad_files_have_their_own_families():
    assert filetypes.family("RPS_Main_v14.ACD") == "logix"
    assert filetypes.family("export.L5X") == "logix"
    assert filetypes.family("Station.mer") == "hmi"
    assert filetypes.family("Station.apa") == "hmi"
    assert filetypes.family("P&ID-101.dwg") == "cad"
    assert filetypes.family("notes") == "other"
    assert filetypes.family("Logix", is_dir=True) == filetypes.FOLDER


def test_a_leading_dot_is_a_name_not_an_extension():
    assert filetypes.extension(".gitignore") == ""
    assert filetypes.tag(".gitignore") == "FILE"


def test_the_tag_is_the_extension_in_capitals_cut_to_four():
    assert filetypes.tag("drawing.dwg") == "DWG"
    assert filetypes.tag("sheet.xlsx") == "XLSX"
    assert filetypes.tag("solution.ccwsln") == "CCWS"


def test_composition_is_by_bytes_largest_first_and_skips_folders():
    parts = filetypes.composition([
        _entry("HMI", is_dir=True),
        _entry("a.apa", 40_000_000),
        _entry("b.ACD", 18_000_000),
        _entry("c.txt", 3_000),
        _entry("d.txt", 3_000),
    ])
    assert [kind for kind, _b, _c in parts] == ["hmi", "logix", "text"]
    assert parts[2] == ("text", 6_000, 2)


def test_a_folder_of_empty_files_is_counted_instead():
    parts = filetypes.composition([_entry("a.pdf"), _entry("b.pdf"), _entry("c.dwg")])
    assert parts[0] == ("pdf", 0, 2)


def test_every_family_has_badge_colours_in_every_theme():
    for theme in ("dark", "light", "paper", "blueprint", "contrast"):
        tokens = qss.build(theme)
        for kind in (*filetypes.FAMILIES, filetypes.FOLDER):
            assert tokens[f"kind_{kind}"].startswith("#")
            assert tokens[f"kind_{kind}_fill"].startswith("rgba(")


# -------------------------------------------------------------------- header

class FakeModel(QObject):
    modelReset = Signal()
    layoutChanged = Signal()
    rowsInserted = Signal()
    rowsRemoved = Signal()

    def __init__(self, rows):
        super().__init__()
        self.rows = rows

    def entries(self):
        return list(self.rows)


def test_the_header_counts_what_the_listing_holds_and_follows_it():
    header = FolderHeader()
    header.apply_tokens(sheet.tokens())
    model = FakeModel([_entry("Logix", is_dir=True), _entry("a.ACD", 1024)])
    header.follow(model, "2026-09-16")
    assert (header.folders, header.files, header.bytes) == (1, 1, 1024)
    assert "1 folder" in header.summary() and "1 file" in header.summary()
    model.rows.append(_entry("b.mer", 2048))
    model.rowsInserted.emit()
    header.recount()  # the timer's job, done now
    assert header.files == 2
    assert header.parts[0][0] == "hmi"


# ------------------------------------------------------------------- history

def test_the_history_dropdown_jumps_straight_to_a_step(tmp_path):
    config = Config({"left.path": "C:\\A"}, str(tmp_path / "c.json"))
    pane = Pane(FakeBridge(), config, "left")
    pane.navigate("C:\\B")
    pane.navigate("C:\\C")
    pane.go_to_history(0)
    assert pane.current.path == "C:\\A"
    assert pane.current.position == 0
    assert pane.current.can_go_forward


# --------------------------------------------------------------------- eject

def test_a_path_is_on_a_drive_only_by_its_letter():
    assert core_eject.on_drive("E:\\Jobs", "E:")
    assert core_eject.on_drive("e:\\", "E:")
    assert not core_eject.on_drive("S:\\E:", "E:")
    assert not core_eject.on_drive("\\\\server\\E", "E:")


def test_a_transfer_touching_the_drive_is_found_either_way_round():
    to_it = SimpleNamespace(destination="E:\\Backup", sources=("C:\\Jobs\\a",))
    from_it = SimpleNamespace(destination="D:\\", sources=("E:\\photo.jpg",))
    elsewhere = SimpleNamespace(destination="D:\\", sources=("C:\\x",))
    assert core_eject.jobs_on("E:", [to_it, from_it, elsewhere]) == [to_it, from_it]


def test_a_moved_pane_goes_to_a_fixed_disk_that_is_not_being_ejected():
    drives = [{"letter": "E:", "type": "fixed", "ejectable": True},
              {"letter": "D:", "type": "fixed", "ejectable": False}]
    assert core_eject.refuge("E:", drives) == "D:\\"
    assert core_eject.refuge("E:", []) == "C:\\"


def test_a_veto_names_the_program_holding_the_drive():
    assert veto_sentence("E:", 3, "C:\\Program Files\\Office\\EXCEL.EXE") == \
        "E: was not ejected: EXCEL.EXE is using it"
    assert "service" in veto_sentence("E:", 4, "")
    assert "still open" in veto_sentence("E:", 5, "")


def test_eject_refuses_while_a_transfer_is_using_the_drive(tmp_path):
    config = Config({}, str(tmp_path / "c.json"))
    bridge = FakeBridge()
    ejector = core_eject.Ejector(bridge, config)
    transfers = SimpleNamespace(active=[SimpleNamespace(destination="E:\\x", sources=())])
    why = ejector.eject("E:", [], transfers, SimpleNamespace(drives=[]))
    assert "1 transfer" in why
    assert not bridge.sent


def test_eject_moves_panes_off_the_drive_then_asks_the_local_worker(tmp_path, qtbot=None):
    config = Config({"left.path": "E:\\Jobs"}, str(tmp_path / "c.json"))
    bridge = FakeBridge()
    pane = Pane(bridge, config, "left")
    ejector = core_eject.Ejector(bridge, config)
    why = ejector.eject("E:", [pane], SimpleNamespace(active=[]),
                        SimpleNamespace(drives=[{"letter": "C:", "type": "fixed"}]))
    assert why == ""
    assert pane.current.path == "C:\\"
    ejector._send()  # noqa: SLF001 - the timer's job
    assert bridge.sent[-1] == (Op.EJECT, "")
    results = []
    ejector.finished.connect(lambda message, ok: results.append((message, ok)))
    ejector._on_reply(Reply(1, Status.ERROR, message="E: was not ejected: x"))  # noqa: SLF001
    assert results == [("E: was not ejected: x", False)]
    assert not ejector.busy


def test_the_eject_button_is_offered_only_on_an_ejectable_drive():
    tokens = sheet.tokens()
    usb = DriveRow("E:", "", "removable", tokens=tokens, ejectable=True)
    usb.resize(220, 30)
    asked = []
    usb.ejectRequested.connect(asked.append)
    chosen = []
    usb.chosen.connect(lambda path, tab: chosen.append(path))
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QEvent

    centre = usb.eject_rect().center()
    press = QMouseEvent(QEvent.MouseButtonRelease, QPointF(centre), QPointF(centre),
                        Qt.LeftButton, Qt.NoButton, Qt.NoModifier)
    usb.mouseReleaseEvent(press)
    assert asked == ["E:"] and not chosen
    QApplication.processEvents()

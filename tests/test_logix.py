"""Checks on reading a Logix export for the preview (0.36).

The export here is a small hand-written one in the shape Logix Designer
writes: the root with its revision attributes, a controller, data types,
modules, an AOI, controller tags, programs with routines and rungs, and
tasks scheduling them.
"""

from __future__ import annotations

import time

import pytest

from app.io import decode, logix
from app.io.protocol import PreviewForm

EXPORT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<RSLogix5000Content SchemaRevision="1.0" SoftwareRevision="33.01" TargetName="RPS_Main"
 TargetType="Controller" ContainsContext="false" ExportDate="Mon Sep 28 14:12:03 2026">
<Controller Use="Target" Name="RPS_Main" ProcessorType="1756-L83E" MajorRev="33" MinorRev="11">
<DataTypes><DataType Name="PumpUDT"/></DataTypes>
<Modules>
 <Module Name="Local" CatalogNumber="1756-L83E"/>
 <Module Name="ENET_Plant" CatalogNumber="1756-EN2T"/>
 <Module Name="DI_Pumps" CatalogNumber="1756-IB32"/>
</Modules>
<AddOnInstructionDefinitions>
 <AddOnInstructionDefinition Name="Motor"><Routines><Routine Name="Logic"/></Routines></AddOnInstructionDefinition>
</AddOnInstructionDefinitions>
<Tags><Tag Name="A"/><Tag Name="B"/></Tags>
<Programs>
 <Program Name="MainProgram"><Tags><Tag Name="C"/></Tags>
  <Routines><Routine Name="Main"><RLLContent><Rung Number="0"><Text><![CDATA[XIC(A)OTE(B);]]></Text></Rung><Rung Number="1"/></RLLContent></Routine></Routines>
 </Program>
 <Program Name="PID_Loops"><Routines><Routine Name="Main"/></Routines></Program>
</Programs>
<Tasks>
 <Task Name="MainTask" Type="CONTINUOUS"><ScheduledPrograms><ScheduledProgram Name="MainProgram"/></ScheduledPrograms></Task>
 <Task Name="Periodic_100ms" Type="PERIODIC" Rate="100"><ScheduledPrograms><ScheduledProgram Name="PID_Loops"/></ScheduledPrograms></Task>
</Tasks>
</Controller>
</RSLogix5000Content>
"""


@pytest.fixture
def export(tmp_path):
    path = tmp_path / "RPS_Main.L5X"
    path.write_text(EXPORT, encoding="utf-8")
    return str(path)


def test_the_summary_counts_what_is_there(export):
    summary = logix.summarise(export, time.monotonic() + 10)
    assert summary["controller"] == "RPS_Main"
    assert summary["processor"] == "1756-L83E"
    assert summary["firmware"] == "33.11"
    assert summary["software"] == "33.01"
    assert summary["tags"] == 3
    assert summary["programs"] == 2
    assert summary["routines"] == 3
    assert summary["rungs"] == 2
    assert summary["aois"] == 1
    assert summary["modules"] == 3
    assert summary["udts"] == 1
    assert [t["name"] for t in summary["tasks"]] == ["MainTask", "Periodic_100ms"]
    assert summary["tasks"][1]["programs"] == ["PID_Loops"]
    assert not summary["partial"]


def test_the_rendering_names_the_things_that_matter(export):
    text = logix.render(logix.summarise(export, time.monotonic() + 10))
    for expected in ("RPS_Main", "1756-L83E", "33.11", "Logix Designer 33.01",
                     "Tags 3", "Periodic_100ms  (periodic, 100 ms)",
                     "ENET_Plant", "1756-EN2T"):
        assert expected in text, expected


def test_xml_that_is_not_an_export_is_not_read_as_one(tmp_path):
    path = tmp_path / "other.l5x"
    path.write_text("<root><Tag/></root>", encoding="utf-8")
    assert logix.summarise(str(path), time.monotonic() + 10) is None


def test_a_deadline_gives_partial_counts(export):
    summary = logix.summarise(export, time.monotonic() - 1)
    assert summary["partial"] is True


def test_the_decoder_uses_it_only_when_asked(export):
    asked = decode.preview(export, box=200, deadline=time.monotonic() + 10,
                           logix=True)
    assert asked.form is PreviewForm.TEXT and asked.source == "logix"
    plain = decode.preview(export, box=200, deadline=time.monotonic() + 10)
    assert plain.source != "logix"
    assert "RSLogix5000Content" in plain.text


def test_a_broken_export_falls_back_to_text(tmp_path):
    path = tmp_path / "broken.L5X"
    path.write_text("<RSLogix5000Content><Controller Name='x'>", encoding="utf-8")
    answer = decode.preview(str(path), box=200, deadline=time.monotonic() + 10,
                            logix=True)
    assert answer.form is PreviewForm.TEXT


# 0.40: the same project as an .L5K text export, in the shape Logix Designer
# writes it -- header comment, controller attributes over several lines,
# blocks closed by END_ keywords.
L5K = '''(*********************************************

  Import-Export
  Version   := RSLogix 5000 v33.01
  Owner     := Plant Controls,
  Exported  := Mon Sep 28 14:12:03 2026

  Note:  File encoded in UTF-8.  Only edit file in a program
         which supports UTF-8 (like Notepad, not Wordpad).

**********************************************)
IE_VER := 2.29;

CONTROLLER RPS_Main (ProcessorType := "1756-L83E",
                     Major := 33,
                     TimeSlice := 20,
                     ShareUnusedTimeSlice := 1)
	DATATYPE PumpUDT (FamilyType := NoFamily)
		DINT Speed;
	END_DATATYPE

	MODULE Local (Parent := "Local",
	              ParentModPortId := 1,
	              CatalogNumber := "1756-L83E",
	              Vendor := 1)
	END_MODULE

	MODULE ENET_Plant (Parent := "Local",
	                   CatalogNumber := "1756-EN2T",
	                   Vendor := 1)
	END_MODULE

	MODULE DI_Pumps (CatalogNumber := "1756-IB32", Vendor := 1)
	END_MODULE

	ADD_ON_INSTRUCTION_DEFINITION Motor (Revision := 1.0)
		ROUTINE Logic
		END_ROUTINE
	END_ADD_ON_INSTRUCTION_DEFINITION

	TAG
		A : DINT (RADIX := Decimal) := 0;
		B : BOOL (RADIX := Decimal,
		          Description := "Pump running") := 0;
	END_TAG

	PROGRAM MainProgram (MainRoutineName := "Main",
	                     Disabled := No)
		TAG
			C : DINT (RADIX := Decimal) := 0;
		END_TAG

		ROUTINE Main
				RC: "First rung";
				N: XIC(A)OTE(B);
				N: NOP();
		END_ROUTINE

	END_PROGRAM

	PROGRAM PID_Loops (MainRoutineName := "Main")
		ROUTINE Main
		END_ROUTINE
	END_PROGRAM

	TASK MainTask (Type := CONTINUOUS,
	               Priority := 10)
			MainProgram;
	END_TASK

	TASK Periodic_100ms (Type := PERIODIC, Rate := 100, Priority := 10)
			PID_Loops;
	END_TASK

END_CONTROLLER
'''


@pytest.fixture
def l5k(tmp_path):
    path = tmp_path / "RPS_Main.L5K"
    path.write_text(L5K, encoding="utf-8")
    return str(path)


def test_an_l5k_gives_the_same_summary_as_the_l5x(l5k, export):
    text = logix.summarise_l5k(l5k, time.monotonic() + 10)
    xml = logix.summarise(export, time.monotonic() + 10)
    for key in ("controller", "processor", "tags", "programs", "routines", "aois",
                "udts", "modules", "rungs"):
        assert text[key] == xml[key], key
    assert text["firmware"] == "33"
    assert text["software"] == "33.01"
    assert text["exported"] == "Mon Sep 28 14:12:03 2026"
    assert text["module_list"] == [("Local", "1756-L83E"), ("ENET_Plant", "1756-EN2T"),
                                   ("DI_Pumps", "1756-IB32")]
    assert [(t["name"], t["type"], t["rate"], t["programs"]) for t in text["tasks"]] == [
        ("MainTask", "continuous", "", ["MainProgram"]),
        ("Periodic_100ms", "periodic", "100", ["PID_Loops"]),
    ]


def test_a_text_file_that_is_not_an_l5k_is_left_alone(tmp_path):
    path = tmp_path / "notes.l5k"
    path.write_text("just some notes\nabout a controller\n", encoding="utf-8")
    assert logix.summarise_l5k(str(path), time.monotonic() + 10) is None


def test_the_preview_shows_the_l5k_summary_when_asked(l5k):
    shown = decode.preview(l5k, box=256, deadline=time.monotonic() + 10, logix=True)
    assert shown.form == PreviewForm.TEXT
    assert shown.source == "logix"
    assert "1756-L83E" in shown.text and "Periodic_100ms" in shown.text


# ------------------------------------------------------------------ 0.53 card

CHASSIS = EXPORT.replace(
    '<Controller Use="Target" Name="RPS_Main" ProcessorType="1756-L83E" MajorRev="33" MinorRev="11">',
    '<Controller Use="Target" Name="RPS_Main" ProcessorType="1756-L83E" MajorRev="33" '
    'MinorRev="11" CommPath="AB_ETHIP-1\\10.1.1.5\\Backplane\\0" '
    'LastModificationDate="Thu Sep 24 13:58:40 2026">',
).replace(
    """ <Module Name="Local" CatalogNumber="1756-L83E"/>
 <Module Name="ENET_Plant" CatalogNumber="1756-EN2T"/>
 <Module Name="DI_Pumps" CatalogNumber="1756-IB32"/>""",
    """ <Module Name="Local" CatalogNumber="1756-L83E/B" ParentModule="Local"><Ports>
   <Port Id="1" Address="0" Type="ICP" Upstream="false"><Bus Size="7"/></Port></Ports></Module>
 <Module Name="ENET_Plant" CatalogNumber="1756-EN2T/D" ParentModule="Local"><Ports>
   <Port Id="1" Address="2" Type="ICP" Upstream="true"/>
   <Port Id="2" Address="10.1.1.6" Type="Ethernet" Upstream="false"/></Ports></Module>
 <Module Name="DI_Pumps" CatalogNumber="1756-IB32/B" ParentModule="Local"><Ports>
   <Port Id="1" Address="3" Type="ICP" Upstream="true"/></Ports></Module>
 <Module Name="Drive1" CatalogNumber="20-750-ENETR" ParentModule="ENET_Plant"><Ports>
   <Port Id="2" Address="10.1.1.21" Type="Ethernet" Upstream="true"/></Ports></Module>
 <Module Name="Rack1" CatalogNumber="1734-AENTR/B" ParentModule="ENET_Plant"><Ports>
   <Port Id="1" Address="10.1.1.40" Type="Ethernet" Upstream="true"/>
   <Port Id="2" Address="0" Type="PointIO" Upstream="false"/></Ports></Module>
 <Module Name="Rack1_IB8" CatalogNumber="1734-IB8/C" ParentModule="Rack1"><Ports>
   <Port Id="1" Address="1" Type="PointIO" Upstream="true"/></Ports></Module>""")


@pytest.fixture
def chassis_export(tmp_path):
    path = tmp_path / "RPS_Main.L5X"
    path.write_text(CHASSIS, encoding="utf-8")
    return str(path)


def test_the_summary_carries_what_the_card_draws(chassis_export):
    from app.core import logixview

    summary = logix.summarise(chassis_export, time.monotonic() + 10)
    assert logixview.address(summary["comm_path"]) == "10.1.1.5, slot 0"
    assert logixview.when(summary["modified"]) == "24 Sep 2026 13:58"
    programs = {p["name"]: p for p in summary["program_list"]}
    assert programs["MainProgram"]["routines"] == 1 and programs["MainProgram"]["rungs"] == 2
    assert logixview.program_line(programs["MainProgram"]) == "1 routine · 2 rungs"
    assert summary["aoi_list"] == [("Motor", "")] and summary["udt_list"] == ["PumpUDT"]

    slots = logixview.chassis(summary)
    assert len(slots) == 7
    assert (slots[0].controller, slots[0].kind) == (True, "controller")
    assert slots[1].empty
    assert (slots[2].name, slots[2].kind) == ("ENET_Plant", "Ethernet")
    assert logixview.short_catalog(slots[3].catalog) == "IB32" and slots[3].kind == "32 in"

    [network] = logixview.networks(summary)
    assert network.via == "ENET_Plant"
    assert [(d.name, d.address, d.children) for d in network.devices] == [
        ("Drive1", "10.1.1.21", 0), ("Rack1", "10.1.1.40", 1)]


def test_the_preview_hands_the_summary_over_as_details(chassis_export):
    shown = decode.preview(chassis_export, box=256, deadline=time.monotonic() + 10, logix=True)
    assert shown.details["kind"] == "logix"
    assert shown.details["controller"] == "RPS_Main"


def test_an_l5k_fills_the_card_as_far_as_it_can(l5k):
    from app.core import logixview

    summary = logix.summarise_l5k(l5k, time.monotonic() + 10)
    programs = {p["name"]: p for p in summary["program_list"]}
    assert programs["MainProgram"]["rungs"] == 2
    assert summary["udt_list"] == ["PumpUDT"]
    assert summary["aoi_list"] == [("Motor", "1.0")]
    assert logixview.networks(summary) == []


def test_task_rates_read_as_words():
    from app.core import logixview

    assert logixview.task_rate({"type": "periodic", "rate": "10"}) == "every 10 ms"
    assert logixview.task_rate({"type": "periodic", "rate": "2.5"}) == "every 2.5 ms"
    assert logixview.task_rate({"type": "continuous"}) == "continuous"


def test_the_card_draws_and_folds_without_overflowing(chassis_export):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from app.ui.logixcard import LogixCard

    app = QApplication.instance() or QApplication([])
    card = LogixCard()
    card.resize(320, 700)
    card.show()
    card.show_summary(logix.summarise(chassis_export, time.monotonic() + 10), "x.L5X")
    app.processEvents()
    assert card.widget().width() <= card.viewport().width() + 1
    card.resize(1000, 700)
    app.processEvents()
    assert card.widget().width() <= card.viewport().width() + 1
    card.hide()


# ------------------------------------------------------------------ 0.56 .ACD

def _acd(tmp_path, header: bytes, name="Line.ACD"):
    path = tmp_path / name
    path.write_bytes(header + b"\x00\x01\x02\xff" * 2000)
    return str(path)


def test_an_acd_header_is_read_and_nothing_past_it(tmp_path):
    path = _acd(tmp_path, b"DO NOT ALTER THIS FILE\r\n"
                          b"Created by RSLogix 5000 v20.04.00 on Mon Jan 13 09:11:02 2025\r\n"
                          b"Saved by Logix Designer v33.01.00 on Thu Sep 24 13:58:40 2026\r\n")
    header = logix.acd_header(path)
    assert header["lines"][0] == "DO NOT ALTER THIS FILE" and len(header["lines"]) == 3
    assert [h["version"] for h in header["history"]] == ["20.04", "33.01"]
    assert header["needs"] == "33.01" and header["first"] == "20.04"
    assert header["history"][1]["when"] == "Thu Sep 24 13:58:40 2026"


def test_a_utf16_header_reads_the_same(tmp_path):
    text = "DO NOT ALTER THIS FILE\r\nLogix Designer V32.02 1/13/2025 9:11 AM\r\n"
    path = _acd(tmp_path, text.encode("utf-16-le"))
    header = logix.acd_header(path)
    assert header["needs"] == "32.02" and header["history"][0]["when"] == "1/13/2025 9:11 AM"


def test_an_acd_that_starts_binary_gets_the_ordinary_preview(tmp_path):
    path = _acd(tmp_path, b"\x00\x00\x00\x10binary")
    assert logix.acd_header(path) is None
    shown = decode.preview(path, box=256, deadline=time.monotonic() + 10, logix=True)
    assert shown.source != "acd"


def test_the_acd_preview_carries_the_header_for_the_card(tmp_path):
    path = _acd(tmp_path, b"DO NOT ALTER THIS FILE\r\nSaved by Logix Designer v33.01.00\r\n")
    shown = decode.preview(path, box=256, deadline=time.monotonic() + 10, logix=True)
    assert shown.source == "acd" and shown.details["kind"] == "acd"
    off = decode.preview(path, box=256, deadline=time.monotonic() + 10, logix=False)
    assert off.source != "acd"

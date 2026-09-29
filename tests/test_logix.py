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

"""Rename several (0.41): the masks, the refusals, the plan, and the worker."""

from __future__ import annotations

import datetime
import queue

import pytest

from app.core.renamer import Item, Preview, Row, Rule, plan, preview, split
from app.io.protocol import Op, Request, Status


def names(result):
    return [(row.old, row.new, row.problem) for row in result.rows]


def test_a_folder_has_no_extension_and_a_dotfile_has_no_stem_split():
    assert split("2026-09-29.backup", is_dir=True) == ("2026-09-29.backup", "")
    assert split(".gitignore") == (".gitignore", "")
    assert split("main.L5X") == ("main", "L5X")


def test_counter_masks_and_slices():
    items = [Item("Pump Station.L5X"), Item("abcdef.txt")]
    result = preview(items, Rule(name="[N2-4]_[C]", width=3, start=7, step=5), [])
    assert [row.new for row in result.rows] == ["ump_007.L5X", "bcd_012.txt"]
    result = preview([Item("abcdef.txt")], Rule(name="[N5-][[x][N1]"), [])
    assert result.rows[0].new == "ef[x]a.txt"


def test_date_and_folder_tokens():
    stamp = datetime.datetime(2026, 9, 29, 8, 30).timestamp()
    result = preview([Item("a.txt", stamp)], Rule(name="[P]_[D]"), [], folder="Line 3")
    assert result.rows[0].new == "Line 3_2026-09-29.txt"


def test_find_and_replace_leaves_the_extension_alone_unless_asked():
    items = [Item("txt notes.txt")]
    assert preview(items, Rule(find="txt", replace="TXT"), []).rows[0].new == "TXT notes.txt"
    assert preview(items, Rule(find="txt", replace="TXT", whole=True), []).rows[0].new \
        == "TXT notes.TXT"
    regex = Rule(find=r"(\w+) (\w+)", replace=r"\2_\1", regex=True)
    assert preview(items, regex, []).rows[0].new == "notes_txt.txt"


def test_a_pattern_that_does_not_compile_is_one_error_not_a_crash():
    result = preview([Item("a.txt")], Rule(find="(", regex=True), [])
    assert result.error and not result.rows and not result.ready


def test_case_changes_leave_the_extension_to_title_case():
    assert preview([Item("pump STATION.l5x")], Rule(case="title"), []).rows[0].new \
        == "Pump Station.l5x"
    assert preview([Item("Pump.L5X")], Rule(case="lower"), []).rows[0].new == "pump.l5x"


@pytest.mark.parametrize("mask, why", [
    ("", "empty"), ("a/b", "cannot contain"), ("a:b", "cannot contain"),
    ("ends in a space ", "end in a space"),
])
def test_names_windows_would_refuse_are_refused_per_row(mask, why):
    result = preview([Item("a")], Rule(name=mask), [])
    assert why in result.rows[0].problem and not result.ready


def test_two_rows_onto_one_name_and_a_name_already_here_are_refused():
    items = [Item("a.txt"), Item("b.txt")]
    same = preview(items, Rule(name="x"), ["a.txt", "b.txt"])
    assert all("same name" in row.problem for row in same.rows)
    taken = preview([Item("a.txt")], Rule(name="other"), ["a.txt", "other.txt"])
    assert "already in this folder" in taken.rows[0].problem


def test_a_name_given_up_by_another_row_is_free():
    # 1 -> 2, 2 -> 3: 2.txt is renamed away, so 1.txt may take it.
    items = [Item("1.txt"), Item("2.txt")]
    result = preview(items, Rule(name="[C]", start=2), ["1.txt", "2.txt"])
    assert result.ready
    steps = plan(result)
    # 2.txt must move before anything lands on it.
    assert steps.index(("2.txt", next(n for o, n in steps if o == "2.txt"))) < \
        [n for _o, n in steps].index("2.txt")


def test_a_swap_goes_through_temporary_names():
    result = Preview(rows=[Row("a.txt", "b.txt"), Row("b.txt", "a.txt")])
    steps = plan(result)
    assert len(steps) == 4
    assert steps[0][0] == "a.txt" and steps[1][0] == "b.txt"
    assert {new for _old, new in steps[2:]} == {"a.txt", "b.txt"}


def test_nothing_changing_is_not_ready():
    assert not preview([Item("a.txt")], Rule(), ["a.txt"]).ready


# ------------------------------------------------------------------ the worker

def run(folder, steps):
    from app.io import worker
    outbox: queue.Queue = queue.Queue()
    worker._rename_many(Request(id=1, op=Op.RENAME_MANY, path=str(folder), timeout=5.0,
                                args={"steps": steps}), outbox)  # noqa: SLF001
    return outbox.get()


def test_the_worker_runs_a_plan_with_a_swap(tmp_path):
    (tmp_path / "a.txt").write_text("A")
    (tmp_path / "b.txt").write_text("B")
    steps = plan(Preview(rows=[Row("a.txt", "b.txt"), Row("b.txt", "a.txt")]))
    reply = run(tmp_path, steps)
    assert reply.status is Status.OK and reply.payload["done"] == 4
    assert (tmp_path / "a.txt").read_text() == "B"
    assert (tmp_path / "b.txt").read_text() == "A"


def test_a_failure_puts_back_what_was_done(tmp_path):
    (tmp_path / "a.txt").write_text("A")
    (tmp_path / "b.txt").write_text("B")
    (tmp_path / "taken.txt").write_text("T")
    reply = run(tmp_path, [["a.txt", "x.txt"], ["b.txt", "taken.txt"]])
    assert reply.status is Status.ERROR and "already exists" in reply.message
    assert reply.payload == {"done": 1, "undone": True}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.txt", "b.txt", "taken.txt"]


def test_a_name_that_leaves_the_folder_is_refused_before_anything_moves(tmp_path):
    (tmp_path / "a.txt").write_text("A")
    reply = run(tmp_path, [["a.txt", "b.txt"], ["b.txt", "..\\escape.txt"]])
    assert reply.status is Status.ERROR
    assert (tmp_path / "a.txt").exists()


# ------------------------------------------------------------ dialog and pane

def test_the_dialog_is_grey_until_the_rule_works_and_hands_back_a_plan():
    from app.ui.renamer import RenameDialog

    items = [Item("a.txt"), Item("b.txt")]
    dialog = RenameDialog(None, items=items, existing=["a.txt", "b.txt"],
                          folder_name="Line3", last={"name": "same"})
    assert not dialog._ok.isEnabled()                    # both would be same.txt
    dialog._name.setText("[P]_[C]")
    dialog._refresh()
    assert dialog._ok.isEnabled()
    steps, moves = dialog.outcome()
    assert moves == [("a.txt", "Line3_1.txt"), ("b.txt", "Line3_2.txt")]
    assert steps == moves
    assert dialog.remembered()["name"] == "[P]_[C]"


def test_a_rule_kept_from_an_older_version_does_not_break_the_dialog():
    from app.ui.renamer import _rule_from

    assert _rule_from({"name": "[N]x", "gone_field": 1}).name == "[N]x"
    assert _rule_from("junk") == Rule()


def test_the_pane_sends_the_plan_as_one_request(tmp_path):
    from app.core.config import Config
    from app.core.pane import Pane
    from tests.test_columns import FakeBridge

    bridge = FakeBridge()
    pane = Pane(bridge, Config({}, path=str(tmp_path / "c.json")), "left")
    pane.navigate(str(tmp_path))
    pane.rename_many([("a.txt", "b.txt")], [("a.txt", "b.txt")])
    sent = bridge.sent[-1]
    assert sent["op"] is Op.RENAME_MANY
    assert sent["args"] == {"steps": [["a.txt", "b.txt"]]}

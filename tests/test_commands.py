"""The external command table: what a template expands to, and what it refuses.

Every test here runs without Windows, without a program installed and without
starting anything, and that is the design working rather than a limitation of
the test. Expansion is string handling over a `Context`, the refusals are a
pure function, and the one part that needs a machine -- finding the executable
-- is deliberately on the other side of the process boundary where the harness
can be pointed at it.

The two failures worth catching here are both silent. A template that splits
after substitution turns `C:\\Program Files\\x` into two arguments and the
program opens something that does not exist; and a shortcut that duplicates one
of the pane's own keys makes F5 stop copying with nothing anywhere to say why.
Neither shows up by pressing the key once.
"""

from __future__ import annotations

import pytest

from app.core.commands import (
    DEFAULTS,
    RESERVED,
    Command,
    Context,
    by_shortcut,
    expand,
    find,
    from_config,
    normalise_shortcut,
    refusal,
    removals,
    shortcut_refusal,
    split,
    to_config,
    with_defaults,
)
from app.io.protocol import LIST_FILE

HERE = r"\\server\share\Jobs"
THERE = r"D:\Archive"


def context(**changes) -> Context:
    values = {"path": HERE, "other_path": THERE, "name": "one.txt"}
    values.update(changes)
    return Context(**values)


# ------------------------------------------------------------------ splitting

def test_a_template_splits_on_spaces() -> None:
    assert split("-a -b -c") == ["-a", "-b", "-c"]


def test_quotes_group_and_then_disappear() -> None:
    """The quotes are the user saying "one argument", not part of the value."""
    assert split('"a b" c') == ["a b", "c"]


def test_an_empty_quoted_argument_survives() -> None:
    """`""` is a real argument and some programs are given one deliberately."""
    assert split('a "" b') == ["a", "", "b"]


def test_nothing_at_all_is_no_arguments() -> None:
    assert split("") == []
    assert split("   ") == []


def test_a_path_with_spaces_lands_in_one_argument_unquoted() -> None:
    """The whole reason splitting happens before substitution.

    The template says `%P` with no quotes around it, which is what somebody
    would write, and the folder has a space in it. Substituting first and
    splitting after would make this two arguments and the failure would be a
    program complaining about a file called `C:\\Program`.
    """
    command = Command(id="x", name="x", program="p.exe", arguments="%P")
    launch = expand(command, context(path=r"C:\Program Files\Thing"))
    assert launch.arguments == (r"C:\Program Files\Thing",)


# ----------------------------------------------------------------- expansion

def test_the_single_value_tokens() -> None:
    command = Command(id="x", name="x", program="p.exe",
                      arguments="%P %T %N %F")
    launch = expand(command, context())
    assert launch.arguments == (
        HERE, THERE, "one.txt", HERE + r"\one.txt",
    )


def test_a_literal_per_cent_survives() -> None:
    command = Command(id="x", name="x", program="p.exe", arguments="100%% sure")
    assert expand(command, context()).arguments == ("100%", "sure")


def test_a_selection_token_of_its_own_becomes_one_argument_each() -> None:
    command = Command(id="x", name="x", program="p.exe", arguments="%S")
    launch = expand(command, context(names=("a.txt", "b.txt")))
    assert launch.arguments == (HERE + r"\a.txt", HERE + r"\b.txt")


def test_a_selection_token_inside_a_larger_one_joins_with_spaces() -> None:
    """The other shape, and it is in the template rather than a setting.

    A program that takes `--files=<list>` wants one argument; a program that
    takes a series of paths wants several. Both are written plainly enough to
    tell apart by looking.
    """
    command = Command(id="x", name="x", program="p.exe", arguments="--files=%S")
    launch = expand(command, context(names=("a.txt", "b.txt")))
    assert launch.arguments == (f"--files={HERE}\\a.txt {HERE}\\b.txt",)


def test_bare_names_are_the_lower_case_token() -> None:
    command = Command(id="x", name="x", program="p.exe", arguments="%s")
    launch = expand(command, context(names=("a.txt", "b.txt")))
    assert launch.arguments == ("a.txt", "b.txt")


def test_nothing_marked_falls_back_to_the_row_under_the_cursor() -> None:
    """Which is what every file manager with this feature does.

    Somebody who arrowed onto a file and pressed the compare key has said which
    file they mean as clearly as somebody who pressed Insert first.
    """
    command = Command(id="x", name="x", program="p.exe", arguments="%S")
    launch = expand(command, context(names=(), name="only.txt"))
    assert launch.arguments == (HERE + r"\only.txt",)


def test_the_list_token_leaves_a_sentinel_and_the_names_beside_it() -> None:
    """The one token `core` cannot finish: writing the file is a write."""
    command = Command(id="x", name="x", program="p.exe", arguments="%L")
    launch = expand(command, context(names=("a.txt", "b.txt")))
    assert launch.arguments == (LIST_FILE,)
    assert launch.list_names == (HERE + r"\a.txt", HERE + r"\b.txt")


def test_the_working_directory_is_expanded_too() -> None:
    command = Command(id="x", name="x", program="p.exe", working="%P")
    assert expand(command, context()).working == HERE


def test_a_template_with_no_tokens_is_passed_through() -> None:
    command = Command(id="x", name="x", program="p.exe", arguments="-NoExit -l")
    assert expand(command, context()).arguments == ("-NoExit", "-l")


def test_what_was_substituted_is_reported() -> None:
    """For the harness and for a diagnosis; nothing runs off it."""
    command = Command(id="x", name="x", program="p.exe", arguments="%P %P %T")
    assert expand(command, context()).used == ("%P", "%T")


# ------------------------------------------------------------------ refusals

def test_a_command_with_no_program_is_refused() -> None:
    assert "no program" in refusal(
        Command(id="x", name="Thing", program=" "), context())


def test_the_other_pane_token_needs_the_other_pane() -> None:
    command = Command(id="x", name="x", program="p.exe", arguments="%P %T")
    assert refusal(command, context(other_path="")) != ""
    assert refusal(command, context()) == ""


def test_the_cursor_tokens_need_a_row_under_the_cursor() -> None:
    command = Command(id="x", name="x", program="p.exe", arguments="%F")
    assert refusal(command, context(name="")) != ""


def test_the_selection_tokens_need_a_selection() -> None:
    command = Command(id="x", name="x", program="p.exe", arguments="%S")
    assert refusal(command, context(name="", names=())) != ""


def test_a_list_token_inside_a_larger_argument_is_refused() -> None:
    """There is nowhere to put a sentinel that survives substitution, so the
    shape is refused with a sentence rather than expanded into something that
    would start a program pointing at nothing.
    """
    command = Command(id="x", name="x", program="p.exe", arguments="@%L")
    assert "%L" in refusal(command, context(names=("a.txt",)))


def test_the_file_compare_refuses_a_single_file_with_no_other_pane() -> None:
    command = find(DEFAULTS, "compare-files")
    assert refusal(command, context(names=("a.txt",), other_path="")) != ""
    assert refusal(command, context(names=("a.txt", "b.txt"), other_path="")) == ""


def test_a_refusal_says_what_is_missing_rather_than_naming_the_token() -> None:
    """Somebody who has never opened the editor has not heard of `%T`."""
    command = Command(id="x", name="x", program="p.exe", arguments="%T")
    assert "%T" not in refusal(command, context(other_path=""))


# ----------------------------------------------------------------- shortcuts

@pytest.mark.parametrize("written, expected", [
    ("ctrl+f2", "Ctrl+F2"),
    ("CTRL + F2", "Ctrl+F2"),
    ("Control-F2", "Ctrl+F2"),
    ("shift+ctrl+g", "Ctrl+Shift+G"),
    ("f9", "F9"),
    ("delete", "Del"),
    ("", ""),
    ("   ", ""),
])
def test_one_spelling_for_a_key(written: str, expected: str) -> None:
    assert normalise_shortcut(written) == expected


def test_modifiers_are_written_in_one_order() -> None:
    """So two rows claiming one key compare equal rather than looking different."""
    assert normalise_shortcut("shift+ctrl+alt+p") == normalise_shortcut("ctrl+alt+shift+p")


def test_a_key_the_pane_already_answers_is_refused() -> None:
    assert shortcut_refusal("F5", DEFAULTS) != ""
    assert shortcut_refusal("ctrl+c", DEFAULTS) != ""


def test_a_key_another_command_has_is_refused_and_names_it() -> None:
    why = shortcut_refusal("F9", DEFAULTS)
    assert "PowerShell" in why


def test_a_command_may_keep_its_own_key() -> None:
    """Otherwise editing a row's name would make its own shortcut illegal."""
    assert shortcut_refusal("F9", DEFAULTS, this_one="terminal") == ""


def test_no_shipped_command_takes_a_reserved_key() -> None:
    """The table ships with keys; this is what stops one of them being F5."""
    for command in DEFAULTS:
        assert normalise_shortcut(command.shortcut) not in RESERVED


def test_no_two_shipped_commands_share_a_key() -> None:
    keys = [normalise_shortcut(c.shortcut) for c in DEFAULTS if c.shortcut]
    assert len(keys) == len(set(keys))


def test_the_key_map_is_the_key_to_the_id() -> None:
    assert by_shortcut(DEFAULTS)["F9"] == "terminal"
    assert by_shortcut(DEFAULTS)["F4"] == "edit"


# -------------------------------------------------------------- the settings

def test_a_table_survives_a_round_trip() -> None:
    assert from_config(to_config(DEFAULTS)) == DEFAULTS


def test_nothing_saved_means_the_shipped_table() -> None:
    assert with_defaults(from_config([])) == DEFAULTS
    assert with_defaults(from_config(None)) == DEFAULTS


def test_a_row_that_is_not_a_command_is_dropped_rather_than_raised() -> None:
    """A hand-edited settings file is the expected way to get one of these,
    and the answer is `Config.load`'s: lose the row, not the application.
    """
    saved = to_config(DEFAULTS) + [{"name": "no id"}, "not a dict", {"id": "no name"}]
    assert from_config(saved) == DEFAULTS


def test_a_shortcut_in_the_settings_file_is_normalised_on_the_way_in() -> None:
    table = from_config([{"id": "x", "name": "x", "shortcut": "ctrl+ f8 "}])
    assert table[0].shortcut == "Ctrl+F8"


def test_a_new_built_in_reaches_somebody_who_has_already_edited_their_table() -> None:
    mine = (Command(id="mine", name="Mine", program="p.exe"),)
    table = with_defaults(mine)
    assert table[0].id == "mine"
    assert {command.id for command in DEFAULTS} <= {command.id for command in table}


def test_a_built_in_that_was_deleted_stays_deleted() -> None:
    """The other half of the same rule, and the one that is noticed: a row
    that came back on every launch would be one there was no way to be rid of.
    """
    kept = tuple(command for command in DEFAULTS if command.id != "prompt")
    gone = removals(kept)
    assert gone == ["prompt"]
    assert with_defaults(kept, gone) == kept


def test_deleting_a_row_somebody_added_is_not_remembered() -> None:
    """Nothing will ever put it back, so nothing has to remember not to."""
    mine = Command(id="mine", name="Mine", program="p.exe")
    assert removals(DEFAULTS, ["mine"]) == []


# ------------------------------------------------------- the worker's half

# Finding the program and starting it is the one part of this feature that a
# machine has to answer, so it is tested against the machine the tests are
# running on rather than mocked. What is asserted is the shape of the answer --
# found or not found, the vector as passed, the list file written -- and never
# a Windows path, so these run wherever the rest of the suite does.

import os  # noqa: E402

from app.io import worker  # noqa: E402
from app.io.protocol import Op, Request, Status  # noqa: E402


class Outbox:
    """The worker's reply queue, as a list."""

    def __init__(self) -> None:
        self.replies = []

    def put(self, reply) -> None:
        self.replies.append(reply)


def run_request(**args):
    outbox = Outbox()
    worker._run(Request(id=1, op=Op.RUN, path=args.pop("path", ""),
                        timeout=15.0, args=args), outbox)
    return outbox.replies[-1]


def test_an_absolute_program_is_taken_as_given(tmp_path) -> None:
    program = tmp_path / "thing"
    program.write_text("")
    assert worker.locate(str(program)) == str(program)


def test_an_absolute_program_that_is_not_there_is_not_found(tmp_path) -> None:
    assert worker.locate(str(tmp_path / "missing")) == ""


def test_a_bare_name_comes_from_the_path() -> None:
    import shutil

    assert worker.locate("python3") == shutil.which("python3")


def test_nothing_at_all_is_not_a_program() -> None:
    assert worker.locate("") == ""


def test_a_program_that_is_not_installed_says_so_and_names_what_was_tried() -> None:
    """The sentence a machine without Beyond Compare gets, and the reason the
    compare tool is optional rather than a dependency.
    """
    reply = run_request(program="bcompare-not-here.exe",
                        alternatives=["winmerge-not-here.exe"])
    assert reply.status is Status.ERROR
    assert "bcompare-not-here.exe" in reply.message
    assert "winmerge-not-here.exe" in reply.message


def test_the_alternative_is_used_when_the_first_is_missing() -> None:
    """The row that means "a compare tool" rather than "Beyond Compare".

    The name is matched without its extension and without case, because that
    is what Windows hands back: `shutil.which("python3")` there answers
    `...\\WindowsApps\\python3.EXE`, and an assertion that the answer ends with
    the name passes on Linux and fails on the machine this ships to.
    """
    reply = run_request(program="definitely-not-a-program",
                        alternatives=["python3"], arguments=["-c", "pass"])
    assert reply.status is Status.OK
    found = os.path.splitext(os.path.basename(reply.payload["program"]))[0]
    assert found.lower() == "python3"


def test_a_started_program_reports_its_pid_and_nothing_about_how_it_went() -> None:
    """The call returns when the process exists, not when somebody closes it.
    Waiting would hold this worker -- and every listing on the volume -- for as
    long as an editor stayed open.
    """
    reply = run_request(program="python3", arguments=["-c", "pass"])
    assert reply.status is Status.OK
    assert isinstance(reply.payload["pid"], int)


def test_a_working_directory_that_is_not_there_is_refused(tmp_path) -> None:
    """Rather than started in whatever the parent's folder is. A terminal that
    opens somewhere other than where it was asked for is worse than one that
    does not open: the next command runs in the wrong place.
    """
    reply = run_request(program="python3", arguments=["-c", "pass"],
                        working=str(tmp_path / "gone"))
    assert reply.status is Status.GONE


def test_the_list_file_is_written_and_its_path_substituted(tmp_path) -> None:
    reply = run_request(program="python3", arguments=["-c", "pass", LIST_FILE],
                        list=[r"C:\a.txt", r"C:\b.txt"])
    assert reply.status is Status.OK
    written = reply.payload["list"]
    assert written and written in reply.payload["arguments"]
    with open(written, "rb") as handle:
        raw = handle.read()
    # A BOM, because a list of file names is exactly the content that carries
    # accented characters and the programs handed one read a file without a
    # BOM as the system code page.
    assert raw.startswith(b"\xef\xbb\xbf")
    assert b"C:\\a.txt" in raw and b"C:\\b.txt" in raw


def test_a_selection_too_long_to_list_is_refused() -> None:
    from app.io.protocol import MAX_LIST_PATHS

    reply = run_request(program="python3", arguments=[LIST_FILE],
                        list=[f"f{n}" for n in range(MAX_LIST_PATHS + 1)])
    assert reply.status is Status.ERROR
    assert "too many" in reply.message


def test_no_list_file_is_written_when_the_template_did_not_ask_for_one() -> None:
    reply = run_request(program="python3", arguments=["-c", "pass"],
                        list=[r"C:\a.txt"])
    assert reply.payload["list"] == ""

"""The programs this application does not contain, and the keys that reach them.

A file manager is where somebody's hands already are when they want a terminal
here, an editor on this file, or a diff of these two folders. Every one of
those is a program that already exists and does the job better than a pane
ever will, so none of them is written here: what is written here is the one
mechanism that reaches all of them, and everything else is a row in a table.

That table is the feature. `F9` opening PowerShell in the folder the cursor is
in, `F4` opening an editor, Beyond Compare on the two panes -- each of those is
a `Command`, and so is whatever the user adds next. Which means the compare
tool is *optional* by construction rather than by a flag: the application does
not know what Beyond Compare is, only that some row names it, and a row whose
program is not on this machine is a row that says so.

What the templates may say
--------------------------

An argument template is split into tokens first and substituted afterwards,
never the other way round. It matters: `C:\\Program Files\\x` substituted into
an unsplit string and then split is two arguments and a mystery, and the user
would have to know to quote it. Split first and a token is a token whatever
lands inside it, so nothing here needs quoting and nothing here can be made to
mean something else by a folder name.

  ``%P``  the folder the active pane is showing
  ``%T``  the folder the other pane is showing
  ``%N``  the name under the cursor, on its own
  ``%F``  the full path of the name under the cursor
  ``%S``  every marked file, as full paths
  ``%s``  every marked file, as bare names
  ``%L``  a file holding the marked paths, one per line
  ``%%``  a literal per cent

`%S` and `%s` are the two that can be more than one thing. A token that is
*exactly* `%S` becomes one argument per file; `%S` inside a larger token joins
them with spaces, which is what a program taking a single quoted list wants.
Both forms are useful and the difference is visible in the template, so both
are kept.

`%L` is the escape hatch for a selection too long for a command line, and it is
the one token this module cannot finish on its own: writing that file is a
filesystem call, so the expansion leaves `LIST_FILE` standing in the arguments
and the worker replaces it with the path of the file it wrote. Nothing in
`core` or `ui` writes it.

What this module refuses
------------------------

`refusal` is a pure function and is the only place a command is decided against.
It answers the question "why can this not run right now" in the words the
status line will use: nothing marked, no second pane, no file under the cursor.
It never asks whether the program exists -- that is a filesystem question, so
the worker answers it, and the answer arrives as a reply like any other.

Shortcuts are refused separately and earlier, when one is typed into the editor.
`RESERVED` is the list of keys that already mean something at the pane, and a
command that took `F5` would be a copy key that silently stopped copying. This
is the cheapest possible version of the keymap editor the wish list asks for --
the table is the keymap, for the keys it owns.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Sequence

from PySide6.QtCore import QObject, Signal

from app.io import paths
from app.io.protocol import LIST_FILE, Op, Reply, Status

#: The keys the pane already answers. A command may not take one of these, and
#: the editor says so rather than letting a copy key quietly stop copying.
#: Written in the same form `normalise_shortcut` produces.
RESERVED: frozenset[str] = frozenset({
    "F2", "F3", "F5", "F6", "F7", "F8",
    "Del", "Shift+Del", "Ins", "Backspace", "Space", "Tab", "Esc",
    "Ctrl+A", "Ctrl+Shift+A", "Ctrl+B", "Ctrl+C", "Ctrl+D", "Ctrl+F",
    "Ctrl+Shift+F", "Ctrl+G", "Ctrl+Shift+G", "Ctrl+J", "Ctrl+L", "Ctrl+P",
    "Ctrl+Shift+P", "Ctrl+Q", "Ctrl+R", "Ctrl+Shift+R", "Ctrl+T",
    "Ctrl+Shift+T", "Ctrl+U", "Ctrl+V", "Ctrl+W", "Ctrl+Shift+W", "Ctrl+X",
    "Ctrl+Shift+C", "Ctrl+Shift+D", "Ctrl+Shift+L", "Ctrl+Shift+M",
    "Ctrl+Shift+Space", "Ctrl+Enter", "Alt+Left", "Alt+Right",
})

#: The order modifiers are written in, so two spellings of one key compare
#: equal. Qt's own order, because the menus display these next to Qt's.
_MODIFIER_ORDER = ("Ctrl", "Alt", "Shift", "Meta")

_MODIFIER_NAMES = {
    "ctrl": "Ctrl", "control": "Ctrl", "ctl": "Ctrl",
    "alt": "Alt", "option": "Alt",
    "shift": "Shift",
    "meta": "Meta", "win": "Meta", "super": "Meta", "cmd": "Meta",
}

_KEY_NAMES = {
    "del": "Del", "delete": "Del", "ins": "Ins", "insert": "Ins",
    "esc": "Esc", "escape": "Esc", "return": "Enter", "enter": "Enter",
    "space": "Space", "tab": "Tab", "backspace": "Backspace",
    "pgup": "PgUp", "pageup": "PgUp", "pgdn": "PgDown", "pagedown": "PgDown",
    "home": "Home", "end": "End",
    "left": "Left", "right": "Right", "up": "Up", "down": "Down",
}


@dataclass(frozen=True)
class Command:
    """One row of the table: a program, what to hand it, and the key for it.

    `program` is a bare executable name most of the time -- `powershell.exe`,
    `BCompare.exe` -- rather than a path, because where a program is installed
    is the machine's business and not this table's. The worker resolves it; see
    `Op.RUN`.

    `alternatives` are tried in order when `program` is not on this machine, and
    exist for exactly one case: a row that means "a compare tool" rather than
    "Beyond Compare". Shipping the default with `WinMergeU.exe` behind it is the
    difference between the compare key working on a machine that has the other
    tool and a menu entry that is permanently grey.
    """

    id: str
    name: str
    program: str
    arguments: str = ""
    working: str = ""
    shortcut: str = ""
    alternatives: tuple[str, ...] = ()
    #: False hides it from the Tools menu without deleting it. What the editor's
    #: checkbox writes, and what a user who wants the key but not the clutter
    #: -- or the reverse -- ends up with.
    shown: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "program": self.program,
            "arguments": self.arguments, "working": self.working,
            "shortcut": self.shortcut, "alternatives": list(self.alternatives),
            "shown": self.shown,
        }


@dataclass(frozen=True)
class Context:
    """What the panes hold at the moment a command is asked for.

    Everything is a plain string or a list of them. Nothing here is a `Path`, a
    row, or a model index: this crosses into `io` and has to stay picklable,
    and it is also what makes the expansion testable without a window.
    """

    path: str = ""
    other_path: str = ""
    name: str = ""
    names: tuple[str, ...] = ()
    other_names: tuple[str, ...] = ()

    def full(self, name: str) -> str:
        return paths.join(self.path, name) if self.path and name else name

    @property
    def selection(self) -> tuple[str, ...]:
        """What `%S` means: the marked files, or the one under the cursor.

        Falling back to the cursor is deliberate and is what every file manager
        with this feature does. A person who has arrowed onto a file and pressed
        the compare key has said which file they mean as clearly as somebody who
        pressed Insert first.
        """
        if self.names:
            return tuple(self.names)
        return (self.name,) if self.name else ()


@dataclass(frozen=True)
class Launch:
    """An expanded command, ready to be sent to a worker.

    `arguments` is a vector rather than a command line: the quoting a command
    line needs is Windows' own and belongs to the thing that builds the process,
    not to a table somebody types into.
    """

    program: str
    arguments: tuple[str, ...] = ()
    working: str = ""
    alternatives: tuple[str, ...] = ()
    list_names: tuple[str, ...] = ()
    #: What was substituted, for the harness and for a diagnosis. Not used to
    #: run anything.
    used: tuple[str, ...] = ()


# --------------------------------------------------------------- the defaults

#: The table a machine that has never been configured starts with.
#:
#: Four of these are the wish list's own words -- a terminal on F9, an editor on
#: F4, a compare tool, Explorer here -- and they are rows rather than features
#: so that changing one's key or its program is an edit rather than a release.
DEFAULTS: tuple[Command, ...] = (
    Command(
        id="terminal",
        name="PowerShell here",
        program="powershell.exe",
        alternatives=("pwsh.exe",),
        working="%P",
        shortcut="F9",
    ),
    Command(
        id="prompt",
        name="Command prompt here",
        program="cmd.exe",
        working="%P",
        shortcut="Shift+F9",
    ),
    Command(
        id="terminal-tabs",
        name="Windows Terminal here",
        program="wt.exe",
        working="%P",
        shortcut="Ctrl+F9",
    ),
    Command(
        id="edit",
        name="Edit",
        program="notepad.exe",
        arguments="%F",
        shortcut="F4",
    ),
    Command(
        id="compare",
        name="Compare the two panes",
        program="BCompare.exe",
        alternatives=("WinMergeU.exe",),
        arguments="%P %T",
        shortcut="Ctrl+F2",
    ),
    Command(
        id="compare-files",
        name="Compare the marked files",
        program="BCompare.exe",
        alternatives=("WinMergeU.exe",),
        arguments="%S",
        shortcut="Alt+F2",
    ),
    Command(
        id="explorer",
        name="Explorer here",
        program="explorer.exe",
        arguments="%P",
    ),
)


# ------------------------------------------------------------ the expansion

_TOKEN = re.compile(r"%[PTNFSsL%]")


def split(template: str) -> list[str]:
    """Break an argument template into tokens, honouring double quotes.

    Quotes group and then disappear, which is what a shell does and what
    somebody typing into the editor expects. Nothing is substituted here: this
    runs first, on the template, so that a substituted value can never change
    where one argument ends and the next begins.
    """
    tokens: list[str] = []
    current: list[str] = []
    quoted = False
    started = False
    for character in template:
        if character == '"':
            quoted = not quoted
            started = True
            continue
        if character.isspace() and not quoted:
            if started or current:
                tokens.append("".join(current))
                current = []
                started = False
            continue
        current.append(character)
        started = True
    if started or current:
        tokens.append("".join(current))
    return tokens


def _value(token: str, context: Context) -> str | None:
    """What one `%x` stands for, or None where it stands for a list."""
    if token == "%%":
        return "%"
    if token == "%P":
        return context.path
    if token == "%T":
        return context.other_path
    if token == "%N":
        return context.name
    if token == "%F":
        return context.full(context.name)
    return None


def expand(command: Command, context: Context) -> Launch:
    """Turn a row of the table into something a worker can start.

    Assumes `refusal` has already said yes. It is separate rather than folded
    in because the two are asked at different moments: the refusal decides
    whether a menu entry is grey, and this runs when it is clicked.
    """
    arguments: list[str] = []
    used: list[str] = []
    wants_list = False
    selection = context.selection

    for token in split(command.arguments):
        found = _TOKEN.findall(token)
        used.extend(found)
        if token == "%S":
            arguments.extend(context.full(name) for name in selection)
            continue
        if token == "%s":
            arguments.extend(selection)
            continue
        if token == "%L":
            arguments.append(LIST_FILE)
            wants_list = True
            continue
        if not found:
            arguments.append(token)
            continue

        def substitute(match: re.Match) -> str:
            marker = match.group(0)
            if marker == "%S":
                return " ".join(context.full(name) for name in selection)
            if marker == "%s":
                return " ".join(selection)
            if marker == "%L":
                # Inside a larger token there is nowhere to put a sentinel that
                # survives, so this is the one shape of `%L` that is not
                # supported. `refusal` says so before anybody gets here.
                return ""
            return _value(marker, context) or ""

        arguments.append(_TOKEN.sub(substitute, token))

    working = command.working
    if working:
        used.extend(_TOKEN.findall(working))
        working = _TOKEN.sub(
            lambda m: _value(m.group(0), context) or "", working)

    return Launch(
        program=command.program,
        arguments=tuple(arguments),
        working=working,
        alternatives=tuple(command.alternatives),
        list_names=tuple(context.full(name) for name in selection) if wants_list else (),
        used=tuple(dict.fromkeys(used)),
    )


def refusal(command: Command, context: Context) -> str:
    """Why this command cannot run right now, or "" when it can.

    Pure, and the only place the question is decided. The words are the ones
    the status line shows, so they say what is missing rather than naming the
    token that wanted it -- somebody who has not opened the editor has never
    heard of `%T`.
    """
    if not command.program.strip():
        return f"{command.name} has no program set"

    template = f"{command.arguments} {command.working}"
    wanted = set(_TOKEN.findall(template))

    if "%L" in wanted and "%L" not in split(command.arguments):
        return "%L has to be an argument of its own"
    if ("%P" in wanted or "%L" in wanted) and not context.path:
        return "this pane is not showing a folder"
    if "%T" in wanted and not context.other_path:
        return "the other pane is not showing a folder"
    if ("%N" in wanted or "%F" in wanted) and not context.name:
        return "nothing is under the cursor"
    if wanted & {"%S", "%s", "%L"} and not context.selection:
        return "nothing is marked"
    if command.id == "compare-files" and len(context.selection) < 2 \
            and not context.other_path:
        # The one command with a rule of its own, because comparing one file
        # with nothing is the mistake this key invites and a diff tool opening
        # on a single file says nothing about why.
        return "mark two files, or open the other pane on the one to compare with"
    return ""


def pair_for_compare(context: Context) -> tuple[str, str] | None:
    """The two things a file compare should open, or None.

    Two marked files are the two. One marked file is that one and the file of
    the same name in the other pane, which is the gesture that makes a dual
    pane worth having -- and it is the reason `compare-files` is allowed to run
    with one file marked when the other pane has a folder.
    """
    selection = context.selection
    if len(selection) >= 2:
        return context.full(selection[0]), context.full(selection[1])
    if len(selection) == 1 and context.other_path:
        name = selection[0]
        return (context.full(name), paths.join(context.other_path, name))
    return None


# -------------------------------------------------------------- the shortcuts

def normalise_shortcut(text: str) -> str:
    """One spelling for a key, so two of them can be compared.

    `ctrl+f2`, `Control+F2` and `CTRL + F2` are one shortcut, and a table that
    stored them as typed would let two rows claim the same key while looking
    different. Unknown key names are passed through with their case intact --
    this is a normaliser, not a validator, and refusing a key Qt understands
    and this does not would be the worse failure.
    """
    if not text or not text.strip():
        return ""
    pieces = [piece.strip() for piece in text.replace("-", "+").split("+")]
    pieces = [piece for piece in pieces if piece]
    if not pieces:
        return ""
    modifiers: list[str] = []
    key = ""
    last = len(pieces) - 1
    for position, piece in enumerate(pieces):
        name = _MODIFIER_NAMES.get(piece.lower())
        # The last piece is the key even when it is spelled like a modifier:
        # "Shift" on its own is a key, and a trailing "Ctrl" is somebody in the
        # middle of typing rather than a shortcut with no key in it.
        if name is not None and position != last:
            if name not in modifiers:
                modifiers.append(name)
        else:
            key = piece
    if not key:
        return ""
    lowered = key.lower()
    if lowered in _KEY_NAMES:
        key = _KEY_NAMES[lowered]
    elif re.fullmatch(r"f\d{1,2}", lowered):
        key = lowered.upper()
    elif len(key) == 1:
        key = key.upper()
    ordered = [name for name in _MODIFIER_ORDER if name in modifiers]
    return "+".join(ordered + [key])


def shortcut_refusal(shortcut: str, commands: Sequence[Command],
                     *, this_one: str = "") -> str:
    """Why a key cannot be given to a command, or "" when it can.

    Two rules and nothing else: a key the pane already answers stays with the
    pane, and a key another row has is that row's. Both are the same failure --
    a key that does two things does the wrong one -- and neither can be noticed
    by pressing it once.
    """
    key = normalise_shortcut(shortcut)
    if not key:
        return ""
    if len(key) == 1 and key.isprintable():
        # A bare letter typed into the listing is the quick search, which
        # catches it before the pane sees it at all. Allowing one here would
        # be a key that is accepted, saved, shown in the menu and never fires.
        return "a letter on its own is the quick search; add Ctrl or Alt"
    if key in RESERVED:
        return f"{key} already means something here"
    for command in commands:
        if command.id != this_one and normalise_shortcut(command.shortcut) == key:
            return f"{key} is {command.name}"
    return ""


def by_shortcut(commands: Iterable[Command]) -> dict[str, str]:
    """The key -> command id map the pane matches against.

    First row wins where two claim a key, which cannot happen through the
    editor and can happen in a hand-edited settings file.
    """
    table: dict[str, str] = {}
    for command in commands:
        key = normalise_shortcut(command.shortcut)
        if key and key not in table:
            table[key] = command.id
    return table


# ------------------------------------------------------------ the config form

def from_config(value: Any) -> tuple[Command, ...]:
    """Read the table out of the settings file, dropping what makes no sense.

    A hand-edited settings file is the expected way to get a bad row here, and
    the answer is the same as `Config.load`'s: lose the row, not the
    application. A row with no id or no name is not a command; everything else
    has a default.
    """
    if not isinstance(value, list):
        return DEFAULTS
    commands: list[Command] = []
    seen: set[str] = set()
    for entry in value:
        if not isinstance(entry, dict):
            continue
        identity = str(entry.get("id", "") or "").strip()
        name = str(entry.get("name", "") or "").strip()
        if not identity or not name or identity in seen:
            continue
        seen.add(identity)
        alternatives = entry.get("alternatives") or ()
        if isinstance(alternatives, str):
            alternatives = (alternatives,)
        commands.append(Command(
            id=identity,
            name=name,
            program=str(entry.get("program", "") or ""),
            arguments=str(entry.get("arguments", "") or ""),
            working=str(entry.get("working", "") or ""),
            shortcut=normalise_shortcut(str(entry.get("shortcut", "") or "")),
            alternatives=tuple(str(item) for item in alternatives if str(item)),
            shown=bool(entry.get("shown", True)),
        ))
    return tuple(commands)


def to_config(commands: Iterable[Command]) -> list[dict[str, Any]]:
    return [command.as_dict() for command in commands]


def with_defaults(commands: Sequence[Command],
                  removed: Iterable[str] = ()) -> tuple[Command, ...]:
    """The saved table, plus any built-in row this machine has not seen yet.

    Two things have to be true at once and neither is free. A version that
    ships a new default has to reach somebody who has already edited their
    table, or the row would only ever appear for a new installation. And a
    built-in row somebody deleted has to stay deleted, or it would come back on
    the next launch and there would be no way to be rid of it.

    So what is saved is the table *and* the ids that were removed from it.
    `removed` is that second list; a row in it is one this user has said no to.
    """
    known = {command.id for command in commands} | {str(item) for item in removed}
    return tuple(commands) + tuple(
        command for command in DEFAULTS if command.id not in known)


def removals(commands: Sequence[Command], before: Iterable[str] = ()) -> list[str]:
    """Which built-in rows are missing from a table, for saving alongside it.

    Only the built-in ids are worth remembering. A row the user added and then
    deleted is simply gone -- nothing will ever put it back, so nothing has to
    remember that it should not.
    """
    built_in = {command.id for command in DEFAULTS}
    present = {command.id for command in commands}
    remembered = {str(item) for item in before} & built_in
    return sorted((built_in - present) | (remembered - present))


def touched(commands: Sequence[Command]) -> bool:
    """Whether this table differs from the shipped one."""
    return tuple(commands) != DEFAULTS


def find(commands: Iterable[Command], identity: str) -> Command | None:
    for command in commands:
        if command.id == identity:
            return command
    return None


def renamed(command: Command, **changes: Any) -> Command:
    """`dataclasses.replace`, re-exported so callers need not import it."""
    return replace(command, **changes)


# ------------------------------------------------------------- the runner

class Commands(QObject):
    """The table, and the one place a row of it becomes a running program.

    Shaped like `FolderSizes` rather than like `Clipboard`: it owns a piece of
    state the UI renders, asks a worker for the thing it cannot do itself, and
    says what happened on a signal. What it deliberately does not own is any
    knowledge of where programs live -- `refusal` answers what can be decided
    from the panes, and everything about this machine comes back in a reply.

    One request at a time is not enforced here and should not be. Starting a
    program is not a scan: it holds its volume's worker for as long as
    `CreateProcess` takes and no longer, so two commands in quick succession
    are two launches rather than a queue. The deadline is what covers the case
    where the folder a terminal is being opened in has stopped answering.
    """

    changed = Signal()                  # the table was edited
    ran = Signal(str, str)              # command id, the program that started
    problem = Signal(str, str)          # command id, why it did not

    def __init__(self, bridge, config, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._config = config
        self._removed = [str(item) for item in (config.get("commands.removed") or [])]
        self._commands = with_defaults(
            from_config(config.get("commands")), self._removed)

    # ------------------------------------------------------------ the table

    @property
    def commands(self) -> tuple[Command, ...]:
        return self._commands

    def visible(self) -> tuple[Command, ...]:
        return tuple(command for command in self._commands if command.shown)

    def keys(self) -> dict[str, str]:
        """Key -> command id, for the pane to match a keystroke against."""
        return by_shortcut(self._commands)

    def replace(self, commands: Sequence[Command]) -> None:
        """Take the editor's answer. Saved immediately rather than on close.

        A table edited and then lost to a crash is worse than most settings
        lost the same way: the user has just typed a command line they will not
        remember, and the keys they assigned are now missing with no sign of
        why.
        """
        self._commands = tuple(commands)
        self._removed = removals(self._commands, self._removed)
        self._config.set("commands", to_config(self._commands))
        self._config.set("commands.removed", self._removed)
        self._config.save()
        self.changed.emit()

    def reset(self) -> None:
        """Back to the shipped table, deletions and all forgotten."""
        self._removed = []
        self.replace(DEFAULTS)

    # -------------------------------------------------------------- running

    def run(self, identity: str, context: Context) -> bool:
        """Start one command. False means it was refused and why was said.

        The refusal goes out on `problem` rather than being raised or returned
        as a string, so the caller does not have to decide where a sentence
        about the panes belongs -- there is one place it belongs and the window
        already connects to it.
        """
        command = find(self._commands, identity)
        if command is None:
            self.problem.emit(identity, "that command is not in the table")
            return False
        why = refusal(command, context)
        if why:
            self.problem.emit(identity, why)
            return False

        launch = expand(command, context)
        self._bridge.submit(
            Op.RUN,
            context.path or launch.working,
            timeout=float(self._config.get("timeout.run")),
            on_reply=self._replier(command),
            args={
                "program": launch.program,
                "alternatives": list(launch.alternatives),
                "arguments": list(launch.arguments),
                "working": launch.working,
                "list": list(launch.list_names),
            },
        )
        return True

    def _replier(self, command: Command):
        def handle(reply: Reply) -> None:
            self._on_reply(command, reply)
        return handle

    def _on_reply(self, command: Command, reply: Reply) -> None:
        if reply.status is Status.OK:
            payload = reply.payload if isinstance(reply.payload, dict) else {}
            self.ran.emit(command.id, str(payload.get("program", "")))
            return
        if reply.status is Status.TIMEOUT:
            # Worth its own sentence. A launch that timed out did not fail to
            # find the program: it found the folder unresponsive, which is a
            # fact about the share and not about the command.
            self.problem.emit(command.id,
                              f"{command.name}: that folder is not answering")
            return
        self.problem.emit(command.id, reply.message or f"{command.name} did not start")

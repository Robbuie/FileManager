"""Reducing the several spellings of a Windows path to the one form everything
else keys on.

Drive letters and UNC paths are used interchangeably in conversation but they
do not behave the same. `S:\\` is a session; `\\\\server\\share` is the thing
the session points at. When the session dies the letter is still present and
still dead, while the UNC is still something a reconnect can be attempted
against. So all real work happens in UNC and the letter survives only as a
display preference.

Anything that keys on a path keys on the resolved form: the worker pool, the
caches, the operation queue. Keying on what was typed gives two entries for one
server, which then hang separately and restart separately, which is the failure
this application exists to avoid.

Nothing in this module touches the network. `WNetGetConnection` reads the local
session table; `GetLogicalDrives` and `GetDriveType` read local state. Presence
of a letter in that table is not evidence that the server behind it answers,
and treating it as evidence is precisely the startup hang being escaped.
"""

from __future__ import annotations

import os
import re
import string
import threading
from dataclasses import dataclass
from typing import Iterable, Mapping

#: Why pywin32 could not be imported, if it could not. Kept as text because a
#: broken install and an absent one both raise ImportError and only the message
#: tells them apart -- a DLL load failure is the usual way pywin32 goes wrong.
_win32_error: str | None = None

try:
    import win32wnet
    import win32file
except Exception as exc:  # noqa: BLE001 - reported by win32_problem()
    win32wnet = None
    win32file = None
    _win32_error = f"{type(exc).__name__}: {exc}"

#: True when drive letters can actually be resolved.
WIN32_AVAILABLE = win32wnet is not None and win32file is not None


def win32_problem() -> str | None:
    """Why drive letters cannot be resolved, or None when they can.

    This gets a function rather than a flag because the failure is silent and
    expensive. Without pywin32 every mapped letter resolves to itself, so
    `volume_key` answers "local" for a network path, so a share is served by
    the worker that serves the local disks. When that share hangs it takes
    every local tab with it, and nothing in the output says why. Anything that
    can report this to the user should.
    """
    if WIN32_AVAILABLE:
        return None
    detail = f" ({_win32_error})" if _win32_error else ""
    if os.name == "nt":
        return (
            "pywin32 is not importable in this interpreter" + detail + ". Drive "
            "letters cannot be resolved to UNC, so every network path is keyed "
            "as a local volume and a hung share would take the local disks with "
            "it. Install it into the interpreter that runs the app: "
            "pip install pywin32"
        )
    return (
        "not running on Windows" + detail + "; drive letters resolve to "
        "themselves and every path is keyed as local"
    )


#: Every local disk shares one worker; only servers get their own.
LOCAL_VOLUME_KEY = "local"

_UNC_RE = re.compile(r"^\\\\([^\\]+)\\([^\\]+)(\\.*)?$")
_DRIVE_RE = re.compile(r"^([A-Za-z]):(\\.*)?$")
_DRIVE_ROOT_RE = re.compile(r"^[A-Z]:\\$")

#: `GetDriveType` results. Local read; it consults the session table, not the
#: server, so a dead mapped drive still answers "remote" immediately.
_DRIVE_TYPES = {
    0: "unknown",
    1: "invalid",
    2: "removable",
    3: "fixed",
    4: "remote",
    5: "cdrom",
    6: "ramdisk",
}


@dataclass(frozen=True, slots=True)
class Drive:
    """A drive letter as the local session table describes it.

    `unc` being set means the letter is a network mapping and names what it is
    mapped to. It says nothing about whether that server is reachable.
    """

    letter: str          # "S:", never with a trailing separator
    type: str            # one of the `_DRIVE_TYPES` values
    unc: str | None      # resolved target for a mapping, else None
    #: 0.27: a stick, a card, or a USB disk -- something Eject is offered for.
    ejectable: bool = False


#: A path is rewritten only if it is recognisably a Windows one: a drive
#: letter, a UNC prefix, or a backslash somewhere in it. Anything else is
#: returned untouched, which is what lets the io layer be exercised on a POSIX
#: machine — the only part of this application that can be tested without a
#: person watching a window.
_WINDOWS_SHAPED_RE = re.compile(r"^[A-Za-z]:|^[\\/]{2}|\\")


def normalize(path: str) -> str:
    """One spelling per path: backslashes, no doubled separators, upper-case
    drive letter, no trailing separator except on a drive root.

    `C:\\` and `C:` are deliberately not the same thing — the second means the
    current directory on C: — so a drive root keeps its separator.
    """
    if not path or not _WINDOWS_SHAPED_RE.search(path):
        return path
    text = path.replace("/", "\\")
    prefix = ""
    if text.startswith("\\\\"):
        # A run of leading separators is one UNC prefix, however many were typed.
        prefix, text = "\\\\", text[2:].lstrip("\\")
    while "\\\\" in text:
        text = text.replace("\\\\", "\\")
    text = prefix + text
    drive = _DRIVE_RE.match(text)
    if drive:
        text = drive.group(1).upper() + ":" + (drive.group(2) or "\\")
    if text.endswith("\\") and not _DRIVE_ROOT_RE.match(text):
        stripped = text.rstrip("\\")
        if stripped not in ("", "\\"):
            text = stripped
    return text


def is_unc(path: str) -> bool:
    return _UNC_RE.match(normalize(path)) is not None


def split_unc(path: str) -> tuple[str, str, str] | None:
    """`(server, share, remainder)` for a UNC path, else None."""
    match = _UNC_RE.match(normalize(path))
    if match is None:
        return None
    return match.group(1), match.group(2), match.group(3) or ""


def drive_letter(path: str) -> str | None:
    """`"S:"` for a path on a drive letter, else None."""
    match = _DRIVE_RE.match(normalize(path))
    return match.group(1) + ":" if match else None


def share_root(path: str) -> str | None:
    r"""`\\server\share` for anything on a share, else None.

    What a reconnect is attempted against. Windows attaches to a *share*, not
    to a folder inside one, so the answer for `\\server\share\jobs\1234` is the
    share above it. Resolution is the caller's job: a drive letter has to be
    resolved first or a path that is on a share after all answers None here.
    """
    parts = split_unc(path)
    if parts is None:
        return None
    return "\\\\" + parts[0] + "\\" + parts[1]


def is_bare_name(name: str) -> bool:
    r"""Whether `name` names something *inside* a folder and nowhere else.

    Every destructive op in this application is addressed as a folder plus the
    names in it, and the join that puts the two together is
    `os.path.join(folder, name)` -- which **discards the folder** when the name
    turns out to be absolute. `os.path.join("S:\\Jobs", "C:\\Windows")` is
    `C:\Windows`, so a name that is not a name is not a mis-delete inside the
    folder, it is a delete somewhere else entirely. That is a check rather than
    an assumption because the names do not always come from a listing: an
    elevation plan is a file in the temp folder, and `io/elevate.py` is plain
    that anything running as this user can rewrite it between the moment it is
    written and the moment the elevated process reads it.

    Deliberately narrower than the character list `_rename` refuses. That one
    is answering "would Windows accept this as a new name", which is a bigger
    question and can afford to be strict. This one is answering "does this
    escape the folder", and a file that already exists has to stay deletable
    even if a POSIX client on the other end of a share gave it a name Windows
    itself would not have allowed. So it refuses exactly the four shapes that
    leave the folder: a separator, a drive-relative colon, the two dot names,
    and nothing at all.
    """
    if not name or name in (".", ".."):
        return False
    return not any(ch in name for ch in "\\/:")


def bare_names(names: Iterable[str]) -> list[str]:
    """The names in `names` that are not bare, for a caller that refuses them.

    Returns the offenders rather than a yes or no: an operation that stops has
    to say which name stopped it, and a message naming the file is the
    difference between a bug report and a shrug.
    """
    return [str(name) for name in names if not is_bare_name(str(name))]


# --------------------------------------------------------------------------
# The extended-length form.
#
# Windows' file calls stop at 260 characters unless a path carries the `\\?\`
# prefix, and a folder tree on a share goes past that without anybody trying --
# a job number, a discipline, a revision and a drawing name is most of it
# before the file is named. What makes it worth handling rather than ignoring
# is how it fails: `os.scandir` answers a folder that is plainly there with
# "The system cannot find the path specified", so it arrives looking like a bug
# in whichever feature reached it first rather than like a length.
#
# Two properties of the prefix decide where it may be used.
#
# It turns path normalization *off*: the string goes to the filesystem as it
# stands, so it has to be fully qualified and already tidy. `normalize` is what
# makes that true here, which is why this is built on it rather than on
# whatever was typed.
#
# And **the shell does not take it**. `SHFileOperation`, `SHGetFileInfo`,
# `IContextMenu`, `ShellExecuteEx` and `IShellItemImageFactory` answer a
# prefixed path with a failure or with nothing. So the prefix belongs at the
# file calls -- scandir, stat, open, mkdir, rename, remove -- and nowhere else.
# It never reaches the shell, never reaches a worker key, and never reaches the
# window: a path bar reading `\\?\UNC\server\share` would be showing somebody
# an implementation detail they cannot type back.
# --------------------------------------------------------------------------

#: The limit the prefix exists to get past.
MAX_PATH = 260

#: Whether a file call gets the prefixed form. Off anywhere but Windows, which
#: is the only place the prefix is understood and the only place the limit
#: exists -- and a module flag rather than an `os.name` test at each call site,
#: so a test can turn it on and read what the io layer would have sent.
EXTENDED_PATHS = os.name == "nt"

_EXTENDED_PREFIX = "\\\\?\\"
_DEVICE_PREFIX = "\\\\.\\"


def extended(path: str) -> str:
    r"""The `\\?\` spelling of a path, on any platform.

    Pure string work and it always rewrites; `api` below is what decides
    whether a call actually gets it. Anything the prefix would be wrong for is
    returned unchanged rather than guessed at: a path that already carries it,
    a device path, and anything not fully qualified -- the prefix means "do not
    work out what this is relative to", and a relative path under it is simply
    not found.
    """
    if not path:
        return path
    text = normalize(path)
    if text.startswith(_EXTENDED_PREFIX) or text.startswith(_DEVICE_PREFIX):
        return text
    if split_unc(text) is not None:
        return _EXTENDED_PREFIX + "UNC\\" + text[2:]
    if _DRIVE_RE.match(text) and text[2:3] == "\\":
        return _EXTENDED_PREFIX + text
    return text


def api(path: str) -> str:
    r"""What to hand a Win32 file call: the extended form where Windows is
    listening, the path itself otherwise.

    Every real filesystem call in `app/io` goes through this, and every call
    that goes to the *shell* deliberately does not. It is idempotent, so a
    path built from one that has already been through it -- `entry.path` from a
    scan, a target joined onto a folder -- costs nothing to pass again.
    """
    return extended(path) if EXTENDED_PATHS else path


# --------------------------------------------------------------------------
# The letter -> UNC table.
#
# Cached because it is read on every resolve and changes only when the user
# maps or unmaps a drive. A mapping whose session has died still reads back
# fine, which is the point: it is what a reconnect is attempted against.
# --------------------------------------------------------------------------

_mapping_lock = threading.Lock()
_mapping_cache: dict[str, str | None] = {}


def mapping_for(letter: str, *, refresh: bool = False) -> str | None:
    """The UNC a mapped letter points at, or None if it is not a mapping."""
    letter = normalize(letter).rstrip("\\").upper()
    if not re.fullmatch(r"[A-Z]:", letter):
        return None
    if not refresh:
        with _mapping_lock:
            if letter in _mapping_cache:
                return _mapping_cache[letter]
    value: str | None = None
    if win32wnet is not None:
        try:
            value = normalize(win32wnet.WNetGetConnection(letter))
        except Exception:
            # Raising is the normal answer for a local disk, and the pywin32
            # error type varies by failure. There is nothing to distinguish
            # here: either a UNC came back or the letter is not a mapping.
            value = None
    with _mapping_lock:
        _mapping_cache[letter] = value
    return value


def invalidate_mappings(letter: str | None = None) -> None:
    """Forget cached mappings, for after a drive is mapped or unmapped."""
    with _mapping_lock:
        if letter is None:
            _mapping_cache.clear()
        else:
            _mapping_cache.pop(normalize(letter).rstrip("\\").upper(), None)


def logical_drives() -> list[str]:
    """Letters present in this session. A bitmask read; no volume is touched."""
    if win32file is None:
        return []
    mask = win32file.GetLogicalDrives()
    return [f"{c}:" for i, c in enumerate(string.ascii_uppercase) if mask >> i & 1]


def drive_type(letter: str) -> str:
    if win32file is None:
        return "unknown"
    root = normalize(letter).rstrip("\\").upper() + "\\"
    return _DRIVE_TYPES.get(win32file.GetDriveType(root), "unknown")


def drives(*, refresh: bool = False) -> list[Drive]:
    """Enumerate drive letters without probing any of them.

    Volume labels and free space are deliberately not here: both open the
    volume, which is a blocking call against a network drive.
    """
    result: list[Drive] = []
    for letter in logical_drives():
        kind = drive_type(letter)
        unc = mapping_for(letter, refresh=refresh) if kind == "remote" else None
        ejectable = False
        if kind in ("removable", "fixed"):
            from app.io.eject import is_ejectable

            ejectable = is_ejectable(letter, kind)
        result.append(Drive(letter=letter, type=kind, unc=unc, ejectable=ejectable))
    return result


#: The scope and shape of a connection enumeration. `RESOURCE_CONNECTED` is the
#: redirector's own table of what this session is attached to *right now* --
#: the same table `WNetGetConnection` reads, so it costs nothing and touches no
#: server. Spelled out rather than imported from `win32netcon` because these
#: four numbers are stable and the import is one more thing to be missing.
_RESOURCE_CONNECTED = 1
_RESOURCETYPE_DISK = 1
_RESOURCEUSAGE_CONNECTABLE = 1
_CONNECT_UPDATE_PROFILE = 1

#: How many entries to ask for per call. A session has a handful, not
#: thousands; this is one round trip for the usual case and a loop for the rest.
_ENUM_BATCH = 32


@dataclass(frozen=True, slots=True)
class Connection:
    r"""A network location this session is attached to.

    `local` is the drive letter it was mapped to, **or empty** -- and the empty
    case is the entire reason this exists. A Hyper-V or Remote Desktop
    redirected drive is a real connection with no letter at all: it is
    `\\tsclient\C` and nothing else, so `GetLogicalDrives` never mentions it
    and a drive list built from letters cannot show it.
    """

    remote: str
    local: str = ""
    provider: str = ""

    @property
    def label(self) -> str:
        """What to call it in a list. The letter when it has one, because that
        is what the user types; otherwise the share's own name."""
        if self.local:
            return self.local.rstrip("\\")
        pieces = split_unc(self.remote)
        return pieces[1] if pieces else self.remote


def connections() -> tuple[list[Connection], str]:
    """Every network location this session is attached to, letters or not.

    Reads the redirector's table of current connections. That table is local --
    it is what `WNetGetConnection` answers from, and what Explorer's "Network
    locations" is drawn from -- so this does not contact a server and cannot
    block on one that has gone. **Enumerating the network itself is a different
    call and is deliberately not made here**: `RESOURCE_GLOBALNET` asks the
    browser service what exists out there, which is a real network round trip
    and is how a file manager ends up hanging on startup.

    Returns the list **and why it is short**, which is not decoration. "No
    connections" and "the enumeration would not open" look identical from the
    outside and mean entirely different things -- the first is a machine with
    nothing mapped, the second is this call being wrong about how Windows
    holds something. On 15 September the first run of `harness network` on a
    Hyper-V guest reported nothing, and with the failure swallowed there was
    no way to tell which of the two it was. Now it says.
    """
    if win32wnet is None:
        return [], "not running on Windows"
    try:
        handle = win32wnet.WNetOpenEnum(
            _RESOURCE_CONNECTED, _RESOURCETYPE_DISK,
            _RESOURCEUSAGE_CONNECTABLE, None)
    except Exception as exc:  # noqa: BLE001 - pywintypes.error is not an OSError
        return [], f"the connection list would not open: {_why(exc)}"

    found: list[Connection] = []
    problem = ""
    try:
        while True:
            try:
                batch = win32wnet.WNetEnumResource(handle, _ENUM_BATCH)
            except Exception as exc:  # noqa: BLE001 - the end of the list raises
                # `ERROR_NO_MORE_ITEMS` is how the list ends and is not a
                # fault; anything else is, and is worth saying rather than
                # reading as an empty machine.
                if getattr(exc, "winerror", None) not in (259, None):
                    problem = f"the connection list stopped: {_why(exc)}"
                break
            if not batch:
                break
            for item in batch:
                remote = normalize(str(getattr(item, "lpRemoteName", "") or ""))
                if not remote or not is_unc(remote):
                    continue
                found.append(Connection(
                    remote=remote,
                    local=str(getattr(item, "lpLocalName", "") or ""),
                    provider=str(getattr(item, "lpProvider", "") or ""),
                ))
    finally:
        try:
            win32wnet.WNetCloseEnum(handle)
        except Exception:  # noqa: BLE001 - nothing useful to do about it
            pass
    return _without_duplicates(found), problem


def _why(exc: BaseException) -> str:
    """A Windows error in the words it came with, and its number."""
    detail = str(getattr(exc, "strerror", None) or exc).strip()
    number = getattr(exc, "winerror", None)
    return f"{detail} ({number})" if number is not None else detail


def _without_duplicates(found: list[Connection]) -> list[Connection]:
    """One row per remote path, preferring the one that has a letter.

    A drive mapped to a share this session also holds without a letter is one
    place, and listing it twice invites somebody to wonder which is the real
    one.
    """
    best: dict[str, Connection] = {}
    for item in found:
        key = item.remote.lower()
        if key not in best or (item.local and not best[key].local):
            best[key] = item
    return sorted(best.values(), key=lambda c: (not c.local, c.label.lower()))


def connect(remote: str, *, remember: bool = False) -> str:
    """Re-establish a connection to a share. Returns "" or why it failed.

    This *is* a network call and belongs nowhere near the UI thread. It is the
    one operation here that can sit for the full SMB timeout, which is why the
    op that carries it has its own deadline.

    No credentials are passed. Windows uses the session's own, which is the
    case that matters -- a share that dropped because the server restarted
    comes back without anybody being asked anything. A share that genuinely
    needs a different account fails here with the reason, and that is a better
    outcome than a prompt this application would have to own.
    """
    if win32wnet is None:
        return "not running on Windows"
    resource = win32wnet.NETRESOURCE()
    resource.dwType = _RESOURCETYPE_DISK
    resource.lpRemoteName = normalize(remote)
    resource.lpLocalName = None
    try:
        win32wnet.WNetAddConnection2(
            resource, None, None, _CONNECT_UPDATE_PROFILE if remember else 0)
    except Exception as exc:  # noqa: BLE001 - pywintypes.error is not an OSError
        return str(getattr(exc, "strerror", None) or exc)
    return ""


def mapped_drives(*, refresh: bool = False) -> dict[str, str]:
    return {d.letter: d.unc for d in drives(refresh=refresh) if d.unc}


# --------------------------------------------------------------------------
# Resolution and display.
# --------------------------------------------------------------------------


def resolve(path: str, *, mapping: Mapping[str, str] | None = None) -> str:
    """The form all real work uses: UNC where the path is on a mapping, the
    normalized path otherwise.

    `mapping` overrides the session table, for tests and for a caller that has
    already read it.
    """
    text = normalize(path)
    letter = drive_letter(text)
    if letter is None:
        return text
    unc = mapping.get(letter) if mapping is not None else mapping_for(letter)
    if not unc:
        return text
    return normalize(normalize(unc) + text[len(letter):])


def volume_key(path: str, *, mapping: Mapping[str, str] | None = None) -> str:
    """The key the worker pool spawns against: one worker per server.

    Per server rather than per share, because the thing that hangs is the
    connection to the server. Two shares on one server that hang independently
    would mean two processes to kill for one failure.
    """
    parts = split_unc(resolve(path, mapping=mapping))
    if parts is None:
        return LOCAL_VOLUME_KEY
    return "\\\\" + parts[0].lower()


def display(
    path: str,
    *,
    prefer_letter: bool,
    mapping: Mapping[str, str] | None = None,
) -> str:
    """The form shown to the user: a per-tab preference, never a stored value.

    Falls back to the UNC when no letter covers the path, rather than inventing
    one; a path that cannot be displayed as a letter is not an error.
    """
    resolved = resolve(path, mapping=mapping)
    if not prefer_letter:
        return resolved
    table = dict(mapping) if mapping is not None else mapped_drives()
    lowered = resolved.lower()
    best: tuple[str, str] | None = None
    for letter, unc in table.items():
        target = normalize(unc or "")
        if not target:
            continue
        low = target.lower()
        if lowered == low or lowered.startswith(low + "\\"):
            if best is None or len(target) > len(best[1]):
                best = (normalize(letter).rstrip("\\").upper(), target)
    if best is None:
        return resolved
    return normalize(best[0] + resolved[len(best[1]):])


# --------------------------------------------------------------------------
# Walking a path. String work only: none of this asks the filesystem anything,
# which is what lets `core` call it while deciding where a pane should go.
# --------------------------------------------------------------------------


def parent(path: str) -> str | None:
    """The folder above, or None when there is nowhere above to go.

    A drive root and a share root both answer None. `\\\\server` on its own is
    not a place a pane can be — there is no listing to show — so it is a root
    too.
    """
    text = normalize(path)
    if _DRIVE_ROOT_RE.match(text):
        return None
    parts = split_unc(text)
    if parts is not None and not parts[2]:
        return None
    head, sep, _ = text.rpartition("\\")
    if not sep:
        return None
    return normalize(head) if head else None


def join(path: str, name: str) -> str:
    """A child of a folder. Not `os.path.join`: this runs in the UI's process."""
    return normalize(normalize(path).rstrip("\\") + "\\" + name)


def leaf(path: str) -> str:
    """The last segment, for a tab label. A root is named by itself."""
    text = normalize(path)
    if _DRIVE_ROOT_RE.match(text):
        return text
    parts = split_unc(text)
    if parts is not None and not parts[2]:
        return text
    _, sep, tail = text.rpartition("\\")
    return tail if sep and tail else text


def crumbs(path: str) -> list[tuple[str, str]]:
    """A path as `(label, path)` from the root down, for a breadcrumb bar.

    Built by walking `parent` rather than by splitting on separators, so the
    two agree by construction: every crumb is somewhere `parent` says is above
    the last one, and the root is whatever `parent` refuses to go above. That
    is what makes `\\\\server\\share` one crumb rather than two empty ones
    followed by a server that is not a place a pane can be.

    Pure string work, and deliberately so -- this is called on every
    navigation and is drawn in the UI's own process. Nothing here asks a
    volume anything.
    """
    text = normalize(path)
    if not text:
        return []
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    while text and text not in seen:
        seen.add(text)
        out.append((leaf(text) or text, text))
        up = parent(text)
        if up is None:
            break
        text = up
    out.reverse()
    return out


def same_volume(a: str, b: str, *, mapping: Mapping[str, str] | None = None) -> bool:
    return volume_key(a, mapping=mapping) == volume_key(b, mapping=mapping)

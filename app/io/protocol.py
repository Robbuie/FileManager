"""The wire format between the UI process and the workers.

Everything crossing the process boundary is defined here and nowhere else, and
everything defined here is plain and picklable: no `Path`, no Qt types, no open
handles. Two rules make late replies harmless, which matters because a tab can
navigate away while a 50,000-file listing is still arriving:

  * every request carries an id, and every reply carries the id it answers, so
    a stale reply can be dropped without special-casing;
  * every call returns a `Reply` whether it succeeded or not. A timeout is an
    ordinary result with `status=TIMEOUT`, not an exception the caller has to
    catch. Callers that must handle failure anyway are better off handling it
    on the same path as success.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Status(str, Enum):
    """Outcome of a request. `str` so it survives a repr in a log unchanged."""

    OK = "ok"
    PARTIAL = "partial"      # a batch of a streamed reply; more are coming
    TIMEOUT = "timeout"      # the operation exceeded its deadline
    CANCELLED = "cancelled"  # the caller withdrew it
    DENIED = "denied"        # access refused; retrying will not help
    GONE = "gone"            # path or volume no longer reachable
    ERROR = "error"          # anything else, with detail in `message`


class Op(str, Enum):
    """Requests a worker understands."""

    LIST = "list"
    STAT = "stat"
    DIR_SIZE = "dir_size"
    RESOLVE = "resolve"
    PING = "ping"

    #: The subfolder names of one folder, capped and in one reply. What a
    #: breadcrumb chevron drops down.
    #:
    #: Deliberately not LIST with a flag. LIST streams, fills a model and is
    #: what a pane is waiting on; this answers a menu that will be thrown away
    #: in a second either way. So it stops at `args["limit"]` names rather than
    #: enumerating a 50,000-row folder to show twenty of them, it returns
    #: names rather than rows because a menu has nowhere to put a size, and it
    #: sorts in the worker so nothing downstream has a list long enough to be
    #: worth sorting. The reply is `{"names": [...], "more": bool}`.
    #:
    #: The cap is on the *scan*, which is worth being clear about: past the
    #: limit the names are the first ones the directory happened to hand over
    #: and not the first alphabetically, and `more` is how the caller knows to
    #: say so. Making it the alphabetically-first N would mean enumerating the
    #: whole folder to find out which those are, which is the cost being
    #: avoided.
    FOLDERS = "folders"

    #: Shell icons for a set of kinds, `args["keys"]`, at `args["size"]`.
    #: A kind is an extension, `ICON_FOLDER` or `ICON_FILE` -- never a path,
    #: and that is the point. The shell is asked with SHGFI_USEFILEATTRIBUTES,
    #: which answers from the extension alone and does not go near a volume,
    #: so a folder of 50,000 rows on a share that is answering slowly costs
    #: one lookup per distinct extension against the local registry rather
    #: than 50,000 reads over SMB.
    #:
    #: The reply is `{"size": n, "icons": {key: bgra}}`, where `bgra` is
    #: `n * n` pixels of premultiplied blue, green, red, alpha -- bytes,
    #: because a handle does not cross a process boundary and an image object
    #: is not picklable. A key the shell had nothing for is absent.
    ICON = "icon"

    #: Hand a path to the shell and let Windows decide what opens it. In a
    #: worker like everything else: ShellExecute against a path on a share that
    #: has gone away blocks exactly as a listing does, and the association
    #: lookup itself can touch the file.
    OPEN = "open"

    #: The drive letters this session has, from the local session table. It
    #: reads nothing off any volume, but it is still a filesystem call and the
    #: UI thread does not make those.
    DRIVES = "drives"

    #: Free and total bytes for the volume a path is on. Unlike DRIVES this
    #: does open the volume, so it carries a deadline and a failure is a blank
    #: readout rather than an error.
    FREE_SPACE = "free_space"

    # The ops that change what is on disk. They are single calls rather than a
    # queue, which is what makes them worker ops: a copy of 4,000 files needs
    # pausing, per-file progress and a conflict rule, and belongs in `ops.py`
    # when that exists. Making a folder does not.
    #
    # What they share is that a partial success is a real outcome -- three of
    # five files deleted, the folder made but not the one inside it -- so each
    # reply says what actually happened rather than only whether it worked.

    #: Create one folder. The path is the folder to create, not its parent.
    MKDIR = "mkdir"

    #: Rename in place. `args["name"]` is a bare name, never a path: a rename
    #: that can move is a move, and a move has a destination the user confirms.
    RENAME = "rename"

    #: Delete `args["names"]` from the folder at `path`, to the Recycle Bin
    #: unless `args["permanent"]`. Several names in one request rather than one
    #: request each, because the shell treats one call as one operation and
    #: that is what makes it one undo.
    DELETE = "delete"

    #: The Explorer context menu for a selection, as a tree of `MenuItem`.
    #: `args["names"]` are the names in the folder at `path`; an empty list
    #: means the menu for the folder itself. `args["extended"]` asks for the
    #: entries Explorer hides behind Shift.
    #:
    #: Answered by the shell host rather than by a volume's worker, and the
    #: reply is `{"token": id, "items": [...]}`. The token is what INVOKE
    #: names: the shell objects behind the menu stay alive in the host until
    #: the menu is invoked or released, because a command cannot be run
    #: through an `IContextMenu` that has been let go of.
    MENU = "menu"

    #: Run one entry of a menu that MENU built. `args["token"]` and
    #: `args["item"]`, the id from the tree. The host holds the only mapping
    #: from that id to the shell's own command, which is why an invoke is a
    #: request rather than something the window can do for itself.
    MENU_INVOKE = "menu_invoke"

    #: Let go of a menu that was built and not used -- the user pressed Escape.
    #: Not merely tidiness: a live `IContextMenu` keeps a third-party DLL's
    #: objects alive, and some of them hold the folder open.
    MENU_RELEASE = "menu_release"

    #: Icon overlays for `args["names"]` in the folder at `path`: the shared
    #: folder arrow, the OneDrive tick, a source control badge.
    #:
    #: The one icon request that carries a path, and it has to: an overlay is
    #: a fact about the file rather than about its type, and the handler is
    #: asked about that file by name. So it goes to that volume's worker,
    #: covers the rows on screen rather than the folder, and is asked for with
    #: a short deadline -- everything ICON avoids by construction, this one
    #: has to bound by hand.
    #:
    #: The reply is `{"size": n, "rows": {name: key}, "images": {key: bgra}}`.
    #: A key is `"<system icon index>:<overlay index>"`, so two files with the
    #: same type and the same badge share one picture: a working copy of 400
    #: modified files is a handful of images, not 400.
    OVERLAY = "overlay"

    #: Run one operation again with administrator rights, after Windows
    #: refused it. `args["plan"]` says which operation and on what. Nothing is
    #: elevated silently: this is only ever sent because a person answered a
    #: prompt, and what it elevates is one operation rather than the
    #: application.
    ELEVATE = "elevate"

    #: Fault injection, and the harness is the only thing allowed to send it.
    #: It exists because the failure this application is built around -- a call
    #: that has not returned and never will -- cannot otherwise be produced on
    #: demand, and a recovery path that has only ever been reasoned about is not
    #: a recovery path. Nothing in `ui` or `core` may send it.
    STALL = "stall"


@dataclass(frozen=True, slots=True)
class Entry:
    """One row of a listing.

    Populated entirely from `os.scandir`, whose `DirEntry` carries size, mtime
    and attributes from the directory enumeration itself. Adding a field that
    needs `os.stat` turns a 50,000-row listing into 50,000 SMB round trips —
    under a second becomes over a minute. Anything that expensive is a separate
    lazy request (`ICON`, `DIR_SIZE`), never a column filled in here.
    """

    name: str
    is_dir: bool
    size: int
    mtime: float
    attributes: int
    is_link: bool = False


@dataclass(frozen=True, slots=True)
class Request:
    """A unit of work for a worker.

    `timeout` is not optional and has no default of `None`. An operation
    without a deadline is an incomplete operation, not a simpler one.
    """

    id: int
    op: Op
    path: str                       # always resolved UNC, never a drive letter
    timeout: float                  # seconds
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Reply:
    """The answer to exactly one `Request`.

    A streamed listing arrives as a series of replies with `status=PARTIAL`,
    each carrying up to `BATCH_SIZE` entries, terminated by one final reply
    with a settled status. `seq` orders them.
    """

    id: int
    status: Status
    payload: Any = None
    message: str = ""
    seq: int = 0


#: Rows per streamed batch. Large enough that the queue is not the bottleneck,
#: small enough that the first rows paint while the rest are still arriving.
BATCH_SIZE = 1000

#: The two icon kinds that are not an extension. A folder is not a file with no
#: extension, and Windows does not think it is one either.
ICON_FOLDER = "folder"
ICON_FILE = "file"

#: The sizes the shell keeps a system image list for. Anything else is one of
#: these scaled, and looks it, so a caller picks between them rather than
#: passing pixels and hoping.
ICON_SIZES = (16, 32)


#: The pool key for the shell host. Not a volume and deliberately not
#: shaped like one: `volume_key` answers a drive letter or a server name, and
#: nothing it can return starts with a space.
MENU_HOST = " shell"

#: What a menu entry is. `SEPARATOR` carries no text and nothing else.
MENU_COMMAND = "command"
MENU_SUBMENU = "submenu"
MENU_SEPARATOR = "separator"


@dataclass(frozen=True, slots=True)
class MenuItem:
    """One entry of an Explorer context menu, as something Qt can draw.

    The shell builds its menu into an `HMENU` full of handles, ids that mean
    something only to the extension that supplied them, and bitmaps. None of
    that crosses a process boundary, so the host walks it into these: text,
    state, a picture as bytes, and an `id` that means something only when
    handed back with the token it came with.

    `id` is the shell's own command id and is not unique across menus. It is
    only ever used with its token, and the host refuses one that does not
    belong to the menu still open.
    """

    id: int
    kind: str = MENU_COMMAND
    text: str = ""
    enabled: bool = True
    checked: bool = False
    default: bool = False
    verb: str = ""              # the extension's own name for it, when it has one
    help: str = ""              # the line Explorer shows in its status bar
    icon: bytes | None = None   # premultiplied BGRA, `icon_size` square
    icon_size: int = 0
    items: tuple["MenuItem", ...] = ()


def icon_key(entry: Entry) -> str:
    """The icon kind an entry draws as.

    Here rather than beside the model because it is the vocabulary of the
    request: the worker answers in these keys and the cache is keyed on them,
    so there is one definition of what a kind is and both ends use it.

    Lowercased, because the association database does not distinguish PDF from
    pdf and a cache that did would ask for both. A leading dot is part of a
    name rather than an extension, which is why `.gitignore` is a file with no
    extension and not a kind of its own.
    """
    if entry.is_dir:
        return ICON_FOLDER
    stem, dot, suffix = entry.name.rpartition(".")
    if not dot or not stem or not suffix:
        return ICON_FILE
    return f".{suffix.lower()}"


# --------------------------------------------------------------------------
# Transfers.
#
# A separate vocabulary from the request/reply above, because a transfer is not
# a request: it is long, it is interactive -- a conflict is a question asked
# back -- and it outlives the folder it started from. What it shares is that
# everything here is plain and picklable.
# --------------------------------------------------------------------------


class Transfer(str, Enum):
    COPY = "copy"
    MOVE = "move"


class Conflict(str, Enum):
    """What to do about a name that is already taken at the destination.

    `ASK` is the default and the only one that stops. The others exist so that
    an answer can be applied to the rest of the queue without asking again --
    which is the difference between a usable copy of 400 files and one that
    holds a dialog up in front of the user 400 times.
    """

    ASK = "ask"
    SKIP = "skip"
    OVERWRITE = "overwrite"
    NEWER = "newer"          # overwrite only when the source is newer
    RENAME = "rename"        # keep both; the incoming one gets "(2)"


@dataclass(frozen=True, slots=True)
class Job:
    """One transfer, as the window asks for it.

    `sources` are full paths and `destination` is a folder that must already
    exist. The application never invents a destination: it is confirmed before
    the job is made, and nothing here will create one.
    """

    id: int
    kind: Transfer
    sources: tuple[str, ...]
    destination: str
    conflict: Conflict = Conflict.ASK


class Progress(str, Enum):
    """What the ops process says while it works."""

    SCANNING = "scanning"    # counting what is about to move
    SCANNED = "scanned"      # totals known: {"files", "bytes"}
    STARTED = "started"      # a job began
    COPYING = "copying"      # {"name", "done", "total", "item_done", "item_total"}
    CONFLICT = "conflict"    # a question; the job waits for an Answer
    FAILED_ITEM = "item"     # one item failed; the job carries on
    PAUSED = "paused"
    RESUMED = "resumed"
    DONE = "done"            # {"copied", "skipped", "failed", "cancelled"}


@dataclass(frozen=True, slots=True)
class Event:
    """Something the ops process wants the window to know.

    One shape for all of them: a job id, what kind of thing happened, and a
    payload whose keys depend on the kind. A stream of differently shaped
    messages would need a match at every hop between here and the status bar.
    """

    job: int
    kind: Progress
    payload: dict[str, Any] = field(default_factory=dict)
    message: str = ""


@dataclass(frozen=True, slots=True)
class Answer:
    """The reply to a `CONFLICT`, and the only message that flows back in.

    `apply_to_all` is what keeps a queue moving: it turns one decision into the
    rule for the rest of this job.
    """

    job: int
    action: Conflict
    apply_to_all: bool = False


#: Bytes per read while copying. Big enough that the syscall overhead does not
#: show over SMB, small enough that a pause or a cancel is noticed within a
#: fraction of a second on a slow link.
CHUNK = 1024 * 1024

#: How often progress is reported, in seconds. A file-per-event stream is
#: thousands of events for a folder of small files, and the status bar cannot
#: read faster than a person can.
PROGRESS_INTERVAL = 0.15

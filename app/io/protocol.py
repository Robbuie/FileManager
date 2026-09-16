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

    #: Every file under a folder, streamed like LIST -- flat view, 0.25.
    #:
    #: Each entry's `name` is its path *relative to the folder asked about*
    #: (`2026-09-15\\HMI\\Screen.mer`), which is what lets everything
    #: downstream keep joining the tab's folder with a row's name and get the
    #: real path. Files only; a folder is walked, not listed. A link or junction
    #: to a folder is not followed, because a loop through one is a walk that
    #: never ends. A folder that cannot be read is skipped and counted.
    #:
    #: `args["limit"]` caps the files. The final reply's message says why the
    #: walk ended early -- `"limit"` -- and how many folders were skipped, as
    #: `"skipped=N"`, separated by a space.
    #:
    #: A walk through thousands of folders with no files in them sends no rows
    #: for a long time, and the pool's watchdog reads a quiet worker as a stuck
    #: one. So a PARTIAL goes out at least every `WALK_HEARTBEAT` seconds, even
    #: an empty one, which is what resets that deadline.
    WALK = "walk"

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

    #: The icon a file carries itself, for `args["names"]` in the folder at
    #: `path`, at `args["size"]`.
    #:
    #: The exception ICON exists to avoid, made explicit and bounded rather
    #: than smuggled in. An executable, a shortcut and an .ico do not draw as
    #: their type: the picture is inside the file, so the shell has to open it,
    #: and that is a read against the volume the rows came from. Which makes
    #: this the second request in the application that carries a path -- and it
    #: is bounded the same way OVERLAY is, with one addition that does most of
    #: the work: only the handful of kinds in `SELF_ICON_KINDS` are ever asked
    #: about, so a folder of 50,000 documents sends nothing at all.
    #:
    #: The reply is `{"size": n, "rows": {name: key}, "images": {key: bgra}}`,
    #: the shape OVERLAY answers in and for the same reason: a key is a digest
    #: of the picture, so a folder holding forty shortcuts to the same program
    #: is one image rather than forty. A name whose icon could not be read is
    #: absent from `rows`; the caller draws the icon for its kind, which is
    #: what it was already drawing.
    FILE_ICON = "file_icon"

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

    #: What one file looks like, decoded as far as it can be: an image, the
    #: text it holds, or the first few hundred bytes of it. `path` is the file
    #: itself rather than its folder, because this request is about one file
    #: and there is nothing to batch -- the viewer shows one thing and the
    #: preview pane shows one thing.
    #:
    #: The reply is a `Preview`. What crosses is a *scaled* picture rather than
    #: the file: `args["box"]` is the longest edge wanted, the worker decodes to
    #: that size and re-encodes as PNG, and `Preview.width`/`height` say how
    #: big the real thing is. A 6,000 x 4,000 photograph is forty megabytes of
    #: pixels and about a hundred kilobytes at the size a window can show, and
    #: the difference is per file on every arrow key.
    #:
    #: The read is the reason this is a worker op at all. Decoding is CPU and
    #: would be safe anywhere; opening a file on a share that has gone is the
    #: forty-five second block this application exists to escape, and it is the
    #: same block whether what follows is a listing or a photograph.
    PREVIEW = "preview"

    #: Small pictures for `args["names"]` in the folder at `path`, at
    #: `args["size"]`, for the grid view.
    #:
    #: Shaped like FILE_ICON rather than like PREVIEW, and the shape is the
    #: point: a grid of a hundred cells asking one request each is a hundred
    #: requests against one volume, so this batches a screenful into one and
    #: keys the pictures on a digest, which is what makes a folder of forty
    #: copies of the same drawing forty cells and one image. The reply is
    #: `{"size": n, "rows": {name: key}, "images": {key: png}}`, absent for a
    #: name nothing could be made of -- and an absent name draws the icon for
    #: its kind, which is what the listing was drawing anyway.
    THUMBNAIL = "thumbnail"

    #: Every network location this session is attached to, letters or not.
    #:
    #: DRIVES answers from `GetLogicalDrives`, which reports **letters** -- so
    #: a connection without one is invisible to it, and a Hyper-V or Remote
    #: Desktop redirected drive is exactly that: `\\tsclient\C` and no letter
    #: anywhere. This reads the redirector's own table of current connections,
    #: which is the table `WNetGetConnection` answers from and is local, so it
    #: contacts no server and cannot block on one that has gone.
    #:
    #: Deliberately *not* an enumeration of the network. `RESOURCE_GLOBALNET`
    #: asks what exists out there, which is a real round trip and is how a file
    #: manager comes to hang while drawing its sidebar. What is out there is
    #: not this application's question; what this session already holds is.
    #:
    #: The reply is a list of `{"remote", "local", "provider", "label"}`.
    NETWORK = "network"

    #: Re-establish a connection to `path`, a UNC share.
    #:
    #: The one op here that is a *network* call by nature rather than by
    #: accident, so it carries its own deadline and is only ever sent because
    #: somebody asked. No credentials cross: Windows uses the session's own,
    #: which is the case that matters -- a share that dropped when a server
    #: restarted comes back with nobody being asked anything, and one that
    #: needs a different account fails with the reason.
    CONNECT = "connect"

    #: Start a program that is not this one. `args["program"]` is what to run,
    #: `args["arguments"]` is the vector to hand it, `args["alternatives"]` are
    #: the programs to try when the first is not installed, and
    #: `args["working"]` is the folder it starts in -- empty meaning the
    #: worker's own, which is the install folder and is always local.
    #:
    #: `path` is the folder this launch belongs to and is what picks the
    #: worker, exactly as it does for every other op. It is deliberately not
    #: the working directory: a command with no working directory of its own
    #: still concerns the folder it was invoked from, and a launch that touches
    #: a wedged share should queue behind that share's own work rather than
    #: behind the local disk's.
    #:
    #: A worker op for the reason OPEN is one, with a second reason on top.
    #: Finding the program is a filesystem read -- `PATH`, then the places
    #: Windows installs things -- and starting it in a folder means that folder
    #: is opened, so a launch into a share that has stopped answering blocks
    #: exactly as a listing does. The second reason is the working directory: a
    #: process inherits it from its parent, and a terminal started by the window
    #: process would hold the window's own folder open for as long as somebody
    #: left the terminal running, which is how an application ends up unable to
    #: eject a drive it is not using.
    #:
    #: `args["list"]` is a list of paths to write to a file, for a selection too
    #: long for a command line; wherever `LIST_FILE` appears in the vector it is
    #: replaced with that file's path. The write happens here because it is a
    #: write, and nothing in `ui` or `core` makes one.
    #:
    #: The reply is `{"program": <what was found>, "arguments": [...],
    #: "pid": n, "list": <the file written, or "">}`. It says the program was
    #: started and nothing about what it did afterwards: this call returns when
    #: the process exists, not when somebody closes it.
    RUN = "run"

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


#: The longest a WALK goes without sending anything. See `Op.WALK`.
WALK_HEARTBEAT = 2.0

#: Rows per streamed batch. Large enough that the queue is not the bottleneck,
#: small enough that the first rows paint while the rest are still arriving.
BATCH_SIZE = 1000

#: Stands in a RUN argument vector where the user's template said `%L`, and is
#: replaced by the worker with the path of the file it wrote the selection to.
#:
#: It lives here rather than beside the rest of the command table because both
#: sides of the process boundary need to agree on it, and the table imports Qt.
#: A worker that imported the table would carry PySide6 into every volume's
#: process -- fifty megabytes and a fifth of a second each -- which is the same
#: trap `decode.py` avoids by importing Qt inside its functions.
#:
#: The NUL bytes are not decoration: a real argument cannot contain one, so
#: this cannot collide with something a user typed.
LIST_FILE = "\x00list-file\x00"

#: How many paths `%L` will write before the worker refuses. A selection this
#: long is a mistake rather than an intention, and a file of it handed to a
#: program that reads it all into memory is a hang somebody else has to explain.
MAX_LIST_PATHS = 100_000

#: The two icon kinds that are not an extension. A folder is not a file with no
#: extension, and Windows does not think it is one either.
ICON_FOLDER = "folder"
ICON_FILE = "file"

#: The kinds whose icon is inside the file rather than in the association
#: database. Everything else draws by kind and costs nothing per row, so this
#: set is the whole bound on FILE_ICON: a name outside it is never asked about.
#:
#: It is short on purpose and each entry earns its place by being a kind a
#: person recognises by its picture. Executables and shortcuts are the reason
#: this exists at all -- a folder of installers or a Start-menu folder is
#: unreadable when every row is the generic one. An `.ico` or `.cur` is a
#: picture of itself. A `.scr` is an executable wearing another extension, a
#: `.msc` and a `.cpl` are consoles and control panels that ship their own,
#: and a `.url` carries the site's.
#:
#: `.dll` is deliberately not here even though most of them contain icons:
#: Explorer draws the generic one too, and a folder like System32 would be
#: several thousand file reads for pictures nobody looks at.
SELF_ICON_KINDS = frozenset({
    ".exe", ".lnk", ".ico", ".cur", ".scr", ".msc", ".cpl", ".url",
})

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


def own_icon_kind(name: str) -> bool:
    """Whether a bare name is one of the kinds whose picture is in the file.

    Takes a name rather than an entry because both ends need it and only one
    of them has an entry: the model asks about a row, the worker is handed a
    list of names, and the harness has read a listing. One definition, so a
    kind added here is asked for and answered without a second edit.
    """
    stem, dot, suffix = name.rpartition(".")
    return bool(dot and stem) and f".{suffix.lower()}" in SELF_ICON_KINDS


def carries_own_icon(entry: Entry) -> bool:
    """Whether this row's picture is inside the file rather than in the
    association database.

    Beside `icon_key` because it is the same vocabulary and the same decision
    made once: what the cache keys on, and what is worth a read against the
    file itself. A folder never is -- a folder with a custom icon says so in a
    `desktop.ini` the shell reads when it enumerates, and reaching for that
    here would mean a read per folder row.
    """
    return not entry.is_dir and own_icon_kind(entry.name)


# --------------------------------------------------------------------------
# Jobs.
#
# A separate vocabulary from the request/reply above, because a job is not a
# request: it is long, it is interactive -- a conflict is a question asked
# back -- and it outlives the folder it started from. What it shares is that
# everything here is plain and picklable.
# --------------------------------------------------------------------------


class JobKind(str, Enum):
    """The four things the queue does.

    Deletes are here rather than on the worker path because of what they have
    in common with a copy and not with a rename: they take as long as the
    number of files, they are worth watching, and a person wants them in the
    same list as everything else that is running. `Op.DELETE` still exists --
    it is what an elevated retry uses, and it is one shell call, which is what
    makes the Recycle Bin one undo.

    The two deletes are separate members rather than a flag because they are
    two different operations. `RECYCLE` is a single shell call that cannot be
    interrupted and can be undone from the Recycle Bin; `ERASE` is this
    application walking the tree itself, item by item, and cannot be undone at
    all. Nothing about them should read as the same thing with a switch.
    """

    COPY = "copy"
    MOVE = "move"
    RECYCLE = "recycle"      # to the Recycle Bin, through the shell
    ERASE = "erase"          # permanently, item by item

    @property
    def removes(self) -> bool:
        return self in (JobKind.RECYCLE, JobKind.ERASE)

    @property
    def asks(self) -> bool:
        """Whether this kind can hit a name collision. Only the two that write."""
        return self in (JobKind.COPY, JobKind.MOVE)


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
    kind: JobKind
    sources: tuple[str, ...]
    destination: str
    conflict: Conflict = Conflict.ASK
    #: The name the one source takes in the destination, for a duplicate: a
    #: copy into the folder the source is already in, under a new name. Empty
    #: for every other job, where each source keeps its own name. Only a copy
    #: of exactly one source may carry it, and a duplicate never merges into
    #: something already there -- `ops` refuses both rather than guessing.
    rename: str = ""


class Progress(str, Enum):
    """What the ops process says while it works."""

    QUEUED = "queued"        # accepted, and where it is in line: {"position"}
    SCANNING = "scanning"    # counting what is about to move
    SCANNED = "scanned"      # totals known: {"files", "bytes"}
    STARTED = "started"      # a job began
    COPYING = "copying"      # {"name", "done", "total", "item_done", "item_total"}

    #: A delete is working on something: {"name", "done", "total"}. A separate
    #: kind from COPYING rather than the same one with a different verb, because
    #: the numbers mean something different -- items, not bytes. A delete's cost
    #: is the number of files, and a progress bar drawn from bytes would sit at
    #: nothing for a folder of 40,000 small files and then jump.
    REMOVING = "removing"

    CONFLICT = "conflict"    # a question; the job waits for an Answer
    FAILED_ITEM = "item"     # one item failed; the job carries on

    #: The job was refused before anything was written, because the
    #: destination does not have room for it: {"destination", "needed",
    #: "free"}, in bytes. A DONE follows as it does for every other ending, so
    #: nothing downstream needs a second way for a job to finish -- and the
    #: numbers are carried raw rather than as a sentence, because the side
    #: that draws them is the side that already knows how this application
    #: writes a size.
    REFUSED = "refused"

    #: The queue as a whole stopped and started. Carried on job 0, because they
    #: are about the queue rather than about any job in it.
    PAUSED = "paused"
    RESUMED = "resumed"

    #: One job was held back and let go again. Unlike PAUSED these name a job:
    #: holding the third thing in the queue leaves the first two running.
    HELD = "held"
    RELEASED = "released"

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


# --------------------------------------------------------------------------
# Previews.
#
# The vocabulary the viewer, the preview pane and the grid all speak, here for
# `icon_key`'s reason: the worker answers in these terms and the caches are
# keyed on them, so what counts as an image and what counts as text is decided
# once rather than in each of the three places that ask.
# --------------------------------------------------------------------------


class PreviewForm(str, Enum):
    """What was made of a file. Three shapes, and one for nothing.

    The three are not a guess about the file's type -- they are what the
    decoder actually produced. A `.jpg` that is really a renamed text file
    comes back as TEXT, and a `.txt` full of nulls comes back as HEX, because
    both were decided by reading the bytes rather than by trusting the name.
    """

    IMAGE = "image"
    TEXT = "text"
    HEX = "hex"
    NONE = "none"     # the file is there and nothing could be made of it


#: The families a name belongs to, which is what picks the order the providers
#: are tried in. Not the same question as `PreviewForm`: this is a guess from
#: the extension about which decoder is worth trying first, and the form is
#: what came back.
FAMILY_IMAGE = "image"
FAMILY_RAW = "raw"
FAMILY_PAGES = "pages"
FAMILY_TEXT = "text"
FAMILY_SHELL = "shell"
FAMILY_UNKNOWN = ""


#: What Qt's own image plugins read. Deliberately a written-out list rather
#: than `QImageReader.supportedImageFormats()`, even though that would be
#: exactly right: the list is needed in the *caller* to decide whether a row is
#: worth asking about at all, and the caller has no business importing an image
#: reader to find out. The worker still asks Qt rather than trusting this, so a
#: machine whose Qt reads one more format reads it.
#:
#: `.pdf` is in here and is not a mistake. PySide6 ships Qt's PDF plugin, which
#: registers itself as an image format and renders a page like any other
#: picture -- so page one of a drawing set costs what a photograph costs and no
#: dependency was added to get it.
PREVIEW_IMAGE_KINDS = frozenset({
    ".png", ".jpg", ".jpeg", ".jfif", ".gif", ".bmp", ".webp", ".tif", ".tiff",
    ".ico", ".cur", ".svg", ".svgz", ".tga", ".pbm", ".pgm", ".ppm", ".xbm",
    ".xpm", ".icns", ".wbmp", ".pdf",
})

#: Camera raw. These are not decoded -- they are *unwrapped*. Every one of
#: these formats carries a full JPEG preview of the shot inside it, written by
#: the camera, and pulling that out is a scan for two byte markers. Demosaicing
#: the sensor data properly would mean a dependency in the installer, several
#: seconds per frame, and a slightly different picture from the one the camera
#: showed on its own screen. The embedded preview is what every other viewer
#: shows first for the same reasons.
PREVIEW_RAW_KINDS = frozenset({
    ".cr2", ".cr3", ".nef", ".nrw", ".arw", ".srf", ".sr2", ".dng", ".orf",
    ".raf", ".rw2", ".pef", ".srw", ".raw", ".x3f", ".erf", ".mrw", ".3fr",
})

#: Kinds with more than one page, where the count is worth saying. Read by the
#: same plugin as an image; separate only because the viewer offers page keys.
PREVIEW_PAGE_KINDS = frozenset({".pdf"})

#: Kinds read as text without sniffing first. Everything not in here that turns
#: out to be text is still shown as text -- the sniff decides that -- so this
#: list is not the bound on what can be read. What it does is settle the
#: ambiguous ones in favour of the source rather than the picture: an `.svg` is
#: in `PREVIEW_IMAGE_KINDS` and draws, and an `.html` is here and does not,
#: because somebody opening an `.html` in a file manager is looking at markup.
PREVIEW_TEXT_KINDS = frozenset({
    ".txt", ".md", ".markdown", ".rst", ".log", ".csv", ".tsv", ".json",
    ".xml", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".conf", ".properties",
    ".py", ".pyw", ".c", ".h", ".cpp", ".hpp", ".cc", ".cs", ".java", ".js",
    ".mjs", ".ts", ".tsx", ".jsx", ".go", ".rs", ".rb", ".php", ".lua", ".pl",
    ".sql", ".sh", ".bash", ".zsh", ".bat", ".cmd", ".ps1", ".psm1", ".vbs",
    ".html", ".htm", ".css", ".scss", ".less", ".diff", ".patch", ".gitignore",
    ".gitattributes", ".editorconfig", ".env", ".dockerfile", ".makefile",
    ".m", ".r", ".jl", ".tex", ".bib", ".asc", ".srt", ".vtt", ".reg", ".iss",
    ".spec", ".pro", ".qss", ".scr", ".lsp", ".mel", ".nc", ".gcode", ".stp",
    ".step", ".igs", ".iges", ".dxf", ".plt", ".hpgl", ".ctb", ".pc3",
})

#: Kinds worth asking Windows about, because Windows has a handler for them and
#: this application never will. A frame from a video, the first slide of a
#: deck, a `.psd` with no sidecar, whatever the camera vendor's codec pack
#: added -- all of it is somebody else's decoder already installed on the
#: machine, reached through `IShellItemImageFactory`.
#:
#: The list is what stops that being a read per row. A shell thumbnail opens
#: the file and runs a third-party provider inside it, so it is asked for by
#: name like an overlay is, and only for kinds where there is likely to be an
#: answer. A kind outside every set here gets the sniff, which is a few hundred
#: bytes.
PREVIEW_SHELL_KINDS = frozenset({
    ".mp4", ".m4v", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".mpg",
    ".mpeg", ".m2ts", ".mts", ".ts", ".vob", ".3gp", ".ogv",
    ".docx", ".doc", ".dotx", ".xlsx", ".xls", ".xltx", ".pptx", ".ppt",
    ".potx", ".odt", ".ods", ".odp", ".rtf", ".pub", ".vsdx", ".msg",
    ".psd", ".psb", ".ai", ".eps", ".indd", ".cdr", ".heic", ".heif", ".avif",
    ".jxr", ".hdp", ".wdp", ".dwg", ".dwf", ".rvt", ".skp", ".ifc", ".3ds",
    ".stl", ".obj", ".fbx", ".mp3", ".flac", ".m4a", ".wma", ".epub", ".mobi",
    ".xps", ".oxps", ".zip", ".7z", ".rar",
})


def preview_family(name: str) -> str:
    """Which decoder is worth trying first for a bare name.

    Takes a name rather than an entry for `own_icon_kind`'s reason: the model
    asks about a row, the worker is handed a path, and the harness has read a
    listing. One definition, so a kind added above is asked for and answered
    without a second edit.

    The order of these tests is the only thing in this function, and two of
    them are worth saying out loud. Text is checked before the shell, so a
    `.reg` or a `.dxf` is the file rather than whatever picture Windows would
    draw of it. Raw is checked before the image plugins, because a `.dng` is a
    TIFF as far as Qt is concerned and Qt would hand back the camera's
    thumbnail strip at 160 pixels wide and call it the photograph.
    """
    kind = _suffix(name)
    if not kind:
        return FAMILY_UNKNOWN
    if kind in PREVIEW_RAW_KINDS:
        return FAMILY_RAW
    if kind in PREVIEW_TEXT_KINDS:
        return FAMILY_TEXT
    if kind in PREVIEW_IMAGE_KINDS:
        return FAMILY_IMAGE
    if kind in PREVIEW_SHELL_KINDS:
        return FAMILY_SHELL
    return FAMILY_UNKNOWN


def draws_a_thumbnail(entry: Entry) -> bool:
    """Whether this row could show a picture in the grid, and so is worth a read.

    The bound on the grid, and the only one that does real work -- the same job
    `carries_own_icon` does for FILE_ICON. A folder never is: what a folder
    looks like is its icon, and the alternative (a stack of the first four
    things inside it) is four listings per cell.

    A text file is not a thumbnail either, even though the decoder would gladly
    make one. Ninety cells of grey lines at 128 pixels are ninety identical
    grey squares, and the icon for the kind says more in less space. The viewer
    and the preview pane do show text, because there they are readable.
    """
    if entry.is_dir:
        return False
    return preview_family(entry.name) in (FAMILY_IMAGE, FAMILY_RAW, FAMILY_SHELL)


def _suffix(name: str) -> str:
    """The lowered extension with its dot, or "" for a name without one.

    `icon_key`'s rule, and for its reason: a leading dot is part of a name, so
    `.gitignore` is a file with no extension. It is in `PREVIEW_TEXT_KINDS`
    anyway, which costs nothing and is wrong -- the sniff would have said text
    regardless, and a reader who goes looking for why it is listed should find
    this sentence rather than a bug.
    """
    stem, dot, suffix = name.rpartition(".")
    if not dot or not stem or not suffix:
        return ""
    return f".{suffix.lower()}"


@dataclass(frozen=True, slots=True)
class Preview:
    """What one file turned out to look like.

    One dataclass with three shapes in it rather than three classes, because
    every caller handles all three: the viewer switches on `form` and so does
    the preview pane, and a union would put that switch in the type system and
    then need it again anyway. Which fields carry anything follows from `form`
    and from nothing else.

    Nothing here is a Qt object. `image` is PNG bytes because a `QPixmap` does
    not cross a process boundary and raw pixels at the size of a photograph
    would be the whole file arriving after all.
    """

    form: PreviewForm = PreviewForm.NONE

    #: Which provider answered: "qt", "raw", "shell", "text", "hex". For the
    #: harness and for the one line the viewer shows, because "this came from
    #: the shell" is the difference between a missing codec pack and a bug.
    source: str = ""

    #: The file's own size in bytes, so a caller that has no listing to hand --
    #: the viewer, opened on one path -- can still say how big it is.
    size: int = 0

    # IMAGE ----------------------------------------------------------------
    image: bytes | None = None      # PNG, at most `box` on its longest edge
    width: int = 0                  # the real picture, not the one enclosed
    height: int = 0
    #: The longest edge of what is actually in `image`. Smaller than
    #: `max(width, height)` means the caller is looking at a scaled copy and
    #: should say so rather than let somebody zoom into softness and wonder.
    shown: int = 0
    #: Pages for a document, frames for an animation. 0 when there is one, so
    #: that "more than one" is a truthy test rather than a comparison.
    pages: int = 0

    # TEXT -----------------------------------------------------------------
    text: str = ""
    #: What the bytes were decoded as, and how that was decided: "utf-8-sig"
    #: names a byte order mark, "utf-8" names a successful strict decode,
    #: "cp1252" names the fallback. Shown, because a file that came out as
    #: mojibake is a question about this line.
    encoding: str = ""
    #: Whether the file goes on past what was read. The viewer says so; without
    #: it a 400 MB log looks like a short one.
    truncated: bool = False
    lines: int = 0

    # HEX ------------------------------------------------------------------
    #: The first bytes of the file, laid out by the caller. Not formatted here:
    #: how many columns fit is a question about the width of a window.
    data: bytes | None = None

    #: Why there is nothing, when there is nothing. A sentence for a person:
    #: "too large to preview", "no decoder for this kind".
    note: str = ""


#: The longest edge a preview pane asks for. Generous rather than exact: the
#: pane is resizable and a picture re-requested on every drag would be a read
#: per pixel of mouse movement, so one decode covers every width the pane is
#: likely to be dragged to and Qt scales the rest of the way for free.
PREVIEW_BOX = 1600

#: The ceiling the viewer asks for, and the honest limit on zoom. A picture
#: larger than this is shown scaled and says so. The number is not a guess
#: about screens: it is what keeps one decoded image under about seventy
#: megabytes of pixels, which is the difference between a viewer that opens and
#: one that swaps.
VIEWER_BOX = 4096

#: Cell sizes the grid offers. Four rather than a slider, because each one is a
#: fresh read of every file on screen and a slider is a request storm with a
#: handle on it.
THUMB_SIZES = (96, 128, 176, 240)

#: How much of a text file is read for a preview. A quarter of a megabyte is
#: several thousand lines, which is more than anybody reads in a preview pane,
#: and it is the whole of almost every real text file.
PREVIEW_TEXT_BYTES = 256 * 1024

#: How much of an undecodable file is read for the hex view. One screenful and
#: a bit: the point of hex here is to see what something actually is -- a magic
#: number, a header -- not to read the file.
PREVIEW_HEX_BYTES = 4096

#: How far into a camera raw file the embedded JPEG is looked for. The preview
#: lives in the header area of every format in `PREVIEW_RAW_KINDS`; reading the
#: whole of a 60 MB frame to find it would cost more than decoding it.
RAW_SCAN_BYTES = 24 * 1024 * 1024

#: How many bytes are enough to tell text from binary. A NUL or a run of
#: control characters in the first kilobyte settles it, and no real text file
#: hides its first NUL past here.
SNIFF_BYTES = 1024

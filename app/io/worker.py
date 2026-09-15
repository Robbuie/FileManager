"""One worker process. Every real filesystem call for one volume happens here.

Why a process rather than a thread: an SMB call against a share that has gone
away blocks in the kernel for 30 to 45 seconds and cannot be interrupted. A
thread in that state can only be waited on. A process can be killed, which is
what the pool does, and why nothing in here holds state worth keeping — being
killed mid-operation is a normal thing to happen to this process, not a fault.

Two consequences shape the code below.

  * The loop handles one request at a time. Overlapping them would let one hung
    call strand the others, and the pool could not tell which of them to fail.
  * The deadline on a request is enforced here only between syscalls, so it
    catches the slow case: a share that answers, badly. The hard case, where a
    single call never returns, cannot be caught from inside this process at all
    and is the pool's job.

The deadline measures time without progress rather than total time. A listing
that keeps delivering batches is not late however long it runs; one that stops
delivering is late even if it started a moment ago.
"""

from __future__ import annotations

import hashlib
import os
import queue
import shutil
import signal
import subprocess
import time
from typing import Any

from app.io import decode, elevate, paths
from app.io.protocol import (
    BATCH_SIZE,
    ICON_FILE,
    ICON_FOLDER,
    LIST_FILE,
    MAX_LIST_PATHS,
    PREVIEW_TEXT_BYTES,
    Entry,
    Op,
    Preview,
    PreviewForm,
    Reply,
    Request,
    Status,
    own_icon_kind,
    preview_family,
)

#: The shell, for opening a file the way Explorer does. Optional at import so
#: the module still loads where pywin32 does not; `_open` says so rather than
#: guessing when it is missing.
try:
    import pythoncom
    import win32con
    import win32event
    import win32gui
    import win32process
    import win32ui
    from win32com.shell import shell as win32shell, shellcon
except Exception:  # noqa: BLE001 - reported by _open, like paths.win32_problem
    pythoncom = None
    win32con = None
    win32event = None
    win32gui = None
    win32process = None
    win32ui = None
    win32shell = None
    shellcon = None

#: Whether this process has initialised COM. Done once, lazily, and only for
#: the shell: `ShellExecuteEx` hands the work to shell extensions, and most of
#: them require a single-threaded apartment. Without it the association can
#: resolve differently -- or not at all -- which looks from the outside like a
#: file manager that cannot open its own files.
_com_ready = False

#: Rows between checks of the clock and the control queue. Checking on every
#: row costs more than the enumeration itself at 50,000 rows; this keeps a
#: cancel responsive to roughly a millisecond of work.
CHECK_INTERVAL = 128

#: How many subfolder names a FOLDERS request returns before it stops looking.
#: A dropdown is a way of getting somewhere quickly, and a menu past about this
#: length has stopped being one -- so the cap is on the scan rather than on the
#: drawing, and the folder is never enumerated past it.
FOLDER_LIMIT = 200

#: Windows error numbers that mean the path or the server is no longer there.
#: They are answered with GONE rather than ERROR because the difference matters
#: to the caller: GONE is what puts a tab into "reconnecting" instead of
#: showing an error nobody can act on.
_GONE_WINERRORS = frozenset({51, 53, 54, 55, 59, 64, 67, 121, 1222, 1231, 1232})

#: Characters that would turn an icon key into something path-shaped. The
#: guarantee an ICON request makes is that it never touches a path, and the
#: way that guarantee gets lost is by someone handing it one.
_SEPARATORS = ("\\", "/", ":")


def run(inbox: Any, outbox: Any, control: Any) -> None:
    """Process entry point.

    Answers every request it takes, including the ones it fails. A request that
    produces no reply is a tab waiting forever, so the failure paths below are
    all explicit and the loop's own exception handler is the backstop.
    """
    # Ctrl-C in the console goes to every process in the group, this one
    # included, and a worker that dies on it leaves the parent waiting on
    # replies that are never coming. When this process ends is the pool's
    # decision and nobody else's, so the signal is ignored here and the pool's
    # sentinel and terminate are the only ways out.
    try:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    except (ValueError, OSError):  # not the main thread, or no such signal
        pass

    cancelled: set[int] = set()
    while True:
        try:
            request = inbox.get()
        except (EOFError, OSError):  # the parent went away
            return
        except KeyboardInterrupt:  # raced the handler being installed
            continue
        if request is None:
            return
        _drain_control(control, cancelled)
        if request.id in cancelled:
            cancelled.discard(request.id)
            outbox.put(Reply(request.id, Status.CANCELLED))
            continue
        try:
            _handle(request, outbox, control, cancelled)
        except Exception as exc:  # noqa: BLE001 - see the docstring
            outbox.put(Reply(request.id, Status.ERROR, message=_describe(exc)))


def _handle(request: Request, outbox: Any, control: Any, cancelled: set[int]) -> None:
    if request.op is Op.LIST:
        _list(request, outbox, control, cancelled)
    elif request.op is Op.STAT:
        _stat(request, outbox)
    elif request.op is Op.DIR_SIZE:
        _dir_size(request, outbox, control, cancelled)
    elif request.op is Op.RESOLVE:
        _resolve(request, outbox)
    elif request.op is Op.OPEN:
        _open(request, outbox)
    elif request.op is Op.RUN:
        _run(request, outbox)
    elif request.op is Op.ELEVATE:
        _elevate(request, outbox)
    elif request.op is Op.OVERLAY:
        _overlays(request, outbox)
    elif request.op is Op.FOLDERS:
        _folders(request, outbox, control, cancelled)
    elif request.op is Op.DRIVES:
        _drives(request, outbox)
    elif request.op is Op.FREE_SPACE:
        _free_space(request, outbox)
    elif request.op is Op.MKDIR:
        _mkdir(request, outbox)
    elif request.op is Op.RENAME:
        _rename(request, outbox)
    elif request.op is Op.DELETE:
        _delete(request, outbox)
    elif request.op is Op.PING:
        outbox.put(Reply(request.id, Status.OK, payload={"time": time.time(), "pid": os.getpid()}))
    elif request.op is Op.STALL:
        # Deliberately ignores the deadline and the control queue. Anything
        # less is not the failure being imitated: a worker that can still
        # notice a cancel is a worker that is answering.
        limit = float(request.args.get("seconds", 0)) or None
        started = time.monotonic()
        while limit is None or time.monotonic() - started < limit:
            time.sleep(0.25)
        outbox.put(Reply(request.id, Status.OK, payload={"stalled": limit}))
    elif request.op is Op.ICON:
        _icon(request, outbox)
    elif request.op is Op.FILE_ICON:
        _file_icons(request, outbox)
    elif request.op is Op.PREVIEW:
        _preview(request, outbox)
    elif request.op is Op.THUMBNAIL:
        _thumbnails(request, outbox)
    else:
        outbox.put(Reply(request.id, Status.ERROR, message=f"unknown op {request.op!r}"))


# --------------------------------------------------------------------------
# Operations.
# --------------------------------------------------------------------------


def _list(request: Request, outbox: Any, control: Any, cancelled: set[int]) -> None:
    """Stream a directory listing in batches, never accumulating the whole of it.

    `os.scandir` and nothing else: on Windows the `DirEntry` carries size, mtime
    and attributes from the directory enumeration itself, so a 50,000-row
    listing with every column is one pass and no extra syscalls. A per-row
    `os.stat` here would be 50,000 SMB round trips.
    """
    deadline = time.monotonic() + request.timeout
    batch: list[Entry] = []
    seq = 0
    seen = 0

    try:
        scanner = os.scandir(request.path)
    except OSError as exc:
        outbox.put(_failure(request, exc))
        return

    with scanner:
        while True:
            if seen % CHECK_INTERVAL == 0:
                _drain_control(control, cancelled)
                if request.id in cancelled:
                    cancelled.discard(request.id)
                    outbox.put(Reply(request.id, Status.CANCELLED, seq=seq))
                    return
                if time.monotonic() > deadline:
                    outbox.put(Reply(request.id, Status.TIMEOUT, seq=seq,
                                     message="no progress within the deadline"))
                    return
            try:
                entry = next(scanner)
            except StopIteration:
                break
            except OSError as exc:
                outbox.put(_failure(request, exc, seq=seq))
                return
            seen += 1
            row = _row(entry)
            if row is not None:
                batch.append(row)
            if len(batch) >= BATCH_SIZE:
                outbox.put(Reply(request.id, Status.PARTIAL, payload=batch, seq=seq))
                seq += 1
                batch = []
                deadline = time.monotonic() + request.timeout

    outbox.put(Reply(request.id, Status.OK, payload=batch, seq=seq))


def _folders(request: Request, outbox: Any, control: Any, cancelled: set[int]) -> None:
    """The subfolder names of one folder, and whether there were more.

    The cap is the whole point. This answers a dropdown, and a dropdown of two
    hundred entries is already past being a way of getting anywhere -- so the
    scan stops at the limit rather than enumerating a 50,000-row folder to
    show the first twenty of it. `more` is what lets the menu say so instead
    of quietly lying about what is in the folder.

    Sorted here rather than by the caller, because here is where the whole
    list exists and it is never longer than the cap.
    """
    limit = max(1, int(request.args.get("limit", FOLDER_LIMIT)))
    deadline = time.monotonic() + request.timeout
    names: list[str] = []
    seen = 0
    more = False

    try:
        scanner = os.scandir(request.path)
    except OSError as exc:
        outbox.put(_failure(request, exc))
        return

    with scanner:
        while True:
            if seen % CHECK_INTERVAL == 0:
                _drain_control(control, cancelled)
                if request.id in cancelled:
                    cancelled.discard(request.id)
                    outbox.put(Reply(request.id, Status.CANCELLED))
                    return
                if time.monotonic() > deadline:
                    # A partial answer, not a failure. Some of the siblings is
                    # a usable menu; nothing at all is a menu that says the
                    # folder is empty, which it is not.
                    outbox.put(Reply(request.id, Status.TIMEOUT, payload={
                        "names": sorted(names, key=str.lower), "more": True,
                    }, message="partial; the scan exceeded its deadline"))
                    return
            try:
                entry = next(scanner)
            except StopIteration:
                break
            except OSError as exc:
                outbox.put(_failure(request, exc))
                return
            seen += 1
            try:
                if not entry.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            if len(names) >= limit:
                more = True
                break
            names.append(entry.name)

    outbox.put(Reply(request.id, Status.OK, payload={
        "names": sorted(names, key=str.lower), "more": more,
    }))


def _row(entry: os.DirEntry) -> Entry | None:
    """One row from a `DirEntry`, or None if it disappeared mid-enumeration.

    A file deleted while the directory is being read is not a failed listing,
    it is a row that is no longer there. Everything read here is served from
    the enumeration on Windows and costs nothing.
    """
    try:
        stat = entry.stat(follow_symlinks=False)
        is_dir = entry.is_dir(follow_symlinks=False)
        is_link = entry.is_symlink()
    except OSError:
        return None
    return Entry(
        name=entry.name,
        is_dir=is_dir,
        size=0 if is_dir else stat.st_size,
        mtime=stat.st_mtime,
        attributes=getattr(stat, "st_file_attributes", 0),
        is_link=is_link,
    )


def _stat(request: Request, outbox: Any) -> None:
    try:
        stat = os.stat(request.path, follow_symlinks=False)
    except OSError as exc:
        outbox.put(_failure(request, exc))
        return
    outbox.put(Reply(request.id, Status.OK, payload=Entry(
        name=os.path.basename(request.path.rstrip("\\")) or request.path,
        is_dir=os.path.isdir(request.path),
        size=stat.st_size,
        mtime=stat.st_mtime,
        attributes=getattr(stat, "st_file_attributes", 0),
        is_link=os.path.islink(request.path),
    )))


def _dir_size(request: Request, outbox: Any, control: Any, cancelled: set[int]) -> None:
    """Recursive size of a folder. Lazy by definition — this is only ever asked
    for on demand, never to fill a column.
    """
    deadline = time.monotonic() + request.timeout
    total = 0
    files = 0
    folders = 0
    stack = [request.path]
    seen = 0

    while stack:
        current = stack.pop()
        try:
            scanner = os.scandir(current)
        except OSError as exc:
            if current == request.path:
                outbox.put(_failure(request, exc))
                return
            continue  # an unreadable subfolder is skipped, not fatal
        with scanner:
            for entry in scanner:
                seen += 1
                if seen % CHECK_INTERVAL == 0:
                    _drain_control(control, cancelled)
                    if request.id in cancelled:
                        cancelled.discard(request.id)
                        outbox.put(Reply(request.id, Status.CANCELLED))
                        return
                    if time.monotonic() > deadline:
                        outbox.put(Reply(request.id, Status.TIMEOUT, payload={
                            "bytes": total, "files": files, "folders": folders,
                        }, message="partial total; the walk exceeded its deadline"))
                        return
                try:
                    if entry.is_dir(follow_symlinks=False):
                        folders += 1
                        stack.append(entry.path)
                    else:
                        files += 1
                        total += entry.stat(follow_symlinks=False).st_size
                except OSError:
                    continue

    outbox.put(Reply(request.id, Status.OK, payload={
        "bytes": total, "files": files, "folders": folders,
    }))


def _open(request: Request, outbox: Any) -> None:
    """Open a path the way Explorer opens it.

    `ShellExecuteEx` with `SEE_MASK_INVOKEIDLIST` rather than `os.startfile`,
    and the difference is not cosmetic. `os.startfile` is `ShellExecuteW` on a
    path string, which resolves the association from the extension alone; the
    IDLIST form builds the shell item first and invokes the default verb from
    its context menu, which is the same path Explorer takes. Where the two
    differ, the string form is the one that ends up asking which application to
    use for a file that already has one -- per-user choices set through Open
    With and the modern association store live on the item, not on the
    extension.

    `SEE_MASK_FLAG_NO_UI` suppresses the shell's own error boxes: this is a
    background process with no window, and a modal dialog it owns is one nobody
    can be sure of finding. Failures come back as a reply and land in the
    pane's status line instead. The picker for a file type that genuinely has
    no default is not an error box and still appears, exactly as it does in
    Explorer.

    The call returns as soon as the shell has taken the request rather than
    when the application exits. It can still block -- the association lookup
    reads the file, and on a share that has stopped answering that read blocks
    like any other -- which is the reason this is in a worker rather than three
    lines in a slot.

    A verb is accepted because the ones Windows already knows -- edit, print,
    properties -- cost nothing to pass through and save inventing a mechanism
    for them later. Empty means the default, which is not the same as "open":
    a folder's default is explore, and a shortcut's is whatever it points at.
    """
    verb = str(request.args.get("verb", "") or "")
    answer = {"path": request.path, "verb": verb}

    if win32shell is not None:
        _ensure_com()
        # `lpVerb` is omitted rather than passed as None, because omitting it is
        # what asks for the item's default verb.
        options = {
            "fMask": shellcon.SEE_MASK_INVOKEIDLIST | shellcon.SEE_MASK_FLAG_NO_UI,
            "lpFile": request.path,
            "nShow": win32con.SW_SHOWNORMAL,
        }
        if verb:
            options["lpVerb"] = verb
        try:
            win32shell.ShellExecuteEx(**options)
        except Exception as exc:  # noqa: BLE001 - pywintypes.error is not an OSError
            outbox.put(Reply(request.id, _shell_status(exc), message=_describe(exc)))
            return
        outbox.put(Reply(request.id, Status.OK, payload=answer))
        return

    # Without pywin32 there is still `os.startfile`, which opens most things
    # correctly. It is the fallback rather than the implementation because the
    # cases where it differs are the ones a file manager gets complained about.
    opener = getattr(os, "startfile", None)
    if opener is None:
        outbox.put(Reply(request.id, Status.ERROR,
                         message="opening a file with the shell needs Windows"))
        return
    try:
        opener(request.path, verb) if verb else opener(request.path)
    except OSError as exc:
        outbox.put(_failure(request, exc))
        return
    outbox.put(Reply(request.id, Status.OK, payload=answer))


#: Where a program that is not on `PATH` is looked for, by the bare name the
#: table holds. Two entries rather than a search: walking Program Files for
#: something called `BCompare.exe` is thousands of reads for a menu entry, and
#: an installer that put it somewhere else is a case for typing the full path
#: into the table, which the table already allows.
#:
#: The versioned folders are listed oldest last so a machine with two installs
#: gets the newer one. Adding a tool here is a convenience, never a dependency:
#: nothing in this application stops working when none of these exist.
KNOWN_PROGRAMS: dict[str, tuple[str, ...]] = {
    "bcompare.exe": (
        r"Beyond Compare 5\BCompare.exe",
        r"Beyond Compare 4\BCompare.exe",
        r"Beyond Compare 3\BCompare.exe",
    ),
    "winmergeu.exe": (r"WinMerge\WinMergeU.exe",),
    "code.exe": (r"Microsoft VS Code\Code.exe",),
    "notepad++.exe": (r"Notepad++\notepad++.exe",),
    "wt.exe": (r"WindowsApps\wt.exe",),
}


def _program_roots() -> list[str]:
    """The folders `KNOWN_PROGRAMS` is relative to, most likely first.

    Read from the environment rather than hardcoded: `ProgramFiles` is where
    Windows says it is, which on a 32-bit process is not the same folder as on
    a 64-bit one, and a machine can have it somewhere else entirely.
    """
    names = ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")
    roots: list[str] = []
    for name in names:
        value = os.environ.get(name)
        if not value:
            continue
        roots.append(value)
        if name == "LOCALAPPDATA":
            roots.append(os.path.join(value, "Programs"))
    return roots


def locate(program: str) -> str:
    """Where a program is on this machine, or "" if it is not here.

    Three places in order, and the order is the answer to "which one did the
    user mean": a path they typed, then `PATH`, then the handful of install
    folders `KNOWN_PROGRAMS` knows about. A bare name that `PATH` answers is
    always the one Windows itself would start.

    A filesystem call, which is why it is here and not in the table.
    """
    if not program:
        return ""
    if os.path.isabs(program) or os.sep in program or "/" in program:
        expanded = os.path.expandvars(program)
        return expanded if os.path.exists(expanded) else ""
    found = shutil.which(program)
    if found:
        return found
    for relative in KNOWN_PROGRAMS.get(program.lower(), ()):
        for root in _program_roots():
            candidate = os.path.join(root, relative)
            if os.path.exists(candidate):
                return candidate
    return ""


def _write_list(paths_to_write: list[str]) -> str:
    """Write the selection to a file and return its path, for `%L`.

    UTF-8 with a BOM, and the BOM is not optional: a list of file names is
    exactly the content that carries accented characters, and the Windows
    programs most likely to be handed one read a file with no BOM as the system
    code page. The file is left in `%TEMP%` rather than deleted, because the
    program that was started is still reading it and nothing here knows when it
    has finished -- `%TEMP%` is the place whose contents are somebody else's
    problem, which is the whole reason it exists.
    """
    import tempfile

    handle = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8-sig", suffix=".txt",
        prefix="filemanager-", delete=False, newline="\r\n")
    with handle:
        for line in paths_to_write:
            handle.write(line + "\n")
    return handle.name


def _run(request: Request, outbox: Any) -> None:
    """Start a program, in a folder, and say that it started.

    Detached on purpose, and in two senses. The process is started with
    `DETACHED_PROCESS` and a new process group so closing this application does
    not close a terminal somebody is still typing in, and so a Ctrl+C in a
    console this was started from does not travel to it. And the reply comes
    back as soon as the process exists: waiting for it would hold this worker
    -- and therefore every listing on this volume -- for as long as somebody
    left an editor open.

    The working directory is the reason this is worth a worker rather than
    three lines in a slot. A child inherits it, and a handle to a folder keeps
    that folder open: a terminal started by the window process would pin the
    window's own folder for the life of the terminal, which is how an
    application ends up unable to eject a drive it is not using.
    """
    program = str(request.args.get("program", "") or "")
    alternatives = [str(item) for item in (request.args.get("alternatives") or ())]
    arguments = [str(item) for item in (request.args.get("arguments") or ())]
    wanted = [str(item) for item in (request.args.get("list") or ())]

    found = ""
    tried: list[str] = []
    for candidate in [program, *alternatives]:
        if not candidate:
            continue
        tried.append(candidate)
        found = locate(candidate)
        if found:
            break
    if not found:
        names = " or ".join(tried) or "nothing"
        outbox.put(Reply(request.id, Status.ERROR,
                         message=f"{names} is not installed on this machine"))
        return

    written = ""
    if any(item == LIST_FILE for item in arguments):
        if len(wanted) > MAX_LIST_PATHS:
            outbox.put(Reply(request.id, Status.ERROR,
                             message=f"{len(wanted)} files is too many to list"))
            return
        try:
            written = _write_list(wanted)
        except OSError as exc:
            outbox.put(_failure(request, exc))
            return
        arguments = [written if item == LIST_FILE else item for item in arguments]

    working = str(request.args.get("working", "") or "") or None
    if working and not os.path.isdir(working):
        # Refused rather than started in whatever the parent's folder is. A
        # terminal that opens somewhere other than where it was asked for is
        # worse than one that does not open: the next command runs in the wrong
        # place and nothing says so.
        outbox.put(Reply(request.id, Status.GONE,
                         message="that folder is not there any more"))
        return

    options: dict[str, Any] = {"cwd": working, "close_fds": True}
    flags = 0
    for name in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP"):
        flags |= getattr(subprocess, name, 0)
    if flags:
        options["creationflags"] = flags

    try:
        started = subprocess.Popen([found, *arguments], **options)  # noqa: S603
    except OSError as exc:
        outbox.put(_failure(request, exc))
        return

    outbox.put(Reply(request.id, Status.OK, payload={
        "program": found,
        "arguments": arguments,
        "pid": started.pid,
        "list": written,
    }))


def _drives(request: Request, outbox: Any) -> None:
    """The drive letters, as plain dicts.

    `paths.drives` reads the session table and the drive-type bitmask and
    probes nothing, so a mapped drive whose server is down is listed as fast as
    a local disk -- which is the whole point, since a picker that hangs on open
    is worse than no picker.
    """
    refresh = bool(request.args.get("refresh", False))
    listed = paths.drives(refresh=refresh)
    outbox.put(Reply(request.id, Status.OK, payload=[
        {"letter": d.letter, "type": d.type, "unc": d.unc} for d in listed
    ]))


def _free_space(request: Request, outbox: Any) -> None:
    try:
        usage = shutil.disk_usage(request.path)
    except (OSError, ValueError) as exc:
        outbox.put(_failure(request, exc if isinstance(exc, OSError) else OSError(str(exc))))
        return
    outbox.put(Reply(request.id, Status.OK, payload={
        "total": usage.total, "used": usage.used, "free": usage.free,
    }))


def _icon(request: Request, outbox: Any) -> None:
    """Shell icons for a set of kinds, without going near a volume.

    `SHGFI_USEFILEATTRIBUTES` is what makes this safe to ask for while a
    listing of 50,000 rows is still painting: it tells the shell to answer
    from the extension and the attributes handed in and not to look at the
    path at all. The names below are invented for that reason. Nothing called
    `file.pdf` needs to exist, and on a share that has stopped answering it
    must not be looked for -- the icon for a PDF is a fact about this machine,
    not about the folder being listed.

    The cost of that is a file with an icon of its own -- an executable, a
    shortcut, an .ico -- showing the generic icon for its type. Reading the
    real one means opening the file, which is a per-path request against that
    file's own volume and is deliberately not this call.

    Missing keys rather than null ones: a kind the shell had nothing for is
    left out of the answer, so a caller can tell "no icon" from "not asked".
    """
    keys = [str(key) for key in (request.args.get("keys") or [])]
    size = 32 if int(request.args.get("size", 16) or 16) > 16 else 16
    if not keys:
        outbox.put(Reply(request.id, Status.OK, payload={"size": size, "icons": {}}))
        return
    if win32shell is None or win32gui is None or win32ui is None or shellcon is None:
        outbox.put(Reply(request.id, Status.ERROR, payload={"size": size, "icons": {}},
                         message="shell icons need pywin32 on Windows"))
        return

    _ensure_com()
    deadline = time.monotonic() + request.timeout
    icons: dict[str, bytes] = {}
    # Why nothing came back, for the reply to carry. An empty answer that does
    # not say why is a diagnostic dead end: an extension the shell genuinely
    # has no icon for and a call that is not there at all look identical from
    # the outside, and one of those is a bug in this file.
    problems: list[str] = []
    for key in keys:
        if time.monotonic() > deadline:
            outbox.put(Reply(request.id, Status.TIMEOUT,
                             payload={"size": size, "icons": icons},
                             message="the shell did not answer within the deadline"))
            return
        pixels = _shell_icon(key, size, problems)
        if pixels is not None:
            icons[key] = pixels
    outbox.put(Reply(request.id, Status.OK, payload={"size": size, "icons": icons},
                     message="" if icons else (problems[0] if problems else
                                               "the shell had no icon for any of these")))


def _file_icons(request: Request, outbox: Any) -> None:
    """The icons a few files carry inside themselves.

    `_icon` above answers from the extension and never opens anything, which
    is what makes it safe to ask for while a share is being listed. The cost
    of that is the row a person actually recognises: an executable, a
    shortcut, an icon file. Their picture is in the file, so it has to be
    read, and this is the request that reads it.

    What keeps that affordable is not this function -- it is the caller asking
    only about the rows on screen, and only about the kinds in
    `SELF_ICON_KINDS`. What this function adds is the last two bounds:

      * **the deadline is checked between files**, so a share that goes quiet
        costs the pictures it had not reached and nothing else;
      * **the answer is a picture per distinct picture**, keyed on a digest of
        the pixels, so a Start-menu folder of forty shortcuts to the same
        program comes back as one image rather than forty.
    """
    names = [str(name) for name in (request.args.get("names") or [])]
    size = 32 if int(request.args.get("size", 16) or 16) > 16 else 16
    empty = {"size": size, "rows": {}, "images": {}}
    if not names:
        outbox.put(Reply(request.id, Status.OK, payload=empty))
        return
    if win32shell is None or win32gui is None or win32ui is None or shellcon is None:
        outbox.put(Reply(request.id, Status.ERROR, payload=empty,
                         message="file icons need pywin32 on Windows"))
        return

    _ensure_com()
    deadline = time.monotonic() + request.timeout
    rows: dict[str, str] = {}
    images: dict[str, bytes] = {}
    problems: list[str] = []

    for name in names:
        if time.monotonic() > deadline:
            outbox.put(Reply(request.id, Status.TIMEOUT,
                             payload={"size": size, "rows": rows, "images": images},
                             message="the shell did not answer within the deadline"))
            return
        # Both guards, and both matter. The kind is checked here as well as in
        # the caller because a bound only one end enforces is a bound a later
        # caller can lose; the separators because a name here comes from a
        # listing and is joined onto the folder, so anything path-shaped is
        # refused rather than followed.
        if any(ch in name for ch in _SEPARATORS) or not own_icon_kind(name):
            continue
        pixels = _path_icon(paths.join(request.path, name), size, problems)
        if pixels is None:
            continue
        key = hashlib.sha1(pixels).hexdigest()[:16]
        rows[name] = key
        images.setdefault(key, pixels)
    outbox.put(Reply(request.id, Status.OK,
                     payload={"size": size, "rows": rows, "images": images},
                     message=problems[0] if problems and not images else ""))


def _path_icon(path: str, size: int, problems: list[str]) -> bytes | None:
    """One file's own icon, as premultiplied BGRA.

    `SHGetFileInfo` again, and the difference from `_shell_icon` is the one
    flag that is not there: without `SHGFI_USEFILEATTRIBUTES` the shell opens
    the path and reads what is inside it. That is the whole point here and the
    reason this request goes to the volume the file is on rather than to the
    local worker.

    A shortcut is why the shell is asked at all rather than the icon being
    pulled out with `ExtractIconEx`: a `.lnk` holds a target and possibly an
    icon location, resolving it is the shell's job, and doing it here would
    mean reimplementing that badly for the one case it matters.
    """
    flags = shellcon.SHGFI_ICON
    flags |= shellcon.SHGFI_LARGEICON if size > 16 else shellcon.SHGFI_SMALLICON
    try:
        answer = win32shell.SHGetFileInfo(path, 0, flags)
    except Exception as exc:  # noqa: BLE001 - a file that has just gone is not an error
        problems.append(f"{path}: {_describe(exc)}")
        return None
    handle = _icon_handle(answer)
    if not handle:
        return None
    try:
        return _icon_pixels(handle, size, problems)
    finally:
        try:
            win32gui.DestroyIcon(handle)
        except Exception:  # noqa: BLE001 - already gone is the outcome wanted
            pass


def _preview(request: Request, outbox: Any) -> None:
    """What one file looks like, at the size the caller asked for.

    Thin on purpose. Every decision about what a file is and which decoder gets
    it lives in `app/io/decode.py`, because the viewer, the preview pane and the
    grid all have to agree about it and two of them come through
    `_thumbnails` below. What this function adds is the three things that are
    about being a worker rather than about decoding:

      * the deadline, turned from a duration into a moment so every rung of the
        ladder can check the same one;
      * the size refusal, which is a policy about somebody's bandwidth rather
        than about decoding -- a 400 MB video is not read to make a picture,
        and the reply says so in a sentence rather than coming back empty;
      * the envelope, so a file that cannot be opened is a `Preview` with a
        note on it and not an exception the caller has to catch.

    The reply status is OK even for a preview that came to nothing, and that is
    deliberate: "this file has no preview" is an answer, not a failure, and a
    caller that had to tell the two apart would need the same branch twice.
    Only a file that could not be reached at all is an error.
    """
    box = max(0, int(request.args.get("box") or 0))
    text_bytes = int(request.args.get("text_bytes", PREVIEW_TEXT_BYTES))
    allow_shell = bool(request.args.get("shell", True))
    page = max(0, int(request.args.get("page") or 0))
    deadline = time.monotonic() + request.timeout

    try:
        size = os.path.getsize(request.path)
    except OSError as exc:
        outbox.put(_failure(request, exc))
        return
    if size > decode.MAX_DECODE_BYTES:
        outbox.put(Reply(request.id, Status.OK, payload=Preview(
            form=PreviewForm.NONE, size=size,
            note=f"{size / (1024 * 1024):,.0f} MB is too large to read for a "
                 f"preview")))
        return

    answer = decode.preview(request.path, box=box, deadline=deadline,
                            text_bytes=text_bytes, allow_shell=allow_shell,
                            page=page)
    outbox.put(Reply(request.id, Status.OK, payload=answer))


def _thumbnails(request: Request, outbox: Any) -> None:
    """Small pictures for a screenful of the grid, in one reply.

    `_file_icons` above with a decoder behind it instead of the shell's icon
    call, and every bound it has for the same reasons: the kind is checked here
    as well as in the caller, because a bound only one end enforces is a bound a
    later caller can lose; anything path-shaped in a name is refused rather than
    followed; the deadline is checked between files, so a share that goes quiet
    costs the cells it had not reached and nothing else; and the pictures are
    keyed on a digest, so a folder holding forty copies of one drawing is one
    image rather than forty.

    A name that produced nothing is simply absent from `rows`, which the caller
    reads as "draw the icon for its kind" -- the thing it was drawing before
    this request existed.
    """
    names = [str(name) for name in (request.args.get("names") or [])]
    size = max(16, int(request.args.get("size") or 128))
    allow_shell = bool(request.args.get("shell", True))
    empty = {"size": size, "rows": {}, "images": {}}
    if not names:
        outbox.put(Reply(request.id, Status.OK, payload=empty))
        return

    deadline = time.monotonic() + request.timeout
    rows: dict[str, str] = {}
    images: dict[str, bytes] = {}

    for name in names:
        if time.monotonic() > deadline:
            outbox.put(Reply(request.id, Status.TIMEOUT,
                             payload={"size": size, "rows": rows,
                                      "images": images},
                             message="the decoder did not reach every file "
                                     "within the deadline"))
            return
        if any(ch in name for ch in _SEPARATORS):
            continue
        if preview_family(name) not in ("image", "raw", "shell"):
            # The caller's bound, restated. Text is not a thumbnail: ninety
            # cells of grey lines at 128 pixels are ninety identical squares,
            # and the icon for the kind says more in less space.
            continue
        try:
            picture = decode.thumbnail(paths.join(request.path, name), size,
                                       deadline=deadline,
                                       allow_shell=allow_shell)
        except Exception:  # noqa: BLE001 - one unreadable file is not a failed request
            continue
        if not picture:
            continue
        key = hashlib.sha1(picture).hexdigest()[:16]
        rows[name] = key
        images.setdefault(key, picture)
    outbox.put(Reply(request.id, Status.OK,
                     payload={"size": size, "rows": rows, "images": images}))


#: `SHGFI_OVERLAYINDEX` and the image list flags that go with it. Not in
#: `shellcon`, which stops short of the overlay flags, so they are written out
#: here with their values from `shellapi.h` and `commctrl.h`.
SHGFI_OVERLAYINDEX = 0x00000040
ILD_TRANSPARENT = 0x00000001

#: An overlay index lives in the top byte of the system icon index, and the
#: image list wants it back in the second byte. Both shifts are the API's,
#: not a convention of this file.
_OVERLAY_SHIFT = 24
_OVERLAY_MASK = 0x0F
_INDEX_MASK = 0x00FFFFFF
_TO_OVERLAY_MASK = 8


def _overlays(request: Request, outbox: Any) -> None:
    """Which of these files carry a badge, and what the badge looks like.

    This is the one icon request that goes near a path, and everything about
    its shape is an attempt to keep that affordable. An overlay is a fact
    about the file rather than about its type -- whether this folder is
    shared, whether this file is in OneDrive, what source control thinks of
    it -- so the handler has to be asked about the file by name, and there is
    no version of this that answers from the extension alone.

    What keeps it bounded:

      * the caller asks about the rows on screen, not the folder;
      * the answer is a picture per *kind of badge on a kind of file* rather
        than per file, keyed on the system icon index and the overlay index,
        so a working copy of 400 modified `.cs` files is one image;
      * the deadline is checked between files, so a share that goes quiet
        costs the badges and nothing else.

    A file with no overlay is left out of the answer rather than carrying a
    null. The caller draws its normal icon for those, which is what it was
    already drawing while this was in flight.
    """
    names = [str(name) for name in (request.args.get("names") or [])]
    size = 32 if int(request.args.get("size", 16) or 16) > 16 else 16
    empty = {"size": size, "rows": {}, "images": {}}
    if not names:
        outbox.put(Reply(request.id, Status.OK, payload=empty))
        return
    if win32shell is None or win32gui is None or shellcon is None:
        outbox.put(Reply(request.id, Status.ERROR, payload=empty,
                         message="icon overlays need pywin32 on Windows"))
        return

    _ensure_com()
    deadline = time.monotonic() + request.timeout
    flags = (shellcon.SHGFI_SYSICONINDEX | SHGFI_OVERLAYINDEX
             | (shellcon.SHGFI_LARGEICON if size > 16 else shellcon.SHGFI_SMALLICON))
    rows: dict[str, str] = {}
    wanted: dict[str, tuple[int, int]] = {}
    image_list = 0

    for name in names:
        if time.monotonic() > deadline:
            outbox.put(Reply(request.id, Status.TIMEOUT,
                             payload={"size": size, "rows": rows, "images": {}},
                             message="the shell did not answer within the deadline"))
            return
        try:
            answer = win32shell.SHGetFileInfo(paths.join(request.path, name), 0, flags)
        except Exception:  # noqa: BLE001 - a file that has just gone is not an error
            continue
        handle, index = _overlay_answer(answer)
        overlay = (index >> _OVERLAY_SHIFT) & _OVERLAY_MASK
        if not overlay:
            continue
        image_list = image_list or handle
        key = f"{index & _INDEX_MASK}:{overlay}"
        rows[name] = key
        wanted.setdefault(key, (index & _INDEX_MASK, overlay))

    images: dict[str, bytes] = {}
    problems: list[str] = []
    for key, (index, overlay) in wanted.items():
        if time.monotonic() > deadline:
            break
        pixels = _overlay_image(image_list, index, overlay, size, problems)
        if pixels is not None:
            images[key] = pixels
    outbox.put(Reply(request.id, Status.OK,
                     payload={"size": size, "rows": rows, "images": images},
                     message=problems[0] if problems and not images else ""))


def _overlay_answer(answer: Any) -> tuple[int, int]:
    """The system image list and the icon index out of what pywin32 returned.

    Asked for with `SHGFI_SYSICONINDEX`, the call's own return value is the
    handle of the shell's image list and the index is in the info structure.
    Read defensively for the same reason `_icon_handle` is: guessing the shape
    wrong here does not draw the wrong picture, it draws nothing, quietly.
    """
    handle = index = 0
    if isinstance(answer, (tuple, list)) and answer:
        if isinstance(answer[0], int):
            handle = int(answer[0])
        for part in answer:
            if isinstance(part, (tuple, list)) and len(part) > 1:
                index = int(part[1] or 0)
                break
    return handle, index


def _overlay_image(image_list: int, index: int, overlay: int, size: int,
                   problems: list[str]) -> bytes | None:
    """The badge drawn onto its file's icon, as one picture.

    Composited by the shell rather than by this application: the image list
    draws the icon and its overlay together when asked, and where an overlay
    sits on an icon is the shell's business. Drawing them separately would
    mean deciding that here, and getting it slightly wrong on every row.
    """
    if not image_list:
        return None
    try:
        handle = win32gui.ImageList_GetIcon(
            image_list, index, ILD_TRANSPARENT | (overlay << _TO_OVERLAY_MASK))
    except Exception as exc:  # noqa: BLE001
        problems.append(f"the image list refused an overlay: {_describe(exc)}")
        return None
    if not handle:
        return None
    try:
        return _icon_pixels(handle, size, problems)
    finally:
        try:
            win32gui.DestroyIcon(handle)
        except Exception:  # noqa: BLE001
            pass


def _shell_icon(key: str, size: int, problems: list[str] | None = None) -> bytes | None:
    """One icon, by kind, as premultiplied BGRA.

    A key is an extension with its dot, or one of the two names for the kinds
    that are not extensions. Anything else is refused rather than passed
    through to the shell.

    `SHGetFileInfo` comes from `win32com.shell.shell` and not from `win32gui`,
    which has most of the rest of this. Worth stating because getting it wrong
    is not a loud failure: the attribute is simply absent, the call raises, and
    every key comes back empty -- which is exactly what an unusual folder full
    of unregistered extensions would also look like. That is what `problems`
    is for.
    """
    if not key or any(ch in key for ch in _SEPARATORS):
        return None
    if key == ICON_FOLDER:
        name, attributes = "folder", win32con.FILE_ATTRIBUTE_DIRECTORY
    elif key == ICON_FILE:
        name, attributes = "file", win32con.FILE_ATTRIBUTE_NORMAL
    elif key.startswith("."):
        name, attributes = "file" + key, win32con.FILE_ATTRIBUTE_NORMAL
    else:
        return None

    flags = shellcon.SHGFI_ICON | shellcon.SHGFI_USEFILEATTRIBUTES
    flags |= shellcon.SHGFI_LARGEICON if size > 16 else shellcon.SHGFI_SMALLICON
    try:
        answer = win32shell.SHGetFileInfo(name, attributes, flags)
    except Exception as exc:  # noqa: BLE001 - a missing association is not an error
        if problems is not None:
            problems.append(f"{key}: {_describe(exc)}")
        return None
    handle = _icon_handle(answer)
    if not handle:
        if problems is not None:
            problems.append(f"{key}: the shell returned no icon handle")
        return None
    try:
        pixels = _icon_pixels(handle, size, problems)
    finally:
        try:
            win32gui.DestroyIcon(handle)
        except Exception:  # noqa: BLE001 - already gone is the outcome wanted
            pass
    return pixels


def _icon_handle(answer: Any) -> int:
    """The HICON out of whatever shape pywin32 handed back.

    Documented as a two-tuple of the API's own result and an SHFILEINFO, and
    the SHFILEINFO starts with the handle -- so the usual shape is
    `(result, (hIcon, ...))`. The others are read too rather than asserted
    against, because the cost of guessing wrong is not a wrong picture: it is
    an icon handle leaked per row, and GDI runs out quietly.
    """
    if isinstance(answer, int):
        return answer
    if isinstance(answer, (tuple, list)) and answer:
        for part in answer:
            if isinstance(part, (tuple, list)) and part:
                return int(part[0] or 0)
        handle = getattr(answer[-1], "hIcon", None)
        if handle is not None:
            return int(handle or 0)
        if len(answer) == 1 and isinstance(answer[0], int):
            return int(answer[0])
    return int(getattr(answer, "hIcon", 0) or 0)


#: The two backgrounds an icon is composited against to recover its alpha.
_ON_BLACK = 0x000000
_ON_WHITE = 0xFFFFFF


def _icon_pixels(hicon: int, size: int, problems: list[str] | None = None) -> bytes | None:
    """An icon as premultiplied BGRA, drawn twice to work out its alpha.

    `DrawIconEx` composites correctly onto whatever is already there, both for
    a modern 32-bit icon and for a legacy one carrying only a mask. What it
    leaves in the fourth byte afterwards is not dependable: a device bitmap
    has no alpha channel to speak of, and reading one back gives zeroes for
    icons that are plainly visible on screen. Deriving the alpha from a mask
    means asking for the mask and handling both icon formats; drawing twice
    handles both without knowing which one this is.

    So the same icon is drawn once on black and once on white. A pixel that
    came out the same on both is opaque, one that differs by the full range
    was never painted, and everything between is the partial coverage at the
    edges. The black pass is by definition the colour already multiplied by
    that alpha, which is the form Qt wants to be handed.
    """
    expected = size * size * 4
    on_black = _draw_icon(hicon, size, _ON_BLACK, problems)
    on_white = _draw_icon(hicon, size, _ON_WHITE, problems)
    if on_black is None or on_white is None:
        return None
    if len(on_black) != expected or len(on_white) != expected:
        # Not the 32-bit surface this assumes. Better nothing than a picture
        # made of the wrong bytes, and said out loud rather than guessed at.
        if problems is not None:
            problems.append(f"a {size}x{size} icon came back as {len(on_black)} "
                            f"bytes rather than {expected}; the desktop may not "
                            f"be 32-bit")
        return None

    pixels = bytearray(on_black)
    for index in range(0, expected, 4):
        # Any channel answers the question and red is as good as the others:
        # the difference between the two passes is exactly what the icon did
        # not cover.
        alpha = 255 - (on_white[index + 2] - on_black[index + 2])
        pixels[index + 3] = 0 if alpha < 0 else (255 if alpha > 255 else alpha)
    return bytes(pixels)


def _draw_icon(hicon: int, size: int, fill: int,
               problems: list[str] | None = None) -> bytes | None:
    """Draw one icon onto a solid background and read the pixels back.

    Every handle taken here is given back in the same call. A worker that
    draws a few thousand icons over an afternoon and leaks one GDI object
    each time stops being able to draw anything at all, and the way that
    presents is a window that goes blank rather than an error anybody can
    trace back to here.
    """
    screen = win32gui.GetDC(0)
    surface = memory = bitmap = None
    try:
        surface = win32ui.CreateDCFromHandle(screen)
        memory = surface.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(surface, size, size)
        memory.SelectObject(bitmap)
        memory.FillSolidRect((0, 0, size, size), fill)
        win32gui.DrawIconEx(memory.GetSafeHdc(), 0, 0, hicon, size, size,
                            0, None, win32con.DI_NORMAL)
        return bytes(bitmap.GetBitmapBits(True))
    except Exception as exc:  # noqa: BLE001 - a drawing failure is one missing icon
        if problems is not None:
            problems.append(f"drawing the icon failed: {_describe(exc)}")
        return None
    finally:
        if bitmap is not None:
            try:
                win32gui.DeleteObject(bitmap.GetHandle())
            except Exception:  # noqa: BLE001
                pass
        if memory is not None:
            try:
                memory.DeleteDC()
            except Exception:  # noqa: BLE001
                pass
        # `surface` wraps the desktop's own DC and is deliberately not deleted:
        # the handle belongs to the desktop, pywin32 does not own it, and
        # releasing it below is the whole of the cleanup it needs.
        win32gui.ReleaseDC(0, screen)


# --------------------------------------------------------------------------
# The ops that change something. Every one of them reports what it did rather
# than only whether it worked, because a caller that has to re-list to find out
# is a caller that will sometimes not bother.
# --------------------------------------------------------------------------


def _mkdir(request: Request, outbox: Any) -> None:
    """One folder, not a tree.

    `os.mkdir` rather than `makedirs`: a typo in a name should fail, not
    quietly build the two folders it implies.
    """
    try:
        os.mkdir(request.path)
    except OSError as exc:
        outbox.put(_failure(request, exc))
        return
    outbox.put(Reply(request.id, Status.OK, payload={"path": request.path}))


def _rename(request: Request, outbox: Any) -> None:
    """Rename within the folder, refusing to overwrite anything.

    `os.rename` on Windows already fails when the target exists, which is the
    behaviour wanted -- but only for a different name. A rename that differs
    only in case is the same file to Windows and must still be allowed, since
    fixing the case of a name is a thing people do.
    """
    name = str(request.args.get("name", ""))
    if not name or any(ch in name for ch in '\\/:*?"<>|'):
        outbox.put(Reply(request.id, Status.ERROR,
                         message=f"{name!r} is not a usable file name"))
        return
    target = os.path.join(os.path.dirname(request.path), name)
    if (os.path.normcase(target) != os.path.normcase(request.path)
            and os.path.exists(target)):
        outbox.put(Reply(request.id, Status.ERROR,
                         message=f"{name} already exists in this folder"))
        return
    try:
        os.rename(request.path, target)
    except OSError as exc:
        outbox.put(_failure(request, exc))
        return
    outbox.put(Reply(request.id, Status.OK, payload={"path": target, "name": name}))


def _delete(request: Request, outbox: Any) -> None:
    """Delete through the shell, so it lands in the Recycle Bin.

    The shell rather than `os.remove` and `shutil.rmtree`, and the difference
    is the whole point: the Recycle Bin is the undo for the one operation in
    this application that has no other undo. It also gets the cases that are
    tedious to get right by hand -- a read-only attribute, a long path, a
    junction that must not be followed into.

    Without pywin32 a recycle is **refused** rather than performed
    permanently. Quietly turning "delete" into "delete forever" because a
    library is missing is the kind of helpfulness that loses somebody's work.
    A permanent delete the user actually asked for still works, because that
    is what they asked for.
    """
    names = [str(name) for name in (request.args.get("names") or [])]
    permanent = bool(request.args.get("permanent", False))
    if not names:
        outbox.put(Reply(request.id, Status.ERROR, message="nothing to delete"))
        return
    targets = [os.path.join(request.path, name) for name in names]

    if win32shell is not None:
        try:
            deleted, aborted, problem, _code = shell_delete(targets,
                                                            permanent=permanent)
        except Exception as exc:  # noqa: BLE001 - pywintypes.error is not an OSError
            outbox.put(Reply(request.id, _shell_status(exc), message=_describe(exc)))
            return
        if problem:
            outbox.put(Reply(request.id, Status.ERROR, message=problem))
            return
        outbox.put(Reply(request.id, Status.OK, payload={
            "deleted": deleted, "permanent": permanent, "aborted": aborted,
        }))
        return

    if not permanent:
        outbox.put(Reply(request.id, Status.ERROR, message=RECYCLE_NEEDS_PYWIN32))
        return

    removed = 0
    for target in targets:
        try:
            if os.path.isdir(target) and not os.path.islink(target):
                shutil.rmtree(target)
            else:
                os.remove(target)
        except OSError as exc:
            outbox.put(Reply(request.id, _status_for(exc), payload={"deleted": removed},
                             message=f"{os.path.basename(target)}: {_describe(exc)}"))
            return
        removed += 1
    outbox.put(Reply(request.id, Status.OK, payload={
        "deleted": removed, "permanent": True, "aborted": False,
    }))


#: Said in one place because two callers say it: the worker's own handler and
#: the queue's recycle job. Turning a recycle into a permanent delete because a
#: library is missing is the kind of helpfulness that loses somebody's work, so
#: neither of them does it.
RECYCLE_NEEDS_PYWIN32 = (
    "the Recycle Bin needs pywin32, which is not importable here. "
    "Nothing was deleted; a permanent delete is still available and "
    "says so before it runs."
)


def shell_delete(targets: list[str], *, permanent: bool = False):
    """One `SHFileOperation` for the whole list.

    Returns (deleted, aborted, problem, code).

    Full paths rather than a folder and names, because the queue's jobs carry
    paths and the worker's requests carry names, and the thing they have in
    common is the paths. One call for the list rather than one per item, which
    is not an optimisation: the shell treats one call as one operation, and
    that is what makes a recycle of forty files a single undo in Explorer.
    """
    _ensure_com()
    flags = (shellcon.FOF_NOCONFIRMATION | shellcon.FOF_NOERRORUI
             | shellcon.FOF_SILENT | shellcon.FOF_NOCONFIRMMKDIR)
    if not permanent:
        flags |= shellcon.FOF_ALLOWUNDO
    result, aborted = _shell_delete(targets, flags)
    if result:
        return 0, bool(aborted), f"the shell refused the delete (code {result})", int(result)
    return len(targets), bool(aborted), "", 0


def recycle(targets: list[str]):
    """To the Recycle Bin. Returns (deleted, problem, code), for the queue's job.

    The queue calls this rather than carrying its own copy, so that the
    destructive path the elevated retry runs and the one an ordinary delete
    runs are the same lines of code. The code comes back with the message
    because the queue has to tell a refusal from a failure: only the first is
    worth offering to run as administrator.
    """
    if win32shell is None:
        return 0, RECYCLE_NEEDS_PYWIN32, 0
    deleted, _aborted, problem, code = shell_delete(targets, permanent=False)
    return deleted, problem, code


def _shell_delete(targets: list[str], flags: int):
    """`SHFileOperation`, whichever spelling of the source list pywin32 wants.

    The source is a double-null-terminated list of paths in the API, and
    pywin32 has accepted both a sequence and a joined string across versions.
    Trying both here beats finding out on somebody's machine which one this
    install wanted.
    """
    operation = (0, shellcon.FO_DELETE, tuple(targets), None, flags, None, None)
    try:
        return win32shell.SHFileOperation(operation)
    except TypeError:
        joined = "\0".join(targets) + "\0\0"
        return win32shell.SHFileOperation(
            (0, shellcon.FO_DELETE, joined, None, flags, None, None)
        )


def _elevate(request: Request, outbox: Any) -> None:
    """Run one refused operation again, as administrator.

    In a worker because it is a shell call and because it blocks: the consent
    prompt is a person reading a dialog, and the wait for it is exactly the
    kind of wait the UI thread never does. The deadline is the operation's own
    plus a grace for the prompt, and a worker killed while waiting leaves the
    elevated process to finish on its own -- it is not a child of this one, so
    nothing this application does can interrupt a delete that is already
    running with administrator rights. That is the right way round.

    Nothing is elevated that `elevate.ACTIONS` does not name, and the check is
    made here as well as in the elevated process. One of those two is
    redundant and neither is the one to leave out.
    """
    plan = request.args.get("plan")
    if not isinstance(plan, dict) or str(plan.get("action") or "") not in elevate.ACTIONS:
        outbox.put(Reply(request.id, Status.ERROR,
                         message="that operation cannot be run as administrator"))
        return
    if win32shell is None or win32event is None or win32process is None:
        outbox.put(Reply(request.id, Status.ERROR,
                         message="running as administrator needs pywin32 on Windows"))
        return

    _ensure_com()
    plan_path = elevate.write_plan(plan, request.timeout)
    executable, prefix, directory = elevate.command()
    try:
        started = win32shell.ShellExecuteEx(
            fMask=shellcon.SEE_MASK_NOCLOSEPROCESS,
            lpVerb="runas",
            lpFile=executable,
            lpParameters=f'{prefix}{elevate.FLAG} "{plan_path}"',
            lpDirectory=directory,
            nShow=win32con.SW_HIDE,
        )
    except Exception as exc:  # noqa: BLE001 - the prompt was refused, or worse
        elevate.clean_up(plan_path)
        status = _shell_status(exc)
        message = ("the request to run as administrator was refused"
                   if status is Status.DENIED else _describe(exc))
        outbox.put(Reply(request.id, status, message=message))
        return

    process = started.get("hProcess") if isinstance(started, dict) else None
    if not process:
        elevate.clean_up(plan_path)
        outbox.put(Reply(request.id, Status.ERROR,
                         message="the elevated process did not start"))
        return

    limit = int((request.timeout + elevate.CONSENT_GRACE) * 1000)
    try:
        waited = win32event.WaitForSingleObject(process, limit)
        if waited != win32event.WAIT_OBJECT_0:
            outbox.put(Reply(request.id, Status.TIMEOUT,
                             message="the elevated operation did not finish in time; "
                                     "it may still be running"))
            return
        code = win32process.GetExitCodeProcess(process)
    except Exception as exc:  # noqa: BLE001
        outbox.put(Reply(request.id, Status.ERROR, message=_describe(exc)))
        return
    finally:
        result = elevate.read_result(plan_path)
        elevate.clean_up(plan_path)
        try:
            win32api_close(process)
        except Exception:  # noqa: BLE001
            pass

    status = _status_named(result.get("status"), code)
    outbox.put(Reply(request.id, status, payload=result.get("payload"),
                     message=str(result.get("message") or
                                 ("" if status is Status.OK else
                                  f"the elevated operation exited with {code}"))))


def win32api_close(handle: Any) -> None:
    """Close a handle from `ShellExecuteEx`. Named rather than inlined because
    forgetting it leaks a process handle per elevation, and a leak that only
    happens when somebody is deleting from Program Files is a leak nobody
    finds.
    """
    import win32api

    win32api.CloseHandle(handle)


def _status_named(name: Any, code: int) -> Status:
    """The status the elevated process reported, or one derived from its exit code."""
    try:
        return Status(str(name))
    except ValueError:
        return Status.OK if code == 0 else Status.ERROR


def _resolve(request: Request, outbox: Any) -> None:
    """Resolution in the worker, so the caller's answer reflects the session
    table as the process that will do the work sees it.
    """
    outbox.put(Reply(request.id, Status.OK, payload={
        "normalized": paths.normalize(request.path),
        "resolved": paths.resolve(request.path),
        "volume_key": paths.volume_key(request.path),
    }))


# --------------------------------------------------------------------------
# Plumbing.
# --------------------------------------------------------------------------


def _drain_control(control: Any, cancelled: set[int]) -> None:
    """Take everything waiting on the control queue without blocking.

    Cancellation arrives here rather than on the inbound queue because the
    inbound queue is only read between requests, and a cancel that has to wait
    for the request it cancels is not a cancel.
    """
    while True:
        try:
            message = control.get_nowait()
        except (queue.Empty, OSError):
            return
        if not message:
            continue
        kind, value = message
        if kind == "cancel":
            cancelled.add(value)


def _failure(request: Request, exc: OSError, *, seq: int = 0) -> Reply:
    return Reply(request.id, _status_for(exc), message=_describe(exc), seq=seq)


def _ensure_com() -> None:
    """Initialise COM for this process, once, before the shell is asked anything.

    An apartment that is already initialised, in either mode, is not a failure
    worth reporting: the call that matters is the one after this, and it will
    say for itself whether it worked.
    """
    global _com_ready
    if _com_ready or pythoncom is None:
        return
    try:
        pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
    except Exception:  # noqa: BLE001 - see the docstring
        pass
    _com_ready = True


def _shell_status(exc: BaseException) -> Status:
    """A shell failure in the same vocabulary as a filesystem one.

    `pywintypes.error` is not an `OSError` and carries its number in `args[0]`
    rather than in `winerror`, so it cannot go through `_status_for`. The
    distinction worth keeping is the same one: whether retrying is sensible.
    """
    number = getattr(exc, "winerror", None)
    if number is None and getattr(exc, "args", None):
        number = exc.args[0] if isinstance(exc.args[0], int) else None
    if number in _GONE_WINERRORS or number in (2, 3):
        return Status.GONE
    if number == 5:
        return Status.DENIED
    return Status.ERROR


def _status_for(exc: OSError) -> Status:
    if isinstance(exc, PermissionError):
        return Status.DENIED
    if isinstance(exc, FileNotFoundError):
        return Status.GONE
    winerror = getattr(exc, "winerror", None)
    if winerror in _GONE_WINERRORS:
        return Status.GONE
    return Status.ERROR


def _describe(exc: BaseException) -> str:
    winerror = getattr(exc, "winerror", None)
    detail = f" (winerror {winerror})" if winerror else ""
    return f"{type(exc).__name__}: {exc}{detail}"

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

import os
import queue
import shutil
import signal
import time
from typing import Any

from app.io import paths
from app.io.protocol import (
    BATCH_SIZE,
    ICON_FILE,
    ICON_FOLDER,
    Entry,
    Op,
    Reply,
    Request,
    Status,
)

#: The shell, for opening a file the way Explorer does. Optional at import so
#: the module still loads where pywin32 does not; `_open` says so rather than
#: guessing when it is missing.
try:
    import pythoncom
    import win32con
    import win32gui
    import win32ui
    from win32com.shell import shell as win32shell, shellcon
except Exception:  # noqa: BLE001 - reported by _open, like paths.win32_problem
    pythoncom = None
    win32con = None
    win32gui = None
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
    if win32gui is None or win32ui is None or shellcon is None:
        outbox.put(Reply(request.id, Status.ERROR, payload={"size": size, "icons": {}},
                         message="shell icons need pywin32 on Windows"))
        return

    _ensure_com()
    deadline = time.monotonic() + request.timeout
    icons: dict[str, bytes] = {}
    for key in keys:
        if time.monotonic() > deadline:
            outbox.put(Reply(request.id, Status.TIMEOUT,
                             payload={"size": size, "icons": icons},
                             message="the shell did not answer within the deadline"))
            return
        pixels = _shell_icon(key, size)
        if pixels is not None:
            icons[key] = pixels
    outbox.put(Reply(request.id, Status.OK, payload={"size": size, "icons": icons}))


def _shell_icon(key: str, size: int) -> bytes | None:
    """One icon, by kind, as premultiplied BGRA.

    A key is an extension with its dot, or one of the two names for the kinds
    that are not extensions. Anything else is refused rather than passed
    through to the shell.
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
        answer = win32gui.SHGetFileInfo(name, attributes, flags)
    except Exception:  # noqa: BLE001 - a missing association is not an error
        return None
    handle = _icon_handle(answer)
    if not handle:
        return None
    try:
        return _icon_pixels(handle, size)
    finally:
        try:
            win32gui.DestroyIcon(handle)
        except Exception:  # noqa: BLE001 - already gone is the outcome wanted
            pass


def _icon_handle(answer: Any) -> int:
    """The HICON out of whatever shape pywin32 returned.

    Documented as a two-tuple of a result and the SHFILEINFO, and returned
    that way by every version this has been run against -- but the structure
    itself starts with the handle, so both shapes are read rather than one
    asserted. Guessing wrong here would leak an icon handle per row.
    """
    if isinstance(answer, (tuple, list)) and answer:
        first = answer[0]
        if isinstance(first, (tuple, list)) and first:
            return int(first[0] or 0)
        if len(answer) > 1 and isinstance(answer[1], (tuple, list)) and answer[1]:
            return int(answer[1][0] or 0)
        if isinstance(first, int) and len(answer) == 1:
            return int(first)
    return 0


#: The two backgrounds an icon is composited against to recover its alpha.
_ON_BLACK = 0x000000
_ON_WHITE = 0xFFFFFF


def _icon_pixels(hicon: int, size: int) -> bytes | None:
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
    on_black = _draw_icon(hicon, size, _ON_BLACK)
    on_white = _draw_icon(hicon, size, _ON_WHITE)
    if on_black is None or on_white is None:
        return None
    if len(on_black) != expected or len(on_white) != expected:
        return None  # not the 32-bit surface this assumes; better nothing

    pixels = bytearray(on_black)
    for index in range(0, expected, 4):
        # Any channel answers the question and red is as good as the others:
        # the difference between the two passes is exactly what the icon did
        # not cover.
        alpha = 255 - (on_white[index + 2] - on_black[index + 2])
        pixels[index + 3] = 0 if alpha < 0 else (255 if alpha > 255 else alpha)
    return bytes(pixels)


def _draw_icon(hicon: int, size: int, fill: int) -> bytes | None:
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
    except Exception:  # noqa: BLE001 - a drawing failure is one missing icon
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
        if surface is not None:
            # Detached rather than deleted: the handle underneath belongs to
            # the desktop, and deleting it takes a DC away from every process.
            try:
                surface.Detach()
            except Exception:  # noqa: BLE001
                pass
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
        _ensure_com()
        flags = (shellcon.FOF_NOCONFIRMATION | shellcon.FOF_NOERRORUI
                 | shellcon.FOF_SILENT | shellcon.FOF_NOCONFIRMMKDIR)
        if not permanent:
            flags |= shellcon.FOF_ALLOWUNDO
        try:
            result, aborted = _shell_delete(targets, flags)
        except Exception as exc:  # noqa: BLE001 - pywintypes.error is not an OSError
            outbox.put(Reply(request.id, _shell_status(exc), message=_describe(exc)))
            return
        if result:
            outbox.put(Reply(request.id, Status.ERROR,
                             message=f"the shell refused the delete (code {result})"))
            return
        outbox.put(Reply(request.id, Status.OK, payload={
            "deleted": len(targets), "permanent": permanent, "aborted": bool(aborted),
        }))
        return

    if not permanent:
        outbox.put(Reply(request.id, Status.ERROR, message=(
            "the Recycle Bin needs pywin32, which is not importable here. "
            "Nothing was deleted; a permanent delete is still available and "
            "says so before it runs."
        )))
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

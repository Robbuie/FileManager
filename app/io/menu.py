"""The Explorer context menu, in a process of its own.

The entries TortoiseSVN, 7-Zip and every other shell extension add are not in
a registry key this application could read. They come from a COM object the
extension supplies, which is handed an `HMENU` and fills it in -- and it does
that inside whichever process asks. That is the whole problem with this
feature: asking for a context menu means loading somebody else's DLL and
running their code, and their code does filesystem work on the selection.

So it happens here, in a process that owns nothing else. A shell extension
that blocks on a share that has gone away blocks this process, the pool's
watchdog kills it, the window is untouched and the menu simply does not
appear. In the window's process the same extension would freeze the
application, which is the defect this whole architecture exists to avoid.

Two things follow from being a separate process and are worth stating plainly.

  * **The menu is walked, not shown.** `TrackPopupMenu` would draw the
    platform's own menu, owned by a window this process would have to make
    foreground, and it would look nothing like the rest of the application.
    Instead the `HMENU` is read into plain `MenuItem`s and the window draws
    its own from them. The cost is the entries an extension paints itself
    rather than naming: they arrive with no text and are labelled from their
    verb.
  * **The shell objects stay alive between two requests.** A command can only
    be run through the `IContextMenu` that produced it, so the menu built for
    a MENU request is held until MENU_INVOKE or MENU_RELEASE. One at a time:
    building a second menu releases the first, because a menu that is no
    longer on screen is a menu nobody will invoke.

Being killed between those two requests is a normal thing to happen to this
process. What is lost is a menu the user has to open again.
"""

from __future__ import annotations

import queue
import signal
import time
from typing import Any

from app.io.protocol import (
    MENU_COMMAND,
    MENU_SEPARATOR,
    MENU_SUBMENU,
    MenuItem,
    Op,
    Reply,
    Request,
    Status,
)

try:
    import pythoncom
    import win32api
    import win32con
    import win32gui
    import win32gui_struct
    import win32ui
    from win32com.shell import shell as win32shell, shellcon
except Exception:  # noqa: BLE001 - reported in the reply, never raised at import
    pythoncom = None
    win32api = None
    win32con = None
    win32gui = None
    win32gui_struct = None
    win32ui = None
    win32shell = None
    shellcon = None

#: The id range handed to the shell. It starts at 1 rather than 0 because the
#: shell numbers its commands from an offset of this value, and an offset of
#: zero is indistinguishable from "no command" in the places that check.
MIN_ID = 1
MAX_ID = 0x7000

#: How deep a submenu is followed. Real menus are two levels; the limit is
#: here because the depth comes from somebody else's DLL and a cycle in it
#: would otherwise be a hang rather than a short menu.
MAX_DEPTH = 4

#: How many entries are read from one menu. Same reason.
MAX_ITEMS = 200

#: Menu item flags. From `winuser.h` rather than `win32con`, which is missing
#: the MFT/MFS spellings, and named here so the tests can use them.
MFT_SEPARATOR = 0x00000800
MFT_OWNERDRAW = 0x00000100
MFS_GRAYED = 0x00000003
MFS_CHECKED = 0x00000008
MFS_DEFAULT = 0x00001000


class _Live:
    """The menu currently built, and the objects it cannot be used without."""

    def __init__(self, token: int, menu: Any, hmenu: int, folder: str) -> None:
        self.token = token
        self.menu = menu
        self.hmenu = hmenu
        self.folder = folder


def run(inbox: Any, outbox: Any, control: Any) -> None:
    """Process entry point, the same shape as a volume worker's.

    It answers every request it takes, including the ones it fails, for the
    same reason: a request with no reply is a window waiting forever.
    """
    try:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    except (ValueError, OSError):
        pass

    state: dict[str, Any] = {"live": None, "hwnd": 0, "com": False}
    while True:
        try:
            request = inbox.get()
        except (EOFError, OSError):
            return
        except KeyboardInterrupt:
            continue
        if request is None:
            _release(state)
            return
        _drain(control)
        try:
            _handle(request, outbox, state)
        except Exception as exc:  # noqa: BLE001 - see the docstring
            outbox.put(Reply(request.id, Status.ERROR, message=_describe(exc)))


def _handle(request: Request, outbox: Any, state: dict[str, Any]) -> None:
    if request.op is Op.MENU:
        _build(request, outbox, state)
    elif request.op is Op.MENU_INVOKE:
        _invoke(request, outbox, state)
    elif request.op is Op.MENU_RELEASE:
        _release(state)
        outbox.put(Reply(request.id, Status.OK))
    elif request.op is Op.PING:
        outbox.put(Reply(request.id, Status.OK, payload={"host": "menu"}))
    else:
        outbox.put(Reply(request.id, Status.ERROR,
                         message=f"the shell host does not answer {request.op.value}"))


# --------------------------------------------------------------------------
# Building.
# --------------------------------------------------------------------------


def _build(request: Request, outbox: Any, state: dict[str, Any]) -> None:
    """Ask the shell for the menu for a selection and walk it into `MenuItem`s.

    The selection is a folder and names within it rather than full paths,
    which is not a convenience: `IShellFolder` binds to the folder once and
    parses each name against it, so a selection of 300 files is one bind. It
    is also the only shape that can express "the folder itself", which is what
    a right-click on empty space asks for.
    """
    if win32shell is None or win32gui is None or win32gui_struct is None:
        outbox.put(Reply(request.id, Status.ERROR,
                         message="the Explorer context menu needs pywin32 on Windows"))
        return

    names = [str(name) for name in (request.args.get("names") or [])]
    extended = bool(request.args.get("extended"))
    folder = request.path
    deadline = time.monotonic() + request.timeout

    _release(state)
    _ensure_com(state)
    hwnd = _owner_window(state)

    try:
        shell_menu = _shell_menu(folder, names, hwnd)
    except Exception as exc:  # noqa: BLE001 - a selection the shell will not bind to
        outbox.put(Reply(request.id, _status_for(exc), message=_describe(exc)))
        return

    hmenu = win32gui.CreatePopupMenu()
    flags = shellcon.CMF_NORMAL | shellcon.CMF_EXPLORE
    if extended:
        flags |= shellcon.CMF_EXTENDEDVERBS
    try:
        shell_menu.QueryContextMenu(hmenu, 0, MIN_ID, MAX_ID, flags)
        items = _walk(shell_menu, hmenu, hwnd, depth=0, deadline=deadline)
    except Exception as exc:  # noqa: BLE001
        _destroy(hmenu)
        outbox.put(Reply(request.id, Status.ERROR, message=_describe(exc)))
        return

    # Held, not destroyed: `InvokeCommand` needs this same object, and the
    # HMENU is what some extensions read their own state back out of.
    state["live"] = _Live(request.id, shell_menu, hmenu, folder)
    outbox.put(Reply(request.id, Status.OK,
                     payload={"token": request.id, "items": items}))


def _shell_menu(folder: str, names: list[str], hwnd: int) -> Any:
    """The `IContextMenu` for a selection.

    With no names this is the menu for the folder as an item, which is what
    Explorer shows for a folder in a listing rather than what it shows for the
    background of an open window. The difference is real -- the background
    menu is the shell view's, and it carries New and Paste -- and it is not
    reached from here.
    """
    desktop = win32shell.SHGetDesktopFolder()
    if names:
        pidl, _flags = win32shell.SHILCreateFromPath(folder, 0)
        parent = (desktop.BindToObject(pidl, None, win32shell.IID_IShellFolder)
                  if pidl else desktop)
        children = [parent.ParseDisplayName(hwnd, None, name)[1] for name in names]
        return parent.GetUIObjectOf(hwnd, children, win32shell.IID_IContextMenu, 0)

    pidl, _flags = win32shell.SHILCreateFromPath(folder, 0)
    if not pidl:
        raise OSError(f"the shell does not recognise {folder}")
    # A root -- a drive, or the root of a share -- has no parent folder to be
    # an item of, so the desktop is asked for it directly.
    parent_pidl = pidl[:-1]
    if parent_pidl:
        parent = desktop.BindToObject(parent_pidl, None, win32shell.IID_IShellFolder)
        return parent.GetUIObjectOf(hwnd, [pidl[-1:]], win32shell.IID_IContextMenu, 0)
    return desktop.GetUIObjectOf(hwnd, [pidl], win32shell.IID_IContextMenu, 0)


def _walk(shell_menu: Any, hmenu: int, hwnd: int, *,
          depth: int, deadline: float) -> list[MenuItem]:
    """Read an `HMENU` into plain items, following its submenus.

    Submenus are told they are opening first. Most extensions fill their
    submenu in during `QueryContextMenu` and this changes nothing for them,
    but the ones that build it on demand -- the shell's own Send To, among
    others -- hand back an empty popup otherwise, and an empty submenu in the
    window is indistinguishable from a broken one.

    Everything here is defensive about a menu it did not build. An entry that
    cannot be read is dropped rather than guessed at: a wrong label on
    somebody else's command is worse than a missing one, because the user will
    click it.
    """
    items: list[MenuItem] = []
    try:
        count = win32gui.GetMenuItemCount(hmenu)
    except Exception:  # noqa: BLE001
        return items

    for position in range(min(count, MAX_ITEMS)):
        if time.monotonic() > deadline:
            break
        try:
            buffer, _extras = win32gui_struct.EmptyMENUITEMINFO()
            win32gui.GetMenuItemInfo(hmenu, position, True, buffer)
            info = win32gui_struct.UnpackMENUITEMINFO(buffer)
        except Exception:  # noqa: BLE001 - an entry that will not be read
            continue

        item_type = int(info.fType or 0)
        state = int(info.fState or 0)
        if item_type & MFT_SEPARATOR:
            items.append(MenuItem(id=0, kind=MENU_SEPARATOR))
            continue

        text = _clean(info.text)
        submenu = int(info.hSubMenu or 0)
        item_id = int(info.wID or 0)
        offset = item_id - MIN_ID

        if submenu:
            _opening(shell_menu, submenu, position)
            children = _walk(shell_menu, submenu, hwnd,
                             depth=depth + 1, deadline=deadline) if depth < MAX_DEPTH else []
            if not text and not children:
                continue
            items.append(MenuItem(
                id=0, kind=MENU_SUBMENU, text=text or "More",
                enabled=not (state & MFS_GRAYED) and bool(children),
                icon=_bitmap(info.hbmpItem), icon_size=_ICON_SIZE if info.hbmpItem else 0,
                items=tuple(children),
            ))
            continue

        if not MIN_ID <= item_id <= MAX_ID:
            continue  # not one of the shell's own commands
        verb = _command_string(shell_menu, offset, _GCS_VERB)
        if not text and item_type & MFT_OWNERDRAW:
            # An entry the extension paints itself. Its verb is the only name
            # it has given us, and a named entry that works beats a blank one.
            text = verb.replace("_", " ").strip() or "(unnamed command)"
        if not text:
            continue
        items.append(MenuItem(
            id=item_id,
            kind=MENU_COMMAND,
            text=text,
            enabled=not (state & MFS_GRAYED),
            checked=bool(state & MFS_CHECKED),
            default=bool(state & MFS_DEFAULT),
            verb=verb,
            help=_command_string(shell_menu, offset, _GCS_HELP),
            icon=_bitmap(info.hbmpItem),
            icon_size=_ICON_SIZE if info.hbmpItem else 0,
        ))
    return items


def _opening(shell_menu: Any, submenu: int, position: int) -> None:
    """Tell an extension its submenu is being opened, if it wants to know.

    `IContextMenu2` is how a menu that is built on demand gets told, and an
    extension that does not implement it is the normal case rather than a
    problem -- hence the whole thing being a guarded query.
    """
    if win32shell is None or win32con is None:
        return
    try:
        handler = shell_menu.QueryInterface(win32shell.IID_IContextMenu2)
    except Exception:  # noqa: BLE001 - it does not want to know
        return
    try:
        handler.HandleMenuMsg(win32con.WM_INITMENUPOPUP, submenu, position)
    except Exception:  # noqa: BLE001 - and it may still refuse
        pass


# --------------------------------------------------------------------------
# Invoking.
# --------------------------------------------------------------------------


def _invoke(request: Request, outbox: Any, state: dict[str, Any]) -> None:
    """Run one entry of the menu that is still open.

    The token check is the whole safety of this call. An id means nothing on
    its own -- it is an index into somebody's menu, and the next menu will use
    the same numbers for different commands -- so an id that arrives after the
    menu it came from has gone is refused rather than run against whatever is
    open now.
    """
    live: _Live | None = state.get("live")
    token = int(request.args.get("token") or 0)
    item_id = int(request.args.get("item") or 0)
    if live is None or live.token != token:
        outbox.put(Reply(request.id, Status.GONE,
                         message="that menu is no longer open; open it again"))
        return
    if not MIN_ID <= item_id <= MAX_ID:
        outbox.put(Reply(request.id, Status.ERROR, message="not a command from that menu"))
        return

    hwnd = _owner_window(state)
    verb = _command_string(live.menu, item_id - MIN_ID, _GCS_VERB)
    try:
        # The window this process keeps is what any dialog the command opens
        # is owned by, and it is put in front first. Windows can refuse that,
        # in which case the dialog is still there and still works -- it
        # announces itself in the taskbar instead of appearing on top.
        _foreground(hwnd)
        live.menu.InvokeCommand((
            0,                      # fMask
            hwnd,
            item_id - MIN_ID,       # the command, as an offset from MIN_ID
            None,                   # lpParameters
            live.folder,            # lpDirectory: where the command runs
            win32con.SW_SHOWNORMAL,
            0,                      # dwHotKey
            None,                   # hIcon
        ))
    except Exception as exc:  # noqa: BLE001
        _release(state)
        outbox.put(Reply(request.id, _status_for(exc), payload={"verb": verb},
                         message=_describe(exc)))
        return

    _release(state)
    # Whether the folder now needs re-listing is not knowable from here: the
    # shell does not say what a verb did. The window re-lists on any invoke
    # that could have changed something, which is every one of them.
    outbox.put(Reply(request.id, Status.OK, payload={"verb": verb}))


def _release(state: dict[str, Any]) -> None:
    live: _Live | None = state.get("live")
    state["live"] = None
    if live is None:
        return
    _destroy(live.hmenu)
    live.menu = None


def _destroy(hmenu: int) -> None:
    if not hmenu or win32gui is None:
        return
    try:
        win32gui.DestroyMenu(hmenu)
    except Exception:  # noqa: BLE001 - already gone is the outcome wanted
        pass


# --------------------------------------------------------------------------
# The odds and ends the shell needs from a process before it will talk to it.
# --------------------------------------------------------------------------

#: `GCS_VERBW` and `GCS_HELPTEXTW`. Named locally so the module has one place
#: that says the wide forms are the ones asked for: the ANSI ones come back as
#: bytes and every extension written this century supplies both.
_GCS_VERB = 4
_GCS_HELP = 5

#: What a menu bitmap is assumed to be. Shell menu bitmaps are 16 pixels
#: square in practice; one that is not is dropped rather than stretched.
_ICON_SIZE = 16

_WINDOW_CLASS = "FileManagerShellMenuHost"


def _ensure_com(state: dict[str, Any]) -> None:
    """Initialise COM as a single-threaded apartment, once.

    Not the multithreaded one, and not optional: shell extensions are written
    against an STA, and several of the common ones fail outright in an MTA --
    quietly, by contributing nothing to the menu, which looks exactly like an
    extension that is not installed.
    """
    if state.get("com") or pythoncom is None:
        return
    try:
        pythoncom.CoInitialize()
    except Exception:  # noqa: BLE001 - already initialised is fine
        pass
    state["com"] = True


def _owner_window(state: dict[str, Any]) -> int:
    """A window for the shell to own its dialogs with.

    Shell extensions take an owner `HWND` and some of them use it: a verb that
    asks a question puts its dialog on it, and a null owner gives a dialog
    with no owner at all, which Windows is entitled to put behind everything.
    So this process keeps one hidden window for the purpose. Failing to make
    it is not fatal -- a null owner is worse, not broken.
    """
    hwnd = int(state.get("hwnd") or 0)
    if hwnd or win32gui is None:
        return hwnd
    try:
        instance = win32api.GetModuleHandle(None)
        window_class = win32gui.WNDCLASS()
        window_class.lpszClassName = _WINDOW_CLASS
        window_class.hInstance = instance
        window_class.lpfnWndProc = {}
        try:
            win32gui.RegisterClass(window_class)
        except Exception:  # noqa: BLE001 - registered by an earlier request
            pass
        hwnd = win32gui.CreateWindow(
            _WINDOW_CLASS, "File Manager", win32con.WS_OVERLAPPED,
            0, 0, 0, 0, 0, 0, instance, None,
        )
    except Exception:  # noqa: BLE001
        hwnd = 0
    state["hwnd"] = hwnd
    return hwnd


def _foreground(hwnd: int) -> None:
    if not hwnd or win32gui is None:
        return
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:  # noqa: BLE001 - Windows decides who gets the foreground
        pass


def _command_string(shell_menu: Any, offset: int, kind: int) -> str:
    """A verb or a help line for one command, or an empty string.

    Guarded because it is optional to implement and several extensions do not.
    An exception here is an entry without a tooltip, not an entry that cannot
    be run.
    """
    if offset < 0:
        return ""
    try:
        value = shell_menu.GetCommandString(offset, kind)
    except Exception:  # noqa: BLE001
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-16-le", "ignore") if kind in (_GCS_VERB, _GCS_HELP) \
            else value.decode("mbcs", "ignore")
    return _clean(value).replace("&", "")


def _bitmap(handle: Any) -> bytes | None:
    """A menu item's own picture, as premultiplied BGRA, or nothing.

    Shell menu bitmaps are 32-bit and already premultiplied, which is the
    format Qt wants and the reason this is a read rather than the two-pass
    drawing the row icons need. A bitmap of another depth or another size is
    dropped: the entry still works, it simply has no picture, and that is a
    much better outcome than a smear of the wrong bytes next to somebody's
    "Delete" command.
    """
    handle = int(handle or 0)
    if not handle or win32ui is None:
        return None
    expected = _ICON_SIZE * _ICON_SIZE * 4
    try:
        bitmap = win32ui.CreateBitmapFromHandle(handle)
        pixels = bytes(bitmap.GetBitmapBits(True))
    except Exception:  # noqa: BLE001
        return None
    if len(pixels) != expected:
        return None
    if not any(pixels[3::4]):
        # No alpha anywhere: a legacy bitmap with no transparency to speak of.
        # Drawn opaque rather than invisible.
        return bytes(bytearray(
            value if index % 4 != 3 else 255 for index, value in enumerate(pixels)
        ))
    return pixels


def _clean(text: Any) -> str:
    """Menu text as Qt should draw it.

    The tab and everything after it is Windows' shortcut column, which this
    application does not honour -- the keys in it belong to Explorer, not
    here, and showing them would promise something that does not happen.
    """
    if not isinstance(text, str):
        return ""
    return text.split("\t")[0].strip()


def _drain(control: Any) -> None:
    """Empty the control queue. A cancel is meaningless here.

    Both requests this process answers are short by construction, and the one
    that is not -- a verb that opens a dialog and waits for a person -- cannot
    be withdrawn from the outside anyway. The queue is drained rather than
    ignored so that it cannot fill.
    """
    while True:
        try:
            control.get_nowait()
        except (queue.Empty, EOFError, OSError, ValueError):
            return


def _status_for(exc: BaseException) -> Status:
    errno = getattr(exc, "winerror", None) or getattr(exc, "errno", None)
    if errno in (2, 3, 53, 67):
        return Status.GONE
    if errno in (5, 1223):   # denied, or the user answered no to a UAC prompt
        return Status.DENIED
    return Status.ERROR


def _describe(exc: BaseException) -> str:
    text = str(exc).strip()
    return text or exc.__class__.__name__

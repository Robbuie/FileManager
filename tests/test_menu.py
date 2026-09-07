"""Checks on the context menu: what is read out of an HMENU, and what an id means.

The part that cannot be checked anywhere but Windows is the shell itself --
whether TortoiseSVN puts anything in the menu is a question about that machine.
Everything around it can be, and the failures worth catching are all on this
side:

  * an entry read wrongly puts somebody else's label on somebody else's
    command, and the user clicks it;
  * an id used without its token runs whatever the *next* menu numbered the
    same way, which is the one failure here that could delete something;
  * a menu built and never released holds a third-party DLL, and sometimes the
    folder, open for the life of the process.
"""

from __future__ import annotations

import pytest

from app.io import menu as host
from app.io.protocol import (
    MENU_SEPARATOR,
    MENU_SUBMENU,
    MENU_HOST,
    Op,
    Reply,
    Request,
    Status,
)


class Item:
    """One entry as `GetMenuItemInfo` would have unpacked it."""

    def __init__(self, text="", wID=0, fType=0, fState=0, hSubMenu=0, hbmpItem=0):
        self.text = text
        self.wID = wID
        self.fType = fType
        self.fState = fState
        self.hSubMenu = hSubMenu
        self.hbmpItem = hbmpItem


class FakeGui:
    """Enough of `win32gui` to walk a menu that was never really built."""

    def __init__(self, menus):
        self.menus = menus
        self.destroyed = []

    def GetMenuItemCount(self, hmenu):  # noqa: N802 - the API's name
        return len(self.menus.get(hmenu, ()))

    def GetMenuItemInfo(self, hmenu, position, by_position, buffer):  # noqa: N802
        buffer[0] = self.menus[hmenu][position]

    def CreatePopupMenu(self):  # noqa: N802
        return 1

    def DestroyMenu(self, hmenu):  # noqa: N802
        self.destroyed.append(hmenu)


class FakeStruct:
    @staticmethod
    def EmptyMENUITEMINFO():  # noqa: N802 - the API's name
        return [None], []

    @staticmethod
    def UnpackMENUITEMINFO(buffer):  # noqa: N802
        return buffer[0]


class FakeContextMenu:
    """The shell's own object, as far as this module is concerned."""

    def __init__(self):
        self.invoked = []
        self.queried = []

    def QueryContextMenu(self, hmenu, index, first, last, flags):  # noqa: N802
        self.queried.append((hmenu, first, last, flags))
        return 0

    def GetCommandString(self, offset, kind):  # noqa: N802
        return f"verb{offset}" if kind == host._GCS_VERB else ""

    def QueryInterface(self, iid):  # noqa: N802
        raise OSError("this extension does not implement IContextMenu2")

    def InvokeCommand(self, info):  # noqa: N802
        self.invoked.append(info)


@pytest.fixture
def walker(monkeypatch):
    """The host with a menu it can read and no Windows underneath it."""
    menus = {}
    gui = FakeGui(menus)
    monkeypatch.setattr(host, "win32gui", gui)
    monkeypatch.setattr(host, "win32gui_struct", FakeStruct)
    monkeypatch.setattr(host, "win32shell", object())
    monkeypatch.setattr(host, "_bitmap", lambda handle: None)
    return menus, gui


class Outbox:
    def __init__(self):
        self.replies = []

    def put(self, reply):
        self.replies.append(reply)


def request(op, **args):
    return Request(id=42, op=op, path="C:\\Jobs", timeout=5.0, args=args)


# ------------------------------------------------------------------ the walk


def test_entries_come_back_with_their_ids_and_verbs(walker):
    menus, _gui = walker
    menus[1] = [Item(text="Open", wID=host.MIN_ID),
                Item(text="7-Zip", wID=host.MIN_ID + 4)]
    items = host._walk(FakeContextMenu(), 1, 0, depth=0, deadline=_later())
    assert [item.text for item in items] == ["Open", "7-Zip"]
    assert [item.id for item in items] == [host.MIN_ID, host.MIN_ID + 4]
    assert items[1].verb == "verb4"


def test_the_shortcut_column_is_not_drawn_as_text(walker):
    """Everything after the tab is Explorer's key, not this application's."""
    menus, _gui = walker
    menus[1] = [Item(text="Rename\tF2", wID=host.MIN_ID)]
    assert host._walk(FakeContextMenu(), 1, 0, depth=0, deadline=_later())[0].text == "Rename"


def test_a_separator_carries_nothing_else(walker):
    menus, _gui = walker
    menus[1] = [Item(fType=host.MFT_SEPARATOR, wID=0)]
    item = host._walk(FakeContextMenu(), 1, 0, depth=0, deadline=_later())[0]
    assert item.kind == MENU_SEPARATOR
    assert item.id == 0


def test_state_is_carried_rather_than_flattened(walker):
    menus, _gui = walker
    menus[1] = [Item(text="Paste", wID=host.MIN_ID, fState=host.MFS_GRAYED),
                Item(text="Open", wID=host.MIN_ID + 1, fState=host.MFS_DEFAULT)]
    items = host._walk(FakeContextMenu(), 1, 0, depth=0, deadline=_later())
    assert items[0].enabled is False
    assert items[1].default is True


def test_a_submenu_is_followed(walker):
    menus, _gui = walker
    menus[1] = [Item(text="TortoiseSVN", hSubMenu=2)]
    menus[2] = [Item(text="Commit...", wID=host.MIN_ID + 9)]
    item = host._walk(FakeContextMenu(), 1, 0, depth=0, deadline=_later())[0]
    assert item.kind == MENU_SUBMENU
    assert [child.text for child in item.items] == ["Commit..."]
    assert item.items[0].id == host.MIN_ID + 9


def test_an_entry_the_extension_paints_itself_is_named_from_its_verb(walker):
    """Owner-drawn entries have no text at all. A named entry that works beats
    a blank one, and blank is what Explorer's own recent-files entries look
    like here.
    """
    menus, _gui = walker
    menus[1] = [Item(text="", wID=host.MIN_ID + 2, fType=host.MFT_OWNERDRAW)]
    assert host._walk(FakeContextMenu(), 1, 0, depth=0, deadline=_later())[0].text == "verb2"


def test_an_entry_outside_the_range_given_to_the_shell_is_dropped(walker):
    """An id outside the range was not put there by this QueryContextMenu, and
    invoking it would be an offset into somebody else's numbering.
    """
    menus, _gui = walker
    menus[1] = [Item(text="Strange", wID=host.MAX_ID + 5)]
    assert host._walk(FakeContextMenu(), 1, 0, depth=0, deadline=_later()) == []


def test_an_unreadable_entry_is_skipped_rather_than_guessed_at(walker, monkeypatch):
    menus, gui = walker
    menus[1] = [Item(text="Fine", wID=host.MIN_ID), Item(text="Bad", wID=host.MIN_ID + 1)]

    def explode(hmenu, position, by_position, buffer):
        if position == 1:
            raise OSError("this entry cannot be read")
        buffer[0] = menus[hmenu][position]

    monkeypatch.setattr(gui, "GetMenuItemInfo", explode)
    items = host._walk(FakeContextMenu(), 1, 0, depth=0, deadline=_later())
    assert [item.text for item in items] == ["Fine"]


# ------------------------------------------------------------- the invocation


def test_an_id_without_its_token_runs_nothing(walker):
    """The check that matters. Ids are reused by the next menu, so one that
    arrives late must not be run against whatever is open now.
    """
    _menus, _gui = walker
    shell_menu = FakeContextMenu()
    state = {"live": host._Live(7, shell_menu, 1, "C:\\Jobs"), "hwnd": 0, "com": True}
    outbox = Outbox()
    host._invoke(request(Op.MENU_INVOKE, token=8, item=host.MIN_ID), outbox, state)
    assert outbox.replies[0].status is Status.GONE
    assert shell_menu.invoked == []


def test_a_command_is_invoked_as_an_offset_from_the_range_start(walker, monkeypatch):
    _menus, _gui = walker
    monkeypatch.setattr(host, "win32con", type("C", (), {"SW_SHOWNORMAL": 1}))
    shell_menu = FakeContextMenu()
    state = {"live": host._Live(7, shell_menu, 1, "C:\\Jobs"), "hwnd": 0, "com": True}
    outbox = Outbox()
    host._invoke(request(Op.MENU_INVOKE, token=7, item=host.MIN_ID + 3), outbox, state)
    assert outbox.replies[0].status is Status.OK
    assert shell_menu.invoked[0][2] == 3
    assert shell_menu.invoked[0][4] == "C:\\Jobs"


def test_the_menu_is_let_go_of_after_it_is_used(walker, monkeypatch):
    """A live IContextMenu keeps somebody else's DLL loaded, and a few of them
    hold the folder open while they exist.
    """
    _menus, gui = walker
    monkeypatch.setattr(host, "win32con", type("C", (), {"SW_SHOWNORMAL": 1}))
    state = {"live": host._Live(7, FakeContextMenu(), 1, "C:\\Jobs"),
             "hwnd": 0, "com": True}
    host._invoke(request(Op.MENU_INVOKE, token=7, item=host.MIN_ID), Outbox(), state)
    assert state["live"] is None
    assert gui.destroyed == [1]


def test_building_a_menu_releases_the_one_before_it(walker):
    _menus, gui = walker
    state = {"live": host._Live(1, FakeContextMenu(), 1, "C:\\Jobs"),
             "hwnd": 0, "com": True}
    outbox = Outbox()
    host._handle(request(Op.MENU_RELEASE), outbox, state)
    assert state["live"] is None
    assert gui.destroyed == [1]
    assert outbox.replies[0].status is Status.OK


def test_without_pywin32_the_menu_is_answered_rather_than_raised(monkeypatch):
    """Every request gets a reply, including the ones that cannot be served.
    A menu request with no answer is a window waiting forever on a right-click.
    """
    monkeypatch.setattr(host, "win32shell", None)
    outbox = Outbox()
    host._handle(request(Op.MENU, names=["a.txt"]), outbox, {"live": None})
    assert outbox.replies[0].status is Status.ERROR
    assert "pywin32" in outbox.replies[0].message


def test_the_host_refuses_work_that_is_not_its_own():
    outbox = Outbox()
    host._handle(request(Op.LIST), outbox, {"live": None})
    assert outbox.replies[0].status is Status.ERROR


# -------------------------------------------------------------------- routing


def test_menu_requests_are_keyed_on_the_host_rather_than_the_volume():
    """Placement is the pool's, as it is for every other request. What this
    checks is that a menu is not sent to the worker listing the folder: a
    shell extension loaded into that process is a hung listing waiting to
    happen.
    """
    from app.io import pool as pool_module

    assert Op.MENU in pool_module.HOST_OPS
    assert Op.MENU_INVOKE in pool_module.HOST_OPS
    assert Op.MENU_RELEASE in pool_module.HOST_OPS
    assert Op.LIST not in pool_module.HOST_OPS
    assert MENU_HOST not in ("C:", "")


# ------------------------------------------------------- which menu is asked for


class FakeFolder:
    """`IShellFolder`, as far as `_shell_menu` is concerned."""

    def __init__(self, shell_menu):
        self.asked = []
        self._menu = shell_menu

    def BindToObject(self, pidl, context, iid):  # noqa: N802 - the API's name
        return self

    def ParseDisplayName(self, hwnd, context, name, attributes=0):  # noqa: N802
        return 0, [name.encode()], 0

    def GetUIObjectOf(self, hwnd, pidls, iid, reserved):  # noqa: N802
        self.asked.append(("items", len(pidls)))
        return self._menu

    def CreateViewObject(self, hwnd, iid):  # noqa: N802
        self.asked.append(("background", 0))
        return self._menu


class FakeShellModule:
    IID_IShellFolder = 1
    IID_IContextMenu = 2
    IID_IContextMenu2 = 3
    IID_IContextMenu3 = 4

    def __init__(self, folder):
        self._folder = folder

    def SHGetDesktopFolder(self):  # noqa: N802 - the API's name
        return self._folder

    def SHILCreateFromPath(self, path, flags):  # noqa: N802
        return [b"one", b"two"], 0


class FakeShellcon:
    CMF_NORMAL = 0x00
    CMF_EXPLORE = 0x04
    CMF_CANRENAME = 0x10
    CMF_ITEMMENU = 0x80
    CMF_EXTENDEDVERBS = 0x100


@pytest.fixture
def builder(monkeypatch, walker):
    """`_build`, with a shell that records which menu it was asked for."""
    menus, gui = walker
    menus[1] = [Item(text="Open", wID=host.MIN_ID)]
    shell_menu = FakeContextMenu()
    folder = FakeFolder(shell_menu)
    monkeypatch.setattr(host, "win32shell", FakeShellModule(folder))
    monkeypatch.setattr(host, "shellcon", FakeShellcon)
    return folder, shell_menu


def test_a_selection_asks_the_folder_about_those_items(builder):
    folder, shell_menu = builder
    outbox = Outbox()
    host._build(request(Op.MENU, names=["a.txt", "b.txt"]), outbox, {"live": None})
    assert folder.asked == [("items", 2)]
    assert outbox.replies[0].status is Status.OK


def test_empty_space_asks_for_the_folder_view_rather_than_the_folder(builder):
    """The difference between the two is New and Paste. Asking for the folder
    as an item gets a menu about the folder, which is not what a right-click
    on the background of a listing means.
    """
    folder, _shell_menu = builder
    host._build(request(Op.MENU, names=[]), Outbox(), {"live": None})
    assert folder.asked == [("background", 0)]


def test_a_selection_is_queried_with_the_flags_explorer_uses(builder):
    """No CMF_CANRENAME means no Rename in the menu, and handlers written
    against Windows 8 and later read CMF_ITEMMENU to know they are being asked
    about an item at all.
    """
    _folder, shell_menu = builder
    host._build(request(Op.MENU, names=["a.txt"]), Outbox(), {"live": None})
    _hmenu, first, _last, flags = shell_menu.queried[0]
    assert first == host.MIN_ID
    assert flags & FakeShellcon.CMF_CANRENAME
    assert flags & FakeShellcon.CMF_ITEMMENU
    assert flags & FakeShellcon.CMF_EXPLORE
    assert not flags & FakeShellcon.CMF_EXTENDEDVERBS


def test_shift_asks_for_the_entries_explorer_hides(builder):
    _folder, shell_menu = builder
    host._build(request(Op.MENU, names=["a.txt"], extended=True), Outbox(), {"live": None})
    assert shell_menu.queried[0][3] & FakeShellcon.CMF_EXTENDEDVERBS


def test_the_background_menu_is_not_asked_about_an_item(builder):
    _folder, shell_menu = builder
    host._build(request(Op.MENU, names=[]), Outbox(), {"live": None})
    flags = shell_menu.queried[0][3]
    assert not flags & FakeShellcon.CMF_ITEMMENU


def test_an_entry_that_is_dropped_says_why(builder):
    """A menu that is short because the shell had nothing and one that is
    short because this code could not read an entry look identical from the
    window. Only one of them is a bug here.
    """
    _folder, _shell_menu = builder
    outbox = Outbox()
    host._build(request(Op.MENU, names=["a.txt"]), outbox, {"live": None})
    assert outbox.replies[0].payload["skipped"] == []


def test_a_submenu_is_told_it_is_opening(walker, monkeypatch):
    """Send to, Open with and New arrive empty and fill themselves when the
    shell is told the popup is opening. Without the handshake the window shows
    an arrow that opens onto nothing.
    """
    menus, _gui = walker
    monkeypatch.setattr(host, "win32con", type("C", (), {"WM_INITMENUPOPUP": 0x117}))
    menus[1] = [Item(text="Send to", hSubMenu=2)]
    menus[2] = []

    told = []

    class Handler:
        def HandleMenuMsg(self, message, submenu, position):  # noqa: N802
            told.append((message, submenu, position))
            menus[2] = [Item(text="Desktop", wID=host.MIN_ID + 1)]

    items = host._walk(FakeContextMenu(), 1, 0, depth=0, deadline=_later(),
                       handler=Handler())
    assert told == [(0x117, 2, 0)]
    assert [child.text for child in items[0].items] == ["Desktop"]


def test_an_extension_without_the_interface_is_the_ordinary_case(walker):
    """`QueryInterface` refusing is not a failure: the extension finished its
    menu during QueryContextMenu and has nothing to be told.
    """
    _menus, _gui = walker
    assert host._menu_handler(FakeContextMenu()) is None


# --------------------------------------------------------------- the core side


class FakeBridge:
    """The pool as `core` sees it: what was submitted, and to what handler."""

    def __init__(self):
        self.sent = []
        self.forgotten = []
        self.host_retries = 0

    def submit(self, op, path, *, timeout, on_reply, args=None):
        self.sent.append({"op": op, "path": path, "args": dict(args or {}),
                          "timeout": timeout, "handler": on_reply})
        return len(self.sent)

    def forget(self, request_id):
        self.forgotten.append(request_id)

    def retry_host(self):
        self.host_retries += 1

    def answer(self, index, status=Status.OK, payload=None, message=""):
        entry = self.sent[index]
        entry["handler"](Reply(index + 1, status, payload=payload, message=message))


@pytest.fixture
def shell_menu():
    from app.core.config import Config
    from app.core.menu import ShellMenu

    bridge = FakeBridge()
    return ShellMenu(bridge, Config({})), bridge


def test_a_menu_is_asked_for_by_folder_and_names(shell_menu):
    menu, bridge = shell_menu
    menu.request("C:\\Jobs", ["a.txt", "b.txt"])
    assert bridge.sent[0]["op"] is Op.MENU
    assert bridge.sent[0]["path"] == "C:\\Jobs"
    assert bridge.sent[0]["args"]["names"] == ["a.txt", "b.txt"]
    # Before every menu, so an extension that wedged the host once does not
    # cost the menu for the rest of the session.
    assert bridge.host_retries == 1


def test_the_token_from_the_reply_is_what_an_invoke_carries(shell_menu):
    menu, bridge = shell_menu
    seen = []
    menu.ready.connect(lambda token, items: seen.append((token, items)))
    menu.request("C:\\Jobs", [])
    bridge.answer(0, payload={"token": 31, "items": []})
    assert seen == [(31, [])]

    menu.invoke(31, host.MIN_ID + 2)
    assert bridge.sent[1]["op"] is Op.MENU_INVOKE
    assert bridge.sent[1]["args"] == {"token": 31, "item": host.MIN_ID + 2}


def test_an_invoke_with_the_wrong_token_is_not_sent(shell_menu):
    menu, bridge = shell_menu
    menu.request("C:\\Jobs", [])
    bridge.answer(0, payload={"token": 31, "items": []})
    menu.invoke(30, host.MIN_ID)
    assert len(bridge.sent) == 1


def test_a_menu_is_not_asked_for_while_a_command_is_still_open(shell_menu):
    """A request queued behind a dialog somebody else opened is a request the
    watchdog eventually reads as a wedged process -- and killing it there
    closes their dialog for them.
    """
    menu, bridge = shell_menu
    menu.request("C:\\Jobs", [])
    bridge.answer(0, payload={"token": 31, "items": []})
    menu.invoke(31, host.MIN_ID)
    said = []
    menu.unavailable.connect(said.append)
    menu.request("C:\\Jobs", [])
    assert len(bridge.sent) == 2
    assert said and "still open" in said[0]


def test_a_finished_command_frees_the_menu_again(shell_menu):
    menu, bridge = shell_menu
    menu.request("C:\\Jobs", [])
    bridge.answer(0, payload={"token": 31, "items": []})
    menu.invoke(31, host.MIN_ID)
    ran = []
    menu.invoked.connect(lambda verb, folder: ran.append((verb, folder)))
    bridge.answer(1, payload={"verb": "commit"})
    assert ran == [("commit", "C:\\Jobs")]
    assert menu.busy is False


def test_a_failure_says_so_in_the_menu_rather_than_in_a_dialog(shell_menu):
    """The menu is already open with the application's own verbs on it. A
    dialog here would take a right-click and turn it into an interruption.
    """
    menu, bridge = shell_menu
    said = []
    menu.unavailable.connect(said.append)
    menu.request("C:\\Jobs", [])
    bridge.answer(0, status=Status.TIMEOUT)
    assert said and "did not answer" in said[0]


def test_a_menu_nobody_used_is_released(shell_menu):
    menu, bridge = shell_menu
    menu.request("C:\\Jobs", [])
    bridge.answer(0, payload={"token": 31, "items": []})
    menu.release()
    assert bridge.sent[1]["op"] is Op.MENU_RELEASE
    assert bridge.sent[1]["args"] == {"token": 31}


def _later() -> float:
    import time

    return time.monotonic() + 60.0

"""Checks on per-file icons: the second request that opens a file.

`core/icons.py` is safe by construction and `core/overlays.py` is bounded by
bookkeeping. This one is both: the bookkeeping is the overlays' -- the rows on
screen, one request per folder, one picture per distinct picture -- plus one
bound the overlays do not have, which is that only a handful of kinds are ever
asked about at all. That extra bound is the whole reason a folder of 50,000
drawings costs nothing, so it is the first thing here.

The failures worth catching:

  * asking about a row that could not have its own icon, which is a file read
    per row and the thing `CLAUDE.md` says not to build;
  * asking about a row more than once, which turns a repaint into a request;
  * *not* asking again after the file has changed, which shows the icon a
    program had before it was rebuilt;
  * asking again after a plain refresh, which is the same read for a picture
    that cannot have changed.
"""

from __future__ import annotations

import pytest

from app.core.config import Config
from app.io.protocol import Entry, Op, Reply, Status


class FakeBridge:
    def __init__(self):
        self.sent = []

    def submit(self, op, path, *, timeout, on_reply, args=None):
        self.sent.append({"op": op, "path": path, "args": dict(args or {}),
                          "timeout": timeout, "handler": on_reply})
        return len(self.sent)

    def answer(self, index, rows, images, status=Status.OK, size=16):
        entry = self.sent[index]
        entry["handler"](Reply(index + 1, status,
                               payload={"size": size, "rows": rows, "images": images}))


def pixels(size=16):
    """A picture that survives the conversion: opaque, and the right length."""
    return bytes([40, 60, 200, 255] * (size * size))


def entry(name, *, mtime=100.0, size=2048, is_dir=False):
    return Entry(name=name, is_dir=is_dir, size=size, mtime=mtime, attributes=0)


@pytest.fixture
def icons():
    from app.core.fileicons import FileIcons

    bridge = FakeBridge()
    return FileIcons(bridge, Config({})), bridge


# ------------------------------------------------------- what is asked about


def test_only_the_kinds_that_carry_an_icon_are_ever_asked_about(icons):
    """The bound that makes this affordable. A folder of documents sends
    nothing, so the ordinary case costs exactly what it did before.
    """
    provider, bridge = icons
    for name in ("plan.dwg", "notes.txt", "report.pdf", "archive.zip"):
        assert provider.icon("C:\\Jobs", entry(name)) is None
    provider.flush()
    assert bridge.sent == []


def test_a_folder_is_never_asked_about(icons):
    """A folder's custom icon is in a desktop.ini the shell reads while it
    enumerates. Reaching for it here would be a read per folder row.
    """
    provider, bridge = icons
    provider.icon("C:\\Jobs", entry("Program Files", is_dir=True))
    provider.icon("C:\\Jobs", entry("setup.exe", is_dir=True))
    provider.flush()
    assert bridge.sent == []


def test_the_kinds_that_do_carry_one_are_asked_about(icons):
    provider, bridge = icons
    for name in ("setup.exe", "Word.lnk", "favicon.ico", "plan.dwg"):
        provider.icon("C:\\Jobs", entry(name))
    provider.flush()
    assert len(bridge.sent) == 1
    assert bridge.sent[0]["op"] is Op.FILE_ICON
    assert bridge.sent[0]["args"]["names"] == ["Word.lnk", "favicon.ico", "setup.exe"]


def test_the_request_goes_to_the_folders_own_worker(icons):
    """Unlike the by-kind icons, which go to the local worker whatever the
    rows came from: this one reads the file, so it belongs to that volume.
    """
    provider, bridge = icons
    provider.icon("\\\\vault\\projects\\bin", entry("tool.exe"))
    provider.flush()
    assert bridge.sent[0]["path"] == "\\\\vault\\projects\\bin"


def test_a_row_is_asked_about_once_however_often_it_is_painted(icons):
    provider, bridge = icons
    for _ in range(20):
        assert provider.icon("C:\\Jobs", entry("setup.exe")) is None
    provider.flush()
    assert len(bridge.sent) == 1
    assert bridge.sent[0]["args"]["names"] == ["setup.exe"]


def test_the_rows_on_screen_go_out_as_one_request_per_folder(icons):
    provider, bridge = icons
    provider.icon("C:\\Jobs", entry("b.exe"))
    provider.icon("C:\\Jobs", entry("a.exe"))
    provider.icon("D:\\Other", entry("c.lnk"))
    provider.flush()
    assert len(bridge.sent) == 2
    jobs = next(item for item in bridge.sent if item["path"] == "C:\\Jobs")
    assert jobs["args"]["names"] == ["a.exe", "b.exe"]


def test_a_request_is_bounded_however_hard_the_view_paints(icons):
    from app.core.fileicons import MAX_PER_REQUEST

    provider, bridge = icons
    for index in range(MAX_PER_REQUEST * 3):
        provider.icon("C:\\Big", entry(f"tool{index}.exe"))
    provider.flush()
    assert len(bridge.sent[0]["args"]["names"]) == MAX_PER_REQUEST


# ------------------------------------------------------------- what comes back


def test_a_file_with_no_icon_of_its_own_is_remembered_as_having_none(icons):
    """The empty answer is the useful one: without it the same row is read
    again on every repaint.
    """
    provider, bridge = icons
    provider.icon("C:\\Jobs", entry("plain.exe"))
    provider.flush()
    bridge.answer(0, rows={}, images={})
    assert provider.icon("C:\\Jobs", entry("plain.exe")) is None
    provider.flush()
    assert len(bridge.sent) == 1


def test_files_with_the_same_picture_share_it(icons):
    """Forty shortcuts to one program are one image, which is what the digest
    the worker keys on is for.
    """
    provider, bridge = icons
    provider.icon("C:\\Menu", entry("Word.lnk"))
    provider.icon("C:\\Menu", entry("Word (2).lnk"))
    provider.flush()
    bridge.answer(0, rows={"Word.lnk": "abc", "Word (2).lnk": "abc"},
                  images={"abc": pixels()})
    first = provider.icon("C:\\Menu", entry("Word.lnk"))
    second = provider.icon("C:\\Menu", entry("Word (2).lnk"))
    assert first is not None
    assert first is second


def test_a_rebuilt_file_is_read_again(icons):
    """The cache keys on what the listing already carries, so a new mtime is
    a new question without anything having to know the file was rebuilt.
    """
    provider, bridge = icons
    provider.icon("C:\\Build", entry("tool.exe", mtime=100.0))
    provider.flush()
    bridge.answer(0, rows={"tool.exe": "abc"}, images={"abc": pixels()})
    assert provider.icon("C:\\Build", entry("tool.exe", mtime=100.0)) is not None

    assert provider.icon("C:\\Build", entry("tool.exe", mtime=200.0)) is None
    provider.flush()
    assert len(bridge.sent) == 2


def test_a_refresh_that_changes_nothing_reads_nothing(icons):
    """The one place this differs from the overlays, and the reason: a badge
    changes while the file does not, an icon cannot.
    """
    provider, bridge = icons
    row = entry("tool.exe")
    provider.icon("C:\\Build", row)
    provider.flush()
    bridge.answer(0, rows={"tool.exe": "abc"}, images={"abc": pixels()})
    for _ in range(5):
        assert provider.icon("C:\\Build", row) is not None
    provider.flush()
    assert len(bridge.sent) == 1


def test_a_failed_request_leaves_the_rows_to_be_read_again(icons):
    """A share that came back should get its icons without a navigation."""
    provider, bridge = icons
    provider.icon("S:\\Tools", entry("tool.exe"))
    provider.flush()
    bridge.sent[0]["handler"](Reply(1, Status.GONE))
    assert provider.icon("S:\\Tools", entry("tool.exe")) is None
    provider.flush()
    assert len(bridge.sent) == 2


def test_a_timeout_keeps_the_pictures_it_did_reach(icons):
    provider, bridge = icons
    provider.icon("S:\\Tools", entry("one.exe"))
    provider.icon("S:\\Tools", entry("two.exe"))
    provider.flush()
    bridge.answer(0, rows={"one.exe": "abc"}, images={"abc": pixels()},
                  status=Status.TIMEOUT)
    assert provider.icon("S:\\Tools", entry("one.exe")) is not None
    assert provider.icon("S:\\Tools", entry("two.exe")) is None


def test_a_timeout_does_not_record_the_rows_it_never_reached(icons):
    """The worker checks its deadline between files, so a partial answer means
    the rest were never looked at. Recording them as having no icon would key
    that mistake to their mtime and keep it until the file changed.
    """
    provider, bridge = icons
    provider.icon("S:\\Tools", entry("one.exe"))
    provider.icon("S:\\Tools", entry("two.exe"))
    provider.flush()
    bridge.answer(0, rows={"one.exe": "abc"}, images={"abc": pixels()},
                  status=Status.TIMEOUT)
    provider.icon("S:\\Tools", entry("two.exe"))
    provider.flush()
    assert len(bridge.sent) == 2
    assert bridge.sent[1]["args"]["names"] == ["two.exe"]


# ------------------------------------------------------------------- the switch


def test_turning_them_off_reads_nothing(icons):
    provider, bridge = icons
    provider._config.set("icons.per_file", False)
    provider.icon("C:\\Jobs", entry("setup.exe"))
    provider.want("C:\\Jobs", entry("other.exe"))
    provider.flush()
    assert bridge.sent == []


def test_they_follow_the_icons_they_replace(icons):
    """With the shell icons off the listing draws no pictures, and one row
    with an icon would be the only picture in the window.
    """
    provider, _bridge = icons
    provider._config.set("icons.shell", False)
    assert provider.enabled is False


# ------------------------------------------------------------------- the model


class FakeIcons:
    def icon(self, entry):
        return "by kind"

    def folder_icon(self):
        return "by kind"


class FakeOverlays:
    def __init__(self, badges=None):
        self.badges = badges or {}

    def icon(self, folder, name):
        return self.badges.get(name)


class FakeFileIcons:
    def __init__(self, own=None):
        self.own = own or {}
        self.asked = []

    def icon(self, folder, entry):
        self.asked.append((folder, entry.name))
        return self.own.get(entry.name)


def _model(file_icons, overlays=None):
    from app.core.listing import ListingModel

    model = ListingModel()
    model.set_icons(FakeIcons())
    model.set_overlays(overlays)
    model.set_file_icons(file_icons)
    model.set_folder("C:\\Jobs")
    model.begin(has_parent=False)
    model.add([entry("setup.exe"), entry("plan.dwg")])
    model.finish()
    return model


def test_a_file_with_its_own_icon_draws_it_and_the_rest_draw_by_kind():
    from PySide6.QtCore import Qt

    model = _model(FakeFileIcons({"setup.exe": "its own"}))
    own = model.index(model.row_of("setup.exe"), 0)
    plain = model.index(model.row_of("plan.dwg"), 0)
    assert model.data(own, Qt.DecorationRole) == "its own"
    assert model.data(plain, Qt.DecorationRole) == "by kind"


def test_a_badge_wins_over_the_files_own_icon():
    """The badged picture the shell hands back was drawn from the real path,
    so it already has this file's own icon underneath it. Asking for both
    would be the same read twice for a picture that is already right.
    """
    from PySide6.QtCore import Qt

    provider = FakeFileIcons({"setup.exe": "its own"})
    model = _model(provider, overlays=FakeOverlays({"setup.exe": "badged"}))
    index = model.index(model.row_of("setup.exe"), 0)
    assert model.data(index, Qt.DecorationRole) == "badged"
    assert provider.asked == []


def test_a_model_without_a_folder_reads_nothing():
    from PySide6.QtCore import Qt

    from app.core.listing import ListingModel

    provider = FakeFileIcons({"setup.exe": "its own"})
    model = ListingModel()
    model.set_icons(FakeIcons())
    model.set_file_icons(provider)
    model.begin(has_parent=False)
    model.add([entry("setup.exe")])
    model.finish()
    assert model.data(model.index(0, 0), Qt.DecorationRole) == "by kind"
    assert provider.asked == []


# ------------------------------------------------------------------ the worker


def test_the_worker_refuses_a_name_that_is_a_path():
    """A name here comes from a listing and is joined onto the folder, so
    anything path-shaped is refused rather than followed.
    """
    from app.io import worker
    from app.io.protocol import Request

    replies = []

    class Outbox:
        def put(self, reply):
            replies.append(reply)

    request = Request(id=1, op=Op.FILE_ICON, path="C:\\Jobs", timeout=5.0,
                      args={"names": ["..\\..\\Windows\\notepad.exe",
                                      "C:\\Windows\\notepad.exe"], "size": 16})
    worker._file_icons(request, Outbox())
    assert replies
    payload = replies[0].payload or {}
    # Either pywin32 is missing, which is an honest error, or the names were
    # refused. What must not happen is a picture coming back for either.
    assert not (payload.get("rows") or {})


def test_the_kinds_are_one_definition():
    """The worker checks the kind as well as the caller, so the two have to
    agree by construction rather than by both being edited.
    """
    from app.io.protocol import SELF_ICON_KINDS, carries_own_icon, own_icon_kind

    assert own_icon_kind("setup.exe") is True
    assert own_icon_kind("plan.dwg") is False
    assert own_icon_kind(".exe") is False        # a name, not an extension
    assert own_icon_kind("SETUP.EXE") is True    # the association db does not care
    assert carries_own_icon(entry("setup.exe")) is True
    assert carries_own_icon(entry("setup.exe", is_dir=True)) is False
    assert ".dll" not in SELF_ICON_KINDS

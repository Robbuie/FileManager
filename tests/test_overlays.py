"""Checks on overlays: the one icon request that carries a path.

Everything in `core/icons.py` is safe by construction -- the shell is asked
about an extension and never about a file. An overlay cannot be, so what
protects the application is bookkeeping, and bookkeeping is exactly the sort
of thing that quietly stops working. The three failures worth catching:

  * asking about a row more than once, which turns a repaint into a request;
  * asking about a file per badge rather than sharing the picture, which is
    400 images for a working copy of 400 files;
  * keeping the answer across a refresh, which shows yesterday's status until
    the application is restarted.
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
                          "handler": on_reply})
        return len(self.sent)

    def answer(self, index, rows, images, status=Status.OK, size=16):
        entry = self.sent[index]
        entry["handler"](Reply(index + 1, status,
                               payload={"size": size, "rows": rows, "images": images}))


def pixels(size=16):
    """A picture that survives the conversion: opaque, and the right length."""
    return bytes([40, 60, 200, 255] * (size * size))


@pytest.fixture
def overlays():
    from app.core.overlays import Overlays

    bridge = FakeBridge()
    return Overlays(bridge, Config({})), bridge


def test_a_row_is_asked_about_once_however_often_it_is_painted(overlays):
    provider, bridge = overlays
    for _ in range(20):
        assert provider.icon("C:\\Jobs", "plan.dwg") is None
    provider.flush()
    assert len(bridge.sent) == 1
    assert bridge.sent[0]["args"]["names"] == ["plan.dwg"]


def test_the_rows_on_screen_go_out_as_one_request_per_folder(overlays):
    provider, bridge = overlays
    for name in ("b.txt", "a.txt"):
        provider.want("C:\\Jobs", name)
    provider.want("D:\\Other", "c.txt")
    provider.flush()
    assert len(bridge.sent) == 2
    jobs = next(item for item in bridge.sent if item["path"] == "C:\\Jobs")
    assert jobs["op"] is Op.OVERLAY
    assert jobs["args"]["names"] == ["a.txt", "b.txt"]


def test_a_row_with_no_badge_is_remembered_as_having_none(overlays):
    """The empty answer is the useful one: without it the same row is asked
    about again on every repaint, which is the request-per-paint failure.
    """
    provider, bridge = overlays
    provider.icon("C:\\Jobs", "plain.txt")
    provider.flush()
    bridge.answer(0, rows={}, images={})
    assert provider.icon("C:\\Jobs", "plain.txt") is None
    provider.flush()
    assert len(bridge.sent) == 1


def test_files_sharing_a_badge_share_its_picture(overlays):
    provider, bridge = overlays
    for name in ("one.cs", "two.cs"):
        provider.want("C:\\Work", name)
    provider.flush()
    bridge.answer(0, rows={"one.cs": "12:2", "two.cs": "12:2"},
                  images={"12:2": pixels()})
    first = provider.icon("C:\\Work", "one.cs")
    second = provider.icon("C:\\Work", "two.cs")
    assert first is not None
    assert first is second


def test_a_folder_listed_again_is_asked_about_again(overlays):
    provider, bridge = overlays
    provider.want("C:\\Work", "one.cs")
    provider.flush()
    bridge.answer(0, rows={"one.cs": "12:2"}, images={"12:2": pixels()})
    provider.forget("C:\\Work")
    assert provider.icon("C:\\Work", "one.cs") is None
    provider.flush()
    assert len(bridge.sent) == 2


def test_a_failed_request_leaves_the_rows_to_be_asked_about_again(overlays):
    """A share that came back should get its badges back without a navigation."""
    provider, bridge = overlays
    provider.want("S:\\Jobs", "one.cs")
    provider.flush()
    bridge.sent[0]["handler"](Reply(1, Status.GONE))
    assert provider.icon("S:\\Jobs", "one.cs") is None
    provider.flush()
    assert len(bridge.sent) == 2


def test_turning_them_off_asks_for_nothing(overlays):
    provider, bridge = overlays
    provider._config.set("icons.overlays", False)
    provider.icon("C:\\Jobs", "one.cs")
    provider.want("C:\\Jobs", "two.cs")
    provider.flush()
    assert bridge.sent == []


def test_overlays_follow_the_icons_they_sit_on(overlays):
    """The shell hands back the badge already composited onto the file's icon,
    so an overlay with the icons turned off would be the only picture in the
    listing.
    """
    provider, _bridge = overlays
    provider._config.set("icons.shell", False)
    assert provider.enabled is False


def test_a_request_is_bounded_however_hard_the_view_paints(overlays):
    from app.core.overlays import MAX_PER_REQUEST

    provider, bridge = overlays
    for index in range(MAX_PER_REQUEST * 3):
        provider.want("C:\\Big", f"file{index}.txt")
    provider.flush()
    assert len(bridge.sent[0]["args"]["names"]) == MAX_PER_REQUEST


# ------------------------------------------------------------------ the model


class FakeIcons:
    def __init__(self, icon):
        self._icon = icon

    def icon(self, entry):
        return self._icon

    def folder_icon(self):
        return self._icon


class FakeOverlays:
    def __init__(self, badges):
        self.badges = badges
        self.asked = []

    def icon(self, folder, name):
        self.asked.append((folder, name))
        return self.badges.get(name)


def test_a_badged_row_draws_the_badged_picture():
    """The composite replaces the icon rather than being drawn over it: where
    a badge sits on an icon is the shell's decision, made when it drew them
    together.
    """
    from PySide6.QtCore import Qt

    from app.core.listing import ListingModel

    model = ListingModel()
    model.set_icons(FakeIcons("plain"))
    model.set_overlays(FakeOverlays({"tracked.cs": "badged"}))
    model.set_folder("C:\\Work")
    model.begin(has_parent=False)
    model.add([_entry("tracked.cs"), _entry("loose.cs")])
    model.finish()

    # By name rather than by row: the model sorts what it was given, and a
    # test that assumes the order it was added in is testing the sort.
    badged = model.index(model.row_of("tracked.cs"), 0)
    loose = model.index(model.row_of("loose.cs"), 0)
    assert model.data(badged, Qt.DecorationRole) == "badged"
    assert model.data(loose, Qt.DecorationRole) == "plain"


def test_a_model_without_a_folder_asks_about_nothing():
    """Overlays are per path, and a model that has not been told where its
    rows are has no path to ask about.
    """
    from PySide6.QtCore import Qt

    from app.core.listing import ListingModel

    provider = FakeOverlays({})
    model = ListingModel()
    model.set_icons(FakeIcons("plain"))
    model.set_overlays(provider)
    model.begin(has_parent=False)
    model.add([_entry("loose.cs")])
    model.finish()
    assert model.data(model.index(0, 0), Qt.DecorationRole) == "plain"
    assert provider.asked == []


def _entry(name: str) -> Entry:
    return Entry(name=name, is_dir=False, size=1, mtime=0.0, attributes=0)

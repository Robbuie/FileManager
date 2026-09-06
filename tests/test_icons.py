"""Checks on the icon path: what a kind is, how many are asked for, and alpha.

The one part of shell icons that cannot be checked anywhere but Windows is the
call to the shell itself. Everything around it can be, and is here, because
the failures worth catching are all on this side: a cache keyed on the row
rather than the kind asks 50,000 times for the same picture, and an icon whose
alpha came out zero is bytes that arrive and draw nothing.
"""

from __future__ import annotations

import os

import pytest

from app.io.protocol import ICON_FILE, ICON_FOLDER, Entry, Op, Reply, Status, icon_key


def entry(name, is_dir=False):
    return Entry(name=name, is_dir=is_dir, size=0, mtime=0.0, attributes=0)


# --------------------------------------------------------------------- kinds


def test_a_folder_is_one_kind_however_it_is_named():
    assert icon_key(entry("Reports", True)) == ICON_FOLDER
    assert icon_key(entry("backup.old", True)) == ICON_FOLDER


def test_the_kind_is_the_extension_and_ignores_its_case():
    assert icon_key(entry("plan.pdf")) == ".pdf"
    assert icon_key(entry("PLAN.PDF")) == ".pdf"
    assert icon_key(entry("report.final.dwg")) == ".dwg"


def test_a_name_without_an_extension_is_the_generic_file():
    assert icon_key(entry("Makefile")) == ICON_FILE
    assert icon_key(entry(".gitignore")) == ICON_FILE
    assert icon_key(entry("notes.")) == ICON_FILE


# ------------------------------------------------------------------- the worker


def test_a_key_shaped_like_a_path_is_refused():
    """The guarantee of an ICON request is that it touches no volume."""
    from app.io import worker

    for key in ("C:", "share/file.txt", chr(92) + "server", "/etc/passwd"):
        assert worker._shell_icon(key, 16) is None


def test_a_key_that_is_not_a_kind_is_refused():
    """An extension arrives with its dot. Anything else is a caller's bug."""
    from app.io import worker

    for key in ("", "txt", "pdf", "FOLDER"):
        assert worker._shell_icon(key, 16) is None


def test_an_empty_request_is_answered_rather_than_ignored():
    from app.io import worker

    replies = []
    worker._icon(_request(keys=[]), _Outbox(replies))
    assert len(replies) == 1
    assert replies[0].status is Status.OK
    assert replies[0].payload == {"size": 16, "icons": {}}


def test_alpha_is_recovered_from_the_two_passes(monkeypatch):
    """Opaque where the two passes agree, absent where they differ by the range.

    The middle case is the one worth stating: a pixel half covered by the icon
    comes back at half alpha, which is what keeps an edge from being a
    staircase. The colour is left as it came off the black pass, because that
    is already the colour multiplied by that alpha.
    """
    from app.io import worker

    # Four pixels, as BGRA: opaque red, untouched, half-covered red, opaque
    # black. The fourth byte of each is whatever GDI left behind and is
    # deliberately not zero, so a function that trusted it would fail here.
    on_black = bytes([0, 0, 255, 7] + [0, 0, 0, 7] + [0, 0, 128, 7] + [0, 0, 0, 7])
    on_white = bytes([0, 0, 255, 7] + [255, 255, 255, 7]
                     + [128, 128, 255, 7] + [0, 0, 0, 7])
    passes = {0x000000: on_black, 0xFFFFFF: on_white}
    monkeypatch.setattr(worker, "_draw_icon", lambda hicon, size, fill: passes[fill])

    pixels = worker._icon_pixels(1, 2)
    assert pixels is not None
    assert list(pixels[3::4]) == [255, 0, 128, 255]
    # Colour untouched: the black pass is the premultiplied form already.
    assert list(pixels[2::4]) == [255, 0, 128, 0]


def test_a_surface_that_is_not_32_bit_is_refused_rather_than_guessed(monkeypatch):
    from app.io import worker

    monkeypatch.setattr(worker, "_draw_icon", lambda hicon, size, fill: b"\x00" * 12)
    assert worker._icon_pixels(1, 2) is None


class _Outbox:
    def __init__(self, into):
        self._into = into

    def put(self, reply):
        self._into.append(reply)


def _request(**args):
    from app.io.protocol import Request

    return Request(id=1, op=Op.ICON, path="", timeout=5.0, args=args)


# -------------------------------------------------------------------- the cache

pytest.importorskip("PySide6")

from app.core.config import Config  # noqa: E402
from app.core.icons import Icons, ROW_ICON  # noqa: E402

# The application these need is the session-wide one in `conftest.py`. Making
# one here would be the thing that file exists to prevent.


class FakeBridge:
    """Records what was submitted and hands back replies on demand."""

    def __init__(self):
        self.sent = []

    def submit(self, op, path, *, timeout, on_reply, args=None):
        self.sent.append({"op": op, "path": path, "args": dict(args or {}),
                          "reply": on_reply})
        return len(self.sent)

    def answer(self, index, icons, *, size=ROW_ICON, status=Status.OK):
        self.sent[index]["reply"](
            Reply(index + 1, status, payload={"size": size, "icons": icons})
        )


def pixels(size=ROW_ICON, alpha=255):
    return bytes([40, 60, 80, alpha]) * (size * size)


@pytest.fixture
def cache():
    bridge = FakeBridge()
    return Icons(bridge, Config({}, path=os.devnull)), bridge


def test_the_generics_are_asked_for_first(cache):
    icons, bridge = cache
    icons.start()
    assert len(bridge.sent) == 1
    assert bridge.sent[0]["op"] is Op.ICON
    assert sorted(bridge.sent[0]["args"]["keys"]) == [ICON_FILE, ICON_FOLDER]


def test_a_thousand_rows_of_one_kind_are_one_request(cache):
    icons, bridge = cache
    icons.start()
    bridge.answer(0, {ICON_FILE: pixels(), ICON_FOLDER: pixels()})
    for index in range(1000):
        icons.icon(entry(f"drawing-{index}.dwg"))
    icons.flush()
    assert len(bridge.sent) == 2
    assert bridge.sent[1]["args"]["keys"] == [".dwg"]


def test_a_row_draws_the_generic_until_its_own_icon_lands(cache):
    icons, bridge = cache
    icons.start()
    bridge.answer(0, {ICON_FILE: pixels(), ICON_FOLDER: pixels()})
    generic = icons.icon(entry("plan.pdf"))
    assert generic is not None and not generic.isNull()
    icons.flush()
    bridge.answer(1, {".pdf": pixels()})
    assert icons.icon(entry("plan.pdf")) is not None


def test_a_kind_the_shell_had_nothing_for_is_not_asked_about_again(cache):
    icons, bridge = cache
    icons.icon(entry("thing.zzz"))
    icons.flush()
    bridge.answer(0, {})          # the shell had no icon for it
    icons.icon(entry("other.zzz"))
    icons.flush()
    assert len(bridge.sent) == 1


def test_a_failed_reply_does_not_leave_the_cache_holding_nothing_useful(cache):
    icons, bridge = cache
    icons.start()
    bridge.answer(0, {}, status=Status.ERROR)
    assert icons.icon(entry("plan.pdf")) is None   # nothing to draw, and no crash


def test_icons_can_be_turned_off_entirely():
    bridge = FakeBridge()
    config = Config({}, path=os.devnull)
    config.set("icons.shell", False)
    icons = Icons(bridge, config)
    icons.start()
    assert bridge.sent == []
    assert icons.icon(entry("plan.pdf")) is None


def test_turning_them_back_on_fetches_the_generics():
    """Nothing was asked for while it was off, so there is nothing for a row
    to fall back to until this does."""
    bridge = FakeBridge()
    config = Config({}, path=os.devnull)
    config.set("icons.shell", False)
    icons = Icons(bridge, config)
    icons.start()
    assert bridge.sent == []
    config.set("icons.shell", True)
    icons.reload()
    assert len(bridge.sent) == 1
    assert sorted(bridge.sent[0]["args"]["keys"]) == [ICON_FILE, ICON_FOLDER]


def test_the_pixels_become_something_the_view_can_draw(cache):
    icons, bridge = cache
    icons.start()
    bridge.answer(0, {ICON_FILE: pixels(32), ICON_FOLDER: pixels(32)}, size=32)
    icon = icons.icon(entry("anything"))
    assert icon is not None
    # A 32-pixel icon on a scaled display still occupies one row's worth.
    pixmap = icon.pixmap(ROW_ICON, ROW_ICON)
    assert not pixmap.isNull()


def test_a_reply_of_the_wrong_length_is_ignored_rather_than_drawn(cache):
    """Bytes that are not a picture are dropped, and the rest of the batch is
    not dropped with them."""
    icons, bridge = cache
    icons.start()
    bridge.answer(0, {ICON_FILE: b"not an icon", ICON_FOLDER: pixels()})
    assert icons.icon(entry("plan.pdf")) is None
    assert icons.icon(entry("Reports", True)) is not None


# --------------------------------------------------------------- the model

from PySide6.QtCore import Qt  # noqa: E402

from app.core.listing import Column, ListingModel  # noqa: E402


class OneIcon:
    """A provider that always has an answer, so the model is what is tested."""

    def __init__(self):
        self.icon_calls = []
        self._icon = _some_icon()

    def icon(self, entry):
        self.icon_calls.append(entry.name)
        return self._icon

    def folder_icon(self):
        return self._icon


def _some_icon():
    from PySide6.QtGui import QIcon, QImage, QPixmap

    image = QImage(pixels(), ROW_ICON, ROW_ICON, ROW_ICON * 4,
                   QImage.Format_ARGB32_Premultiplied).copy()
    return QIcon(QPixmap.fromImage(image))


def _filled():
    model = ListingModel()
    model.begin(has_parent=True)
    model.add([entry("Reports", True), entry("plan.pdf")])
    model.finish()
    return model


def test_a_model_without_a_provider_draws_no_icons():
    model = _filled()
    assert model.data(model.index(1, Column.NAME), Qt.DecorationRole) is None


def test_the_icon_goes_on_the_name_and_nowhere_else():
    model = _filled()
    provider = OneIcon()
    model.set_icons(provider)
    assert model.data(model.index(1, Column.NAME), Qt.DecorationRole) is not None
    for column in (Column.EXT, Column.SIZE, Column.MODIFIED):
        assert model.data(model.index(1, column), Qt.DecorationRole) is None


def test_the_parent_row_draws_a_folder_without_being_an_entry():
    model = _filled()
    model.set_icons(OneIcon())
    assert model.is_parent_row(0)
    assert model.data(model.index(0, Column.NAME), Qt.DecorationRole) is not None

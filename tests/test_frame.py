"""The 0.26 window: its own title bar, the glass or solid backdrop, the glow.

Most of what matters about a custom frame only shows on Windows -- snapping,
the shadow, the snap layouts flyout -- and none of that can be checked here.
What can be checked is everything those depend on: which backdrop a machine
gets, which part of the window a point is, that hiding the menu bar did not
take the menus' keys with it, and that the light follows the active pane.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.core.backdrop import MICA_BUILD, Machine, choose  # noqa: E402
from app.core.capacity import Capacity  # noqa: E402
from app.core.config import Config  # noqa: E402
from app.core.favorites import Favorites  # noqa: E402
from app.core.pane import Pane  # noqa: E402
from app.core.transfers import TransferQueue  # noqa: E402
from app.theme import qss  # noqa: E402
from app.ui import winframe  # noqa: E402
from app.ui.window import MainWindow  # noqa: E402

from tests.test_window import LEFT, RIGHT, FakeBridge, FakeVolumes  # noqa: E402

WIN11 = Machine(windows=True, build=MICA_BUILD, remote=False, transparency=True)


# ------------------------------------------------------------------ backdrop

def test_auto_is_glass_on_a_local_windows_11_session():
    assert choose("auto", WIN11)[0] == "glass"


def test_a_remote_session_is_solid_because_hyper_v_enhanced_session_is_one():
    kind, why = choose("auto", Machine(windows=True, build=MICA_BUILD, remote=True))
    assert kind == "solid"
    assert "remote" in why


def test_transparency_switched_off_is_solid():
    assert choose("auto", Machine(windows=True, build=MICA_BUILD,
                                  transparency=False))[0] == "solid"


def test_glass_can_be_insisted_on_in_a_remote_session():
    assert choose("glass", Machine(windows=True, build=MICA_BUILD, remote=True))[0] == "glass"


def test_nothing_older_than_22621_or_off_windows_gets_glass_even_if_asked():
    assert choose("glass", Machine(windows=True, build=22000))[0] == "solid"
    assert choose("glass", Machine())[0] == "solid"


def test_an_unknown_preference_is_auto():
    assert choose("frosted", WIN11)[0] == "glass"


def test_glass_leaves_the_backdrop_unpainted_and_solid_paints_it():
    assert qss.build(backdrop="glass")["backdrop"] == "transparent"
    solid = qss.build(backdrop="solid")
    assert solid["backdrop"] == solid["bg_0"]


# -------------------------------------------------------------------- region

def _region(x, y, **kw):
    kw.setdefault("maximized", False)
    kw.setdefault("over_max", False)
    kw.setdefault("over_caption", False)
    return winframe.region(x, y, 1000, 700, **kw)


def test_corners_resize_before_anything_else_claims_them():
    assert _region(1, 1, over_caption=True) == winframe.HTTOPLEFT
    assert _region(998, 1, over_max=True) == winframe.HTTOPRIGHT
    assert _region(2, 698) == winframe.HTBOTTOMLEFT
    assert _region(999, 699) == winframe.HTBOTTOMRIGHT


def test_edges_resize():
    assert _region(0, 300) == winframe.HTLEFT
    assert _region(999, 300) == winframe.HTRIGHT
    assert _region(500, 0) == winframe.HTTOP
    assert _region(500, 699) == winframe.HTBOTTOM


def test_a_maximised_window_has_no_edges_to_grab():
    assert _region(0, 300, maximized=True) == winframe.HTCLIENT
    assert _region(500, 0, maximized=True, over_caption=True) == winframe.HTCAPTION


def test_the_maximise_button_is_htmaxbutton_so_windows_11_offers_snap_layouts():
    assert _region(900, 20, over_max=True, over_caption=False) == winframe.HTMAXBUTTON


def test_the_rest_of_the_window_is_client():
    assert _region(500, 300) == winframe.HTCLIENT


# -------------------------------------------------------------------- window

def _window(tmp_path, frame=None):
    config = Config({"left.path": LEFT, "right.path": RIGHT},
                    str(tmp_path / "config.json"))
    bridge = FakeBridge()
    made = MainWindow(config, Pane(bridge, config, "left"),
                      Pane(bridge, config, "right"), FakeVolumes(),
                      TransferQueue(), None, Favorites(config),
                      Capacity(bridge, config), frame=frame)
    made.resize(1200, 600)
    made.show()
    QApplication.processEvents()
    return made


def test_the_custom_frame_hides_the_menu_bar_and_keeps_its_keys(tmp_path):
    window = _window(tmp_path)
    try:
        assert not window.menuBar().isVisible()
        keys = {action.shortcut().toString() for action in window.actions()}
        # One from the View menu and one from Tabs: both would be dead with the
        # bar hidden if they had not been moved onto the window.
        assert "Ctrl+Shift+B" in keys
        assert "Ctrl+T" in keys
    finally:
        window.hide()


def test_the_system_frame_is_the_old_window(tmp_path):
    window = _window(tmp_path, frame="system")
    try:
        assert window.menuBar().isVisible()
        assert window._titlebar is None  # noqa: SLF001
    finally:
        window.hide()


def test_the_title_bar_is_caption_except_over_its_buttons(tmp_path):
    window = _window(tmp_path)
    try:
        bar = window._titlebar  # noqa: SLF001
        empty = bar.mapTo(window, QPoint(bar.width() // 2 - 200, bar.height() // 2))
        assert window.hit_parts(empty) == (False, True)
        middle = bar.max_button.geometry().center()
        over, caption = window.hit_parts(bar.mapTo(window, middle))
        assert over and not caption
        assert window.hit_parts(bar.mapTo(window, bar.go.geometry().center())) == (False, False)
    finally:
        window.hide()


def test_the_glow_follows_the_active_pane(tmp_path):
    window = _window(tmp_path)
    try:
        deck = window._splitter  # noqa: SLF001
        assert deck.target is window._widgets[0]  # noqa: SLF001
        window._set_active(1)  # noqa: SLF001
        assert deck.target is window._widgets[1]  # noqa: SLF001
    finally:
        window.hide()


def test_the_hints_never_overlap(tmp_path):
    window = _window(tmp_path)
    try:
        items = window._hints.layout_items()  # noqa: SLF001
        for (rect, text_x, _k, _l), (after, _t, _k2, _l2) in zip(items, items[1:]):
            assert rect.right() < text_x < after.left()
    finally:
        window.hide()

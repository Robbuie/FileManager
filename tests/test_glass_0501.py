"""0.50.1: glass that made a window feel locked up.

Reported the morning after 0.50: with a glass look on, dragging the divider
between the panes did nothing and the window felt stuck. A see-through window
is drawn by handing Windows the whole window on every repaint, and three
things in 0.50 multiplied the repaints -- a splitter that re-laid both panes
out on every pixel of a drag, a listing that was see-through under Frosted
(so it could not scroll by moving pixels), and nothing to notice when it got
that bad. These pin the three answers.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from app.theme import qss  # noqa: E402


@pytest.fixture(scope="module")
def glass_window(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("glass")
    from app.core.capacity import Capacity
    from app.core.config import Config
    from app.core.favorites import Favorites
    from app.core.pane import Pane
    from app.core.transfers import TransferQueue
    from app.ui.window import MainWindow
    from tests.test_window import FakeBridge, FakeVolumes

    # Windows' own title bar, so the window is not made see-through: what is
    # being checked is the splitter, and a translucent window left behind
    # slowed every theme change for the rest of a full run.
    config = Config({"left.path": "C:\\Jobs", "right.path": "D:\\Archive",
                     "window.backdrop": "glass", "window.frame": "system"},
                    str(tmp_path / "config.json"))
    bridge = FakeBridge()
    made = MainWindow(config, Pane(bridge, config, "left"),
                      Pane(bridge, config, "right"), FakeVolumes(),
                      TransferQueue(), None, Favorites(config),
                      Capacity(bridge, config), backdrop="glass")
    yield made
    # Hidden rather than deleted, the way every other window test leaves its
    # window: deleting one mid-run left the application sheet pointing at
    # freed widgets and the next theme change crashed.
    made.hide()


def test_the_divider_is_not_live_on_glass(glass_window) -> None:
    assert glass_window._splitter.opaqueResize() is False


def test_the_listing_stays_solid_under_frosted() -> None:
    from app.theme import sheet

    tokens = qss.build("dark", backdrop="frosted")
    text = qss.render(sheet.TEMPLATE, tokens)
    table = text[text.index("QTableView {"):text.index("}", text.index("QTableView {"))]
    assert tokens["bg_2"] in table and "rgba" not in table
    assert tokens["pane_bg"].startswith("rgba(")


def test_a_slow_window_gives_up_glass_for_next_time(glass_window, monkeypatch) -> None:
    import time

    from app.ui import window as module

    clock = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    glass_window._config.set("window.backdrop", "glass")
    glass_window._glass_slow = []
    glass_window._glass_timer.start()
    glass_window._glass_beat = clock[0]
    for _ in range(module.GLASS_STRIKES):
        clock[0] += module.GLASS_BEAT_MS / 1000.0 + 1.0     # a second late
        glass_window._watch_glass()
    assert glass_window._config.get("window.backdrop") == "solid"
    assert not glass_window._glass_timer.isActive()


def test_an_ordinary_beat_changes_nothing(glass_window, monkeypatch) -> None:
    import time

    from app.ui import window as module

    clock = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    glass_window._config.set("window.backdrop", "glass")
    glass_window._glass_slow = []
    glass_window._glass_beat = clock[0]
    for _ in range(20):
        clock[0] += module.GLASS_BEAT_MS / 1000.0 + 0.05
        glass_window._watch_glass()
    assert glass_window._config.get("window.backdrop") == "glass"

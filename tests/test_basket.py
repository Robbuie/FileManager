"""Checks on the basket (0.38): what goes in, and that emptying it is a
prompt-confirmed transfer rather than something a click does by itself."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from app.core.basket import Basket  # noqa: E402


def test_each_path_once_whatever_its_case():
    basket = Basket()
    assert basket.add(["C:\\Jobs\\a.txt", "c:\\jobs\\A.TXT", "D:\\b.pdf"]) == 2
    assert basket.names() == ["a.txt", "b.pdf"]
    assert basket.folders() == 2


def test_remove_and_clear():
    basket = Basket()
    basket.add(["C:\\a.txt", "C:\\b.txt"])
    basket.remove("c:\\A.txt")
    assert basket.paths == ["C:\\b.txt"]
    basket.clear()
    assert len(basket) == 0


def test_the_tray_shows_only_with_something_in_it():
    from PySide6.QtWidgets import QWidget

    from app.ui.basket import BasketTray

    host = QWidget()
    host.resize(800, 600)
    host.show()
    basket = Basket()
    tray = BasketTray(basket, host)
    assert not tray.isVisible()
    basket.add(["C:\\a.txt"])
    assert tray.isVisible()
    tray.set_enabled(False)
    assert not tray.isVisible()
    host.deleteLater()


def test_emptying_goes_through_the_prompt(monkeypatch, tmp_path):
    from tests.test_options import window as _window  # noqa: F401
    from app.ui import window as window_module

    class Refused:
        def __init__(self, *a, **k):
            pass

        def exec(self):
            return 0

    monkeypatch.setattr(window_module, "TransferPrompt", Refused)
    from tests.test_window import FakeBridge, FakeVolumes
    from app.core.capacity import Capacity
    from app.core.config import Config
    from app.core.favorites import Favorites
    from app.core.pane import Pane
    from app.core.transfers import TransferQueue

    config = Config({"left.path": "C:\\Jobs"}, str(tmp_path / "c.json"))
    bridge = FakeBridge()
    queue = TransferQueue()
    sent = []
    monkeypatch.setattr(queue, "copy", lambda *a, **k: sent.append(a))
    made = window_module.MainWindow(config, Pane(bridge, config, "left"),
                                    Pane(bridge, config, "right"), FakeVolumes(),
                                    queue, None, Favorites(config),
                                    Capacity(bridge, config))
    made._fill_basket(["C:\\Jobs\\a.txt"])
    made._empty_basket(move=False)
    assert sent == []
    assert len(made._basket) == 1

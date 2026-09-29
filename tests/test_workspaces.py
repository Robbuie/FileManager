"""Checks on workspaces (0.38)."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from app.core import workspaces  # noqa: E402
from app.core.config import Config  # noqa: E402


class Side:
    def __init__(self, tabs, index):
        self._tabs, self.index = tabs, index

    def session(self):
        return list(self._tabs)


def test_save_replaces_the_same_name_and_keeps_order():
    store = workspaces.Workspaces(Config({}))
    left = Side([{"path": "C:\\Jobs", "locked": False}], 0)
    right = Side([{"path": "D:\\A", "locked": True}, {"path": "D:\\B", "locked": False}], 1)
    store.save("Office", left, right)
    store.save("Riverside", left, right)
    store.save("office", left, Side([{"path": "E:\\", "locked": False}], 0))
    assert store.names() == ["Riverside", "office"]
    tabs, index = workspaces.side(store.named("OFFICE"), "right")
    assert tabs == [{"path": "E:\\", "locked": False}] and index == 0


def test_a_malformed_side_costs_only_its_bad_tabs():
    entry = {"name": "x", "left": {"tabs": ["C:\\a", 7, {"path": ""},
                                            {"path": "C:\\b", "locked": 1}],
                                   "tab": "9"}}
    tabs, index = workspaces.side(entry, "left")
    assert [t["path"] for t in tabs] == ["C:\\a", "C:\\b"]
    assert index == 1
    assert workspaces.side(entry, "right") == ([], 0)


def test_opening_one_replaces_both_panes_tabs(tmp_path):
    from tests.test_options import window  # noqa: F401 - fixture shape
    from PySide6.QtWidgets import QApplication

    from tests.test_window import FakeBridge, FakeVolumes
    from app.core.capacity import Capacity
    from app.core.favorites import Favorites
    from app.core.pane import Pane
    from app.core.transfers import TransferQueue
    from app.ui.window import MainWindow

    config = Config({"left.path": "C:\\Jobs", "right.path": "D:\\Archive"},
                    str(tmp_path / "c.json"))
    bridge = FakeBridge()
    made = MainWindow(config, Pane(bridge, config, "left"), Pane(bridge, config, "right"),
                      FakeVolumes(), TransferQueue(), None, Favorites(config),
                      Capacity(bridge, config))
    made._panes[0].open_tab("C:\\Second")
    made._workspaces.save("Two", *made._panes)
    made._panes[0].close_tab(1)
    assert len(made._panes[0].tabs) == 1
    made._open_workspace("Two")
    assert [t.path for t in made._panes[0].tabs] == ["C:\\Jobs", "C:\\Second"]
    assert made._panes[0].index == 1
    entries = [a.text() for a in made._workspaces_menu.actions()]
    assert any(text.startswith("Two") for text in entries)
    QApplication.processEvents()

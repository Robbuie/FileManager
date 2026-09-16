"""0.24, the look: what can be checked without looking.

The rest -- tab shapes, the strip, the pane edges -- is in the renders from
`tools/preview.py`, which is where a look is checked.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QObject, Qt, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication, QTabBar  # noqa: E402

from app.core.config import Config  # noqa: E402
from app.core.listing import Column  # noqa: E402
from tests.test_columns import FakeBridge  # noqa: E402


def make(tmp_path, values=None):
    from app.core.pane import Pane
    from app.theme import sheet
    from app.ui.pane import PaneWidget

    class Volumes(QObject):
        changed = Signal()
        drives = [{"letter": "C:", "type": "fixed"}, {"letter": "S:", "type": "remote",
                                                       "unc": "\\\\dc01\\projects"}]

        def letter_for(self, path):
            return "C:"

        def refresh(self, *, rescan=False):
            pass

    config = Config({"left.path": "C:\\Jobs", **(values or {})},
                    str(tmp_path / "config.json"))
    model = Pane(FakeBridge(), config, "left")
    widget = PaneWidget(model, Volumes(), sheet.metrics("normal"), None)
    widget.apply_tokens(sheet.tokens())
    widget.resize(900, 500)
    widget.show()
    QApplication.processEvents()
    return widget


# ------------------------------------------------------------------ defaults

def test_ext_starts_hidden_and_the_favorites_bar_off(tmp_path) -> None:
    config = Config({}, str(tmp_path / "c.json"))
    assert int(Column.EXT) in config.get("left.columns_hidden")
    assert int(Column.EXT) in config.get("right.columns_hidden")
    assert config.get("favorites.bar") is False


def test_an_older_settings_file_is_brought_onto_the_new_look_once(tmp_path) -> None:
    path = tmp_path / "old.json"
    path.write_text(json.dumps({"favorites.bar": True, "left.columns_hidden": [3]}))
    config = Config.load(str(path))
    assert config.get("favorites.bar") is False
    assert config.get("left.columns_hidden") == [1, 3]
    assert config.get("look.024") is True


def test_a_bar_turned_back_on_after_that_stays_on(tmp_path) -> None:
    path = tmp_path / "new.json"
    path.write_text(json.dumps({"favorites.bar": True, "left.columns_hidden": [],
                                "look.024": True}))
    config = Config.load(str(path))
    assert config.get("favorites.bar") is True
    assert config.get("left.columns_hidden") == []


# ------------------------------------------------------------------ the pane

def test_the_drive_picker_is_in_the_path_bar_not_beside_it(tmp_path) -> None:
    widget = make(tmp_path)
    assert not widget._drives.isVisible()
    assert widget._drive_button.isVisible()
    assert widget._drive_button.parent() is widget._crumbs


def test_the_drive_button_offers_every_drive(tmp_path) -> None:
    widget = make(tmp_path)
    widget._sync_drives()
    texts = [widget._drives.itemText(row) for row in range(widget._drives.count())]
    assert texts[:2] == ["C:", "S:"]


def test_only_the_current_or_hovered_tab_offers_to_close(tmp_path) -> None:
    widget = make(tmp_path)
    widget._pane.open_tab("C:\\Other")
    widget._pane.open_tab("C:\\Third")
    QApplication.processEvents()
    tabs = widget._tabs
    current = tabs.currentIndex()
    shown = [tabs.tabButton(i, QTabBar.RightSide).property("shown")
             for i in range(tabs.count())]
    assert shown[current] == "true"
    assert shown.count("true") == 1
    widget._tab_hover = (current + 1) % tabs.count()
    widget._show_close_buttons()
    shown = [tabs.tabButton(i, QTabBar.RightSide).property("shown")
             for i in range(tabs.count())]
    assert shown.count("true") == 2


def test_every_tab_carries_the_folder_icon(tmp_path) -> None:
    widget = make(tmp_path)
    widget._pane.open_tab("C:\\Other")
    QApplication.processEvents()
    for index in range(widget._tabs.count()):
        assert not widget._tabs.tabIcon(index).isNull()


def test_the_header_knows_the_sorted_column(tmp_path) -> None:
    widget = make(tmp_path)
    widget._view.sortByColumn(int(Column.SIZE), Qt.DescendingOrder)
    assert widget._header.sortIndicatorSection() == int(Column.SIZE)
    assert widget._header.isSortIndicatorShown()


def test_the_extension_is_drawn_on_the_name_while_its_column_is_hidden(tmp_path) -> None:
    from PySide6.QtWidgets import QStyleOptionViewItem
    from app.io.protocol import Entry
    widget = make(tmp_path)
    model = widget._view.model()
    model.begin(has_parent=False)
    model.add([Entry(name="report.pdf", is_dir=False, size=10, mtime=0.0, attributes=0)])
    model.finish()
    index = model.index(0, int(Column.NAME))
    option = QStyleOptionViewItem()
    option.widget = widget._view
    assert widget._rows._inline_ext(option, index) == "pdf"
    widget._view.setColumnHidden(int(Column.EXT), False)
    assert widget._rows._inline_ext(option, index) == ""


def test_digits_are_asked_for_at_one_width() -> None:
    from PySide6.QtGui import QFont
    from app.ui.rows import tabular
    font = tabular(QFont())
    if hasattr(QFont, "Tag"):
        assert font.featureValue(QFont.Tag("tnum")) == 1

"""Checks on the favorites bar, and on the one thing it must never do.

The bar is a row of buttons and there is not much to get wrong in drawing it.
There is one thing, and it is not obvious: a widget with a layout takes its
`minimumSize` *property* from that layout by default, and a property beats
every size hint an override can return. Left alone, eight favourites would set
a floor under the pane holding them -- the splitter could not be dragged past
it, and a window asked for 820 pixels came back 1596 wide, which is how it was
found. So the first test here is that the bar asks the pane for nothing.

The rest is behaviour worth pinning because it is invisible until it is wrong:
the bar hides itself while there is nothing in it, and what does not fit goes
behind one button rather than off the edge.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QVBoxLayout, QWidget  # noqa: E402

from app.core.config import Config  # noqa: E402
from app.core.favorites import Favorites  # noqa: E402
from app.ui.favorites import FavoritesBar  # noqa: E402

NAMES = ("Jobs", "Drawings", "Standards", "Scans", "Archive 2025",
         "Templates", "Downloads", "Site photos")


def saved(tmp_path, names=NAMES):
    config = Config({"favorites": [{"name": name, "path": f"C:\\{name}"}
                                   for name in names]},
                    str(tmp_path / "config.json"))
    return Favorites(config)


@pytest.fixture
def bar(tmp_path):
    return FavoritesBar(saved(tmp_path))


# ------------------------------------------------------ the one that mattered

def test_the_bar_puts_no_floor_under_the_widget_holding_it(bar):
    """The regression. A layout's default size constraint writes its widget's
    minimumSize property, and that property is what a parent layout reads --
    so without `SetNoConstraint` the pane cannot be made narrower than the
    favourites in it.
    """
    assert bar.minimumSize().width() == 0
    assert bar.minimumSizeHint().width() == 0

    host = QWidget()
    layout = QVBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(bar)
    assert layout.minimumSize().width() == 0


def test_a_long_favorite_name_does_not_widen_the_pane(tmp_path):
    wide = saved(tmp_path, ("A folder with a deliberately very long name",))
    assert FavoritesBar(wide).minimumSize().width() == 0


# ---------------------------------------------------------------- visibility

def test_an_empty_list_means_no_bar_at_all(tmp_path):
    """A row of chrome that does nothing until somebody discovers a keystroke
    is worse than no row."""
    assert not FavoritesBar(saved(tmp_path, ())).isVisibleTo(QWidget())


def test_the_first_favorite_brings_the_bar_back(tmp_path):
    favorites = saved(tmp_path, ())
    bar = FavoritesBar(favorites)
    host = QWidget()
    QVBoxLayout(host).addWidget(bar)
    favorites.add("Jobs", "C:\\Jobs")
    assert bar.isVisibleTo(host)


def test_turning_it_off_hides_it_even_with_favorites_in_it(bar):
    host = QWidget()
    QVBoxLayout(host).addWidget(bar)
    assert bar.isVisibleTo(host)
    bar.set_wanted(False)
    assert not bar.isVisibleTo(host)
    bar.set_wanted(True)
    assert bar.isVisibleTo(host)


def test_turning_it_on_with_nothing_saved_still_shows_nothing(tmp_path):
    bar = FavoritesBar(saved(tmp_path, ()), wanted=False)
    host = QWidget()
    QVBoxLayout(host).addWidget(bar)
    bar.set_wanted(True)
    assert not bar.isVisibleTo(host)


# ------------------------------------------------------------------ overflow

def test_what_does_not_fit_goes_behind_one_button(bar):
    bar.resize(240, 30)
    bar.show()
    shown = [button for button in bar._buttons if button.isVisible()]
    assert 0 < len(shown) < len(NAMES)
    assert bar._more.isVisible()


def test_widening_brings_them_back(bar):
    bar.resize(240, 30)
    bar.show()
    bar.resize(1400, 30)
    assert all(button.isVisible() for button in bar._buttons)
    assert not bar._more.isVisible()


# -------------------------------------------------------------- what it emits

def test_a_click_asks_for_the_path_in_this_tab(bar):
    seen = []
    bar.chosen.connect(lambda path, new_tab: seen.append((path, new_tab)))
    bar.resize(1400, 30)
    bar._buttons[1].click()
    assert seen == [("C:\\Drawings", False)]


def test_the_list_changing_redraws_the_row(tmp_path):
    favorites = saved(tmp_path, ("Jobs",))
    bar = FavoritesBar(favorites)
    assert [button.text() for button in bar._buttons] == ["Jobs"]
    favorites.add("Drawings", "C:\\Drawings")
    assert [button.text() for button in bar._buttons] == ["Jobs", "Drawings"]
    favorites.remove(0)
    assert [button.text() for button in bar._buttons] == ["Drawings"]

"""Checks on the Options dialog and the table behind it (0.33).

Two kinds. The table's own checks run without a screen and are the ones that
matter most: a row naming a setting that does not exist, or a choice whose
default is not among its choices, is a dialog that draws nothing selected or
raises when opened -- and nothing about writing the row would show it. The
window checks are about the promise the dialog makes, which is that a change
applies at once and shows in the View menu as well.
"""

from __future__ import annotations

import pytest

from app.core import options
from app.core.config import Config, DEFAULTS
from app.core.listing import ATTRIBUTE_HIDDEN, ATTRIBUTE_SYSTEM
from app.io.protocol import Entry


def test_the_table_is_consistent():
    assert options.check() == []


def test_every_page_has_something_on_it():
    for key, _label in options.SECTIONS:
        assert options.in_section(key), key


def test_a_hand_edited_value_is_still_shown_as_a_choice():
    config = Config({"notify.after": 45.0})
    row = options.by_key()["notify.after"]
    choices = options.choices_for(config, row)
    assert any(value == 45.0 for value, _ in choices)
    assert options.current(config, row) == 45.0


def test_a_float_setting_matches_an_int_choice():
    config = Config({"preview.thumb_size": 128.0})
    row = options.by_key()["preview.thumb_size"]
    assert options.current(config, row) == 128
    assert len(options.choices_for(config, row)) == len(row.choices)


def test_search_matches_every_word_anywhere():
    row = options.by_key()["preview.thumbnails"]
    assert options.matches(row, "grid pictures")
    assert options.matches(row, "")
    assert not options.matches(row, "grid hidden")
    # The page's own name counts, so a search for the page finds its rows.
    assert options.matches(options.by_key()["listing.hidden"], "listing")


def test_a_row_that_needs_another_setting_says_when_it_is_live():
    row = options.by_key()["icons.overlays"]
    assert not options.live(Config({"icons.style": "badges"}), row)
    assert options.live(Config({"icons.style": "icons"}), row)


def test_hidden_and_system_default_to_what_earlier_versions_did():
    assert DEFAULTS["listing.hidden"] is True
    assert DEFAULTS["listing.system"] is True


# ------------------------------------------------------------------- model

def _entry(name, attributes=0):
    return Entry(name=name, is_dir=False, size=1, mtime=1.0, attributes=attributes)


@pytest.fixture
def model():
    pytest.importorskip("PySide6")
    from app.core.listing import ListingModel

    made = ListingModel()
    made.begin(has_parent=False)
    made.add([_entry("plain.txt"), _entry("secret.txt", ATTRIBUTE_HIDDEN),
              _entry("desktop.ini", ATTRIBUTE_HIDDEN | ATTRIBUTE_SYSTEM),
              _entry("boot.sys", ATTRIBUTE_SYSTEM)])
    made.finish()
    return made


def test_hiding_hidden_files_is_a_refilter(model):
    model.set_attribute_rule(hidden=False, system=True)
    names = [e.name for e in model.entries()]
    assert names == ["boot.sys", "plain.txt"]
    # A new name still has to avoid what is hidden.
    assert "secret.txt" in model.names()
    assert "shown" in model.summary()


def test_system_is_separate_from_hidden(model):
    model.set_attribute_rule(hidden=True, system=False)
    assert [e.name for e in model.entries()] == ["plain.txt", "secret.txt"]


def test_rows_arriving_later_follow_the_rule(model):
    model.set_attribute_rule(hidden=False, system=False)
    model.add([_entry("late.txt", ATTRIBUTE_HIDDEN), _entry("late2.txt")])
    assert "late.txt" not in [e.name for e in model.entries()]
    assert "late2.txt" in [e.name for e in model.entries()]


def test_the_rule_survives_a_new_folder_and_a_typed_filter_does_not(model):
    model.set_attribute_rule(hidden=False, system=True)
    model.set_filter("txt")
    model.begin(has_parent=False)
    assert model.filter_text == ""
    model.add([_entry("a.txt", ATTRIBUTE_HIDDEN), _entry("b.txt")])
    model.finish()
    assert [e.name for e in model.entries()] == ["b.txt"]


# ------------------------------------------------------------------ window

@pytest.fixture
def window(tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from tests.test_window import FakeBridge, FakeVolumes
    from app.core.capacity import Capacity
    from app.core.favorites import Favorites
    from app.core.pane import Pane
    from app.core.transfers import TransferQueue
    from app.ui.window import MainWindow

    config = Config({"left.path": "C:\\Jobs", "right.path": "D:\\Archive"},
                    str(tmp_path / "config.json"))
    bridge = FakeBridge()
    made = MainWindow(config, Pane(bridge, config, "left"),
                      Pane(bridge, config, "right"), FakeVolumes(),
                      TransferQueue(), None, Favorites(config),
                      Capacity(bridge, config))
    made.resize(1200, 600)
    made.show()
    QApplication.processEvents()
    yield made
    if made._options is not None:
        made._options.close()
    made.hide()


def test_the_dialog_draws_every_row(window):
    window.open_options()
    drawn = {row.option.key for row in window._options.rows()}
    assert drawn == {option.key for option in options.OPTIONS}


def test_a_switch_in_the_dialog_applies_and_ticks_the_menu(window):
    window.open_options("look")
    row = next(r for r in window._options.rows() if r.option.key == "look.motion")
    assert window._bound["look.motion"].isChecked()
    row.control.click()
    assert window._config.get("look.motion") is False
    assert not window._bound["look.motion"].isChecked()


def test_the_menu_moves_the_dialog(window):
    window.open_options()
    window._bound["pane.header"].trigger()
    row = next(r for r in window._options.rows() if r.option.key == "pane.header")
    assert window._config.get("pane.header") is False
    assert row.control.isChecked() is False


def test_a_choice_applies_the_theme(window):
    window.open_options()
    row = next(r for r in window._options.rows() if r.option.key == "theme")
    buttons = row.control.findChildren(type(row.control._group.button(0)))
    paper = next(b for b in buttons if b.text() == "Warm paper")
    paper.click()
    assert window._config.get("theme") == "paper"
    assert window._tokens["theme_name"] == "paper"
    assert window._bound_choices["theme"]["paper"].isChecked()


def test_search_hides_pages_with_nothing_on_them(window):
    window.open_options()
    dialog = window._options
    dialog._find.setText("hidden files")
    shown = [dialog._nav.item(i).data(0x0100) for i in range(dialog._nav.count())
             if not dialog._nav.item(i).isHidden()]
    assert shown == ["listing"]
    dialog._find.setText("")
    assert all(not dialog._nav.item(i).isHidden()
               for i in range(dialog._nav.count()))


def test_turning_hidden_files_off_reaches_every_tab(window):
    window.apply_setting("listing.hidden", False)
    for pane in window._panes:
        for tab in pane.tabs:
            assert tab.model._show_hidden is False


def test_a_found_accent_replaces_the_named_one(window):
    window._on_accent_found((10, 200, 100), "")
    assert window._tokens["accent"] == "#0ac864"
    window.apply_setting("accent.source", "named")
    assert window._tokens["accent_name"] == window._config.get("accent")


def test_the_grid_follows_the_theme_and_its_switch(window):
    window.apply_setting("theme", "blueprint")
    assert window._drafting.shown
    window.apply_setting("look.blueprint_grid", False)
    assert not window._drafting.shown
    window.apply_setting("look.blueprint_grid", True)
    window.apply_setting("theme", "dark")
    assert not window._drafting.shown


def test_the_grid_is_drawn_in_both_listings_of_both_panes(window):
    """0.39: on the Deck it only showed in the gaps between the panes."""
    views = [view for widget in window._widgets for view in widget.listing_views()]
    assert len(views) == 4
    assert all(view in window._drafting._views for view in views)


def test_the_glow_can_be_turned_off(window):
    window.apply_setting("look.pane_glow", False)
    assert window._splitter._glow_on is False

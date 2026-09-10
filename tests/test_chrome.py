"""Checks on the parts of 0.11's chrome that are drawn rather than styled.

Three of them, and each is here because its failure mode is silent:

  * **The breadcrumb's width.** A bar whose minimum is its own contents puts a
    floor under the pane and stops the splitter. That already happened once
    with the favorites bar, it was only found because a preview render came
    back wider than it was asked for, and a long UNC path is exactly the thing
    that would bring it back.
  * **Reading a token.** Half the token set is `#rrggbb` and half is
    `rgba(r, g, b, a)`, which `QColor` does not parse -- it returns an invalid
    colour and paints nothing, without raising. Every tint in a row goes
    through that function.
  * **The crumbs themselves**, against the paths this actually runs on: a UNC
    share, a drive root, and something deep enough to be elided.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.io.paths import crumbs  # noqa: E402
from app.theme import sheet  # noqa: E402
from app.ui import glyphs  # noqa: E402
from app.ui.breadcrumb import ELLIPSIS, Breadcrumb  # noqa: E402
from app.ui.rows import RowDelegate, parse_colour, parse_px  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


# --------------------------------------------------------------- the crumbs

def test_a_drive_path_keeps_its_root_whole():
    assert crumbs("C:\\Users\\rjokr") == [
        ("C:\\", "C:\\"),
        ("Users", "C:\\Users"),
        ("rjokr", "C:\\Users\\rjokr"),
    ]


def test_a_unc_share_is_one_crumb_not_two_empty_ones_and_a_server():
    """`\\\\vault` on its own is not a place a pane can be -- there is no
    listing to show -- which is why the walk is `parent`'s and not a split on
    separators."""
    assert crumbs("\\\\vault\\projects\\2025") == [
        ("\\\\vault\\projects", "\\\\vault\\projects"),
        ("2025", "\\\\vault\\projects\\2025"),
    ]


def test_a_root_is_one_crumb_and_an_empty_path_is_none():
    assert crumbs("C:\\") == [("C:\\", "C:\\")]
    assert crumbs("\\\\vault\\projects") == [("\\\\vault\\projects",
                                              "\\\\vault\\projects")]
    assert crumbs("") == []


def test_a_forward_slash_path_is_normalised_before_it_is_split():
    assert [label for label, _ in crumbs("C:/Jobs/2025")] == ["C:\\", "Jobs", "2025"]


# ------------------------------------------------------------- the bar

def test_the_bar_never_puts_a_floor_under_its_pane(app):
    """The favorites bar bug, in the one other widget that could have it. A
    minimum that grows with the path is a splitter that jams on a deep
    folder."""
    bar = Breadcrumb()
    bar.set_crumbs(crumbs("C:\\"))
    narrow = bar.minimumSizeHint().width()
    bar.set_crumbs(crumbs(
        "\\\\vault\\projects\\2025\\2025-114 Fairgrounds Pavilion\\Drawings"
        "\\Issued for Construction\\Structural"))
    assert bar.minimumSizeHint().width() <= narrow


def test_a_deep_path_is_elided_from_the_middle(app):
    """The two ends are the server and where you are. Everything dropped comes
    from between them."""
    bar = Breadcrumb()
    deep = "\\\\vault\\projects\\a\\b\\c\\d\\e\\f\\g"
    bar.set_crumbs(crumbs(deep))
    shown = [label for label, path in bar._visible()]
    assert shown[0] == "\\\\vault\\projects"
    assert shown[-1] == "g"
    assert ELLIPSIS not in shown[0]
    assert any(path is None for _, path in bar._visible())


def test_a_shallow_path_is_not_elided_at_all(app):
    bar = Breadcrumb()
    bar.set_crumbs(crumbs("C:\\Users\\rjokr"))
    assert [path for _, path in bar._visible()] == \
        ["C:\\", "C:\\Users", "C:\\Users\\rjokr"]


def test_a_crumb_says_where_it_goes(app):
    bar = Breadcrumb()
    bar.set_crumbs(crumbs("C:\\Users\\rjokr"))
    went = []
    bar.navigate.connect(went.append)
    for child in bar.findChildren(type(bar._rest)):
        if child.property("role") == "crumb" and child.text() == "Users":
            child.click()
    assert went == ["C:\\Users"]


# ------------------------------------------------------------ reading tokens

def test_both_shapes_of_token_come_back_as_a_colour():
    solid = parse_colour("#1d2128")
    assert solid.isValid() and solid.alpha() == 255

    tint = parse_colour("rgba(74, 145, 255, 0.14)")
    assert tint.isValid()
    assert (tint.red(), tint.green(), tint.blue()) == (74, 145, 255)
    assert 30 <= tint.alpha() <= 40


def test_nothing_and_nonsense_come_back_invalid_rather_than_raising():
    """A delegate that raises during a paint takes the window with it."""
    for value in (None, "", "rgba(1, 2)", "rgba(a, b, c, d)", "not a colour"):
        assert not parse_colour(value).isValid()


def test_a_pixel_token_is_read_as_a_number():
    assert parse_px("10px", 0) == 10
    assert parse_px("12.5px", 0) == 12
    assert parse_px(None, 5) == 5
    assert parse_px("wat", 7) == 7


def test_every_colour_the_delegate_asks_for_exists_in_every_theme():
    """The delegate names its tokens as strings, so a renamed one is a chip
    that stops drawing rather than an error. Both halves of the age set are
    checked: the tint per step and the ink shared by all of them."""
    wanted = ["accent_row", "accent_row_idle", "bg_3", "bg_4", "txt_2",
              "age_text", "age_fresh", "age_recent", "age_month", "radius_sm"]
    for theme in ("dark", "light", "paper", "blueprint", "contrast"):
        tokens = sheet.tokens(theme, "blue", "normal")
        for name in wanted:
            assert name in tokens, f"{name} missing from {theme}"
            if name != "radius_sm":
                assert parse_colour(tokens[name]).isValid(), f"{name} in {theme}"


def test_the_age_chip_steps_down_in_the_unit_its_font_was_set_in(app):
    """A pixel-sized font answers -1 when asked for its point size, and every
    font in this application is pixel-sized because the sheet says `13px`. A
    step measured in points therefore ignored the density and drew the same
    small chip at every setting.
    """
    from PySide6.QtGui import QFont

    from app.ui.rows import _one_step_smaller

    pixels = QFont()
    pixels.setPixelSize(13)
    stepped = _one_step_smaller(pixels)
    assert stepped.pixelSize() == 12
    assert stepped.pointSizeF() == -1

    points = QFont()
    points.setPointSizeF(11.0)
    assert _one_step_smaller(points).pointSizeF() == 10.0

    tiny = QFont()
    tiny.setPixelSize(6)
    assert _one_step_smaller(tiny).pixelSize() >= 8


def test_the_delegate_takes_a_radius_off_the_tokens(app):
    rows = RowDelegate()
    rows.apply_tokens(sheet.tokens("dark", "blue", "normal"))
    assert rows._radius >= 3


# ------------------------------------------------------------------- glyphs

def test_every_chrome_icon_draws_something(app):
    for name in glyphs.STROKES:
        icon = glyphs.icon(name, colour="#aab3c0", muted="#79828f")
        assert not icon.isNull()
        assert not icon.pixmap(16, 16).isNull()


def test_an_icon_is_built_once_and_kept(app):
    glyphs.forget()
    first = glyphs.icon("up", colour="#aab3c0", muted="#79828f")
    again = glyphs.icon("up", colour="#aab3c0", muted="#79828f")
    other = glyphs.icon("up", colour="#ffffff", muted="#79828f")
    assert first is again
    assert other is not first


def test_a_disabled_icon_is_the_muted_grey_and_not_a_faded_one(app):
    """Qt makes a disabled icon by fading the normal one, and a faded tint of
    a grey is not the grey the rest of the disabled chrome is using. Forward
    with no history should look like everything else that is unavailable."""
    from PySide6.QtGui import QIcon

    icon = glyphs.icon("forward", colour="#ffffff", muted="#404040", size=32)
    normal = icon.pixmap(32, 32, QIcon.Normal).toImage()
    off = icon.pixmap(32, 32, QIcon.Disabled).toImage()

    def darkest(image):
        best = None
        for y in range(image.height()):
            for x in range(image.width()):
                pixel = QColor(image.pixelColor(x, y))
                if pixel.alpha() > 200:
                    if best is None or pixel.lightness() < best:
                        best = pixel.lightness()
        return best

    assert darkest(normal) is not None and darkest(off) is not None
    assert darkest(off) < darkest(normal)

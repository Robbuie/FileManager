"""0.48: six more themes, a theme that follows Windows or the clock, a font and
corners of your choosing, and an accent for the right-hand pane.

The switching rule is the part with edges: a schedule that runs over midnight,
a light and dark pair naming a theme a later version added, a Windows that will
not say. Each of those falls back to the picker's theme rather than to a theme
nobody chose, and that is what is pinned here.
"""

from __future__ import annotations

import pytest

from app.core import options, themeswitch
from app.core.config import DEFAULTS
from app.theme import qss
from app.theme.tokens import (
    CORNERS,
    FONTS,
    LIGHT_THEMES,
    THEME_LABELS,
    THEMES,
)

NEW = ("graphite", "control", "phosphor", "dusk", "frost", "ink")


def luminance(hex_colour: str) -> float:
    value = hex_colour.lstrip("#")
    channels = [int(value[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
              for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a: str, b: str) -> float:
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


@pytest.mark.parametrize("name", NEW)
def test_the_new_themes_have_every_grey_the_old_ones_have(name) -> None:
    assert set(THEMES[name]) == set(THEMES["dark"])
    assert name in THEME_LABELS


@pytest.mark.parametrize("name", NEW)
def test_the_new_themes_are_readable(name) -> None:
    """Body text at AA on the pane, and the muted grey at the large-text
    line -- the same bar the high contrast theme was built to."""
    theme = THEMES[name]
    assert contrast(theme["txt_0"], theme["bg_2"]) >= 7.0
    assert contrast(theme["txt_1"], theme["bg_2"]) >= 4.5
    assert contrast(theme["txt_2"], theme["bg_2"]) >= 3.0


@pytest.mark.parametrize("name", sorted(THEMES))
def test_light_themes_are_the_light_ones(name) -> None:
    light = luminance(THEMES[name]["bg_2"]) > 0.4
    assert (name in LIGHT_THEMES) == light


def test_font_and_corners_reach_the_tokens() -> None:
    plain = qss.build("dark")
    mono = qss.build("dark", font="mono", corners="square")
    assert mono["font"] == FONTS["mono"][0]
    assert mono["radius_lg"] == CORNERS["square"]["radius_lg"]
    assert plain["radius_lg"] == CORNERS["round"]["radius_lg"]
    assert qss.build("dark", font="nonsense")["font"] == plain["font"]


# --------------------------------------------------------------- switching

def test_off_is_the_picker() -> None:
    assert themeswitch.pick("off", "dusk", "light", "dark",
                            windows_light=True, hour=12) == "dusk"


def test_windows_light_and_dark() -> None:
    assert themeswitch.pick("windows", "dusk", "frost", "graphite",
                            windows_light=True, hour=3) == "frost"
    assert themeswitch.pick("windows", "dusk", "frost", "graphite",
                            windows_light=False, hour=3) == "graphite"


def test_windows_that_will_not_say_keeps_the_picker() -> None:
    assert themeswitch.pick("windows", "dusk", "frost", "graphite",
                            windows_light=None, hour=3) == "dusk"


@pytest.mark.parametrize("hour,expected", [(6, "dark"), (7, "light"),
                                           (18, "light"), (19, "dark"), (0, "dark")])
def test_schedule(hour, expected) -> None:
    assert themeswitch.pick("schedule", "paper", "light", "dark", windows_light=None,
                            hour=hour, day_from=7, night_from=19) == expected


def test_a_schedule_over_midnight() -> None:
    """Day from 20:00 to 06:00, for somebody on nights."""
    pick = lambda h: themeswitch.pick("schedule", "paper", "light", "dark",  # noqa: E731
                                      windows_light=None, hour=h,
                                      day_from=20, night_from=6)
    assert pick(23) == "light" and pick(2) == "light"
    assert pick(12) == "dark"


def test_an_unknown_theme_falls_back_to_the_picker() -> None:
    assert themeswitch.pick("windows", "dusk", "from-a-later-version", "dark",
                            windows_light=True, hour=3) == "dusk"


def test_windows_is_not_asked_off_windows() -> None:
    import sys

    if sys.platform != "win32":
        assert themeswitch.windows_light() is None


def test_current_reads_the_settings() -> None:
    from app.core.config import Config

    config = Config({"theme": "ink", "theme.follow": "schedule",
                     "theme.light": "frost", "theme.dark": "dusk"}, "unused")
    assert themeswitch.current(config, hour=10) == "frost"
    assert themeswitch.current(config, hour=22) == "dusk"


def test_the_settings_and_their_rows() -> None:
    for key in ("theme.follow", "theme.light", "theme.dark", "theme.day_from",
                "theme.night_from", "look.font", "look.corners", "accent.right"):
        assert key in DEFAULTS
    keys = {row.key for row in options.OPTIONS}
    assert {"theme.follow", "look.font", "look.corners", "accent.right"} <= keys
    assert not options.check()

"""Checks on the 0.35 accent sources and the look switches.

The colour arithmetic is the part worth testing: a wallpaper accent that is
the average of a photograph (brown) or that disappears against the theme is a
feature that looks broken, and neither needs a screen to catch.
"""

from __future__ import annotations

import pytest

from app.core import accent
from app.theme import qss


def test_choose_prefers_the_colourful_patch_over_the_grey_majority():
    pixels = [(120, 120, 120)] * 900 + [(240, 120, 20)] * 100
    r, g, b = accent.choose(pixels)
    assert r > 200 and b < 60


def test_a_grey_picture_has_no_colour():
    assert accent.choose([(30, 30, 30), (200, 200, 200)] * 50) is None


def test_readable_keeps_the_hue_and_finds_contrast():
    import colorsys

    dark_ground = (16, 18, 22)
    navy = (10, 20, 60)
    lifted = accent.readable(navy, dark_ground)
    assert accent.contrast(lifted, dark_ground) >= 3.0
    h1 = colorsys.rgb_to_hls(*(c / 255 for c in navy))[0]
    h2 = colorsys.rgb_to_hls(*(c / 255 for c in lifted))[0]
    assert abs(h1 - h2) < 0.03


def test_readable_darkens_on_a_light_ground():
    paper = (255, 253, 248)
    yellow = (250, 240, 120)
    assert accent.contrast(accent.readable(yellow, paper), paper) >= 3.0


def test_a_triple_replaces_the_named_accent_everywhere():
    tokens = qss.build(accent="blue", accent_rgb=(10, 200, 100))
    assert tokens["accent"] == "#0ac864"
    assert tokens["accent_name"] == "custom"
    assert "10, 200, 100" in tokens["accent_soft"]


def test_the_registry_answers_nothing_off_windows(monkeypatch):
    import sys

    if sys.platform.startswith("win"):
        pytest.skip("reads the real registry on Windows")
    assert accent.windows_accent() is None
    assert accent.wallpaper_path() is None


def test_grid_tokens_exist_for_every_theme():
    from app.theme.tokens import THEMES

    for theme in THEMES:
        tokens = qss.build(theme)
        assert tokens["grid_minor"].startswith("rgba(")

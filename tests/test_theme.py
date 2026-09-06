"""Checks on the design-system port.

These are worth having because the failure they catch is invisible: a theme
that renders fine but no longer tracks the accent picker, or a token that goes
missing and leaves one widget unstyled.
"""

from app.theme import qss
from app.theme.tokens import ACCENTS, DENSITIES, THEMES


def test_every_combination_builds():
    for theme in THEMES:
        for accent in ACCENTS:
            for density in DENSITIES:
                tokens = qss.build(theme, accent, density)
                assert tokens["bg_0"].startswith("#")
                assert tokens["accent"].startswith("#")


def test_unknown_names_fall_back_rather_than_raise():
    tokens = qss.build("from-a-later-version", "chartreuse", "enormous")
    assert tokens["theme_name"] == "dark"
    assert tokens["accent_name"] == "blue"
    assert tokens["density_name"] == "normal"


def test_accent_derivations_track_the_accent():
    blue = qss.build(accent="blue")
    red = qss.build(accent="redline")
    for key in ("accent", "accent_dim", "accent_text", "accent_soft"):
        assert blue[key] != red[key], f"{key} does not follow the accent"


def test_density_changes_metrics_and_nothing_else():
    compact = qss.build(density="compact")
    large = qss.build(density="large")
    assert compact["row_h"] != large["row_h"]
    assert compact["bg_0"] == large["bg_0"]
    assert compact["accent"] == large["accent"]


def test_render_rejects_an_unknown_token():
    import pytest

    with pytest.raises(KeyError):
        qss.render("QWidget {{ color: {not_a_token}; }}", qss.build())

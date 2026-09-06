"""Builds the token set for one (theme, accent, density) combination.

This is the stand-in for what CSS gives Redline PDF for free. `app.css`
derives every tint of the accent with `rgba(var(--accent-rgb), a)` and
`color-mix()`, so its picker sets one property and the whole sheet follows.
QSS has neither, so the derivation happens here in Python and the results are
written into the generated stylesheet as literals.

The consequence is that the sheet is regenerated and re-applied whenever any
axis changes. That is the whole mechanism — there is no partial update path,
and adding one would reintroduce exactly the half-updated chrome this avoids.
"""

from __future__ import annotations

from app.theme.tokens import (
    ACCENT_ALPHA,
    ACCENTS,
    DEFAULTS,
    DENSITIES,
    SHAPE,
    THEMES,
)

RGB = tuple[int, int, int]


def _clamp(v: float) -> int:
    return max(0, min(255, round(v)))


def mix(colour: RGB, other: RGB, weight: float) -> str:
    """Mix `colour` with `other`, `weight` being the share of `colour`.

    The equivalent of `color-mix(in srgb, a W%, b)`. sRGB, not linear, because
    that is what the CSS uses and the point is to match it.
    """
    return rgb(tuple(  # type: ignore[arg-type]
        _clamp(c * weight + o * (1 - weight)) for c, o in zip(colour, other)
    ))


def rgb(colour: RGB) -> str:
    return "#%02x%02x%02x" % colour


def rgba(colour: RGB, alpha: float) -> str:
    """QSS understands `rgba(r, g, b, a)` with a float alpha."""
    r, g, b = colour
    return f"rgba({r}, {g}, {b}, {alpha:g})"


_BLACK: RGB = (0, 0, 0)
_WHITE: RGB = (255, 255, 255)
_LIFT: RGB = (255, 176, 102)


def build(
    theme: str | None = None,
    accent: str | None = None,
    density: str | None = None,
) -> dict[str, str]:
    """Return every token for one combination, ready to substitute into QSS.

    An unknown name falls back to the default rather than raising: a settings
    file carrying a theme from a later version should not stop the application
    starting.
    """
    theme_name = theme if theme in THEMES else DEFAULTS["theme"]
    accent_name = accent if accent in ACCENTS else DEFAULTS["accent"]
    density_name = density if density in DENSITIES else DEFAULTS["density"]

    out: dict[str, str] = {}
    out.update(THEMES[theme_name])
    out.update(SHAPE)

    a = ACCENTS[accent_name]
    out["accent"] = rgb(a)
    out["accent_dim"] = mix(a, _BLACK, 0.70)
    out["accent_text"] = mix(a, _WHITE, 0.74)
    out["accent_lift"] = mix(a, _LIFT, 0.62)
    for name, alpha in ACCENT_ALPHA.items():
        out[f"accent_{name}"] = rgba(a, alpha)

    for key, value in DENSITIES[density_name].items():
        out[key] = f"{value:g}px" if key == "ui_font" else f"{int(value)}px"

    out["theme_name"] = theme_name
    out["accent_name"] = accent_name
    out["density_name"] = density_name
    return out


def render(template: str, tokens: dict[str, str]) -> str:
    """Substitute `{token}` placeholders in a QSS template.

    Raises on an unknown placeholder rather than leaving it in the sheet, where
    it would silently produce an unstyled widget.
    """
    try:
        return template.format(**tokens)
    except KeyError as exc:  # pragma: no cover - a template bug, not a state
        raise KeyError(f"unknown token in QSS template: {exc.args[0]}") from exc

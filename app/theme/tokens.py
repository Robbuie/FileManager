"""Design tokens. Values are ported verbatim from Redline PDF's `app.css`.

They are copied rather than re-derived because the family look depends on the
three applications using the same numbers. If a grey changes in Redline PDF, it
changes here; do not tune one in isolation.
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# Themes. Each restates the greys only. The accent and the density come from
# their own axes and are never named in here.
# --------------------------------------------------------------------------

THEMES: dict[str, dict[str, str]] = {
    # The default: neutral greys, content forward.
    "dark": {
        "bg_0": "#101216",       # app backdrop
        "bg_1": "#171a20",       # chrome: toolbar, tab strip, status bar
        "bg_2": "#1d2128",       # raised chrome: pane background, dialogs
        "bg_3": "#252a33",       # hover
        "bg_4": "#2f3540",       # active, strong borders
        "line": "#2a2f38",
        "line_soft": "#22262d",
        "txt_0": "#e7ebf2",
        "txt_1": "#aab3c0",
        "txt_2": "#79828f",
        "info": "#4aa8ff",
        "good": "#46c98b",
        "warn": "#f2c14e",
        "sel": "#4aa8ff",
    },
    "light": {
        "bg_0": "#eceef2",
        "bg_1": "#f7f8fa",
        "bg_2": "#ffffff",
        "bg_3": "#e8ebf0",
        "bg_4": "#d7dbe2",
        "line": "#d5d9e0",
        "line_soft": "#e3e6eb",
        "txt_0": "#14181f",
        "txt_1": "#4a525e",
        "txt_2": "#7b838f",
        "info": "#4aa8ff",
        "good": "#46c98b",
        "warn": "#f2c14e",
        "sel": "#4aa8ff",
    },
    # Warm light: chrome knocked off pure white, for a long session on a
    # bright monitor.
    "paper": {
        "bg_0": "#ece7dd",
        "bg_1": "#f7f3ea",
        "bg_2": "#fffdf8",
        "bg_3": "#eae4d7",
        "bg_4": "#d9d1c0",
        "line": "#d8d0c0",
        "line_soft": "#e6e0d3",
        "txt_0": "#211d17",
        "txt_1": "#564f43",
        "txt_2": "#857c6c",
        "info": "#4aa8ff",
        "good": "#46c98b",
        "warn": "#f2c14e",
        "sel": "#4aa8ff",
    },
    # Cool, deep chrome.
    "blueprint": {
        "bg_0": "#0a1220",
        "bg_1": "#0f1b2d",
        "bg_2": "#142339",
        "bg_3": "#1b2f4a",
        "bg_4": "#26405f",
        "line": "#21374f",
        "line_soft": "#182b40",
        "txt_0": "#e2ecf8",
        "txt_1": "#9fb4cc",
        "txt_2": "#6d8299",
        "info": "#6cc0ff",
        "good": "#46c98b",
        "warn": "#f2c14e",
        "sel": "#6cc0ff",
    },
    # Not a style, an accessibility target. Every border is a visible line
    # rather than a hint, and the muted grey is lifted until it passes as body
    # text, because in this theme it is being read rather than skimmed.
    "contrast": {
        "bg_0": "#000000",
        "bg_1": "#0a0a0c",
        "bg_2": "#141418",
        "bg_3": "#23232a",
        "bg_4": "#3a3a45",
        "line": "#55555f",
        "line_soft": "#3d3d46",
        "txt_0": "#ffffff",
        "txt_1": "#e4e4ea",
        "txt_2": "#b6b6c0",
        "info": "#7cc4ff",
        "good": "#5ee0a0",
        "warn": "#ffd75e",
        "sel": "#7cc4ff",
    },
}

THEME_LABELS: dict[str, str] = {
    "dark": "Dark",
    "light": "Light",
    "paper": "Warm paper",
    "blueprint": "Blueprint",
    "contrast": "High contrast",
}

# --------------------------------------------------------------------------
# Accents. Channel triples, not hex, because every tint is derived from them
# at render time. A literal accent colour written anywhere re-pins that spot to
# one choice and it stops tracking the picker.
# --------------------------------------------------------------------------

ACCENTS: dict[str, tuple[int, int, int]] = {
    "redline": (255, 91, 74),
    "amber": (242, 165, 60),
    "green": (70, 201, 139),
    "cyan": (54, 191, 210),
    "blue": (74, 145, 255),
    "violet": (154, 122, 255),
}

ACCENT_LABELS: dict[str, str] = {
    "redline": "Redline red",
    "amber": "Amber",
    "green": "Field green",
    "cyan": "Cyan",
    "blue": "Drafting blue",
    "violet": "Violet",
}

#: Alpha for each derived tint of the accent.
ACCENT_ALPHA: dict[str, float] = {
    "soft": 0.14,   # fill behind a selected or armed control
    "wash": 0.17,   # fill behind an active row
    "line": 0.45,   # border on an active element
    "glow": 0.80,   # focus ring
}

#: Alpha for each step of the age chip, strongest for the freshest.
#:
#: A semantic set, not an accent tint: how recently a file changed has nothing
#: to do with which colour the picker is on, and tying it to the accent would
#: make "changed today" mean something different in every theme. It derives
#: from the theme's own `good` instead, so it stays legible in all five without
#: a literal being written anywhere.
#: The keys are the step names `app.core.listing.age_step` returns. They are
#: the join between a rule about time and a rule about colour, which live in
#: different layers on purpose -- `tests/test_listing.py` pins that every name
#: one produces has a tint here, because a mismatch draws no chip and raises
#: nothing.
AGE_ALPHA: dict[str, float] = {
    "fresh": 0.22,    # under a day
    "recent": 0.13,   # under a week
    "month": 0.085,   # under a month
}

# --------------------------------------------------------------------------
# Density. The same metrics at three sizes. Row height matters more here than
# anywhere in the other two applications: it is the thing being looked at
# 50,000 times.
# --------------------------------------------------------------------------

DENSITIES: dict[str, dict[str, int | float]] = {
    "compact": {
        "ui_font": 12,
        "toolbar_h": 30,
        "row_h": 20,
        "tab_h": 26,
        "status_h": 22,
        "sidebar_w": 238,
        "button_h": 26,
    },
    "normal": {
        "ui_font": 13,
        "toolbar_h": 34,
        "row_h": 22,
        "tab_h": 30,
        "status_h": 26,
        "sidebar_w": 268,
        "button_h": 30,
    },
    "large": {
        "ui_font": 14.5,
        "toolbar_h": 38,
        "row_h": 26,
        "tab_h": 34,
        "status_h": 30,
        "sidebar_w": 304,
        "button_h": 34,
    },
}

DENSITY_LABELS: dict[str, str] = {
    "compact": "Compact",
    "normal": "Normal",
    "large": "Large",
}

# --------------------------------------------------------------------------
# Shape and type. Not axed; the same in every combination.
# --------------------------------------------------------------------------

#: 0.27: file families, for the badge on a row and the folder's bar. Channel
#: triples like the accents, because the badge's fill and text are derived from
#: them per theme in `qss.build` -- the same hue reads on dark and on paper.
#: These are semantic colours in CLAUDE.md's sense: they say what a file is,
#: so they follow neither the accent nor the theme's hue.
KINDS: dict[str, tuple[int, int, int]] = {
    "logix":   (45, 196, 176),
    "hmi":     (154, 122, 255),
    "cad":     (240, 160, 40),
    "pdf":     (238, 96, 96),
    "sheet":   (70, 190, 110),
    "doc":     (80, 145, 245),
    "image":   (220, 110, 180),
    "archive": (190, 150, 100),
    "code":    (100, 180, 220),
    "program": (150, 160, 175),
    "text":    (140, 148, 160),
    "other":   (125, 133, 145),
    "folder":  (125, 133, 145),
}

SHAPE: dict[str, str] = {
    "radius": "7px",
    "radius_sm": "5px",
    # The pane and the listing. Bigger than a control's on purpose: a
    # container and the buttons inside it drawn at the same corner read as
    # one flat object, which is most of what "blocky" means.
    "radius_lg": "12px",
    "font": '"Segoe UI", Inter, system-ui, sans-serif',
    "mono": '"Cascadia Mono", Consolas, monospace',
}

#: Redline PDF defaults to its own red. Blue is this family's non-markup
#: accent and is what DWG Viewer defaults to.
DEFAULTS: dict[str, str] = {
    "theme": "dark",
    "accent": "blue",
    "density": "normal",
}

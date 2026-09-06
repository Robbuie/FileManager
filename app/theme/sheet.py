"""The QSS template and the one function that applies it.

The template is the whole stylesheet. It is written once, with `{token}`
placeholders, and rendered by substitution — which is why there is not a colour
literal in it and must never be. Braces are doubled because the render is a
`str.format`.

Two things QSS cannot do are done here in Python instead:

  * **Row height.** `QTableView` takes it from the vertical header's default
    section size, not from a style rule, so `metrics()` hands the density's
    number to the view.
  * **Selection colour.** Qt draws selection from the platform palette, not the
    sheet, so the palette is set explicitly. Without this the Windows blue
    appears in a themed window, which is the single most obvious way a port of
    a design system looks half done.
"""

from __future__ import annotations

from typing import Any

from app.theme import qss
from app.theme.tokens import DENSITIES, DEFAULTS

TEMPLATE = """
QWidget {{
    background: {bg_0};
    color: {txt_0};
    font-family: {font};
    font-size: {ui_font};
    selection-background-color: {accent};
    selection-color: {bg_0};
}}

QMainWindow, QDialog {{ background: {bg_0}; }}
QToolTip {{
    background: {bg_2};
    color: {txt_0};
    border: 1px solid {line};
    padding: 4px 6px;
}}

/* Chrome: toolbar, menu bar, status bar. */
QToolBar {{
    background: {bg_1};
    border: none;
    border-bottom: 1px solid {line};
    padding: 3px 6px;
    spacing: 4px;
    min-height: {toolbar_h};
}}
QMenuBar {{ background: {bg_1}; border-bottom: 1px solid {line}; }}
QMenuBar::item {{ padding: 5px 10px; background: transparent; }}
QMenuBar::item:selected {{ background: {accent_soft}; color: {accent_text}; }}
QMenu {{
    background: {bg_2};
    border: 1px solid {line};
    border-radius: {radius_sm};
    padding: 4px;
}}
QMenu::item {{ padding: 5px 22px 5px 22px; border-radius: {radius_sm}; }}
QMenu::item:selected {{ background: {accent_soft}; color: {accent_text}; }}
QMenu::separator {{ height: 1px; background: {line_soft}; margin: 4px 6px; }}
QStatusBar {{
    background: {bg_1};
    border-top: 1px solid {line};
    color: {txt_1};
    min-height: {status_h};
}}
QStatusBar::item {{ border: none; }}

/* Buttons. */
QToolButton, QPushButton {{
    background: {bg_2};
    border: 1px solid {line};
    border-radius: {radius_sm};
    color: {txt_0};
    padding: 3px 10px;
    min-height: {button_h};
}}
QToolButton:hover, QPushButton:hover {{ background: {bg_3}; }}
QToolButton:pressed, QPushButton:pressed {{ background: {bg_4}; }}
QToolButton:disabled, QPushButton:disabled {{ color: {txt_2}; background: {bg_1}; }}
QToolButton:focus, QPushButton:focus {{ border: 1px solid {accent_line}; }}

/* The path bar. */
QLineEdit {{
    background: {bg_2};
    border: 1px solid {line};
    border-radius: {radius_sm};
    color: {txt_0};
    padding: 3px 8px;
    min-height: {button_h};
    selection-background-color: {accent};
    selection-color: {bg_0};
}}
QLineEdit:focus {{ border: 1px solid {accent_line}; background: {bg_2}; }}
QLineEdit[state="stale"] {{ color: {txt_2}; }}

/* The filter. Marked with the accent down its left edge, because a listing
   that is hiding rows has to say so from across the room. */
QLineEdit[role="filter"] {{
    background: {bg_1};
    border: 1px solid {line};
    border-left: 3px solid {accent};
}}

/* The drive picker. No drop-down arrow: the platform draws that one from its
   own palette, and a dark arrow on dark chrome is worse than no arrow beside
   a letter that is already the whole content. */
QComboBox {{
    background: {bg_2};
    border: 1px solid {line};
    border-radius: {radius_sm};
    color: {txt_0};
    padding: 3px 8px;
    min-height: {button_h};
    min-width: 44px;
    max-width: 170px;
}}
QComboBox:hover {{ background: {bg_3}; }}
QComboBox:on {{ border: 1px solid {accent_line}; }}
QComboBox::drop-down {{ width: 0px; border: none; }}
QComboBox QAbstractItemView {{
    background: {bg_2};
    border: 1px solid {line};
    color: {txt_0};
    outline: none;
    selection-background-color: {accent_row};
    selection-color: {txt_0};
}}

/* Tabs. One strip per pane. */
QTabBar {{ background: {bg_1}; qproperty-drawBase: 0; }}
QTabBar::tab {{
    background: {bg_1};
    color: {txt_1};
    border: 1px solid transparent;
    border-bottom: 1px solid {line};
    padding: 4px 12px;
    min-height: {tab_h};
    margin-right: 1px;
}}
QTabBar::tab:hover {{ background: {bg_3}; color: {txt_0}; }}
QTabBar::tab:selected {{
    background: {bg_2};
    color: {txt_0};
    border: 1px solid {line};
    border-bottom: 1px solid {bg_2};
}}
QToolButton[role="tabclose"] {{
    background: transparent;
    border: none;
    color: {txt_2};
    padding: 0px;
    margin: 0px 0px 0px 4px;
    min-width: 14px;
    max-width: 14px;
    min-height: 14px;
    max-height: 14px;
}}
QToolButton[role="tabclose"]:hover {{ color: {txt_0}; background: {bg_4}; border-radius: 2px; }}

/* The listing itself. Everything above exists to frame this. */
QTableView {{
    background: {bg_2};
    alternate-background-color: {bg_1};
    color: {txt_0};
    border: 1px solid {line};
    border-radius: {radius_sm};
    gridline-color: {line_soft};
    outline: none;
    selection-background-color: {accent_row};
    selection-color: {txt_0};
}}
QTableView::item {{ padding: 0px 6px; border: none; }}
QTableView::item:selected {{ background: {accent_row}; color: {txt_0}; }}
QTableView::item:selected:!active {{ background: {accent_row_idle}; color: {txt_1}; }}
QTableView::item:focus {{ border: none; }}
QHeaderView::section {{
    background: {bg_1};
    color: {txt_1};
    border: none;
    border-right: 1px solid {line_soft};
    border-bottom: 1px solid {line};
    padding: 4px 6px;
}}
QHeaderView::section:hover {{ background: {bg_3}; color: {txt_0}; }}

/* The pane frame. The active pane is the one the accent is on -- with two
   panes and one keyboard there is no other way to know where a keystroke
   lands. */
QFrame[pane="true"] {{
    background: {bg_2};
    border: 1px solid {line};
    border-radius: {radius};
}}
QFrame[pane="true"][active="true"] {{ border: 1px solid {accent_line}; }}
QLabel[role="status"] {{ background: transparent; color: {txt_1}; padding: 2px 6px; }}
QLabel[role="status"][state="busy"] {{ color: {accent_text}; }}
QLabel[role="status"][state="bad"] {{ color: {warn}; }}
QLabel[role="space"] {{ background: transparent; color: {txt_2}; padding: 2px 6px; }}

/* Dialogs. The one in front of a delete has to be readable at a glance, so
   what happens to the files is coloured and the list of them is not. */
QDialog QLabel {{ background: transparent; color: {txt_0}; }}
QLabel[role="note"] {{ color: {txt_1}; }}
QLabel[role="warn"] {{ color: {warn}; }}
QListWidget {{
    background: {bg_1};
    border: 1px solid {line};
    border-radius: {radius_sm};
    color: {txt_1};
    padding: 2px;
}}
QListWidget::item {{ padding: 2px 4px; }}
QDialogButtonBox QPushButton {{ min-width: 92px; }}

/* The transfer readout in the status bar. It is only there while something is
   running, so it is allowed to use the accent. */
QLabel[role="transfer"] {{ background: transparent; color: {txt_1}; }}
QToolButton[role="status"] {{
    min-height: 16px;
    max-height: 18px;
    padding: 0px 8px;
    color: {txt_1};
}}
QToolButton[role="status"]:hover {{ color: {txt_0}; }}

/* A checkbox with no indicator styling draws the platform's own, which on a
   dark sheet is a box that cannot be seen. Filled with the accent when it is
   on, because the one that exists is the one that changes what a click does to
   the rest of a transfer. */
QCheckBox {{ background: transparent; color: {txt_0}; spacing: 8px; }}
QCheckBox::indicator {{
    width: 14px;
    height: 14px;
    border: 1px solid {line};
    border-radius: 3px;
    background: {bg_2};
}}
QCheckBox::indicator:hover {{ border: 1px solid {accent_line}; }}
QCheckBox::indicator:checked {{ background: {accent}; border: 1px solid {accent}; }}
QProgressBar {{
    background: {bg_2};
    border: 1px solid {line};
    border-radius: {radius_sm};
    height: 10px;
    text-align: center;
}}
QProgressBar::chunk {{ background: {accent}; border-radius: {radius_sm}; }}

QSplitter::handle {{ background: {bg_0}; }}
QSplitter::handle:horizontal {{ width: 6px; }}
QSplitter::handle:hover {{ background: {accent_soft}; }}

QScrollBar:vertical {{ background: {bg_1}; width: 12px; margin: 0; }}
QScrollBar:horizontal {{ background: {bg_1}; height: 12px; margin: 0; }}
QScrollBar::handle {{ background: {bg_4}; border-radius: {radius_sm}; min-height: 24px; }}
QScrollBar::handle:hover {{ background: {accent_dim}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
"""


def metrics(density: str | None = None) -> dict[str, int]:
    """Density numbers a widget has to apply itself, as ints."""
    values = DENSITIES.get(density or DEFAULTS["density"], DENSITIES[DEFAULTS["density"]])
    return {key: int(value) for key, value in values.items()}


def apply(
    app: Any,
    *,
    theme: str | None = None,
    accent: str | None = None,
    density: str | None = None,
) -> dict[str, str]:
    """Render the sheet for one combination and put it on the application.

    Returns the tokens, because a caller that has to set a metric itself needs
    to know which density it actually got — an unknown name falls back rather
    than raising, so what was asked for and what was applied can differ.
    """
    from PySide6.QtGui import QColor, QPalette

    tokens = qss.build(theme, accent, density)
    app.setStyleSheet(qss.render(TEMPLATE, tokens))

    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(tokens["bg_0"]))
    palette.setColor(QPalette.WindowText, QColor(tokens["txt_0"]))
    palette.setColor(QPalette.Base, QColor(tokens["bg_2"]))
    palette.setColor(QPalette.AlternateBase, QColor(tokens["bg_1"]))
    palette.setColor(QPalette.Text, QColor(tokens["txt_0"]))
    palette.setColor(QPalette.Button, QColor(tokens["bg_2"]))
    palette.setColor(QPalette.ButtonText, QColor(tokens["txt_0"]))
    palette.setColor(QPalette.Highlight, QColor(tokens["accent_row"]))
    palette.setColor(QPalette.HighlightedText, QColor(tokens["txt_0"]))
    palette.setColor(QPalette.ToolTipBase, QColor(tokens["bg_2"]))
    palette.setColor(QPalette.ToolTipText, QColor(tokens["txt_0"]))
    palette.setColor(QPalette.PlaceholderText, QColor(tokens["txt_2"]))
    app.setPalette(palette)
    return tokens

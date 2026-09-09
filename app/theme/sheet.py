"""The QSS template and the one function that applies it.

The template is the whole stylesheet. It is written once, with `{token}`
placeholders, and rendered by substitution -- which is why there is not a
colour literal in it and must never be. Braces are doubled because the render
is a `str.format`.

Three things QSS cannot do are done elsewhere instead:

  * **Row height.** `QTableView` takes it from the vertical header's default
    section size, not from a style rule, so `metrics()` hands the density's
    number to the view.
  * **Selection colour.** Qt draws selection from the platform palette, not the
    sheet, so the palette is set explicitly. Without this the Windows blue
    appears in a themed window, which is the single most obvious way a port of
    a design system looks half done.
  * **Anything rounded or measured inside a row** -- the selected row's
    corners, the size bar, the age chip. `QTableView::item` takes no radius and
    QSS has no way to draw a second shape in a cell, so `app/ui/rows.py` paints
    those from the same tokens this sheet is rendered from.

On the weight of borders, since it is the thing this sheet is mostly about: a
line is spent where it separates two things that behave differently, and
nowhere else. The pane has one because the two panes take keystrokes
separately. The listing header has one under it because the rows below it
scroll and it does not. A button does not, because a button that is only ever
inside a pane is not a separate surface -- it is a target, and a target says so
by lighting up under the mouse. Every border removed here was removed on that
test, not for being square.
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
    border-radius: {radius_sm};
    padding: 4px 7px;
}}

/* ------------------------------------------------------------------ chrome */

QToolBar {{
    background: {bg_1};
    border: none;
    padding: 3px 6px;
    spacing: 4px;
    min-height: {toolbar_h};
}}
QMenuBar {{ background: {bg_1}; border: none; padding: 3px 4px; }}
QMenuBar::item {{
    padding: 4px 11px;
    background: transparent;
    border-radius: {radius};
}}
QMenuBar::item:selected {{ background: {accent_soft}; color: {accent_text}; }}
QMenu {{
    background: {bg_2};
    border: 1px solid {line};
    border-radius: {radius};
    padding: 5px;
}}
QMenu::item {{ padding: 5px 22px 5px 22px; border-radius: {radius_sm}; }}
QMenu::item:selected {{ background: {accent_soft}; color: {accent_text}; }}
QMenu::separator {{ height: 1px; background: {line_soft}; margin: 4px 8px; }}
QStatusBar {{
    background: {bg_1};
    border: none;
    color: {txt_2};
    min-height: {status_h};
}}
QStatusBar::item {{ border: none; }}

/* ----------------------------------------------------------------- buttons */

/* The bordered button is now the dialog button and nothing else. Inside a
   pane a control gets `role="nav"` below and no border at all. */
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

/* Back, forward, up, refresh, filter. Square, borderless, and lit by the
   mouse rather than outlined at rest. Four buttons in a row were four boxes;
   they are now four icons with somewhere to land. */
QToolButton[role="nav"] {{
    background: transparent;
    border: none;
    border-radius: {radius};
    color: {txt_1};
    padding: 0px;
    min-width: {button_h};
    max-width: {button_h};
    min-height: {button_h};
    max-height: {button_h};
}}
QToolButton[role="nav"]:hover {{ background: {bg_3}; color: {txt_0}; }}
QToolButton[role="nav"]:pressed {{ background: {bg_4}; }}
QToolButton[role="nav"]:disabled {{ color: {txt_2}; background: transparent; }}
QToolButton[role="nav"]:focus {{ background: {bg_3}; }}
QToolButton[role="nav"][state="on"] {{ background: {accent_soft}; color: {accent_text}; }}

/* ------------------------------------------------------------- the path bar */

/* Two widgets share this slot: the breadcrumb, which is what is normally
   there, and this field, which Ctrl+L swaps in. They are drawn the same so the
   swap is a change of contents rather than a change of furniture. */
QLineEdit {{
    background: {bg_1};
    border: 1px solid {line_soft};
    border-radius: {radius};
    color: {txt_0};
    padding: 3px 8px;
    min-height: {button_h};
    selection-background-color: {accent};
    selection-color: {bg_0};
}}
QLineEdit:focus {{ border: 1px solid {accent_line}; background: {bg_1}; }}
QLineEdit[state="stale"] {{ color: {txt_2}; }}

QWidget[role="crumbbar"] {{
    background: {bg_1};
    border: 1px solid {line_soft};
    border-radius: {radius};
}}
QWidget[role="crumbbar"][state="hot"] {{ border: 1px solid {accent_line}; }}
/* A segment. The whole bar is a path, so the segments are not separate
   objects and are not drawn as any -- until the mouse is on one, which is the
   moment it becomes a place to go. */
QToolButton[role="crumb"] {{
    background: transparent;
    border: none;
    border-radius: {radius_sm};
    color: {txt_1};
    padding: 2px 7px;
    min-height: 20px;
}}
QToolButton[role="crumb"]:hover {{ background: {bg_3}; color: {txt_0}; }}
QToolButton[role="crumb"]:pressed {{ background: {bg_4}; }}
QToolButton[role="crumb"][state="last"] {{ color: {txt_0}; font-weight: 600; }}
QToolButton[role="crumb"][state="root"] {{ font-family: {mono}; font-weight: 600; }}
QLabel[role="crumbsep"] {{
    background: transparent;
    color: {txt_2};
    padding: 0px 1px;
}}
/* The rest of the bar. Clicking the empty space edits the path, which is why
   it is a button and not a spacer. */
QToolButton[role="crumbrest"] {{
    background: transparent;
    border: none;
    min-height: 20px;
}}

/* The filter. Marked with the accent down its left edge, because a listing
   that is hiding rows has to say so from across the room. */
QLineEdit[role="filter"] {{
    background: {bg_1};
    border: 1px solid {line_soft};
    border-left: 3px solid {accent};
}}

/* The drive picker. A quiet chip rather than a combo box: it carries one or
   two characters and the platform's own arrow is drawn from the platform's own
   palette, which on a dark sheet is a dark arrow on dark chrome. */
QComboBox {{
    background: {bg_3};
    border: 1px solid transparent;
    border-radius: {radius};
    color: {txt_0};
    font-weight: 600;
    padding: 3px 9px;
    min-height: {button_h};
    min-width: 44px;
    max-width: 170px;
}}
QComboBox:hover {{ background: {bg_4}; }}
QComboBox:on {{ border: 1px solid {accent_line}; }}
QComboBox::drop-down {{ width: 0px; border: none; }}
QComboBox QAbstractItemView {{
    background: {bg_2};
    border: 1px solid {line};
    border-radius: {radius_sm};
    color: {txt_0};
    outline: none;
    padding: 4px;
    selection-background-color: {accent_soft};
    selection-color: {accent_text};
}}

/* -------------------------------------------------------------------- tabs */

/* No trapezoids and no boxes. A tab is a pill that fills in when it is the
   one in front, with the accent as a rule under it -- which is the one place
   the eye goes back to after looking at a listing. */
QTabBar {{ background: transparent; qproperty-drawBase: 0; }}
QTabBar::tab {{
    background: transparent;
    color: {txt_2};
    border: none;
    border-bottom: 2px solid transparent;
    border-radius: {radius};
    padding: 3px 10px;
    min-height: {tab_h};
    margin-right: 2px;
}}
QTabBar::tab:hover {{ background: {bg_3}; color: {txt_1}; }}
QTabBar::tab:selected {{
    background: {bg_3};
    color: {txt_0};
    border-bottom: 2px solid {accent};
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
QToolButton[role="tabclose"]:hover {{
    color: {txt_0};
    background: {bg_4};
    border-radius: 3px;
}}

/* The favorites bar. Quieter than the chrome around it on purpose: it is a row
   of places that is always on screen, and a row of places that draws like a
   toolbar competes with the listing it sits above. The accent arrives on
   hover, which is where a target the mouse is on wants it. */
QToolButton[role="favorite"] {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: {radius};
    color: {txt_1};
    padding: 1px 10px;
    min-height: 21px;
}}
QToolButton[role="favorite"]:hover {{
    background: {accent_soft};
    border: 1px solid {accent_line};
    color: {txt_0};
}}
QToolButton[role="favorite"]:pressed {{ background: {bg_4}; }}

/* ----------------------------------------------------------------- listing */

/* Everything above exists to frame this, so this is where the borders are
   not. No grid, no stripes, no outline -- the pane is already a container and
   drawing a second one inside it is the box grid this sheet is getting rid of.
   The one line kept is under the header, which separates a thing that scrolls
   from a thing that does not.

   Selection is painted in `app/ui/rows.py`, not here: a rounded row is not
   something `QTableView::item` can be asked for. */
QTableView {{
    background: {bg_2};
    alternate-background-color: {bg_2};
    color: {txt_0};
    border: none;
    gridline-color: transparent;
    outline: none;
    selection-background-color: {accent_row};
    selection-color: {txt_0};
}}
QTableView::item {{ padding: 0px 6px; border: none; }}
QTableView::item:focus {{ border: none; }}
QHeaderView {{ background: {bg_2}; }}
QHeaderView::section {{
    background: {bg_2};
    color: {txt_2};
    border: none;
    border-bottom: 1px solid {line_soft};
    padding: 5px 7px;
    font-size: {head_font};
    font-weight: 600;
}}
QHeaderView::section:hover {{ color: {txt_0}; }}
QHeaderView::up-arrow, QHeaderView::down-arrow {{ width: 0px; height: 0px; }}

/* ------------------------------------------------------------- the pane */

/* The active pane is the one the accent is on -- with two panes and one
   keyboard there is no other way to know where a keystroke lands. It is a bar
   down the left edge rather than a rectangle around everything, because a
   rectangle competes with the listing inside it and a bar does not. Both
   states declare the same widths so nothing shifts by a pixel when focus
   moves. */
QFrame[pane="true"] {{
    background: {bg_2};
    border: 1px solid {line_soft};
    border-left: 2px solid {line_soft};
    border-radius: {radius_lg};
}}
QFrame[pane="true"][active="true"] {{
    border: 1px solid {accent_line};
    border-left: 2px solid {accent};
}}
QLabel[role="status"] {{ background: transparent; color: {txt_1}; padding: 2px 6px; }}
QLabel[role="status"][state="busy"] {{ color: {accent_text}; }}
QLabel[role="status"][state="bad"] {{ color: {warn}; }}
QLabel[role="space"] {{ background: transparent; color: {txt_2}; padding: 2px 6px; }}

/* ----------------------------------------------------------------- dialogs */

QDialog QLabel {{ background: transparent; color: {txt_0}; }}
QLabel[role="note"] {{ color: {txt_1}; }}
QLabel[role="warn"] {{ color: {warn}; }}
QListWidget {{
    background: {bg_1};
    border: 1px solid {line_soft};
    border-radius: {radius_sm};
    color: {txt_1};
    padding: 3px;
}}
QListWidget::item {{ padding: 3px 5px; border-radius: {radius_sm}; }}
QListWidget::item:selected {{ background: {accent_soft}; color: {accent_text}; }}
QDialogButtonBox QPushButton {{ min-width: 92px; }}

QLabel[role="transfer"] {{ background: transparent; color: {txt_1}; }}
QToolButton[role="status"] {{
    background: transparent;
    border: none;
    min-height: 16px;
    max-height: 18px;
    padding: 0px 8px;
    color: {txt_1};
}}
QToolButton[role="status"]:hover {{ color: {txt_0}; background: {bg_3}; }}

QCheckBox {{ background: transparent; color: {txt_0}; spacing: 8px; }}
QCheckBox::indicator {{
    width: 14px;
    height: 14px;
    border: 1px solid {line};
    border-radius: 4px;
    background: {bg_2};
}}
QCheckBox::indicator:hover {{ border: 1px solid {accent_line}; }}
QCheckBox::indicator:checked {{ background: {accent}; border: 1px solid {accent}; }}
QProgressBar {{
    background: {bg_1};
    border: none;
    border-radius: 4px;
    height: 6px;
    text-align: center;
}}
QProgressBar::chunk {{ background: {accent}; border-radius: 4px; }}

/* ------------------------------------------------------- splitter, scrollbars */

QSplitter::handle {{ background: {bg_0}; }}
QSplitter::handle:horizontal {{ width: 8px; }}
QSplitter::handle:hover {{ background: {accent_soft}; }}

/* Thin, and no trough. A scrollbar is a position readout most of the time and
   a target only occasionally, so it is drawn as the former and grows into the
   latter under the mouse. */
QScrollBar:vertical {{
    background: transparent;
    width: 11px;
    margin: 2px 2px 2px 0px;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 11px;
    margin: 0px 2px 2px 2px;
}}
QScrollBar::handle {{
    background: {bg_4};
    border-radius: 4px;
    min-height: 28px;
    min-width: 28px;
}}
QScrollBar::handle:hover {{ background: {accent_dim}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
"""


def metrics(density: str | None = None) -> dict[str, int]:
    """Density numbers a widget has to apply itself, as ints."""
    values = DENSITIES.get(density or DEFAULTS["density"], DENSITIES[DEFAULTS["density"]])
    return {key: int(value) for key, value in values.items()}


def tokens(
    theme: str | None = None,
    accent: str | None = None,
    density: str | None = None,
) -> dict[str, str]:
    """The rendered token set, for a caller that paints rather than styles.

    `app/ui/rows.py` needs the same greens and greys the sheet was built from,
    and the alternative -- letting it read the theme dictionaries itself --
    would be a second place that knows how a tint is derived.
    """
    return qss.build(theme, accent, density)


def apply(
    app: Any,
    *,
    theme: str | None = None,
    accent: str | None = None,
    density: str | None = None,
) -> dict[str, str]:
    """Render the sheet for one combination and put it on the application.

    Returns the tokens, because a caller that has to set a metric or paint a
    cell itself needs to know which density it actually got -- an unknown name
    falls back rather than raising, so what was asked for and what was applied
    can differ.
    """
    from PySide6.QtGui import QColor, QPalette

    values = qss.build(theme, accent, density)
    app.setStyleSheet(qss.render(TEMPLATE, values))

    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(values["bg_0"]))
    palette.setColor(QPalette.WindowText, QColor(values["txt_0"]))
    palette.setColor(QPalette.Base, QColor(values["bg_2"]))
    palette.setColor(QPalette.AlternateBase, QColor(values["bg_2"]))
    palette.setColor(QPalette.Text, QColor(values["txt_0"]))
    palette.setColor(QPalette.Button, QColor(values["bg_2"]))
    palette.setColor(QPalette.ButtonText, QColor(values["txt_0"]))
    palette.setColor(QPalette.Highlight, QColor(values["accent_row"]))
    palette.setColor(QPalette.HighlightedText, QColor(values["txt_0"]))
    palette.setColor(QPalette.ToolTipBase, QColor(values["bg_2"]))
    palette.setColor(QPalette.ToolTipText, QColor(values["txt_0"]))
    palette.setColor(QPalette.PlaceholderText, QColor(values["txt_2"]))
    app.setPalette(palette)
    return values

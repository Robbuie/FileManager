"""The design system, ported from Redline PDF.

Three independent axes — theme, accent, density — kept independent on purpose.
A theme sets the greys, the accent sets one colour, the density sets the chrome
metrics. Anything that mixes them multiplies the combinations and half of them
look wrong.

QSS has no custom properties and no `color-mix`, so the substitute is here:
`tokens.py` holds the values, `qss.py` derives every tint in Python and renders
a stylesheet by substitution. Changing any axis re-renders and re-applies the
whole sheet; there is no partial update path.

**No literal colour belongs anywhere outside this package.** A hardcoded grey
in a widget is a spot that stops following the theme, which reads to the user
as a picker that half works.
"""

from app.theme.tokens import ACCENTS, DENSITIES, DEFAULTS, THEMES  # noqa: F401

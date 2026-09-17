"""The chrome's icons, drawn rather than loaded.

The listing's icons are the real Windows shell icons and always will be -- a
file's picture is a fact about that file and Windows owns it. The chrome is a
different question. Back, forward, up and refresh are this application's own,
they have to follow the theme through five sets of greys, and they have to
match at 100, 125 and 150 per cent. A bitmap does none of that; the arrows
they replace were text glyphs, which follow the colour but are drawn by
whichever font answered and land at whatever weight and size it felt like.

So they are painted here, from a handful of coordinates on a 24 by 24 grid,
stroked at one weight and tinted with a token. That buys three things: they
match each other, they follow the theme picker, and they add no dependency --
no QtSvg, no resource file, nothing to put in the installer.

Cached on everything that changes the picture, colour and device ratio
included, because a toolbar asks for the same icon on every repolish.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

#: Each icon as strokes on a 24 by 24 grid. A stroke is a run of points; an
#: arc is `("arc", x, y, w, h, start, span)` in the degrees `QPainterPath`
#: uses. Nothing is filled -- one weight everywhere is most of why a set of
#: icons reads as a set.
STROKES: dict[str, tuple] = {
    "back":    (((19, 12), (5, 12)), ((11, 18), (5, 12), (11, 6))),
    "forward": (((5, 12), (19, 12)), ((13, 6), (19, 12), (13, 18))),
    "up":      (((12, 19), (12, 5)), ((6, 11), (12, 5), (18, 11))),
    "refresh": (("arc", 4.5, 4.5, 15, 15, 90, -290), ((4, 4), (4, 10), (10, 10))),
    "filter":  (((3.5, 5), (20.5, 5), (14, 13), (14, 19.5), (10, 21), (10, 13),
                 (3.5, 5)),),
    "close":   (((6, 6), (18, 18)), ((18, 6), (6, 18))),
    # 0.24: the tab strip, the rail and the header.
    "folder":  (((3, 7), (3, 19), (21, 19), (21, 9.5), (11.5, 9.5), (9.5, 6.5),
                 (3.5, 6.5), (3, 7)),),
    "drive":   (((3, 8), (21, 8), (21, 16), (3, 16), (3, 8)),
                ((16.5, 12), (17.5, 12))),
    "network": (((4, 4), (20, 4), (20, 14), (4, 14), (4, 4)),
                ((12, 14), (12, 18)), ((7, 20), (17, 20))),
    "star":    (((12, 3), (14.6, 8.6), (20.5, 9.3), (16.1, 13.4), (17.3, 19.3),
                 (12, 16.4), (6.7, 19.3), (7.9, 13.4), (3.5, 9.3), (9.4, 8.6),
                 (12, 3)),),
    "place":   (((4, 11), (12, 4), (20, 11), (20, 20), (4, 20), (4, 11)),),
    "sort_up":   (((7, 14.5), (12, 9.5), (17, 14.5)),),
    "sort_down": (((7, 9.5), (12, 14.5), (17, 9.5)),),
    # 0.26: the title bar. The caption buttons are drawn thinner by being
    # asked for smaller, so they sit with Windows' own rather than with the
    # heavier pane chrome.
    "minimize": (((6, 12), (18, 12)),),
    "maximize": (((6.5, 6.5), (17.5, 6.5), (17.5, 17.5), (6.5, 17.5), (6.5, 6.5)),),
    "restore":  (((6.5, 9), (15, 9), (15, 17.5), (6.5, 17.5), (6.5, 9)),
                 ((9, 9), (9, 6.5), (17.5, 6.5), (17.5, 15), (15, 15))),
    "mark":     (((5, 7), (12, 7)), ((5, 12), (19, 12)), ((12, 17), (19, 17))),
    "search":   (("arc", 4, 4, 13, 13, 0, 360), ((15.2, 15.2), (20, 20))),
    "eject":    (((5, 14), (12, 6), (19, 14), (5, 14)), ((5, 18.5), (19, 18.5))),
}

#: Stroke width on the 24-unit grid. 1.9 rather than 2 because at a 16 pixel
#: icon on a 100 per cent display 2 lands a shade heavier than the 13 pixel
#: text beside it, and chrome that is bolder than its labels reads as clutter.
WEIGHT = 1.9

_cache: dict[tuple, QIcon] = {}


def _pixmap(name: str, size: int, colour: QColor, ratio: float) -> QPixmap:
    pixmap = QPixmap(int(size * ratio), int(size * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    scale = size / 24.0
    pen = QPen(colour)
    pen.setWidthF(WEIGHT * scale)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)

    for stroke in STROKES[name]:
        path = QPainterPath()
        if stroke and stroke[0] == "arc":
            _, x, y, w, h, start, span = stroke
            box = QRectF(x * scale, y * scale, w * scale, h * scale)
            path.arcMoveTo(box, start)
            path.arcTo(box, start, span)
        else:
            path.moveTo(QPointF(stroke[0][0] * scale, stroke[0][1] * scale))
            for x, y in stroke[1:]:
                path.lineTo(QPointF(x * scale, y * scale))
        painter.drawPath(path)
    painter.end()
    return pixmap


def icon(name: str, *, colour: str, muted: str, size: int = 16,
         ratio: float = 1.0) -> QIcon:
    """One chrome icon, in the theme's text colour and its muted one.

    Both modes are given rather than left to Qt, which makes a disabled icon by
    fading the normal one -- and a faded tint of a grey is not the grey the
    rest of the disabled chrome is using. Forward with no history should look
    like every other unavailable thing in the window.
    """
    key = (name, size, colour, muted, round(ratio, 2))
    hit = _cache.get(key)
    if hit is not None:
        return hit

    built = QIcon()
    built.addPixmap(_pixmap(name, size, QColor(colour), ratio), QIcon.Normal)
    built.addPixmap(_pixmap(name, size, QColor(muted), ratio), QIcon.Disabled)
    _cache[key] = built
    return built


def forget() -> None:
    """Drop the cache. For a test that wants a clean count, not for the app --
    a theme change makes new keys rather than invalidating old ones."""
    _cache.clear()

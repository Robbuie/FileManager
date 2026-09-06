"""Draws the application icon. Build-time only; nothing here ships.

The three applications share a mark as well as a stylesheet: a dark rounded
tile, one light shape on it, one stroke of the accent. Redline PDF is a sheet
of paper with a red line through it. This is two panes with the active one
carrying the Drafting blue, which is the same idea said about this application.

Pillow rather than Qt on purpose -- the icon is needed by the installer before
anything Qt-shaped has been built, and a build step that has to import the
application to draw a picture is a build step that fails for reasons that have
nothing to do with the picture.

    pip install -r packaging/requirements-build.txt
    python packaging/icon.py

Writes `packaging/icon.ico` (the sizes Windows asks for) and
`assets/icon.png` (256px, for the README and anywhere else a picture is
wanted). Both are committed: a checkout must be able to build an installer
without Pillow.
"""

from __future__ import annotations

import os

from PIL import Image, ImageDraw

#: Everything is drawn at this size and reduced, which is what gives the
#: corners and the thin rows clean edges. Pillow has no antialiased shape
#: drawing; it has a very good resampler.
CANVAS = 1024
SCALE = CANVAS // 256

#: Straight from `app/theme/tokens.py`. The tile is the dark theme's chrome,
#: the panes are the light theme's raised surface, and the accent is Drafting
#: blue -- the same triple the application defaults to.
TILE_TOP = (32, 36, 44)
TILE_BOTTOM = (16, 18, 22)
PANE = (233, 237, 244)
PANE_DIM = (203, 210, 221)
ROW = (168, 176, 189)
ACCENT = (74, 145, 255)

#: The sizes Windows actually asks for. 16 is the one that decides whether the
#: mark works: anything that needs three colours to be legible is wrong.
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


def _px(value: float) -> int:
    return int(round(value * SCALE))


def draw() -> Image.Image:
    image = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    canvas = ImageDraw.Draw(image)

    # The tile. A vertical gradient rather than a flat fill, because the other
    # two icons have one and a flat tile beside them reads as a different set.
    gradient = Image.new("RGB", (1, CANVAS))
    for y in range(CANVAS):
        ratio = y / (CANVAS - 1)
        gradient.putpixel((0, y), tuple(
            int(round(top + (bottom - top) * ratio))
            for top, bottom in zip(TILE_TOP, TILE_BOTTOM)
        ))
    gradient = gradient.resize((CANVAS, CANVAS))

    mask = Image.new("L", (CANVAS, CANVAS), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, CANVAS - 1, CANVAS - 1), radius=_px(58), fill=255,
    )
    image.paste(gradient, (0, 0), mask)

    # The two panes. Inset far enough that the tile still reads as a tile at
    # 16px, and split with a gap rather than a line -- a one-pixel divider is
    # the first thing to disappear when this is scaled down.
    inset = _px(38)
    top = _px(52)
    bottom = CANVAS - _px(52)
    gap = _px(14)
    middle = CANVAS // 2
    radius = _px(12)
    left = (inset, top, middle - gap // 2, bottom)
    right = (middle + gap // 2, top, CANVAS - inset, bottom)

    canvas.rounded_rectangle(left, radius=radius, fill=PANE)
    canvas.rounded_rectangle(right, radius=radius, fill=PANE_DIM)

    # The accent: the active pane's header. One stroke, one colour, and the
    # thing that says which side has the focus -- which is the whole argument
    # for a dual-pane file manager in one shape.
    header = top + _px(30)
    canvas.rounded_rectangle((left[0], top, left[2], header),
                             radius=radius, fill=ACCENT)
    canvas.rectangle((left[0], header - radius, left[2], header), fill=ACCENT)

    # Rows. Four on each side, the same weight as the lines on the Redline PDF
    # sheet, and deliberately not aligned across the gap: two panes showing the
    # same folder is not what this is for.
    _rows(canvas, left, start=header + _px(24), count=4)
    _rows(canvas, right, start=top + _px(20), count=5)
    return image


def _rows(canvas: ImageDraw.ImageDraw, box, *, start: int, count: int) -> None:
    x0, _, x1, y1 = box
    pad = _px(12)
    step = _px(24)
    height = _px(7)
    for index in range(count):
        y = start + index * step
        if y + height > y1 - pad:
            return
        # The last row of each pane is short, the way a listing ends.
        width = (x1 - x0 - pad * 2) * (0.55 if index == count - 1 else 1.0)
        canvas.rounded_rectangle(
            (x0 + pad, y, x0 + pad + width, y + height),
            radius=height // 2, fill=ROW,
        )


def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    master = draw()

    ico = os.path.join(here, "icon.ico")
    master.resize((256, 256), Image.LANCZOS).save(
        ico, format="ICO", sizes=[(size, size) for size in ICO_SIZES],
    )

    png = os.path.join(root, "assets", "icon.png")
    os.makedirs(os.path.dirname(png), exist_ok=True)
    master.resize((256, 256), Image.LANCZOS).save(png, format="PNG")

    print(f"wrote {ico}")
    print(f"wrote {png}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

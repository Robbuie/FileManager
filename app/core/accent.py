"""Where the accent comes from when it is not one of the six named ones (0.35).

Three sources, chosen in Options: a named accent (every version before this),
the accent Windows itself is using, or a colour picked out of the desktop
wallpaper. The last two produce a channel triple that `qss.build` takes in
place of a name, and from there every tint is derived exactly as it is for a
named accent -- the design system's one-colour rule is not bent, only the
source of the one colour.

Two cautions shaped this module.

  * **The wallpaper is a file, so reading it is the worker's job.** Its path
    comes out of the registry here; the picture itself is asked for through
    `Op.PREVIEW`, the same decoder the preview pane uses, at a size small
    enough that the answer is a few kilobytes. The window never opens it.
  * **A colour somebody did not choose has to stay readable.** A wallpaper's
    most colourful patch can be nearly black or nearly white. `readable`
    moves the lightness -- never the hue -- until text on the accent and the
    accent on the theme's backdrop both have contrast to spare, so whatever
    the desktop is, the selection still reads as a selection.

The registry reads are local and immediate, and off Windows they answer None,
which the window treats as "use the named accent". That is also what the tests
exercise.
"""

from __future__ import annotations

import colorsys
import os

from PySide6.QtCore import QObject, Signal

from app.io.protocol import Op, Preview, PreviewForm, Reply, Status

RGB = tuple[int, int, int]

SOURCES = ("named", "windows", "wallpaper")

#: How large a picture of the wallpaper is asked for. The longest edge, in
#: pixels: a colour survives being made this small, and the answer stays a
#: few kilobytes of PNG however large the wallpaper is.
WALLPAPER_BOX = 64


def windows_accent() -> RGB | None:
    """The accent colour Windows is drawing with, or None.

    `AccentColor` under DWM is a DWORD in ABGR order -- red in the low byte --
    which is the detail every first attempt gets backwards.
    """
    try:
        import winreg  # noqa: PLC0415 - Windows only
    except ImportError:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\DWM") as key:
            value, _kind = winreg.QueryValueEx(key, "AccentColor")
    except OSError:
        return None
    value = int(value) & 0xFFFFFFFF
    return (value & 0xFF, (value >> 8) & 0xFF, (value >> 16) & 0xFF)


def wallpaper_path() -> str | None:
    """Where the desktop picture is, according to the registry, or None.

    `TranscodedWallpaper` is where Windows keeps its own copy of whatever was
    chosen, and it is what is on screen even when the original has since been
    moved or deleted -- so it is preferred when the registry's path is empty.
    Neither is checked for existence here: that would be a file call, and the
    worker that reads it reports a missing file as an ordinary failure.
    """
    try:
        import winreg  # noqa: PLC0415 - Windows only
    except ImportError:
        return None
    chosen = ""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop") as key:
            chosen, _kind = winreg.QueryValueEx(key, "WallPaper")
    except OSError:
        chosen = ""
    appdata = os.environ.get("APPDATA", "")
    transcoded = (os.path.join(appdata, "Microsoft", "Windows", "Themes",
                               "TranscodedWallpaper") if appdata else "")
    return transcoded or (str(chosen) if chosen else None)


def choose(pixels: list[RGB]) -> RGB | None:
    """The colour a person would name if asked what colour the picture is.

    Not the average -- the average of most photographs is brown -- but the
    most common *colourful* hue: every pixel votes for one of 24 hue buckets
    with a weight of its saturation times its brightness, so a grey sky and a
    black frame vote for nothing and a strip of orange sunset votes loudly.
    The winner is the weighted mean of the pixels in that bucket. None when
    the picture has no colour worth the name (a greyscale wallpaper), which
    the window answers with the named accent.
    """
    buckets: dict[int, list[float]] = {}
    for r, g, b in pixels:
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        weight = s * v
        if s < 0.18 or v < 0.15:
            continue
        slot = buckets.setdefault(int(h * 24) % 24, [0.0, 0.0, 0.0, 0.0])
        slot[0] += weight
        slot[1] += r * weight
        slot[2] += g * weight
        slot[3] += b * weight
    if not buckets:
        return None
    best = max(buckets.values(), key=lambda slot: slot[0])
    if best[0] <= 0:
        return None
    return (round(best[1] / best[0]), round(best[2] / best[0]), round(best[3] / best[0]))


def _luminance(colour: RGB) -> float:
    def channel(c: int) -> float:
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = colour
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a: RGB, b: RGB) -> float:
    """WCAG contrast ratio between two colours."""
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def readable(colour: RGB, backdrop: RGB, *, against: float = 3.0) -> RGB:
    """`colour` with its lightness moved until it stands off `backdrop`.

    Only lightness moves, in small steps, toward whichever end the backdrop is
    not at; hue stays, and saturation is lifted a little if the picture was
    washed out, because a pastel accent on a light theme is invisible. Gives
    up after a few dozen steps and returns the best it found -- a colour, not
    an exception, is what the sheet needs.
    """
    h, l, s = colorsys.rgb_to_hls(*(c / 255 for c in colour))
    s = max(s, 0.45)
    dark_ground = _luminance(backdrop) < 0.3
    step = 0.02 if dark_ground else -0.02
    best = colour
    for _ in range(60):
        candidate = tuple(round(c * 255) for c in colorsys.hls_to_rgb(h, l, s))
        best = candidate  # type: ignore[assignment]
        if contrast(best, backdrop) >= against:
            break
        l = max(0.05, min(0.9, l + step))
    return best  # type: ignore[return-value]


class AccentSource(QObject):
    """Asks for the colour a source gives, and says what it found.

    `found(rgb, why)` carries a triple, or None with a reason, which the window
    answers with the named accent. A named source never emits: there is
    nothing to find.
    """

    found = Signal(object, str)

    def __init__(self, bridge, config, parent=None) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._config = config
        self._request_id: int | None = None

    def resolve(self, backdrop: RGB) -> None:
        """Work out the accent for the current setting, now or when it arrives."""
        self.cancel()
        source = str(self._config.get("accent.source"))
        if source == "windows":
            colour = windows_accent()
            if colour is None:
                self.found.emit(None, "Windows did not say what its accent is")
            else:
                self.found.emit(readable(colour, backdrop), "")
            return
        if source != "wallpaper":
            return
        path = wallpaper_path()
        if not path or self._bridge is None:
            self.found.emit(None, "no wallpaper to take a colour from")
            return
        self._request_id = self._bridge.submit(
            Op.PREVIEW, path, timeout=float(self._config.get("timeout.preview")),
            on_reply=lambda reply: self._on_picture(reply, backdrop),
            args={"box": WALLPAPER_BOX, "text_bytes": 0, "shell": False})

    def cancel(self) -> None:
        if self._request_id is not None and self._bridge is not None:
            self._bridge.cancel(self._request_id)
            self._bridge.forget(self._request_id)
        self._request_id = None

    def _on_picture(self, reply: Reply, backdrop: RGB) -> None:
        self._request_id = None
        picture = reply.payload
        if reply.status is not Status.OK or not isinstance(picture, Preview) \
                or picture.form is not PreviewForm.IMAGE or not picture.image:
            self.found.emit(None, "the wallpaper could not be read")
            return
        colour = choose(_pixels(picture.image))
        if colour is None:
            self.found.emit(None, "the wallpaper has no colour to take")
            return
        self.found.emit(readable(colour, backdrop), "")


def _pixels(png: bytes) -> list[RGB]:
    """The pixels of a small PNG, from bytes already in memory."""
    from PySide6.QtGui import QImage  # noqa: PLC0415

    image = QImage.fromData(png)
    if image.isNull():
        return []
    image = image.convertToFormat(QImage.Format_RGB32)
    out: list[RGB] = []
    for y in range(image.height()):
        for x in range(image.width()):
            value = image.pixel(x, y)
            out.append(((value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF))
    return out

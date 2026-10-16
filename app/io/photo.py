"""What a photograph says about itself (0.54): EXIF and a brightness histogram.

Read in the worker, beside the decode, for the preview surfaces only -- the
preview panel, the peek card, quick view and the viewer -- and never for a
grid cell, which has nowhere to put a sentence. Two reads, both small:

  * **EXIF, through Pillow.** Pillow is already in the installer as pi-heif's
    dependency, it reads the tags out of a JPEG, a TIFF, a PNG, a WebP and
    (with the plugin registered) a HEIC, and `Image.open` reads the header
    only: the pixels are never decoded here. A file with no tags, or a kind
    Pillow cannot open, is simply a picture with no details.
  * **A histogram of the picture already decoded** -- the scaled PNG that is
    about to cross the process boundary anyway, shrunk again to a few hundred
    pixels, so it costs a few milliseconds and never touches the file.

The location is shown as numbers and nothing else. Turning it into a place
name would be a network call, and this application makes one, to its own
release feed (see Scope in CLAUDE.md).
"""

from __future__ import annotations

import datetime
from fractions import Fraction
from io import BytesIO
from typing import Any

from app.io import paths

#: Bars in the histogram. Enough to see clipping at either end and the shape
#: of the middle; more would be noise at the width a panel draws them.
BINS = 32

_EXIF_IFD = 0x8769
_GPS_IFD = 0x8825
_TAGS = {
    "make": 0x010F, "model": 0x0110, "software": 0x0131, "orientation": 0x0112,
    "taken_ifd0": 0x0132,
}
_EXIF_TAGS = {
    "taken": 0x9003, "exposure": 0x829A, "fnumber": 0x829D, "iso": 0x8827,
    "focal": 0x920A, "focal35": 0xA405, "lens": 0xA434, "lens_make": 0xA433,
    "flash": 0x9209,
}


def _number(value: Any) -> float | None:
    try:
        if isinstance(value, tuple) and len(value) == 2:
            return float(value[0]) / float(value[1]) if value[1] else None
        return float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    return str(value or "").strip("\x00 ").strip()


def exposure_text(seconds: float | None) -> str:
    """`1/120 s` below a second, `2.5 s` above."""
    if not seconds or seconds <= 0:
        return ""
    if seconds >= 0.5:
        return f"{seconds:g} s"
    return f"1/{round(1 / seconds)} s"


def _taken(text: str) -> str:
    """EXIF's `2026:09:24 07:41:03` as `24 Sep 2026 07:41`."""
    try:
        when = datetime.datetime.strptime(text[:19], "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return text
    return f"{when.day} {when.strftime('%b %Y %H:%M')}"


def _degrees(parts: Any, ref: str) -> float | None:
    try:
        d, m, s = (_number(p) for p in parts)
    except (TypeError, ValueError):
        return None
    if d is None or m is None or s is None:
        return None
    value = d + m / 60 + s / 3600
    return -value if ref in ("S", "W") else value


def camera_name(make: str, model: str) -> str:
    """`Apple iPhone 15 Pro`, without the make twice when the model repeats it
    (`Canon` + `Canon EOS R5`)."""
    if model.lower().startswith(make.lower()):
        return model
    return f"{make} {model}".strip()


def read_exif(path: str) -> dict[str, Any]:
    """The tags worth showing, as plain strings and numbers. {} if none."""
    try:
        from PIL import Image  # noqa: PLC0415 - see decode.py on imports here
    except ImportError:
        return {}
    _register_heif()
    try:
        with paths.open_shared(path, "rb") as raw, Image.open(raw) as picture:
            exif = picture.getexif()
            if not exif:
                return {}
            main = {key: exif.get(tag) for key, tag in _TAGS.items()}
            inner_ifd = exif.get_ifd(_EXIF_IFD) or {}
            inner = {key: inner_ifd.get(tag) for key, tag in _EXIF_TAGS.items()}
            gps = exif.get_ifd(_GPS_IFD) or {}
    except Exception:  # noqa: BLE001 - a picture Pillow cannot read has no details
        return {}

    out: dict[str, Any] = {}
    camera = camera_name(_text(main["make"]), _text(main["model"]))
    if camera:
        out["camera"] = camera
    lens = _text(inner["lens"])
    if lens:
        out["lens"] = lens
    taken = _text(inner["taken"]) or _text(main["taken_ifd0"])
    if taken:
        out["taken"] = _taken(taken)
    exposure = exposure_text(_number(inner["exposure"]))
    if exposure:
        out["exposure"] = exposure
    fnumber = _number(inner["fnumber"])
    if fnumber:
        out["aperture"] = f"f/{fnumber:.1f}".replace(".0", "")
    iso = inner["iso"]
    if isinstance(iso, tuple):
        iso = iso[0] if iso else None
    if iso:
        out["iso"] = f"ISO {int(iso)}"
    focal = _number(inner["focal"])
    focal35 = _number(inner["focal35"])
    if focal:
        out["focal"] = f"{focal:g} mm" + (f" ({focal35:g} mm equiv.)" if focal35 else "")
    software = _text(main["software"])
    if software:
        out["software"] = software
    if gps:
        lat = _degrees(gps.get(2), _text(gps.get(1)))
        lon = _degrees(gps.get(4), _text(gps.get(3)))
        if lat is not None and lon is not None:
            out["location"] = f"{lat:.5f}, {lon:.5f}"
    return out


def histogram(png: bytes) -> list[int]:
    """Brightness in `BINS` bars, from the already-scaled picture."""
    try:
        from PIL import Image  # noqa: PLC0415
    except ImportError:
        return []
    try:
        with Image.open(BytesIO(png)) as picture:
            picture.thumbnail((256, 256))
            counts = picture.convert("L").histogram()
    except Exception:  # noqa: BLE001
        return []
    step = 256 // BINS
    return [sum(counts[i:i + step]) for i in range(0, 256, step)]


def details(path: str, png: bytes | None) -> dict[str, Any] | None:
    """Everything above for one picture, or None when there is nothing."""
    out = read_exif(path)
    bars = histogram(png) if png else []
    if bars:
        out["histogram"] = bars
    if not out:
        return None
    return {"kind": "photo", **out}


def _register_heif() -> None:
    from importlib import import_module  # noqa: PLC0415

    for module in ("pi_heif", "pillow_heif"):
        try:
            import_module(module).register_heif_opener()
            return
        except Exception:  # noqa: BLE001
            continue


# Kept for the tests' arithmetic; a rational exposure such as 1/120 arrives
# from Pillow as an IFDRational, which `float` already understands.
__all__ = ["details", "read_exif", "histogram", "exposure_text", "camera_name", "Fraction"]

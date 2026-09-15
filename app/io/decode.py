"""One decoder, read by the viewer, the preview pane and the grid.

The three surfaces added in 0.16 look like three features and are one. A viewer
shows a photograph large, a preview pane shows the same photograph small beside
the listing, and the grid shows ninety of them at 128 pixels. What differs
between them is a number -- how big -- and everything else is identical: find
something that can read this file, read it, scale it, hand back a picture. So
the decision about *what a file is* lives here and nowhere else, and the three
callers differ only in the box they ask for.

The ladder, in order, with what each rung is for:

  1. **Qt's image plugins.** Everything with a real decoder shipped in the box,
     which is more than it sounds: PNG, JPEG, GIF, BMP, WebP, TIFF, ICO, SVG,
     and -- because PySide6 ships the PDF plugin, which registers as an image
     format -- a rendered page of a PDF. No dependency was added for any of it.
  2. **The embedded preview in a camera raw file.** Unwrapping, not decoding:
     every raw format writes a full JPEG of the shot into its header, so this
     is a scan for two byte markers and then rung 1 again. Demosaicing the
     sensor data would mean a package in the installer and several seconds a
     frame, for a picture marginally different from the one the camera made.
  3. **Whatever Windows has a handler for**, through `IShellItemImageFactory`.
     Video frames, Office documents, `.psd`, `.heic`, DWG with the right
     viewer installed -- somebody else's decoder, already on the machine. This
     is the rung that makes the feature cover the user's actual folders, and it
     is the only rung that runs third-party code.
  4. **Text, with the encoding worked out** rather than assumed.
  5. **Hex**, which cannot fail and is therefore the floor. A file that reaches
     here is still *shown*; "no preview available" is not an outcome this
     module produces for a file it could open.

Three things about the shape of it are deliberate.

**Nothing here imports Qt at module level.** This module is imported by
`app/io/worker.py`, which is spawned once per volume, and an unconditional
PySide6 import would put fifty megabytes and a fifth of a second into every
worker process including the ones that only ever list folders. The import
happens inside the function that needs it, so a worker pays for an image
decoder the first time somebody asks it for a picture.

**Every rung takes a deadline and honours it between units of work,** because
the thing being guarded is not slowness. A read from a share that has gone
returns in forty-five seconds or not at all, and the deadline is what turns
that into a missing preview instead of a wedged process.

**The decode happens here rather than in the window,** and that is the one
decision worth arguing with. Decoding is CPU, not I/O, and it would be safe on
the UI thread. Two things put it in the worker anyway: a 6,000 x 4,000
photograph is forty megabytes of pixels and about ninety kilobytes at the size
a pane can show it, so scaling before the process boundary is most of the cost
of the whole feature; and an image decoder handed a malformed file is exactly
the kind of code that hangs or dies, which in a worker is a process the pool
restarts and in the window is the window.
"""

from __future__ import annotations

import os
import time
from typing import Any

from app.io import paths
from app.io.protocol import (
    FAMILY_IMAGE,
    FAMILY_PAGES,
    FAMILY_RAW,
    FAMILY_SHELL,
    FAMILY_TEXT,
    PREVIEW_HEX_BYTES,
    PREVIEW_PAGE_KINDS,
    PREVIEW_TEXT_BYTES,
    RAW_SCAN_BYTES,
    SNIFF_BYTES,
    Preview,
    PreviewForm,
    preview_family,
)

#: A file this large is not decoded at all. Not a limit on what can be shown --
#: rung 5 will hex the first four kilobytes of anything -- but a limit on what
#: is *read*, and the number is about a share rather than about memory: reading
#: three hundred megabytes over SMB to make a thumbnail is a minute of somebody
#: else's bandwidth for a picture that will be scrolled past.
#:
#: The image rungs are exempt and say why at the call site: Qt reads a JPEG
#: header, learns the dimensions, and decodes straight to the scaled size
#: without the whole file ever being in memory, so a large photograph is
#: cheaper than this number suggests.
MAX_DECODE_BYTES = 320 * 1024 * 1024

#: The same, for a grid cell. Stricter, because a grid asks about a screenful
#: at a time: one 200 MB frame is a decision somebody made by opening it, and
#: ninety of them is a decision somebody made by pressing a view key.
MAX_THUMBNAIL_BYTES = 96 * 1024 * 1024

#: The byte markers around a JPEG. `\xff\xd8\xff` starts one -- the third byte
#: is part of the first marker segment and is included because a bare two-byte
#: match hits far too often in sensor data -- and `\xff\xd9` ends one.
JPEG_START = b"\xff\xd8\xff"
JPEG_END = b"\xff\xd9"

#: The smallest embedded JPEG worth treating as the preview. Raw files carry
#: several: a 160-pixel thumbnail for the camera's browse mode and a full-size
#: one for its playback screen. Anything under this is the former.
MIN_RAW_PREVIEW = 24 * 1024

#: Byte order marks, longest first -- UTF-32LE begins with the UTF-16LE mark,
#: so testing the short one first would decode every UTF-32 file as UTF-16 and
#: produce text with a null between every character.
BOMS: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xfe\x00\x00", "utf-32-le"),
    (b"\x00\x00\xfe\xff", "utf-32-be"),
    (b"\xef\xbb\xbf", "utf-8-sig"),
    (b"\xff\xfe", "utf-16-le"),
    (b"\xfe\xff", "utf-16-be"),
)

#: Control characters that appear in real text. Everything else below 0x20 is
#: evidence of a binary file.
TEXT_CONTROLS = frozenset(b"\t\n\r\f\v\b\x1b")


def preview(path: str, *, box: int, deadline: float,
            text_bytes: int = PREVIEW_TEXT_BYTES,
            allow_shell: bool = True, page: int = 0) -> Preview:
    """Everything this module can say about one file, at one size.

    `box` is the longest edge wanted from a picture, `deadline` a
    `time.monotonic()` value, `page` which page or frame of a document or an
    animation -- zero for the first, which is what every caller but the viewer
    ever asks for. The result always describes the file: a form of NONE means
    the file could not be *opened*, not that nothing could be made of it,
    because rung 5 cannot fail on a file that opens.
    """
    try:
        size = os.path.getsize(paths.api(path))
    except OSError as exc:
        return Preview(form=PreviewForm.NONE, note=_short(exc))

    family = preview_family(os.path.basename(path))
    for attempt in _ladder(family, allow_shell=allow_shell):
        if time.monotonic() > deadline:
            return Preview(form=PreviewForm.NONE, size=size,
                           note="no answer within the deadline")
        try:
            answer = attempt(path, size, box, deadline, text_bytes, page)
        except Exception as exc:  # noqa: BLE001 - a rung that fails is a rung skipped
            # Deliberately broad, and the broadest thing in this file. A rung
            # is an attempt: a codec that raises on a truncated file, a shell
            # provider that throws because it is not installed, a text decode
            # that goes wrong in a way no codec documents. The next rung is the
            # answer to all of them, and the last rung is hex, which cannot
            # raise on a file that opened.
            del exc
            continue
        if answer is not None:
            return answer
    return Preview(form=PreviewForm.NONE, size=size,
                   note="nothing could read this file")


def thumbnail(path: str, size: int, *, deadline: float,
              allow_shell: bool = True) -> bytes | None:
    """One small picture as PNG, or None.

    The grid's entry point, and it is `preview` with two differences rather
    than a second implementation. It returns a picture or nothing -- a cell has
    nowhere to put a sentence, and a cell with no picture draws the icon for
    its kind, which is what the listing was drawing anyway -- and it refuses a
    file over `MAX_THUMBNAIL_BYTES`, because a grid asks about a screenful at a
    time.
    """
    try:
        if os.path.getsize(paths.api(path)) > MAX_THUMBNAIL_BYTES:
            return None
    except OSError:
        return None
    answer = preview(path, box=size, deadline=deadline, text_bytes=0,
                     allow_shell=allow_shell)
    return answer.image if answer.form is PreviewForm.IMAGE else None


# ------------------------------------------------------------------ the ladder


def _ladder(family: str, *, allow_shell: bool) -> list[Any]:
    """The rungs to try, in the order this family deserves.

    Every ladder ends in text and hex, so every file that opens is shown as
    something. What the family changes is the order of the middle: an `.arw`
    tries the raw unwrapper before the image plugins, a `.dxf` tries text
    before the shell, and a name with no extension at all is sniffed, which is
    what `_bytes` does when it looks at the first kilobyte.
    """
    shell = [_shell] if allow_shell else []
    if family == FAMILY_RAW:
        return [_raw, _image, *shell, _bytes]
    if family == FAMILY_IMAGE:
        return [_image, *shell, _bytes]
    if family == FAMILY_TEXT:
        return [_bytes, _image, *shell]
    if family == FAMILY_SHELL:
        return [*shell, _image, _bytes]
    # No extension, or one nobody has claimed. The image reader is asked first
    # because it sniffs content rather than trusting the name, so a photograph
    # somebody saved as `plan.bak` still draws; then the shell, which knows
    # about kinds this list has never heard of; then the bytes, which decide
    # between text and hex by looking.
    return [_image, *shell, _bytes]


def _image(path: str, size: int, box: int, deadline: float,
           text_bytes: int, page: int) -> Preview | None:
    """Rung 1: whatever Qt can read, decoded straight to the size wanted."""
    del text_bytes, deadline
    return _render(path, size, box, source="qt", page=page)


def _raw(path: str, size: int, box: int, deadline: float,
         text_bytes: int, page: int) -> Preview | None:
    """Rung 2: the JPEG the camera wrote into its own file.

    Every format in `PREVIEW_RAW_KINDS` carries at least one, usually two: a
    small one for the camera's browse grid and a full-size one for its playback
    screen. The largest is the one to want, so all of them are found before any
    is decoded.

    This is a byte scan rather than a parse of each vendor's header, and that is
    the point: CR2, CR3, NEF, ARW, RAF and RW2 put their previews in six
    different places under four different container formats, and a scan for
    `ff d8 ff ... ff d9` finds them all without knowing which one it is looking
    at. The cost is bounded by `RAW_SCAN_BYTES` rather than by the file, because
    a preview lives in the header area and reading a whole 60 MB frame to find
    one would cost more than decoding it properly would have.

    What is found then goes through rung 1 rather than being handed back as it
    stands, and that is not tidiness. The embedded preview in a modern raw file
    is the full-size shot -- six thousand pixels and several megabytes -- so
    returning it unscaled would send the whole thing across the process boundary
    and into a 128-pixel grid cell, which is the one cost this module exists to
    avoid.
    """
    del text_bytes, page
    with open(paths.api(path), "rb") as handle:
        head = handle.read(min(size, RAW_SCAN_BYTES))
    best: tuple[int, int] | None = None          # (length, start)
    at = head.find(JPEG_START)
    while at >= 0:
        if time.monotonic() > deadline:
            break
        end = head.find(JPEG_END, at + len(JPEG_START))
        if end < 0:
            break
        length = end + len(JPEG_END) - at
        if length >= MIN_RAW_PREVIEW and (best is None or length > best[0]):
            best = (length, at)
        # Past the end of this one rather than one byte on: a JPEG's own
        # thumbnail is a JPEG inside it, and finding that instead would be a
        # 160-pixel preview of a 6,000-pixel preview.
        at = head.find(JPEG_START, end + len(JPEG_END))
    if best is None:
        return None
    return _render(head[best[1]:best[1] + best[0]], size, box, source="raw",
                   note="the camera's own preview")


def _render(source_data: Any, size: int, box: int, *, source: str,
            note: str = "", page: int = 0) -> Preview | None:
    """Decode with Qt's image plugins and scale on the way, from a path or bytes.

    Shared by rungs 1 and 2 because the raw unwrapper's output is an ordinary
    JPEG and decoding it twice as differently would be two places to get the
    scaling wrong.

    `setScaledSize` before `read` is the whole performance story of this
    feature. Qt's JPEG reader honours it by scaling during decompression, so a
    6,000 pixel photograph asked for at 240 never has six thousand pixels of
    anything in memory. Asking for the full image and scaling afterwards is the
    same picture and about thirty times the cost.

    `canRead` is what makes this safe to try on anything: it sniffs the first
    bytes rather than trusting the extension, so a `.dat` that is really a PNG
    draws and a `.png` that is really a text file falls through to the rung that
    can read it.
    """
    from PySide6.QtCore import QBuffer, QByteArray, QSize     # noqa: PLC0415
    from PySide6.QtGui import QImageReader                    # noqa: PLC0415

    # Both names matter and neither is decoration. `QBuffer` holds a *pointer*
    # to the byte array it was built from, and `QImageReader` holds a pointer to
    # the device -- so writing `QImageReader(QBuffer(QByteArray(data)))` leaves
    # the reader looking at two objects Python has already collected. It does
    # not raise: `canRead` answers False with "Unsupported image format", which
    # reads exactly like a file Qt cannot decode, and reading anyway segfaults
    # the worker. The same trap as the proxy style in `tools/diagnose_font.py`,
    # and the same fix.
    array: Any = None
    store: Any = None
    if isinstance(source_data, (bytes, bytearray)):
        array = QByteArray(bytes(source_data))
        store = QBuffer(array)
        store.open(QBuffer.ReadOnly)
        reader = QImageReader(store)
    else:
        reader = QImageReader(str(source_data))
    # Off for now. On, Qt applies the EXIF orientation itself, which is what is
    # wanted -- but it also leaves `size()` reporting the pre-rotation
    # dimensions while the image that comes back is rotated, so a portrait
    # photograph would be described as landscape in the line under it. Turned
    # on below, once the natural size has been read.
    reader.setAutoTransform(False)
    if not reader.canRead():
        return None
    count = reader.imageCount()
    pages = count if (count > 1 and (reader.supportsAnimation()
                                     or bytes(reader.format()) == b"pdf")) else 0
    # Before `size()`, because the pages of a PDF are not all the same shape and
    # the dimensions reported are the current page's. A jump that fails leaves
    # the reader on the page it was on, which is the right outcome: the caller
    # gets page one rather than nothing.
    if page > 0 and pages > 1:
        reader.jumpToImage(min(page, pages - 1))
    natural = reader.size()
    width, height = max(0, natural.width()), max(0, natural.height())

    longest = max(width, height)
    if longest > box > 0 and width > 0 and height > 0:
        scale = box / longest
        reader.setScaledSize(QSize(max(1, round(width * scale)),
                                   max(1, round(height * scale))))
    reader.setAutoTransform(True)
    image = reader.read()
    if image.isNull():
        return None
    # An SVG has no natural size worth reporting -- Qt answers with whatever
    # the document happens to declare, which may be tiny -- so the drawn size
    # stands in for both and nothing claims the picture was scaled.
    if not width or not height:
        width, height = image.width(), image.height()
    encoded = _png(image, QBuffer, QByteArray)
    if encoded is None:
        return None
    return Preview(
        form=PreviewForm.IMAGE, source=source, size=size,
        image=encoded, width=width, height=height,
        shown=max(image.width(), image.height()), pages=pages, note=note,
    )


def _shell(path: str, size: int, box: int, deadline: float,
           text_bytes: int, page: int) -> Preview | None:
    """Rung 3: whatever Windows already knows how to draw.

    `IShellItemImageFactory` is the modern shell thumbnail path: it asks
    whichever `IThumbnailProvider` is registered for the kind, falls back to the
    file's own icon if there is none, and is what Explorer's own grid uses. It
    is the rung that makes this feature cover the folders somebody actually
    has -- a folder of `.mp4` or `.docx` or `.heic` is nothing to rungs 1 and 2
    and a wall of real pictures to this one.

    Two flags carry the whole of its behaviour and both matter.
    `SIIGBF_THUMBNAILONLY` refuses to be handed the icon for the kind: an icon
    dressed as a thumbnail is a cell that looks like it worked, and the caller
    already draws the icon itself when this returns nothing.
    `SIIGBF_INCACHEONLY` is deliberately *not* set -- a thumbnail cache that is
    only ever read is a cache that is empty on a folder nobody has opened in
    Explorer, which is most of them.

    Runs in the volume's own worker rather than in a host of its own, which is
    worth being explicit about because `CLAUDE.md` sends the context menu to a
    host for running third-party code. A thumbnail provider is the shape of an
    icon handler and not the shape of a menu: one call in, pixels out, nothing
    held across a person's decision. `Op.OVERLAY` and `Op.FILE_ICON` already
    load third-party shell code in this process on exactly that reasoning, and
    a wedged provider is a killed worker either way. What a host *would* buy is
    isolation from a hang, and what it would cost is one process per volume
    plus a thumbnail for a file on a dead share taking down thumbnails for
    every other volume -- which for a grid of a hundred cells is the worse
    trade.
    """
    del deadline, text_bytes, page
    try:
        import pythoncom                                       # noqa: PLC0415
        from win32com.shell import shell, shellcon              # noqa: PLC0415
        import win32gui                                         # noqa: PLC0415
        import win32ui                                          # noqa: PLC0415
    except ImportError:
        return None

    # Flags from `shobjidl_core.h`. `shellcon` stops short of the
    # `SIIGBF_` set, so they are written out with their own values.
    siigbf_thumbnailonly = 0x08
    siigbf_biggersizeok = 0x01

    try:
        pythoncom.CoInitialize()
    except Exception:  # noqa: BLE001 - already initialised on this thread
        pass
    item = shell.SHCreateItemFromParsingName(
        path, None, shell.IID_IShellItemImageFactory)
    wanted = max(16, int(box) or 256)
    bitmap = item.GetImage((wanted, wanted),
                           siigbf_thumbnailonly | siigbf_biggersizeok)
    try:
        return _from_hbitmap(bitmap, size, win32gui, win32ui)
    finally:
        try:
            win32gui.DeleteObject(bitmap)
        except Exception:  # noqa: BLE001 - already gone is the outcome wanted
            pass


def _bytes(path: str, size: int, box: int, deadline: float,
           text_bytes: int, page: int) -> Preview | None:
    """Rungs 4 and 5: the file as text if it is text, and as hex if it is not.

    One function for both because they are one read. The first bytes decide
    which it is, and the decision is made by looking rather than by the name --
    which is what makes a `.txt` full of sensor data come back as hex and a
    `.bin` that is really a config file come back as readable.

    Never returns None on a file it could open, which is what makes it the
    floor of the ladder: there is no file that opens and has no preview.
    """
    del box, deadline, page
    want = max(text_bytes, PREVIEW_HEX_BYTES)
    with open(paths.api(path), "rb") as handle:
        head = handle.read(min(size, want) or PREVIEW_HEX_BYTES)

    encoding = _bom(head)
    if encoding is None and not _looks_textual(head[:SNIFF_BYTES]):
        return Preview(form=PreviewForm.HEX, source="hex", size=size,
                       data=head[:PREVIEW_HEX_BYTES])
    if text_bytes <= 0:
        # A grid cell asked, and text is not a thumbnail. Said here rather than
        # by leaving this rung off the ladder, so that the ladder stays the
        # same list for every caller and only the numbers differ.
        return None

    text, encoding = _decode(head[:text_bytes], encoding,
                             truncated=size > text_bytes)
    if text is None:
        return Preview(form=PreviewForm.HEX, source="hex", size=size,
                       data=head[:PREVIEW_HEX_BYTES])
    return Preview(form=PreviewForm.TEXT, source="text", size=size,
                   text=text, encoding=encoding,
                   truncated=size > text_bytes,
                   lines=text.count("\n") + 1)


# ------------------------------------------------------------------- internals


def _png(image: Any, buffer_class: Any, array_class: Any) -> bytes | None:
    """A `QImage` as PNG bytes.

    PNG rather than JPEG for two reasons that pull the same way: a screenshot
    or a diagram re-encoded as JPEG picks up ringing around every edge, which
    in a preview reads as a fault in the file; and these pictures are small by
    the time they get here, so the size difference is a few tens of kilobytes
    over a pipe that is not the bottleneck.

    An image with an alpha channel keeps it, which matters for the one case
    somebody notices: a `.png` logo on transparency drawn against the pane's
    own background rather than against a white square.
    """
    store = array_class()
    buffer = buffer_class(store)
    buffer.open(buffer_class.WriteOnly)
    try:
        if not image.save(buffer, "PNG"):
            return None
    finally:
        buffer.close()
    return bytes(store.data())


def _from_hbitmap(bitmap: int, size: int, win32gui: Any,
                  win32ui: Any) -> Preview | None:
    """A Windows `HBITMAP` as a PNG preview.

    The shell hands back a GDI bitmap handle, which is neither picklable nor
    something Qt will take, so it is copied out through a device context and
    re-encoded. `GetBitmapBits` rather than `GetDIBits` because the bitmap the
    shell returns is already 32-bit top-down BGRA, which is exactly the layout
    `QImage.Format_ARGB32_Premultiplied` wants -- the same conversion
    `worker.py` does for icons, and for the same reason.
    """
    from PySide6.QtCore import QBuffer, QByteArray                # noqa: PLC0415
    from PySide6.QtGui import QImage                              # noqa: PLC0415

    info = win32gui.GetObject(bitmap)
    width, height = int(info.bmWidth), int(info.bmHeight)
    if width <= 0 or height <= 0:
        return None
    handle = win32ui.CreateBitmapFromHandle(bitmap)
    pixels = handle.GetBitmapBits(True)
    if not pixels or len(pixels) < width * height * 4:
        return None
    image = QImage(bytes(pixels), width, height, width * 4,
                   QImage.Format_ARGB32_Premultiplied).copy()
    if image.isNull():
        return None
    encoded = _png(image, QBuffer, QByteArray)
    if encoded is None:
        return None
    return Preview(form=PreviewForm.IMAGE, source="shell", size=size,
                   image=encoded, width=width, height=height,
                   shown=max(width, height),
                   note="from the Windows thumbnail handler")


def _bom(head: bytes) -> str | None:
    for mark, encoding in BOMS:
        if head.startswith(mark):
            return encoding
    return None


def _looks_textual(sample: bytes) -> bool:
    """Whether a sample reads as text.

    Two tests, and the first one settles almost everything: a NUL byte in the
    first kilobyte means binary, because no encoding in use writes one into
    running text except the UTF-16 and UTF-32 families, and those announce
    themselves with a byte order mark that has already been checked.

    The second catches the rest -- a stream of control bytes with no nulls,
    which is most compressed formats. The threshold is loose on purpose: a text
    file with a stray form feed or an ANSI escape sequence in it is still a
    text file, and being wrong here costs a hex view of something readable.
    """
    if not sample:
        return True          # an empty file is an empty text file
    if b"\x00" in sample:
        return False
    odd = sum(1 for byte in sample if byte < 0x20 and byte not in TEXT_CONTROLS)
    return odd * 100 <= len(sample) * 3


def _decode(head: bytes, encoding: str | None, *,
            truncated: bool = False) -> tuple[str | None, str]:
    """Bytes as a string, and what they turned out to be.

    Three steps, in the order that gets the answer right most often. A byte
    order mark is proof and is believed. Failing that, a strict UTF-8 decode is
    tried, and a *successful* strict decode of more than a few bytes is strong
    evidence: UTF-8's multi-byte sequences are structured enough that arbitrary
    bytes almost never pass. Failing that, cp1252, which cannot fail -- it maps
    every byte -- and is what a Windows text file that is not UTF-8 almost
    always is.

    The tail is trimmed rather than repaired. Reading the first 256 kB of a
    UTF-8 file will usually cut a character in half, and a replacement
    character at the end of a truncated preview is a glyph somebody would go
    looking for in the file.
    """
    if encoding is not None:
        try:
            # `errors="replace"` rather than strict, and only on this branch: a
            # byte order mark is proof of the encoding, so a byte that does not
            # fit it is a damaged file rather than the wrong guess, and showing
            # the rest of a damaged file is more use than a hex dump of it.
            #
            # The mark itself is dropped. Only `utf-8-sig` strips one for you;
            # the UTF-16 and UTF-32 codecs leave it as a zero-width character
            # at the start of the text, which draws as a space that cannot be
            # selected or deleted and reads as a fault in the file.
            return head.decode(encoding, errors="replace").lstrip("﻿"), encoding
        except LookupError:            # pragma: no cover - a codec Python lacks
            pass
    for candidate in ("utf-8", "cp1252"):
        try:
            return _trim(head, candidate, truncated=truncated), candidate
        except UnicodeDecodeError:
            continue
    return None, ""


def _trim(head: bytes, encoding: str, *, truncated: bool) -> str:
    """Decode strictly, forgiving only a character cut in half by the read.

    The forgiveness has to be narrow, and the obvious version is not: trying to
    drop one, two and three bytes off the end until something decodes sounds
    like the same thing and is not. A cp1252 file ending `caf\\xe9\\n` fails a
    strict UTF-8 decode at the `\\xe9` -- correctly, it is not UTF-8 -- and
    dropping two bytes leaves `caf`, which decodes perfectly. The file then
    comes back as UTF-8 with its last characters quietly missing, and the
    encoding line under it says so confidently.

    Two conditions separate a cut-off character from the wrong encoding, and
    both are needed. **The read has to have been truncated at all**: a file read
    to its end has no half character, so a bad byte in one is a bad byte. And
    the failure has to be **at the boundary** -- `UnicodeDecodeError.start`
    within the last three bytes, three being the longest tail a UTF-8 sequence
    can be missing. Anything earlier belongs to the next candidate encoding, not
    to a trim.
    """
    try:
        return head.decode(encoding)
    except UnicodeDecodeError as bad:
        if not truncated or bad.start < len(head) - 3:
            raise
        # Raises again if the shorter form is no better, which is the right
        # answer: the caller moves on to the next encoding.
        return head[:bad.start].decode(encoding)


def _suffix(path: str) -> str:
    name = os.path.basename(path)
    stem, dot, suffix = name.rpartition(".")
    return f".{suffix.lower()}" if dot and stem else ""


def _short(exc: BaseException) -> str:
    """One clause for a status line. `strerror` where there is one, because
    `repr` of an `OSError` is a tuple of numbers."""
    message = getattr(exc, "strerror", None) or str(exc)
    return str(message).strip() or exc.__class__.__name__

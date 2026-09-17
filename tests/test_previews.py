"""Checks on the previewer: the decoder, and the two caches in front of it.

The decoder is the first part of this application that can be checked properly
without Windows, because deciding what a file is happens by reading bytes rather
than by asking the shell. So the tests here are unusually load-bearing: they run
real images through the real ladder and assert on what comes back.

The failures worth catching, in the order they would hurt:

  * **a picture crossing the process boundary unscaled**, which is the whole
    cost of the feature arriving in one go -- a 24-megapixel photograph is forty
    megabytes of pixels and a hundred kilobytes at the size a pane can show it,
    and nothing about the second case looks different from the first except the
    number;
  * **asking about a row that could not draw a thumbnail**, which is a file read
    per row in a folder of documents and the thing `CLAUDE.md` forbids;
  * **a request per row of a held arrow key**, which is what the debounce and
    the one-at-a-time rule exist to stop;
  * **an abandoned decode left running**, which holds the volume the next one
    wants;
  * **text decoded as the wrong thing**, which is the failure a person sees as
    mojibake and cannot do anything about.
"""

from __future__ import annotations

import os
import time

import pytest

from app.core.config import Config
from app.io import decode
from app.io.protocol import (
    FAMILY_HEIF,
    FAMILY_IMAGE,
    FAMILY_RAW,
    FAMILY_SHELL,
    FAMILY_TEXT,
    FAMILY_UNKNOWN,
    Entry,
    Op,
    Preview,
    PreviewForm,
    Reply,
    Status,
    draws_a_thumbnail,
    preview_family,
)

pytest.importorskip("PySide6.QtGui")


class FakeBridge:
    def __init__(self):
        self.sent = []
        self.cancelled = []
        self.forgotten = []

    def submit(self, op, path, *, timeout, on_reply, args=None):
        self.sent.append({"op": op, "path": path, "args": dict(args or {}),
                          "timeout": timeout, "handler": on_reply})
        return len(self.sent)

    def cancel(self, request_id):
        self.cancelled.append(request_id)

    def forget(self, request_id):
        self.forgotten.append(request_id)

    def answer(self, index, payload, status=Status.OK):
        self.sent[index]["handler"](Reply(index + 1, status, payload=payload))


def entry(name, *, mtime=100.0, size=2048, is_dir=False):
    return Entry(name=name, is_dir=is_dir, size=size, mtime=mtime, attributes=0)


@pytest.fixture
def photo(tmp_path):
    """A real JPEG, large enough that scaling it is visible in the numbers."""
    from PySide6.QtGui import QImage

    image = QImage(2400, 1600, QImage.Format_RGB32)
    image.fill(0x2F6F9F)
    path = str(tmp_path / "DSC_4417.jpg")
    assert image.save(path, "JPEG")
    return path


def soon() -> float:
    return time.monotonic() + 30


# ------------------------------------------------------------- what is asked


def test_only_kinds_that_could_draw_are_ever_asked_about():
    """The bound that makes the grid affordable. A folder of source code sends
    nothing, so switching a folder of text files to the grid costs no reads."""
    for name in ("notes.txt", "build.py", "register.csv", "config.json",
                 "CMakeLists.txt", "readme"):
        assert not draws_a_thumbnail(entry(name)), name


def test_a_folder_never_draws_a_thumbnail():
    """What a folder looks like is its icon. The alternative -- a stack of the
    first four things inside it -- is four listings per cell."""
    for name in ("Site photos", "photo.jpg", "scan.pdf"):
        assert not draws_a_thumbnail(entry(name, is_dir=True))


def test_the_kinds_that_can_draw_are_asked_about():
    for name in ("DSC_4417.jpg", "plan.png", "frame.arw", "walkthrough.mp4",
                 "brief.docx", "A-101.pdf", "IMG_4417.heic"):
        assert draws_a_thumbnail(entry(name)), name


def test_the_family_decides_which_decoder_goes_first():
    """The order in `preview_family` is the whole of its behaviour, and two of
    the choices are not obvious."""
    assert preview_family("photo.jpg") == FAMILY_IMAGE
    assert preview_family("walkthrough.mp4") == FAMILY_SHELL
    # A .dng is a TIFF as far as Qt is concerned, so without raw winning here
    # the camera's 160-pixel thumbnail strip would come back as the photograph.
    assert preview_family("frame.dng") == FAMILY_RAW
    # An .svg draws and an .html does not, even though both are markup:
    # somebody opening an .html in a file manager is looking at the source.
    assert preview_family("logo.svg") == FAMILY_IMAGE
    assert preview_family("index.html") == FAMILY_TEXT
    # A .dxf is a drawing exchange file that Windows may well have a handler
    # for, and it is still text that somebody opens to read.
    assert preview_family("site.dxf") == FAMILY_TEXT
    # A .heic was a shell kind until 0.29.11 and is its own family now, which
    # is what puts libheif in front of the Windows thumbnail handler. Getting
    # this back to FAMILY_SHELL would work on a machine with the Store's
    # extensions installed and on no other.
    assert preview_family("IMG_4417.heic") == FAMILY_HEIF
    assert preview_family("clip.avif") == FAMILY_HEIF
    assert preview_family("mystery") == FAMILY_UNKNOWN


# --------------------------------------------------------------- the decoder


def test_a_large_picture_crosses_the_boundary_scaled(photo):
    """The point of the whole feature, in one assertion.

    What is sent is the picture at the size asked for; what is *reported* is the
    real one. A version of this that scaled after the boundary rather than
    before would pass every other test in this file.
    """
    answer = decode.preview(photo, box=240, deadline=soon(), allow_shell=False)
    assert answer.form is PreviewForm.IMAGE
    assert (answer.width, answer.height) == (2400, 1600)
    assert answer.shown == 240
    # Forty megabytes of pixels, if it had come across unscaled.
    assert len(answer.image) < 64 * 1024


def test_the_box_is_a_ceiling_and_not_a_target(photo):
    """A picture smaller than the box is not enlarged to fill it. Enlarging is
    something a person asks for with a key, and a 40-pixel icon blown up to a
    preview pane is a wall of soft squares."""
    from PySide6.QtGui import QImage

    small = os.path.join(os.path.dirname(photo), "favicon.png")
    image = QImage(48, 48, QImage.Format_RGB32)
    image.fill(0)
    assert image.save(small, "PNG")
    answer = decode.preview(small, box=1600, deadline=soon(), allow_shell=False)
    assert answer.shown == 48


def test_content_decides_the_form_rather_than_the_extension(photo, tmp_path):
    """A photograph saved as `plan.bak` still draws, because `canRead` sniffs
    the bytes. This is what keeps the extension lists a hint rather than a
    contract."""
    disguised = tmp_path / "plan.bak"
    disguised.write_bytes(open(photo, "rb").read())
    answer = decode.preview(str(disguised), box=200, deadline=soon(),
                            allow_shell=False)
    assert answer.form is PreviewForm.IMAGE
    assert answer.source == "qt"


def test_a_text_file_wearing_an_image_extension_comes_back_as_text(tmp_path):
    """The other direction, and the one that matters more: nothing should be
    reported as a picture that is not one."""
    lying = tmp_path / "screenshot.png"
    lying.write_text("this is not a picture\n" * 40, encoding="utf-8")
    answer = decode.preview(str(lying), box=200, deadline=soon(),
                            allow_shell=False)
    assert answer.form is PreviewForm.TEXT


def test_the_embedded_preview_is_pulled_out_of_a_raw_file(photo, tmp_path):
    """A raw file is unwrapped rather than demosaiced: a scan for two byte
    markers, which is what makes camera raw work with no dependency at all."""
    jpeg = open(photo, "rb").read()
    frame = tmp_path / "frame_0001.arw"
    frame.write_bytes(b"\x00\x11\x22" * 8000 + jpeg + b"\x99" * 4000)
    answer = decode.preview(str(frame), box=300, deadline=soon(),
                            allow_shell=False)
    assert answer.form is PreviewForm.IMAGE
    assert answer.source == "raw"
    assert (answer.width, answer.height) == (2400, 1600)
    # And scaled on the way out, not handed over whole. The embedded preview in
    # a modern raw file is the full-size shot, so returning it as found would
    # send several megabytes into a 128-pixel cell.
    assert answer.shown == 300
    assert len(answer.image) < len(jpeg)


def test_a_camera_browse_thumbnail_is_too_small_to_be_the_preview(tmp_path):
    """Raw files carry two JPEGs and the small one is for the camera's own grid.
    Picking it would give a 160-pixel preview of a 24-megapixel photograph."""
    tiny = decode.JPEG_START + b"\x00" * 200 + decode.JPEG_END
    frame = tmp_path / "frame.nef"
    frame.write_bytes(b"\x07" * 500 + tiny + b"\x07" * 500)
    assert decode._raw(str(frame), frame.stat().st_size, 300, soon(), 0, 0) is None


def test_a_pdf_renders_a_page_and_says_how_many_there_are(tmp_path):
    """PySide6 ships Qt's PDF plugin, which registers as an image format. That
    is why there is no PDF dependency in the installer, and this is the test
    that notices if a future PySide6 stops shipping it."""
    pdf = tmp_path / "A-101.pdf"
    pdf.write_bytes(
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R 4 0 R]/Count 2>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 100]>>endobj\n"
        b"4 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 100]>>endobj\n"
        b"trailer<</Root 1 0 R>>\n")
    answer = decode.preview(str(pdf), box=300, deadline=soon(), allow_shell=False)
    if answer.form is not PreviewForm.IMAGE:
        pytest.skip("this Qt has no PDF image plugin")
    assert answer.pages == 2


# ------------------------------------------------------------ heic and heif


#: A real HEIC: 4032 x 3024, HEVC-coded, the shape and the codec a phone
#: writes, and eight kilobytes because the picture is one flat colour. Committed
#: rather than generated, which is the only binary fixture in this suite and
#: needs the reason saying: `pi-heif` is a *decoder*, so from 0.29.11 nothing in
#: the pinned dependencies can write one of these, and a test that skipped
#: unless somebody happened to have the encoder installed would be a test that
#: never ran on the configuration that actually ships.
HEIC = os.path.join(os.path.dirname(__file__), "data", "IMG_4417.heic")


def heif_reader():
    """The installed build of the reader, or a skip.

    `pi-heif` is what `requirements.txt` pins and `pillow-heif` is the same
    project with an encoder nobody here needs, so the decoder takes either and
    so does this.
    """
    from importlib import import_module

    for module in ("pi_heif", "pillow_heif"):
        try:
            return import_module(module)
        except ImportError:
            continue
    pytest.skip("neither pi-heif nor pillow-heif is installed")


def test_a_heic_decodes_here_rather_than_being_asked_about():
    """The reason the dependency is in the installer. `allow_shell=False` is
    what makes the assertion mean something: this is a machine with no Windows
    thumbnail handler at all, which is also the state of a Windows machine
    whose owner has not bought HEVC Video Extensions."""
    heif_reader()
    answer = decode.preview(HEIC, box=240, deadline=soon(), allow_shell=False)
    assert answer.form is PreviewForm.IMAGE
    assert answer.source == "heif"
    assert (answer.width, answer.height) == (4032, 3024)
    # Scaled before the boundary, the same as every other rung. libheif has no
    # scale-during-decode call, so this is the resize that has to be there.
    assert answer.shown == 240
    assert len(answer.image) < 64 * 1024


def test_a_portrait_picture_is_not_described_as_landscape(tmp_path):
    """AVIF rather than HEIC only because Pillow can write one: it is the same
    container, the same rung and the same arithmetic, and writing the picture
    here is what lets the shape be part of the test rather than of a file."""
    heif_reader()
    from PIL import Image

    path = str(tmp_path / "IMG_4418.avif")
    try:
        Image.new("RGB", (3024, 4032), (200, 90, 40)).save(path, quality=50)
    except (KeyError, OSError, ValueError):     # pragma: no cover
        pytest.skip("this Pillow cannot write AVIF")
    answer = decode.preview(path, box=240, deadline=soon(), allow_shell=False)
    assert answer.source == "heif"
    assert (answer.width, answer.height) == (3024, 4032)


def test_a_picture_nothing_can_draw_says_so_instead_of_hexing_it(tmp_path):
    """The floor is off for the picture families. Four kilobytes of hex under a
    name ending `.heic` reads as the application not knowing what a photograph
    is, which is what it looked like before there was a rung that could read
    one -- and the sentence names the machine, because a missing codec and a
    damaged file are worth telling apart."""
    broken = tmp_path / "IMG_4419.heic"
    broken.write_bytes(bytes(range(256)) * 60)
    answer = decode.preview(str(broken), box=240, deadline=soon(),
                            allow_shell=False)
    assert answer.form is PreviewForm.NONE
    assert "picture" in answer.note


def test_the_hex_floor_is_only_off_for_the_picture_families(tmp_path):
    """Everything else still ends in hex, which is the floor that makes "no
    preview available" not an outcome. An archive is a file somebody might well
    want the first bytes of."""
    archive = tmp_path / "drawings.zip"
    archive.write_bytes(b"PK\x03\x04" + bytes(range(256)) * 16)
    answer = decode.preview(str(archive), box=240, deadline=soon(),
                            allow_shell=False)
    assert answer.form is PreviewForm.HEX


def test_text_still_comes_back_under_a_picture_name(tmp_path):
    """Only the hex floor is withheld from the picture families, not the text
    rung above it: a `.heic` that is really an error page is still readable."""
    lying = tmp_path / "IMG_4420.heic"
    lying.write_text("<html>404 not found</html>\n" * 20, encoding="utf-8")
    answer = decode.preview(str(lying), box=240, deadline=soon(),
                            allow_shell=False)
    assert answer.form is PreviewForm.TEXT


# -------------------------------------------------------- text and encodings


@pytest.mark.parametrize("raw, encoding, expected", [
    ("plain ascii\n".encode("ascii"), "utf-8", "plain ascii\n"),
    ("caf\u00e9 na\u00efve\n".encode("utf-8"), "utf-8", "caf\u00e9 na\u00efve\n"),
    # cp1252 is what a Windows text file that is not UTF-8 almost always is, and
    # it cannot fail -- it maps every byte -- so it is the last resort.
    ("caf\u00e9\n".encode("cp1252"), "cp1252", "caf\u00e9\n"),
    ("\ufeffbom utf-8\n".encode("utf-8-sig"), "utf-8-sig", "bom utf-8\n"),
    ("\ufeffwide\n".encode("utf-16-le"), "utf-16-le", "wide\n"),
    ("\ufeffwide\n".encode("utf-16-be"), "utf-16-be", "wide\n"),
])
def test_encodings_are_detected_and_the_mark_is_dropped(tmp_path, raw, encoding,
                                                        expected):
    """The byte order mark is proof of the encoding and must not survive into
    the text: only `utf-8-sig` strips one, and the UTF-16 codecs leave it as a
    zero-width character that draws as a space nobody can delete."""
    target = tmp_path / "notes.txt"
    target.write_bytes(raw)
    answer = decode.preview(str(target), box=0, deadline=soon(),
                            allow_shell=False)
    assert answer.form is PreviewForm.TEXT
    assert answer.encoding == encoding
    assert answer.text == expected


def test_utf32_is_not_read_as_utf16(tmp_path):
    """UTF-32LE begins with the UTF-16LE mark, so a shorter-first check would
    decode every UTF-32 file as UTF-16 with a null between each character."""
    target = tmp_path / "wide.txt"
    target.write_bytes("\ufeffhello\n".encode("utf-32-le"))
    answer = decode.preview(str(target), box=0, deadline=soon(),
                            allow_shell=False)
    assert answer.encoding == "utf-32-le"
    assert answer.text == "hello\n"


def test_binary_falls_to_hex_rather_than_to_nothing(tmp_path):
    """Hex is the floor of the ladder, which is what makes "no preview" an
    outcome only for a file that could not be opened."""
    target = tmp_path / "sensor.dat"
    target.write_bytes(bytes(range(256)) * 40)
    answer = decode.preview(str(target), box=0, deadline=soon(),
                            allow_shell=False)
    assert answer.form is PreviewForm.HEX
    assert len(answer.data) == decode.PREVIEW_HEX_BYTES


def test_a_truncated_character_does_not_send_a_file_to_hex(tmp_path):
    """Reading the first N bytes of a UTF-8 file usually cuts a character in
    half. Trimming up to three bytes is what stops that being read as binary."""
    target = tmp_path / "long.txt"
    target.write_text("\u00e9" * 4000, encoding="utf-8")
    answer = decode.preview(str(target), box=0, deadline=soon(),
                            text_bytes=1001, allow_shell=False)
    assert answer.form is PreviewForm.TEXT
    assert answer.truncated
    assert "\ufffd" not in answer.text


def test_a_grid_cell_is_never_given_text(tmp_path):
    """Ninety cells of grey lines at 128 pixels are ninety identical squares,
    and the icon for the kind says more in less space."""
    target = tmp_path / "notes.txt"
    target.write_text("hello\n" * 200, encoding="utf-8")
    assert decode.thumbnail(str(target), 128, deadline=soon(),
                            allow_shell=False) is None


# ----------------------------------------------------------- the worker's end


def test_a_very_large_file_is_refused_rather_than_read(tmp_path, monkeypatch):
    """A policy about somebody's bandwidth rather than about memory: reading
    three hundred megabytes over SMB to make a picture that will be scrolled
    past is a minute of the share nobody asked for. It comes back as an answer
    with a sentence on it, not as an error."""
    from app.io import worker
    from app.io.protocol import Request

    target = tmp_path / "huge.jpg"
    target.write_bytes(b"\x00" * 16)
    monkeypatch.setattr(worker.os.path, "getsize",
                        lambda _p: decode.MAX_DECODE_BYTES + 1)

    replies = []
    worker._preview(Request(id=1, op=Op.PREVIEW, path=str(target), timeout=5.0,
                            args={"box": 400}),
                    type("Box", (), {"put": lambda _s, r: replies.append(r)})())
    assert replies[0].status is Status.OK
    assert replies[0].payload.form is PreviewForm.NONE
    assert "too large" in replies[0].payload.note


def test_the_worker_refuses_a_name_that_is_path_shaped(tmp_path):
    """A name here arrives from a listing and is joined onto the folder, so
    anything path-shaped is refused rather than followed. The same guard
    `_file_icons` has, for the same reason."""
    from app.io import worker
    from app.io.protocol import Request

    replies = []
    worker._thumbnails(
        Request(id=1, op=Op.THUMBNAIL, path=str(tmp_path), timeout=5.0,
                args={"names": ["..\\..\\secret.jpg", "sub/one.jpg"],
                      "size": 96, "shell": False}),
        type("Box", (), {"put": lambda _s, r: replies.append(r)})())
    assert replies[0].payload["rows"] == {}


# ------------------------------------------------------------- core.Previews


@pytest.fixture
def previews():
    from app.core.previews import Previews

    bridge = FakeBridge()
    return Previews(bridge, Config({})), bridge


def an_image(width=2400, height=1600):
    return Preview(form=PreviewForm.IMAGE, source="qt", size=4_182_355,
                   image=b"PNG-ish", width=width, height=height, shown=400)


def test_the_cursor_sweeping_a_folder_sends_one_request(previews):
    """A held arrow key crosses thirty rows on the way to row thirty-one. The
    debounce is what makes that one decode rather than thirty, each cancelling
    the last and the wanted one queued behind all of them."""
    provider, bridge = previews
    for row in range(30):
        provider.ask(f"C:\\Jobs\\file{row}.jpg", mtime=1.0, size=10)
    provider.fire_now()
    assert len(bridge.sent) == 1
    assert bridge.sent[0]["path"] == "C:\\Jobs\\file29.jpg"


def test_an_abandoned_decode_is_cancelled_at_the_worker(previews):
    """Cancelled, not merely forgotten: a decode nobody is waiting for is still
    a file being read off a volume, and the next row wants that worker."""
    provider, bridge = previews
    provider.ask("C:\\Jobs\\a.jpg", mtime=1.0, size=10, delay_ms=0)
    provider.ask("C:\\Jobs\\b.jpg", mtime=1.0, size=10, delay_ms=0)
    assert bridge.cancelled == [1]
    assert bridge.forgotten == [1]


def test_arrowing_back_to_a_file_answers_from_the_cache(previews):
    """Down four rows and back up three is what people actually do, and going
    back through the worker for it would be a visible blink every time."""
    provider, bridge = previews
    seen = []
    provider.ready.connect(lambda path, answer: seen.append(path))
    provider.ask("C:\\Jobs\\a.jpg", mtime=1.0, size=10, delay_ms=0)
    bridge.answer(0, an_image())
    provider.ask("C:\\Jobs\\a.jpg", mtime=1.0, size=10, delay_ms=0)
    assert len(bridge.sent) == 1
    assert seen == ["C:\\Jobs\\a.jpg", "C:\\Jobs\\a.jpg"]


def test_a_file_that_has_changed_is_read_again(previews):
    """Keyed on the row's own mtime and size, so an edited photograph gets its
    new picture without anything having to know that it was edited."""
    provider, bridge = previews
    provider.ask("C:\\Jobs\\a.jpg", mtime=1.0, size=10, delay_ms=0)
    bridge.answer(0, an_image())
    provider.ask("C:\\Jobs\\a.jpg", mtime=2.0, size=10, delay_ms=0)
    assert len(bridge.sent) == 2


def test_a_preview_for_a_different_box_is_not_reused(previews):
    """The preview pane's 1600 and a grid cell's 128 are different pictures, and
    handing the small one to the viewer would be a soft photograph."""
    provider, bridge = previews
    provider.ask("C:\\Jobs\\a.jpg", box=400, mtime=1.0, size=10, delay_ms=0)
    bridge.answer(0, an_image())
    provider.ask("C:\\Jobs\\a.jpg", box=4096, mtime=1.0, size=10, delay_ms=0)
    assert len(bridge.sent) == 2


def test_an_answer_for_a_file_nobody_is_on_is_dropped(previews):
    """One decode is outstanding for the *window*, so an answer arrives at the
    pane that did not ask. It is dropped by comparing the path."""
    provider, bridge = previews
    seen = []
    provider.ready.connect(lambda path, answer: seen.append(path))
    provider.ask("C:\\Jobs\\a.jpg", mtime=1.0, size=10, delay_ms=0)
    provider.ask("C:\\Jobs\\b.jpg", mtime=1.0, size=10, delay_ms=0)
    bridge.answer(0, an_image())         # the answer for a.jpg, arriving late
    assert seen == []


def test_a_file_with_no_preview_is_remembered_as_having_none(previews):
    """"Nothing could be made of this" is an answer. Re-asking for it every time
    the cursor passes would be a read per pass for a sentence that cannot change
    unless the file does."""
    provider, bridge = previews
    provider.ask("C:\\Jobs\\odd.bin", mtime=1.0, size=10, delay_ms=0)
    bridge.answer(0, Preview(form=PreviewForm.NONE, note="no decoder"))
    provider.ask("C:\\Jobs\\odd.bin", mtime=1.0, size=10, delay_ms=0)
    assert len(bridge.sent) == 1


def test_a_failed_request_is_not_cached(previews):
    """A share that comes back should show its pictures without somebody having
    to navigate away and back."""
    provider, bridge = previews
    provider.ask("S:\\Jobs\\a.jpg", mtime=1.0, size=10, delay_ms=0)
    bridge.answer(0, None, status=Status.GONE)
    provider.ask("S:\\Jobs\\a.jpg", mtime=1.0, size=10, delay_ms=0)
    assert len(bridge.sent) == 2


def test_the_cache_does_not_grow_without_bound(previews):
    """These are decoded images. A hundred of them is real memory."""
    from app.core.previews import CACHE_SIZE

    provider, bridge = previews
    for row in range(CACHE_SIZE * 3):
        provider.ask(f"C:\\Jobs\\f{row}.jpg", mtime=1.0, size=10, delay_ms=0)
        bridge.answer(row, an_image())
    assert len(provider._cache) == CACHE_SIZE


# ----------------------------------------------------------- core.Thumbnails


@pytest.fixture
def thumbs():
    from app.core.thumbnails import Thumbnails

    bridge = FakeBridge()
    return Thumbnails(bridge, Config({})), bridge


def test_the_grid_asks_only_about_cells_that_could_draw(thumbs):
    provider, bridge = thumbs
    for name in ("a.jpg", "b.txt", "c.mp4", "d.py", "e.arw", "f.json"):
        provider.picture("C:\\Jobs", entry(name))
    provider.picture("C:\\Jobs", entry("Sub", is_dir=True))
    provider.flush()
    assert bridge.sent[0]["args"]["names"] == ["a.jpg", "c.mp4", "e.arw"]


def test_a_screenful_is_one_request_per_folder(thumbs):
    """A grid of a hundred cells asking one request each is a hundred requests
    against one volume."""
    provider, bridge = thumbs
    for row in range(80):
        provider.picture("C:\\Jobs", entry(f"shot{row}.jpg"))
    provider.flush()
    assert len(bridge.sent) == 1
    assert len(bridge.sent[0]["args"]["names"]) == 80


def test_a_repaint_does_not_become_a_request(thumbs):
    """`picture` is called while the view paints, so a scroll that touches a
    cell forty times must not ask forty times."""
    provider, bridge = thumbs
    provider.picture("C:\\Jobs", entry("a.jpg"))
    provider.flush()
    bridge.answer(0, {"size": 128, "rows": {"a.jpg": "k1"},
                      "images": {"k1": _png_bytes()}})
    for _ in range(40):
        provider.picture("C:\\Jobs", entry("a.jpg"))
    provider.flush()
    assert len(bridge.sent) == 1


def test_a_timeout_records_only_the_cells_it_reached(thumbs):
    """The worker checks its deadline between files, so a partial answer means
    "these are done, the rest were never reached". Recording the rest as having
    no picture would key that mistake to their mtime and hold it for as long as
    the files do not change."""
    provider, bridge = thumbs
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        provider.picture("C:\\Jobs", entry(name))
    provider.flush()
    bridge.answer(0, {"size": 128, "rows": {"a.jpg": "k1"},
                      "images": {"k1": _png_bytes()}}, status=Status.TIMEOUT)
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        provider.picture("C:\\Jobs", entry(name))
    provider.flush()
    assert bridge.sent[1]["args"]["names"] == ["b.jpg", "c.jpg"]


def test_a_settled_answer_records_the_cells_that_had_nothing(thumbs):
    """The empty answer is what stops a file nothing could be made of being read
    again on every repaint."""
    provider, bridge = thumbs
    for name in ("a.jpg", "b.jpg"):
        provider.picture("C:\\Jobs", entry(name))
    provider.flush()
    bridge.answer(0, {"size": 128, "rows": {"a.jpg": "k1"},
                      "images": {"k1": _png_bytes()}})
    for name in ("a.jpg", "b.jpg"):
        provider.picture("C:\\Jobs", entry(name))
    provider.flush()
    assert len(bridge.sent) == 1


def test_changing_the_cell_size_throws_the_pictures_away(thumbs):
    """A 96-pixel picture blown up to 240 is the soft square that makes a grid
    look broken, so each step is a fresh decode. Which is also why the sizes are
    four steps rather than a slider."""
    provider, bridge = thumbs
    provider.picture("C:\\Jobs", entry("a.jpg"))
    provider.flush()
    bridge.answer(0, {"size": 128, "rows": {"a.jpg": "k1"},
                      "images": {"k1": _png_bytes()}})
    provider.set_size(240)
    provider.picture("C:\\Jobs", entry("a.jpg"))
    provider.flush()
    assert bridge.sent[1]["args"]["size"] == 240


def test_dropping_old_pictures_drops_the_cells_pointing_at_them(thumbs):
    """A cell left pointing at a picture that has been trimmed would draw
    nothing forever, which is worse than asking again: asking again is one read
    and drawing nothing is permanent."""
    from app.core.thumbnails import MAX_IMAGES

    provider, bridge = thumbs
    provider._images = {f"k{i}": object() for i in range(MAX_IMAGES + 10)}
    provider._recent = [f"k{i}" for i in range(MAX_IMAGES + 10)]
    provider._rows = {("c:\\jobs", f"f{i}.jpg"): (1.0, 10, f"k{i}")
                      for i in range(MAX_IMAGES + 10)}
    provider._trim()
    assert len(provider._images) == MAX_IMAGES
    assert all(value[2] in provider._images for value in provider._rows.values())


def test_thumbnails_off_means_nothing_is_asked_for(thumbs):
    """The switch for the day a thumbnail handler misbehaves: it costs the
    pictures and nothing else, and the grid is still a grid."""
    provider, bridge = thumbs
    provider._config.set("preview.thumbnails", False)
    provider.picture("C:\\Jobs", entry("a.jpg"))
    provider.flush()
    assert bridge.sent == []


# ------------------------------------------------------------------ the words


def test_a_scaled_picture_says_so():
    """Otherwise somebody zooms in, finds softness, and goes looking for it in
    the file."""
    from app.core.previews import describe

    assert "shown at 400 px" in describe(an_image())
    whole = Preview(form=PreviewForm.IMAGE, width=300, height=200, shown=300,
                    size=40_000)
    assert "shown at" not in describe(whole)


def test_a_truncated_text_file_says_it_goes_on():
    """Without it a 400 MB log looks like a short one."""
    from app.core.previews import describe

    line = describe(Preview(form=PreviewForm.TEXT, lines=1840, truncated=True,
                            encoding="utf-8", size=61_204))
    assert "1,840 lines so far" in line
    assert "utf-8" in line


def test_the_hex_dump_lines_up():
    from app.ui.viewer import hex_dump

    lines = hex_dump(bytes(range(20))).splitlines()
    assert lines[0].startswith("00000000  00 01 02 03")
    assert lines[1].startswith("00000010  10 11 12 13")
    # The short last row is padded, so the character column starts in the same
    # place on every line. A dump whose last row jumps left is the one thing a
    # hex view is not allowed to do -- the columns are the whole point of it.
    column = 10 + 16 * 3 - 1 + 2
    assert lines[0][column:] == "." * 16
    assert lines[1][column:] == "...."


# ---------------------------------------------------------------- the window
#
# The only tests here that build real widgets. Two facts are worth the cost,
# because both are the kind that a diff reads as correct: that the grid and the
# listing are one selection rather than two, and that F3 stopped being the quick
# search's key without the quick search losing one.


@pytest.fixture
def window(tmp_path):
    """A real window over fake io, in the shape `tests/test_window.py` uses."""
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QApplication

    from app.core.capacity import Capacity
    from app.core.favorites import Favorites
    from app.core.pane import Pane
    from app.core.previews import Previews
    from app.core.thumbnails import Thumbnails
    from app.core.transfers import TransferQueue
    from app.ui.window import MainWindow

    class Volumes(QObject):
        changed = Signal()
        drives = []

        def letter_for(self, path):
            return None

        def refresh(self, *, rescan=False):
            pass

    config = Config({"left.path": "C:\\Jobs", "right.path": "D:\\Archive"},
                    str(tmp_path / "config.json"))
    bridge = FakeBridge()
    previews = Previews(bridge, config)
    thumbnails = Thumbnails(bridge, config)
    made = MainWindow(
        config,
        Pane(bridge, config, "left", previews=previews, thumbnails=thumbnails),
        Pane(bridge, config, "right", previews=previews, thumbnails=thumbnails),
        Volumes(), TransferQueue(), None, Favorites(config),
        Capacity(bridge, config))
    made.resize(1200, 600)
    made.show()
    QApplication.processEvents()
    yield made
    made.hide()


def fill(pane, names):
    """Put rows into a pane's model the way a listing would."""
    model = pane.current.model
    model.begin(has_parent=False)
    model.add([entry(name) for name in names])
    model.finish()


def test_the_grid_and_the_listing_are_one_selection(window):
    """The reason nothing else in `ui/pane.py` had to change when the grid
    arrived. One selection model means F5, Del, the context menu and the group
    keys all act on what is marked without asking which view is in front -- and
    it means switching view cannot lose a selection, because there is only one.
    """
    from PySide6.QtCore import QItemSelectionModel

    widget = window._widgets[0]
    fill(window._panes[0], ["a.jpg", "b.jpg", "c.jpg", "d.txt"])
    assert widget._grid.selectionModel() is widget._view.selectionModel()

    model = window._panes[0].current.model
    picker = widget._view.selectionModel()
    picker.select(model.index(1, 0),
                  QItemSelectionModel.Select | QItemSelectionModel.Rows)
    widget.set_view_mode("grid")
    assert widget.selected_names() == ["b.jpg"]
    widget.set_view_mode("list")
    assert widget.selected_names() == ["b.jpg"]


def test_switching_a_tab_does_not_break_the_sharing(window):
    """`setModel` makes a new selection model and drops the shared one, so the
    sharing has to be redone on every tab change. Missing it would leave the
    grid selecting independently of the listing, which looks like nothing at all
    until a delete acts on the wrong files."""
    widget = window._widgets[0]
    window._panes[0].open_tab("C:\\Jobs\\other")
    from PySide6.QtWidgets import QApplication
    QApplication.processEvents()
    assert widget._grid.selectionModel() is widget._view.selectionModel()
    assert widget._grid.model() is window._panes[0].current.model


def test_f3_asks_for_the_viewer_rather_than_stepping_a_search(window):
    """F3 was find-next until 0.16. The key moved because a viewer is worth a
    bare function key and stepping a quick search is not -- and because it is
    what Double Commander does with it."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent

    widget = window._widgets[0]
    fill(window._panes[0], ["a.jpg", "b.jpg", "c.jpg"])
    asked = []
    widget.viewRequested.connect(lambda folder, names, at: asked.append((names, at)))
    widget.keyPressEvent(QKeyEvent(QKeyEvent.KeyPress, Qt.Key_F3, Qt.NoModifier))
    assert asked and asked[0][0] == ["a.jpg", "b.jpg", "c.jpg"]


def test_the_quick_search_still_steps_on_ctrl_g(window):
    """The other half of that move. Losing find-next entirely would be the
    change quietly costing something."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent

    widget = window._widgets[0]
    fill(window._panes[0], ["alpha.txt", "apple.txt", "avocado.txt"])
    widget._set_search("a")
    first = widget.current_row()
    handled = widget._on_search_key(
        QKeyEvent(QKeyEvent.KeyPress, Qt.Key_G, Qt.ControlModifier))
    assert handled
    assert widget.current_row() != first


def test_ctrl_g_with_nothing_ever_searched_is_not_taken(window):
    """Only the keys the search actually uses are taken, so a key pressed
    before anything has been looked for goes on reaching everything it reached
    before."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent

    widget = window._widgets[0]
    fill(window._panes[0], ["a.txt"])
    assert not widget._on_search_key(
        QKeyEvent(QKeyEvent.KeyPress, Qt.Key_G, Qt.ControlModifier))


def test_ctrl_g_still_steps_after_the_search_has_stopped_narrowing(window):
    """Reported from the window as "Ctrl+G doesn't seem to bring anything up".

    The quick search stops accumulating 1.5 seconds after the last keystroke,
    which is about not extending a search nobody remembers typing. Find-next
    was reading that as "there is no search", so the key worked only within a
    second and a half of typing and did nothing at any other time -- which is
    not a key that reads as unavailable, it reads as a key that is broken.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent

    widget = window._widgets[0]
    fill(window._panes[0], ["alpha.txt", "apple.txt", "avocado.txt", "zebra.txt"])
    widget._set_search("a")
    widget._clear_search()          # what the timer does, and only that
    assert widget._search == ""
    at = widget.current_row()
    assert widget._on_search_key(
        QKeyEvent(QKeyEvent.KeyPress, Qt.Key_G, Qt.ControlModifier))
    assert widget.current_row() != at


def test_reviving_a_search_steps_exactly_one_match(window):
    """Reviving must not also jump. `_set_search` moves to the first match from
    the cursor, so going through it would make one Ctrl+G move two."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent

    widget = window._widgets[0]
    fill(window._panes[0], ["a1.txt", "a2.txt", "a3.txt", "a4.txt"])
    widget._set_search("a")
    first = widget.current_row()
    widget._clear_search()
    widget._on_search_key(QKeyEvent(QKeyEvent.KeyPress, Qt.Key_G, Qt.ControlModifier))
    assert widget.current_row() == first + 1


def test_escape_forgets_the_term_and_the_timeout_does_not(window):
    """The two callers of `_clear_search` mean different things. Escape is
    somebody saying they are done; the timer is somebody having stopped typing.
    """
    widget = window._widgets[0]
    fill(window._panes[0], ["alpha.txt"])
    widget._set_search("a")
    widget._clear_search()
    assert widget._last_search == "a"
    widget._clear_search(forget=True)
    assert widget._last_search == ""


def test_leaving_the_folder_forgets_the_term(window):
    """A Ctrl+G in a new folder stepping a name typed in the last one would be
    the cursor jumping for a reason nothing on screen explains."""
    from PySide6.QtWidgets import QApplication

    widget = window._widgets[0]
    fill(window._panes[0], ["alpha.txt"])
    widget._set_search("a")
    window._panes[0].open_tab("C:\\Jobs\\elsewhere")
    QApplication.processEvents()
    assert widget._last_search == ""


def test_the_grid_gets_the_keys_that_have_to_beat_the_view(window):
    """The event filter named one view and the grid was the second.

    Everything that reads `selectionModel()` kept working in grid view because
    the selection model is shared, which is exactly why this was invisible: the
    commands were all fine. What broke was only the keys that have to be caught
    *before* a view answers them -- Space, the group keys, and the quick search
    -- so in the grid, Space marked a cell instead of counting a folder and
    typing a name went to Qt's own prefix jump.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent

    widget = window._widgets[0]
    fill(window._panes[0], ["alpha.txt", "apple.txt"])
    widget.set_view_mode("grid")
    typed = QKeyEvent(QKeyEvent.KeyPress, Qt.Key_A, Qt.NoModifier, "a")
    assert widget.eventFilter(widget._grid, typed)
    assert widget._search == "a"


def test_the_viewer_walks_files_and_not_folders(window):
    """There is nothing to view in a folder, and a walk that jumped over every
    folder in the listing would read as the arrow keys skipping."""
    model = window._panes[0].current.model
    model.begin(has_parent=False)
    model.add([entry("Site photos", is_dir=True), entry("a.jpg"),
               entry("Survey", is_dir=True), entry("b.jpg")])
    model.finish()
    assert window._panes[0].file_names() == ["a.jpg", "b.jpg"]


def test_the_preview_panel_asks_for_the_row_under_the_cursor(window):
    """And for nothing while it is closed, which is what keeps the whole feature
    free for somebody who never opens it."""
    from PySide6.QtCore import QItemSelectionModel
    from PySide6.QtWidgets import QApplication

    widget = window._widgets[0]
    pane = window._panes[0]
    fill(pane, ["a.jpg", "b.jpg"])
    bridge = pane.previews._bridge
    before = len(bridge.sent)
    widget._view.selectionModel().setCurrentIndex(
        pane.current.model.index(0, 0), QItemSelectionModel.NoUpdate)
    QApplication.processEvents()
    assert len(bridge.sent) == before      # the panel is closed

    widget.show_preview(True)
    pane.previews.fire_now()
    assert len(bridge.sent) == before + 1
    assert bridge.sent[-1]["op"] is Op.PREVIEW


def test_closing_the_panel_withdraws_what_it_was_waiting_for(window):
    """A panel nobody can see is a read nobody is waiting for, and the abandoned
    decode is still holding the volume the next listing wants."""
    from PySide6.QtCore import QItemSelectionModel

    widget = window._widgets[0]
    pane = window._panes[0]
    fill(pane, ["a.jpg"])
    # The cursor is put on the row rather than left to land there. Whether it
    # lands on its own after a reset is `_on_rows_settled`'s business and is
    # timing that does not reproduce against a model filled in one go; what is
    # under test here is what closing the panel does to a request that exists.
    widget._view.selectionModel().setCurrentIndex(
        pane.current.model.index(0, 0), QItemSelectionModel.NoUpdate)
    widget.show_preview(True)
    pane.previews.fire_now()
    bridge = pane.previews._bridge
    assert bridge.sent
    widget.show_preview(False)
    assert bridge.cancelled


def _png_bytes() -> bytes:
    from PySide6.QtCore import QBuffer, QByteArray
    from PySide6.QtGui import QImage

    image = QImage(8, 8, QImage.Format_ARGB32)
    image.fill(0xFF336699)
    store = QByteArray()
    buffer = QBuffer(store)
    buffer.open(QBuffer.WriteOnly)
    image.save(buffer, "PNG")
    buffer.close()
    return bytes(store.data())

"""Photo details (0.54): EXIF and a histogram beside a picture, never for a
grid cell, and the widgets that draw them."""

from __future__ import annotations

import time

import pytest

PIL = pytest.importorskip("PIL")

from PIL import Image  # noqa: E402
from PIL.TiffImagePlugin import IFDRational  # noqa: E402

from app.io import decode, photo  # noqa: E402
from app.io.protocol import PreviewForm  # noqa: E402


@pytest.fixture
def shot(tmp_path):
    picture = Image.new("RGB", (640, 480), (120, 90, 60))
    exif = Image.Exif()
    exif[0x010F] = "Canon"
    exif[0x0110] = "Canon EOS R5"
    inner = exif.get_ifd(0x8769)
    inner[0x9003] = "2026:09:24 07:41:03"
    inner[0x829A] = IFDRational(1, 250)
    inner[0x829D] = IFDRational(56, 10)
    inner[0x8827] = 400
    inner[0x920A] = IFDRational(50, 1)
    gps = exif.get_ifd(0x8825)
    gps[1], gps[3] = "S", "E"
    gps[2] = (IFDRational(33, 1), IFDRational(52, 1), IFDRational(0, 1))
    gps[4] = (IFDRational(151, 1), IFDRational(12, 1), IFDRational(36, 1))
    path = tmp_path / "IMG_0001.jpg"
    picture.save(path, exif=exif)
    return str(path)


def test_the_tags_worth_showing_are_read(shot):
    found = photo.read_exif(shot)
    assert found["camera"] == "Canon EOS R5"
    assert found["taken"] == "24 Sep 2026 07:41"
    assert (found["exposure"], found["aperture"], found["iso"]) == ("1/250 s", "f/5.6", "ISO 400")
    assert found["focal"] == "50 mm"
    assert found["location"] == "-33.86667, 151.21000"


def test_a_picture_without_tags_has_only_its_histogram(tmp_path):
    path = tmp_path / "plain.png"
    Image.new("L", (64, 64), 200).save(path)
    shown = decode.preview(str(path), box=256, deadline=time.monotonic() + 10, details=True)
    assert set(shown.details) == {"kind", "histogram"}
    assert len(shown.details["histogram"]) == photo.BINS
    assert sum(shown.details["histogram"][24:]) > 0 and sum(shown.details["histogram"][:8]) == 0


def test_details_come_only_when_asked_and_never_for_a_grid_cell(shot):
    assert decode.preview(shot, box=256, deadline=time.monotonic() + 10).details is None
    cell = decode.preview(shot, box=128, deadline=time.monotonic() + 10, text_bytes=0,
                          details=True)
    assert cell.form is PreviewForm.IMAGE and cell.details is None
    asked = decode.preview(shot, box=256, deadline=time.monotonic() + 10, details=True)
    assert asked.details["kind"] == "photo"


def test_words_for_numbers():
    assert photo.exposure_text(1 / 120) == "1/120 s"
    assert photo.exposure_text(2.5) == "2.5 s"
    assert photo.camera_name("Apple", "iPhone 15 Pro") == "Apple iPhone 15 Pro"
    assert photo.camera_name("NIKON", "NIKON Z 6") == "NIKON Z 6"


def test_the_panel_lays_the_rows_out_in_a_column_or_a_band(shot):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from app.ui.photoinfo import PhotoInfo, rows

    app = QApplication.instance() or QApplication([])
    details = photo.details(shot, None)
    assert [label for label, _ in rows(details)] == [
        "Taken", "Camera", "Exposure", "Focal length", "Location"]
    panel = PhotoInfo()
    panel.show_details(details)
    panel.set_band(True)
    panel.show_details(details)
    app.processEvents()
    texts = [panel._grid.itemAt(i).widget().text() for i in range(panel._grid.count())]
    assert texts.count("Taken") == 1 and len(texts) == 10
    panel.show_details(None)
    assert not panel.isVisible()

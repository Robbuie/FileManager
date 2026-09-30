"""One window (0.41): the parts that are not a named pipe."""

from __future__ import annotations

import pytest

from app.io import instance


@pytest.mark.parametrize("argument, folder", [
    ('C:"', "C:\\"),                      # "C:\" after Windows splits it
    ("D:", "D:\\"),
    ('"S:\\Jobs\\2026-09-29"', "S:\\Jobs\\2026-09-29"),
    ("\\\\server\\share\\Jobs", "\\\\server\\share\\Jobs"),
    ("", ""),
])
def test_what_explorer_passes_is_made_into_a_folder(argument, folder):
    assert instance.clean_folder(argument) == folder


def test_a_message_round_trips_and_junk_is_refused():
    assert instance.decode(instance.encode("S:\\Jobs")) == "S:\\Jobs"
    assert instance.decode(instance.encode(None)) == ""
    assert instance.decode(b"\xff\xfe") is None
    assert instance.decode(b'{"open": 5}') is None
    assert instance.decode(b"[1, 2]") is None


def test_the_pipe_is_per_user_and_named_safely(monkeypatch):
    monkeypatch.setenv("USERNAME", "rj okr\\..")
    assert instance.pipe_name() == r"\\.\pipe\FileManager.rjokr"


def test_off_windows_it_starts_as_the_only_window():
    if instance.available():
        pytest.skip("Windows answers with a real pipe")
    assert instance.claim() == (True, None)
    assert instance.send("C:\\") is False


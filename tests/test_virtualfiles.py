"""0.62.2: the table a Remote Desktop session puts on the clipboard."""

import struct

from app.io import virtualfiles as vf


def _descriptor(name, *, flags=0, attributes=0, size=0, written=0):
    return vf._LAYOUT.pack(flags, b"\0" * 16, b"\0" * 8, b"\0" * 8, attributes,
                           0, 0, written, size >> 32, size & 0xFFFFFFFF,
                           name.encode("utf-16-le").ljust(520, b"\0"))


def test_a_table_reads_back_names_sizes_and_folders():
    written = vf._EPOCH_AS_FILETIME + 10_000_000 * 1_700_000_000
    raw = struct.pack("<I", 2) \
        + _descriptor("Logix", flags=vf.FD_ATTRIBUTES,
                      attributes=vf.FILE_ATTRIBUTE_DIRECTORY) \
        + _descriptor("Logix\\Line 3.ACD", flags=vf.FD_FILESIZE | vf.FD_WRITESTIME,
                      size=5_000_000_000, written=written)
    folder, file = vf.parse(raw)
    assert folder.is_dir and folder.name == "Logix" and folder.size is None
    assert not file.is_dir and file.index == 1
    assert file.name == "Logix\\Line 3.ACD"
    assert file.size == 5_000_000_000
    assert file.mtime == 1_700_000_000


def test_a_short_table_stops_rather_than_inventing_rows():
    raw = struct.pack("<I", 3) + _descriptor("one.txt")
    assert [d.name for d in vf.parse(raw)] == ["one.txt"]
    assert vf.parse(b"") == []


def test_names_that_escape_the_folder_are_refused():
    assert vf.safe_parts("a\\b.txt") == ["a", "b.txt"]
    for bad in ("", "\\x", "C:\\x", "..\\x", "a\\..\\..\\x", "a|b", "\\\\server\\s"):
        assert vf.safe_parts(bad) is None, bad

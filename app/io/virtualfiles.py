"""Files on the clipboard that have no path: what they are called and where
they go.

A Remote Desktop session offers the files copied inside it to this machine as
`FileGroupDescriptorW` -- a table of names, sizes and dates -- and one
`FileContents` stream per file, fetched across the connection only when it is
asked for. Outlook attachments arrive the same way. There is no `CF_HDROP` and
no path, so the queue, which copies from paths, cannot take them; the worker
reads the streams instead (`Op.PASTE_VIRTUAL`).

This module is the half of that which needs no Windows: reading the table and
deciding whether a name is safe to write. It is separate so both are tested
everywhere. The names come from another machine, and a name in that table is
joined onto a real folder here -- the archive rule applies for the archive's
reason: a name that is absolute, has a drive or climbs with `..` is refused,
because `os.path.join` would otherwise write it somewhere else entirely.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

#: Bytes in one `FILEDESCRIPTORW`: flags, CLSID, SIZEL, POINTL, attributes,
#: three FILETIMEs, the size as two words, and a 260-character name.
DESCRIPTOR_SIZE = 592
_LAYOUT = struct.Struct("<I16s8s8sIQQQII520s")

FD_ATTRIBUTES = 0x0004
FD_CREATETIME = 0x0008
FD_WRITESTIME = 0x0020
FD_FILESIZE = 0x0040
FILE_ATTRIBUTE_DIRECTORY = 0x10

#: FILETIME counts 100 ns from 1601; this is 1970 in those units.
_EPOCH_AS_FILETIME = 116444736000000000


@dataclass(frozen=True, slots=True)
class Described:
    """One entry of the table, in this application's terms."""

    index: int          # the `lindex` its contents are asked for with
    name: str           # relative, backslash-separated, as sent
    is_dir: bool
    size: int | None    # None when the sender did not say
    mtime: float | None
    ctime: float | None


def _seconds(filetime: int) -> float | None:
    if filetime <= 0:
        return None
    return (filetime - _EPOCH_AS_FILETIME) / 10_000_000


def parse(raw: bytes) -> list[Described]:
    """The table, or an empty list for one too short to hold what it claims."""
    data = bytes(raw or b"")
    if len(data) < 4:
        return []
    (count,) = struct.unpack_from("<I", data, 0)
    found: list[Described] = []
    for index in range(count):
        offset = 4 + index * DESCRIPTOR_SIZE
        if offset + DESCRIPTOR_SIZE > len(data):
            break
        (flags, _clsid, _sizel, _pointl, attributes, created, _accessed, written,
         high, low, name) = _LAYOUT.unpack_from(data, offset)
        text = name.decode("utf-16-le", errors="replace").split("\x00", 1)[0]
        is_dir = bool(flags & FD_ATTRIBUTES and attributes & FILE_ATTRIBUTE_DIRECTORY)
        found.append(Described(
            index=index,
            name=text.replace("/", "\\"),
            is_dir=is_dir,
            size=((high << 32) | low) if flags & FD_FILESIZE and not is_dir else None,
            mtime=_seconds(written) if flags & FD_WRITESTIME else None,
            ctime=_seconds(created) if flags & FD_CREATETIME else None,
        ))
    return found


def safe_parts(name: str) -> list[str] | None:
    """The folders and name a relative entry writes, or None to refuse it."""
    if not name or name.startswith("\\") or ":" in name:
        return None
    parts = [part for part in name.split("\\") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        return None
    if any(any(c in part for c in '<>"|?*') or ord(min(part)) < 32 for part in parts):
        return None
    return parts

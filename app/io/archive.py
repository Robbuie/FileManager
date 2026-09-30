"""Archives as folders (0.41): read a .zip or a tar as if it were a directory.

A path that goes *into* an archive is the archive's own path followed by the
names inside it: `S:\\Jobs\\export.zip\\HMI\\screen.mer`. `split` finds the
archive in such a path by its suffix, and every worker operation that reads --
LIST, FOLDERS, DIR_SIZE, PREVIEW, THUMBNAIL, OPEN -- asks it first, and falls
back to the ordinary filesystem when the "archive" turns out to be a folder
that merely has `.zip` in its name. The transfer engine asks it too, which is
how F5 out of an archive is an extraction.

Read-only, on purpose. Writing into a zip means rewriting the whole file, and
doing that safely -- beside the original, renamed over it, the transfer
engine's rule -- on a 2 GB archive on a share is a feature of its own. Every
write aimed inside an archive is refused with a sentence saying so.

Only the standard library: `zipfile` and `tarfile`. 7-Zip and RAR would need a
bundled binary, which is exactly the kind of weight 0.39 went looking for.

**The index.** An archive's table of contents is read once and kept, per
worker process, for the few archives looked at most recently -- keyed on the
file's path, size and modified time, so a changed archive is read again and an
unchanged one costs one `stat` per listing, which is what lets the live check
poll an archive folder like any other. A zip keeps its table at the end of the
file and reading it is fast whatever the size; a compressed tar has none and
has to be read through, which is why a large `.tar.gz` is slower to open.

**Names.** A member's name is split on `/` and checked. One that leaves the
archive -- an absolute path, a drive, a `..` -- is left out of the index
entirely ("zip slip"): the engine joins these names onto a real destination,
and a name that escapes it would write somewhere nobody chose. A name Windows
cannot hold is left out too, and counted, so the listing can say some were.
"""

from __future__ import annotations

import datetime
import hashlib
import os
import re
import shutil
import tarfile
import threading
import time
import zipfile
from dataclasses import dataclass, field
from typing import Callable, Iterator

from app.io import paths
from app.io.protocol import Entry

#: Suffixes read as archives, longest first so `.tar.gz` wins over `.gz`.
ZIP_SUFFIXES = (".zip",)
TAR_SUFFIXES = (".tar.gz", ".tar.bz2", ".tar.xz", ".tgz", ".tbz2", ".txz", ".tar")
SUFFIXES = ZIP_SUFFIXES + TAR_SUFFIXES

#: How many archives' tables a worker keeps.
CACHED = 4

#: Characters a Windows name cannot hold, and the names it reserves.
_BAD = set('<>:"|?*') | {chr(n) for n in range(32)}
_RESERVED = re.compile(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?$", re.IGNORECASE)

#: The message a refused write carries, everywhere it is refused.
READ_ONLY = "archives are read-only here -- copy the files out, change them, and pack again"


def is_archive_name(name: str) -> bool:
    lowered = name.lower()
    return any(lowered.endswith(suffix) and len(lowered) > len(suffix) for suffix in SUFFIXES)


def split(path: str) -> tuple[str, str] | None:
    """`(archive, inner)` for a path whose components include an archive name.

    `inner` is the part inside, with backslashes, "" for the archive's root.
    Found by name only -- whether the archive is a file is the caller's
    question, asked where a filesystem call is allowed. The first component
    that looks like an archive is the archive: archives inside archives are
    not opened.
    """
    if not path:
        return None
    parts = re.split(r"([\\/])", path)
    # `parts` alternates component, separator, component...
    built = ""
    for index in range(0, len(parts), 2):
        component = parts[index]
        built += component
        if component and is_archive_name(component) and index > 0:
            rest = "".join(parts[index + 1:])
            inner = "\\".join(bit for bit in re.split(r"[\\/]", rest) if bit)
            return built, inner
        if index + 1 < len(parts):
            built += parts[index + 1]
    return None


def inside(path: str) -> bool:
    """Whether a path is *in* an archive -- not the archive file itself.

    `C:\\x\\a.zip` is a file; `C:\\x\\a.zip\\` and `C:\\x\\a.zip\\b` are in it.
    The trailing separator is what an "extract everything" job uses to mean
    the contents rather than the file.
    """
    found = split(path)
    if found is None:
        return False
    archive, inner = found
    return bool(inner) or len(path.rstrip("\\/")) < len(path) and path.rstrip("\\/") == archive


def kind_of(archive: str) -> str:
    lowered = archive.lower()
    return "zip" if lowered.endswith(ZIP_SUFFIXES) else "tar"


def usable(parts: list[str]) -> bool:
    """Whether a member's path, split into parts, can be a path on Windows."""
    if not parts:
        return False
    for part in parts:
        if part in ("", ".", "..") or part.endswith((" ", ".")):
            return False
        if _BAD & set(part) or _RESERVED.match(part):
            return False
    return True


@dataclass
class Member:
    name: str            # the name inside the archive, as the archive has it
    size: int
    mtime: float
    is_dir: bool


@dataclass
class Index:
    """One archive's table of contents, by folder."""

    kind: str
    #: Lower-cased inner folder ("" for the root) -> {lower-cased name: Entry}.
    folders: dict[str, dict[str, Entry]] = field(default_factory=dict)
    #: Lower-cased inner path -> the member it came from; folders that only
    #: exist because something is inside them have none.
    members: dict[str, Member] = field(default_factory=dict)
    #: Members left out because their names escape the archive or cannot
    #: exist on Windows.
    skipped: int = 0

    def _folder(self, inner: str) -> dict[str, Entry]:
        return self.folders.setdefault(inner.lower(), {})

    def add(self, member: Member) -> None:
        parts = [bit for bit in member.name.replace("\\", "/").split("/") if bit != ""]
        if member.name.startswith(("/", "\\")) or (parts and ":" in parts[0]) \
                or not usable(parts):
            self.skipped += 1
            return
        # Every folder on the way exists, whether or not the archive lists it.
        for depth in range(len(parts) - 1):
            parent = "\\".join(parts[:depth])
            name = parts[depth]
            here = self._folder(parent)
            if name.lower() not in here:
                here[name.lower()] = Entry(name=name, is_dir=True, size=0,
                                           mtime=member.mtime, attributes=0)
        parent = "\\".join(parts[:-1])
        name = parts[-1]
        here = self._folder(parent)
        existing = here.get(name.lower())
        if member.is_dir:
            self._folder("\\".join(parts))
            if existing is None or not existing.is_dir:
                here[name.lower()] = Entry(name=name, is_dir=True, size=0,
                                           mtime=member.mtime, attributes=0)
        else:
            here[name.lower()] = Entry(name=name, is_dir=False, size=member.size,
                                       mtime=member.mtime, attributes=0)
        self.members["\\".join(parts).lower()] = member

    def listing(self, inner: str) -> list[Entry] | None:
        found = self.folders.get(inner.strip("\\").lower())
        return None if found is None else list(found.values())

    def entry(self, inner: str) -> Entry | None:
        inner = inner.strip("\\")
        parent, _sep, name = inner.rpartition("\\")
        return self.folders.get(parent.lower(), {}).get(name.lower())

    def member(self, inner: str) -> Member | None:
        return self.members.get(inner.strip("\\").lower())

    def files_under(self, inner: str) -> Iterator[tuple[str, Entry]]:
        """Every file below a folder, as (inner path, entry), folders first."""
        start = inner.strip("\\")
        stack = [start]
        while stack:
            folder = stack.pop()
            for entry in (self.folders.get(folder.lower()) or {}).values():
                child = f"{folder}\\{entry.name}" if folder else entry.name
                yield child, entry
                if entry.is_dir:
                    stack.append(child)

    def size_under(self, inner: str) -> tuple[int, int, int]:
        """Bytes, files and folders below an inner folder."""
        total = files = folders = 0
        for _path, entry in self.files_under(inner):
            if entry.is_dir:
                folders += 1
            else:
                files += 1
                total += entry.size
        return total, files, folders


def _zip_time(info: zipfile.ZipInfo) -> float:
    try:
        return datetime.datetime(*info.date_time).timestamp()
    except (ValueError, OverflowError):
        return 0.0


def _read_index(archive: str, deadline: float) -> Index:
    index = Index(kind=kind_of(archive))
    if index.kind == "zip":
        with zipfile.ZipFile(paths.api(archive)) as handle:
            for number, info in enumerate(handle.infolist()):
                if number % 2048 == 0 and time.monotonic() > deadline:
                    raise TimeoutError("reading the archive took too long")
                index.add(Member(info.filename, info.file_size, _zip_time(info),
                                 info.is_dir()))
        return index
    with tarfile.open(paths.api(archive), "r:*") as handle:
        for number, info in enumerate(handle):
            if number % 512 == 0 and time.monotonic() > deadline:
                raise TimeoutError("reading the archive took too long")
            if info.isdir():
                index.add(Member(info.name, 0, float(info.mtime), True))
            elif info.isfile():
                index.add(Member(info.name, int(info.size), float(info.mtime), False))
            else:
                # Links and devices have no content to copy out.
                index.skipped += 1
    return index


_cache: dict[tuple[str, int, float], Index] = {}
_order: list[tuple[str, int, float]] = []
_lock = threading.Lock()


def index_for(archive: str, deadline: float) -> Index:
    """The archive's table of contents, read now or remembered.

    Raises OSError when the archive cannot be read, `BadArchive` when it can
    but is not what its name says, TimeoutError past the deadline.
    """
    stat = os.stat(paths.api(archive))
    key = (os.path.normcase(archive), int(stat.st_size), float(stat.st_mtime))
    with _lock:
        found = _cache.get(key)
        if found is not None:
            _order.remove(key)
            _order.append(key)
            return found
    try:
        index = _read_index(archive, deadline)
    except (zipfile.BadZipFile, tarfile.TarError, EOFError) as exc:
        raise BadArchive(f"not a readable {kind_of(archive)} archive: {exc}") from exc
    with _lock:
        _cache[key] = index
        _order.append(key)
        while len(_order) > CACHED:
            _cache.pop(_order.pop(0), None)
    return index


class BadArchive(Exception):
    """A file named like an archive that is not one, or is damaged."""


def is_archive_file(archive: str) -> bool:
    """Whether the archive part of a path is a file: one stat, worker side."""
    try:
        return os.path.isfile(paths.api(archive))
    except OSError:
        return False


def extract(archive: str, inner: str, target: str, *,
            progress: Callable[[int], None] | None = None,
            checkpoint: Callable[[], None] | None = None,
            chunk: int = 1 << 20) -> None:
    """Write one member's bytes to `target`, which must not exist.

    In chunks, so a large member can be cancelled part way and reports as it
    goes. The caller writes to a partial name and renames it into place --
    this only fills the file it is given. The member's modified time is put
    on the result.
    """
    index = index_for(archive, time.monotonic() + 600)
    member = index.member(inner)
    if member is None or member.is_dir:
        raise FileNotFoundError(f"{inner} is not a file in {os.path.basename(archive)}")
    done = 0
    with _open_member(archive, index.kind, member) as source, \
            open(paths.api(target), "xb") as sink:
        while True:
            if checkpoint is not None:
                checkpoint()
            block = source.read(chunk)
            if not block:
                break
            sink.write(block)
            done += len(block)
            if progress is not None:
                progress(done)
    if member.mtime:
        try:
            os.utime(paths.api(target), (member.mtime, member.mtime))
        except OSError:
            pass


class _Closing:
    """A member's stream that closes the archive with it."""

    def __init__(self, stream, *owners) -> None:
        self._stream = stream
        self._owners = owners

    def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> None:
        for thing in (self._stream, *self._owners):
            try:
                thing.close()
            except Exception:  # noqa: BLE001
                pass


def _open_member(archive: str, kind: str, member: Member) -> _Closing:
    if kind == "zip":
        handle = zipfile.ZipFile(paths.api(archive))
        return _Closing(handle.open(member.name), handle)
    handle = tarfile.open(paths.api(archive), "r:*")
    stream = handle.extractfile(member.name)
    if stream is None:
        handle.close()
        raise FileNotFoundError(member.name)
    return _Closing(stream, handle)


# ------------------------------------------------------------ the temp copy

def temp_root() -> str:
    base = os.environ.get("TEMP") or os.environ.get("TMPDIR") or "/tmp"
    return os.path.join(base, "FileManager", "archives")


def temp_copy(archive: str, inner: str, *, limit: int | None = None) -> str:
    """A member extracted to a temp folder, for the things that need a file:
    opening it in its program, previewing it, drawing its thumbnail.

    The folder is named for the archive and its modified time, so the same
    member is extracted once and a changed archive gets fresh copies. The copy
    is marked read-only: an edit saved into it would be lost the moment the
    archive changed, and a program that refuses to save says so where a
    silent loss would not. `limit` refuses members larger than it, for the
    preview surfaces that should never extract a gigabyte to draw a picture.
    """
    index = index_for(archive, time.monotonic() + 60)
    member = index.member(inner)
    if member is None or member.is_dir:
        raise FileNotFoundError(f"{inner} is not a file in {os.path.basename(archive)}")
    if limit is not None and member.size > limit:
        raise ValueError("too large to extract for a preview")
    stamp = int(os.stat(paths.api(archive)).st_mtime)
    digest = hashlib.sha1(os.path.normcase(archive).encode("utf-8", "replace")).hexdigest()
    tag = f"{digest[:12]}-{stamp}"
    folder = os.path.join(temp_root(), tag, *inner.split("\\")[:-1])
    target = os.path.join(folder, inner.split("\\")[-1])
    if os.path.isfile(target) and os.path.getsize(target) == member.size:
        return target
    os.makedirs(folder, exist_ok=True)
    partial = target + ".fm-part"
    try:
        os.remove(partial)
    except OSError:
        pass
    try:
        extract(archive, inner, partial)
        if os.path.exists(target):
            try:
                os.chmod(target, 0o666)
                os.remove(target)
            except OSError:
                pass
        os.replace(partial, target)
    except BaseException:
        try:
            os.remove(partial)
        except OSError:
            pass
        raise
    try:
        os.chmod(target, 0o444)
    except OSError:
        pass
    return target


def clear_temp(older_than_days: float = 2.0) -> None:
    """Remove temp copies from earlier sessions. Best effort, never raises."""
    root = temp_root()
    try:
        entries = list(os.scandir(root))
    except OSError:
        return
    cutoff = time.time() - older_than_days * 86400
    for entry in entries:
        try:
            if entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry.path, onerror=_writable_and_retry)
        except OSError:
            pass


def _writable_and_retry(function, path, _info) -> None:
    try:
        os.chmod(path, 0o666)
        function(path)
    except OSError:
        pass

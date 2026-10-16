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

Zip and tar with the standard library: `zipfile` and `tarfile`. Since 0.51,
`.7z` and `.rar` through the 7-Zip already installed on the machine -- `7z l`
for the table and `7z e -so` for a member's bytes, run in the worker like any
other read. Nothing is bundled: a 7-Zip inside the installer is the kind of
weight 0.39 went looking for, and the machines this runs on have 7-Zip
anyway. Without it, a `.7z` says so rather than opening.

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
import subprocess
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
#: 0.51: read through the installed 7-Zip, never bundled.
SEVEN_SUFFIXES = (".7z", ".rar")
SUFFIXES = ZIP_SUFFIXES + TAR_SUFFIXES + SEVEN_SUFFIXES

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
    if lowered.endswith(ZIP_SUFFIXES):
        return "zip"
    if lowered.endswith(SEVEN_SUFFIXES):
        return "7z"
    return "tar"


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
    if index.kind == "7z":
        for member in seven_members(archive, deadline):
            index.add(member)
        return index
    if index.kind == "zip":
        # 0.58.1: through `open_shared`, so browsing an archive does not stop
        # it being deleted. A ZipFile handed a file does not close it, hence
        # the second name in the `with`.
        with paths.open_shared(archive, "rb") as raw, zipfile.ZipFile(raw) as handle:
            for number, info in enumerate(handle.infolist()):
                if number % 2048 == 0 and time.monotonic() > deadline:
                    raise TimeoutError("reading the archive took too long")
                index.add(Member(info.filename, info.file_size, _zip_time(info),
                                 info.is_dir()))
        return index
    with paths.open_shared(archive, "rb") as raw, \
            tarfile.open(fileobj=raw, mode="r:*") as handle:
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
        # A stream that ended early is a damaged archive or a wrong password,
        # not a short file: the caller's partial is discarded, never renamed.
        source.verify()
    if done != member.size:
        raise OSError(f"{inner}: read {done:,} of {member.size:,} bytes from the archive")
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

    def verify(self) -> None:
        """Raise if the stream did not end cleanly. Only 7-Zip's can fail
        this way -- `zipfile` and `tarfile` raise as they read."""

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> None:
        for thing in (self._stream, *self._owners):
            try:
                thing.close()
            except Exception:  # noqa: BLE001
                pass


def _open_member(archive: str, kind: str, member: Member) -> _Closing:
    if kind == "7z":
        return _SevenStream(archive, member)
    if kind == "zip":
        raw = paths.open_shared(archive, "rb")
        try:
            handle = zipfile.ZipFile(raw)
            return _Closing(handle.open(member.name), handle, raw)
        except BaseException:
            raw.close()
            raise
    raw = paths.open_shared(archive, "rb")
    try:
        handle = tarfile.open(fileobj=raw, mode="r:*")
    except BaseException:
        raw.close()
        raise
    stream = handle.extractfile(member.name)
    if stream is None:
        handle.close()
        raw.close()
        raise FileNotFoundError(member.name)
    return _Closing(stream, handle, raw)


# ------------------------------------------------------------- 7-Zip (0.51)
#
# Three rules, each from a way a child program can hold a worker:
#
# - **stdin is closed and `-p` is always given.** 7-Zip asks for a password on
#   the console when an archive's names are encrypted, and a prompt nobody can
#   see is a worker waiting forever. With an empty password it fails at once,
#   and the failure says the archive is encrypted.
# - **Every run has the caller's deadline** and is killed at it, the way a
#   worker is killed by the pool.
# - **`-spd`:** a member name is a name, not a wildcard. Windows names cannot
#   hold `*` or `?`, but 7-Zip also treats a name as a pattern to be matched
#   recursively unless told otherwise.

#: Overridden by the tests; None means "look for it".
SEVEN_ZIP: str | None = None

#: Kept per process once found, and so is not finding it.
_seven_found: list[str | None] = []

NO_SEVEN_ZIP = ("7-Zip is needed to open .7z and .rar files and was not found -- "
                "install it from 7-zip.org, or open the file from its right-click menu")


def _registry_7zip() -> list[str]:
    try:
        import winreg  # noqa: PLC0415 - Windows only
    except ImportError:
        return []
    found = []
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (0, getattr(winreg, "KEY_WOW64_64KEY", 0)):
            try:
                with winreg.OpenKey(hive, r"Software\7-Zip", 0,
                                    winreg.KEY_READ | view) as key:
                    for value in ("Path64", "Path"):
                        try:
                            folder, _type = winreg.QueryValueEx(key, value)
                        except OSError:
                            continue
                        found.append(os.path.join(str(folder), "7z.exe"))
            except OSError:
                continue
    return found


def seven_zip() -> str | None:
    """The installed 7-Zip's console program, or None.

    Where its installer registers it, then the two Program Files folders, then
    the PATH. Local reads only; asked once per process.
    """
    if SEVEN_ZIP is not None:
        return SEVEN_ZIP or None
    if _seven_found:
        return _seven_found[0]
    candidates = _registry_7zip()
    for variable in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(variable)
        if base:
            candidates.append(os.path.join(base, "7-Zip", "7z.exe"))
    found = next((path for path in candidates if os.path.isfile(path)), None)
    if found is None:
        found = shutil.which("7z") or shutil.which("7zz") or shutil.which("7za")
    _seven_found.append(found)
    return found


def _seven_path(archive: str) -> str:
    """The archive's path as 7-Zip is given it: plain while it is short,
    since a command line is not a Win32 file call, and the extended form only
    past the length where nothing else would reach it."""
    return archive if len(archive) < 240 else paths.api(archive)


def _seven(arguments: list[str], **extra) -> subprocess.Popen:
    program = seven_zip()
    if program is None:
        raise BadArchive(NO_SEVEN_ZIP)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.Popen([program, *arguments], stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=flags, **extra)


def _seven_error(text: str, archive: str) -> str:
    lowered = text.lower()
    if "wrong password" in lowered or "encrypted" in lowered:
        return f"{os.path.basename(archive)} is password protected -- open it in 7-Zip"
    lines = [line.strip() for line in text.splitlines() if line.strip()
             and not line.startswith(("7-Zip", "Scanning", "Break"))]
    detail = "; ".join(lines[-2:]) if lines else "7-Zip could not read it"
    return f"not a readable archive: {detail}"


def _seven_time(text: str) -> float:
    text = text.strip()
    if not text:
        return 0.0
    try:
        return datetime.datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S").timestamp()
    except (ValueError, OverflowError):
        return 0.0


def parse_slt(text: str) -> Iterator[Member]:
    """Members out of `7z l -slt -ba`: blocks of `Key = value`, blank-separated.

    A folder is `Folder = +` in some formats and an attribute string starting
    with `D` in others; either is believed.
    """
    for block in re.split(r"\r?\n\s*\r?\n", text):
        fields: dict[str, str] = {}
        for line in block.splitlines():
            key, sep, value = line.partition(" = ")
            if sep:
                fields[key.strip()] = value
            elif line.rstrip().endswith(" ="):
                fields[line.rstrip()[:-2].strip()] = ""
        name = fields.get("Path")
        if not name:
            continue
        is_dir = fields.get("Folder") == "+" or fields.get("Attributes", "").startswith("D")
        try:
            size = int(fields.get("Size") or 0)
        except ValueError:
            size = 0
        yield Member(name, 0 if is_dir else size, _seven_time(fields.get("Modified", "")),
                     is_dir)


def seven_members(archive: str, deadline: float) -> list[Member]:
    """The table of a 7-Zip-readable archive, within the deadline."""
    process = _seven(["l", "-slt", "-ba", "-sccUTF-8", "-p", "--", _seven_path(archive)])
    try:
        out, err = process.communicate(timeout=max(0.5, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise TimeoutError("reading the archive took too long") from None
    text = out.decode("utf-8", "replace")
    if process.returncode != 0:
        raise BadArchive(_seven_error(err.decode("utf-8", "replace") + text, archive))
    return list(parse_slt(text))


class _SevenStream(_Closing):
    """One member's bytes from `7z e -so`, closed by killing it if unfinished."""

    def __init__(self, archive: str, member: Member) -> None:
        self._archive = archive
        self._process = _seven(["e", "-so", "-spd", "-r-", "-bd", "-sccUTF-8", "-p",
                                "--", _seven_path(archive), member.name])
        super().__init__(self._process.stdout)

    def verify(self) -> None:
        try:
            self._process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self._process.kill()
            raise OSError("7-Zip did not finish") from None
        if self._process.returncode != 0:
            err = self._process.stderr.read().decode("utf-8", "replace") \
                if self._process.stderr else ""
            raise OSError(_seven_error(err, self._archive))

    def __exit__(self, *_exc) -> None:
        if self._process.poll() is None:
            self._process.kill()
        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        for stream in (self._process.stdout, self._process.stderr):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass


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

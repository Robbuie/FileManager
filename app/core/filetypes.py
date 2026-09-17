"""What family a file belongs to, for the badge on its row and the folder's bar.

0.27 draws a small tag with the extension in place of the icon, coloured by
family, and a bar above the listing that says what the folder is made of. Both
need one answer to "what kind of file is this", and it is here so they cannot
disagree.

The families are the ones that matter in this user's folders -- PLC projects,
HMI runtimes and archives, drawings -- before the ones every file manager has.
A family is a colour and nothing else: nothing here opens a file or asks
Windows, so the answer is instant for a 50,000-row listing and the same on a
share that has gone away as on a local disk.
"""

from __future__ import annotations

from collections.abc import Iterable

#: Family names in the order a legend lists them when they tie.
FAMILIES = ("logix", "hmi", "cad", "pdf", "sheet", "doc", "image", "archive",
            "code", "program", "text", "other")

LABELS = {
    "logix": "Logix", "hmi": "HMI", "cad": "Drawings", "pdf": "PDF",
    "sheet": "Sheets", "doc": "Documents", "image": "Images",
    "archive": "Archives", "code": "Code", "program": "Programs",
    "text": "Text", "other": "Other",
}

_EXTENSIONS: dict[str, tuple[str, ...]] = {
    "logix": ("acd", "l5x", "l5k", "l5z", "rss", "rsp", "ccwsln", "ccwarc",
              "ap17", "ap16", "ap15", "zap17", "zap16", "s7p", "gsd", "gsdml",
              "eds", "aml"),
    "hmi": ("mer", "apa", "med", "mvi", "cli", "pvi", "pvi2", "vbs"),
    "cad": ("dwg", "dxf", "dwf", "dwfx", "dgn", "step", "stp", "igs", "iges",
            "vsd", "vsdx"),
    "pdf": ("pdf",),
    "sheet": ("xls", "xlsx", "xlsm", "xlsb", "csv", "ods"),
    "doc": ("doc", "docx", "rtf", "odt", "ppt", "pptx", "msg", "eml"),
    "image": ("jpg", "jpeg", "png", "gif", "bmp", "tif", "tiff", "heic",
              "webp", "svg", "raw", "cr2", "nef", "mp4", "mov", "avi"),
    "archive": ("zip", "7z", "rar", "gz", "tar", "tgz", "bz2", "xz", "cab", "iso"),
    "code": ("py", "js", "ts", "json", "xml", "yaml", "yml", "ini", "cfg",
             "conf", "bat", "cmd", "ps1", "sh", "c", "h", "cpp", "cs", "html",
             "css", "sql", "st", "scl"),
    "program": ("exe", "msi", "dll", "lnk", "sys", "appx", "msix"),
    "text": ("txt", "log", "md", "nfo"),
}

_BY_EXT = {ext: family for family, exts in _EXTENSIONS.items() for ext in exts}

#: The tag drawn for a folder. Folders have no family; they get the neutral
#: chip with this in it.
FOLDER = "folder"

#: Longest tag. Past four characters the tag stops being a glance and starts
#: being a word, and the column it sits in is sized for four.
TAG_CHARS = 4


def extension(name: str) -> str:
    """The extension without its dot, lower case, or "" for none. A leading
    dot alone (".gitignore") is a name, not an extension."""
    dot = name.rfind(".")
    if dot <= 0 or dot == len(name) - 1:
        return ""
    return name[dot + 1:].lower()


def family(name: str, is_dir: bool = False) -> str:
    if is_dir:
        return FOLDER
    return _BY_EXT.get(extension(name), "other")


def tag(name: str, is_dir: bool = False) -> str:
    """The text on the badge: the extension in capitals, cut to four."""
    if is_dir:
        return ""
    ext = extension(name)
    return ext[:TAG_CHARS].upper() if ext else "FILE"


def composition(entries: Iterable) -> list[tuple[str, int, int]]:
    """What a folder is made of: `(family, bytes, files)`, largest first.

    By bytes, because a folder of one 40 MB HMI archive and thirty 2 KB notes
    is an HMI folder. Folders are skipped -- their sizes are not known without
    walking them. A folder of empty files falls back to counting, so it still
    has a bar.
    """
    size: dict[str, int] = {}
    count: dict[str, int] = {}
    for entry in entries:
        if entry.is_dir:
            continue
        kind = _BY_EXT.get(extension(entry.name), "other")
        size[kind] = size.get(kind, 0) + max(0, int(entry.size or 0))
        count[kind] = count.get(kind, 0) + 1
    if not count:
        return []
    by_bytes = sum(size.values()) > 0
    order = {name: i for i, name in enumerate(FAMILIES)}
    rows = [(kind, size.get(kind, 0), count[kind]) for kind in count]
    rows.sort(key=lambda row: (-(row[1] if by_bytes else row[2]), order[row[0]]))
    return rows

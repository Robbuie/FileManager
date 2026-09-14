"""What Ctrl+C, Ctrl+X and Ctrl+V mean here, and how Explorer is told.

The Windows clipboard is the one place this application has to agree with a
program it did not write. Copying in Explorer and pasting here, or the reverse,
is the gesture that decides whether this feels like part of the machine or like
a separate thing with its own rules -- so the format is Explorer's, exactly,
rather than anything of ours.

Two pieces make a file on the clipboard:

  * **`CF_HDROP`** -- the list of paths. Qt writes and reads it as a list of
    `file://` URLs, so `QMimeData.setUrls` is all that is needed on this side.
  * **`Preferred DropEffect`** -- a four-byte word saying whether this was a
    copy (1) or a cut (2). Qt has no name of its own for a Windows format it
    does not understand, so it passes one through under
    `application/x-qt-windows-mime;value="..."` with the Windows name inside.
    Without this word a cut is indistinguishable from a copy and Ctrl+X
    quietly becomes Ctrl+C, which is the way this feature is usually wrong.

Two things this deliberately does not do:

  * **No text of its own.** Qt synthesizes a text form from a URL list, which
    is standard and harmless; what this does not do is put the paths on as a
    deliberate, separate text copy. `Ctrl+Shift+C` is the key that copies a
    path as text and it is a separate gesture on purpose, because an
    application that quietly does both surprises whatever is on the other end
    of the paste.
  * **No filesystem call.** Nothing here asks whether a path exists, what size
    it is, or whether it is a folder. A clipboard is a list of names; the queue
    is what finds out whether they are still there, and it does that in its own
    process. A `Ctrl+V` that stopped to check a dead share before pasting would
    be the exact hang this application exists to escape, arriving through the
    one door nobody was watching.

The cut mark is a fact about the clipboard, not about the pane, which is why it
lives here: two panes, five tabs and Explorer can all be looking at the folder a
file was cut from, and every one of them should show it greyed. The clipboard
is the authority -- the mark is recomputed from whatever is on it whenever it
changes, rather than remembered from what this application last put there, so a
cut made in Explorer greys the row here too.
"""

from __future__ import annotations

import re
import struct
from typing import Iterable

from PySide6.QtCore import QMimeData, QObject, QUrl, Signal
from PySide6.QtGui import QGuiApplication

from app.io import paths

#: The Windows clipboard format name, wrapped the way Qt passes an unknown one
#: through. The quoting is part of the format string and not decoration.
DROP_EFFECT = 'application/x-qt-windows-mime;value="Preferred DropEffect"'

#: `DROPEFFECT_COPY` and `DROPEFFECT_MOVE` from the Windows headers. They are
#: flags rather than an enumeration, and some programs set more than one, so a
#: cut is read as "the move bit is set" rather than "the word equals 2".
DROPEFFECT_COPY = 1
DROPEFFECT_MOVE = 2

#: A leading separator in front of a drive letter, which `QUrl.toLocalFile`
#: leaves on everywhere except Windows. It matters because the tests run
#: everywhere: without this they would assert `\\C:\\Jobs` and prove nothing
#: about the machine this runs on. A stray one on Windows is wrong anyway.
_LEADING_DRIVE = re.compile(r"^[\\/]([A-Za-z]:)")


def local_path(url) -> str:
    """A `QUrl` as this application spells a path, or empty if it is not one."""
    text = url.toLocalFile()
    if not text:
        return ""
    return paths.normalize(_LEADING_DRIVE.sub(r"\1", text))


def effect_bytes(effect: int) -> bytes:
    """The four bytes Explorer expects: one little-endian unsigned word."""
    return struct.pack("<I", effect)


def read_effect(raw: bytes | bytearray | memoryview) -> int:
    """The word back out, or zero when there is nothing readable there.

    Zero rather than an exception: a program that put a `CF_HDROP` on the
    clipboard without a drop effect has copied, which is what Windows assumes
    too, and an empty clipboard is not an error to report.
    """
    data = bytes(raw or b"")
    if len(data) < 4:
        return 0
    return struct.unpack("<I", data[:4])[0]


def refusal(sources: Iterable[str], destination: str, *, cut: bool,
            mapping=None) -> str:
    """Why this paste must not happen, in the words the status line will use.

    An empty string means go ahead. Three of these are worth stopping for and
    the first is the only one that could lose work:

      * **A folder into itself or into its own subtree.** A copy of a folder
        into a folder inside it is a walk that keeps finding what it has just
        written. The queue would eventually fill the disk; the answer is not
        to start.
      * **A cut pasted back where it came from.** A move from a folder to
        itself is nothing happening, and running it through the queue would
        say "moved" about files that never went anywhere.
      * **Nothing on the clipboard**, which is worth a word rather than a
        silent key.

    A copy pasted into the folder it came from is *not* refused: that is how a
    duplicate is made, and the caller asks the queue to rename rather than to
    ask about every name.

    All the comparing is done on the resolved form, because `S:\\Jobs` and
    `\\\\server\\jobs` are one folder and a check that believed otherwise would
    let a folder be pasted into itself through a drive letter.
    """
    items = [paths.normalize(item) for item in sources if item]
    if not items:
        return "nothing on the clipboard"
    target = paths.resolve(paths.normalize(destination), mapping=mapping)
    if not target:
        return "nowhere to paste to"
    lowered = target.lower()
    for item in items:
        source = paths.resolve(item, mapping=mapping)
        low = source.lower()
        if lowered == low:
            return f"{paths.leaf(item)} cannot be pasted into itself"
        if lowered.startswith(low.rstrip("\\") + "\\"):
            return f"{paths.leaf(item)} cannot be pasted into a folder inside it"
    if cut:
        parents = {paths.resolve(paths.parent(item) or item, mapping=mapping).lower()
                   for item in items}
        if parents == {lowered}:
            return "already in this folder"
    return ""


class Clipboard(QObject):
    """The system clipboard, as this application needs to see it.

    One of these for the window, shared by both panes: a cut made in one pane
    is greyed in the other, and both paste from the same place.
    """

    #: Something changed -- here or in another program. The panes redraw, and
    #: the menu entries work out whether Paste has anything to do.
    changed = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        #: Resolved paths currently marked cut. Recomputed from the clipboard.
        self._cut: frozenset[str] = frozenset()
        #: One answer per folder, so a repaint of 50,000 rows is a set lookup
        #: rather than 50,000 path resolutions. Dropped whenever the clipboard
        #: changes, which is the only thing that can invalidate it.
        self._by_folder: dict[str, frozenset[str]] = {}
        board = QGuiApplication.clipboard()
        if board is not None:
            board.dataChanged.connect(self._on_data_changed)

    # -------------------------------------------------------------- commands

    def copy(self, sources: Iterable[str]) -> bool:
        """Put these on the clipboard as a copy."""
        return self._put(sources, DROPEFFECT_COPY)

    def cut(self, sources: Iterable[str]) -> bool:
        """Put these on the clipboard as a cut, greyed until something moves."""
        return self._put(sources, DROPEFFECT_MOVE)

    def clear(self) -> None:
        """Take everything off, the way Explorer does after a cut is pasted.

        A cut that stayed on the clipboard after the move would offer to move
        the files a second time, from a folder they are no longer in.
        """
        board = QGuiApplication.clipboard()
        if board is not None:
            board.clear()
        self._cut = frozenset()
        self._by_folder.clear()
        self.changed.emit()

    # --------------------------------------------------------------- reading

    def contents(self) -> tuple[list[str], bool]:
        """What is on the clipboard: the paths, and whether it was a cut."""
        board = QGuiApplication.clipboard()
        data = board.mimeData() if board is not None else None
        if data is None or not data.hasUrls():
            return [], False
        found = []
        for url in data.urls():
            local = local_path(url)
            if local:
                found.append(local)
        cut = bool(read_effect(data.data(DROP_EFFECT)) & DROPEFFECT_MOVE)
        return found, cut

    def has_files(self) -> bool:
        """Whether Paste has anything to do, without resolving any of it."""
        board = QGuiApplication.clipboard()
        data = board.mimeData() if board is not None else None
        return bool(data is not None and data.hasUrls())

    def cut_names(self, folder: str) -> frozenset[str]:
        """The names in this folder that are marked cut.

        Names rather than paths, and one answer per folder rather than one per
        row: this is asked once for every row that paints, so the work has to
        have happened already. The resolution of the folder is the only real
        cost and it happens once, on the first row.
        """
        if not self._cut or not folder:
            return frozenset()
        key = paths.normalize(folder)
        known = self._by_folder.get(key)
        if known is None:
            here = paths.resolve(key).lower()
            known = frozenset(
                paths.leaf(item) for item in self._cut
                if paths.resolve(paths.parent(item) or "").lower() == here)
            self._by_folder[key] = known
        return known

    def is_cut(self, folder: str, name: str) -> bool:
        return name in self.cut_names(folder)

    # ---------------------------------------------------------------- inside

    def _put(self, sources: Iterable[str], effect: int) -> bool:
        board = QGuiApplication.clipboard()
        items = [paths.normalize(item) for item in sources if item]
        if board is None or not items:
            return False
        data = QMimeData()
        data.setUrls([QUrl.fromLocalFile(item) for item in items])
        data.setData(DROP_EFFECT, effect_bytes(effect))
        board.setMimeData(data)
        # Set here as well as in the change handler. `dataChanged` is what
        # keeps this honest about another program's clipboard, but it does not
        # always arrive before the next repaint, and a cut whose rows grey a
        # moment later reads as a key that did not take.
        self._remember(items if effect & DROPEFFECT_MOVE else [])
        self.changed.emit()
        return True

    def _on_data_changed(self) -> None:
        """The clipboard is the authority, including when it was not us.

        Recomputed rather than remembered: a cut made in Explorer greys the
        rows here, and a copy made anywhere clears a cut mark this application
        put up -- which is what Explorer does with its own.
        """
        found, cut = self.contents()
        self._remember(found if cut else [])
        self.changed.emit()

    def _remember(self, items: Iterable[str]) -> None:
        self._cut = frozenset(paths.resolve(item) for item in items)
        self._by_folder.clear()

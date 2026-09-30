"""The folder map's walk (0.43): one `Op.WALK`, collected into a tree.

The walk is the one flat view uses, sent to the folder's own worker, so a
share that stops answering costs this map and nothing else, and the pool's
watchdog and cancel apply as they do to any walk. Rows arrive in batches and
are kept as `(path, size)` only; the tree is built once, when the walk ends --
building it per batch would redraw a map that is about to change again.

One map at a time for the window: a second one asked for cancels the first
at the worker, the breadcrumb chevron's rule, because an abandoned walk still
holds the volume the next one wants.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, Signal

from app.core import treemap
from app.io import paths
from app.io.protocol import Op, Reply, Status


class FolderMap(QObject):
    #: Files seen so far, while the walk runs.
    progress = Signal(int)
    #: The tree, and a note on anything it is missing ("" when complete).
    ready = Signal(object, str)
    failed = Signal(str)

    def __init__(self, bridge: Any, config: Any, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._config = config
        self._request: int | None = None
        self._rows: list[tuple[str, int]] = []
        self.folder = ""

    @property
    def running(self) -> bool:
        return self._request is not None

    def start(self, folder: str) -> None:
        self.cancel()
        self.folder = folder
        self._rows = []
        self._request = self._bridge.submit(
            Op.WALK, folder,
            timeout=float(self._config.get("timeout.listing")),
            on_reply=self._on_reply,
            args={"limit": int(self._config.get("map.limit"))},
        )

    def cancel(self) -> None:
        if self._request is not None:
            self._bridge.cancel(self._request)
            self._request = None

    def _on_reply(self, reply: Reply) -> None:
        if reply.id != self._request:
            return
        for entry in reply.payload or []:
            if not entry.is_dir:
                self._rows.append((entry.name, int(entry.size)))
        if reply.status is Status.PARTIAL:
            self.progress.emit(len(self._rows))
            return
        self._request = None
        if reply.status is not Status.OK:
            self.failed.emit(reply.message or reply.status.value)
            return
        words = (reply.message or "").split()
        notes = []
        if "limit" in words:
            notes.append(f"stopped at {len(self._rows):,} files -- the map is of those")
        for word in words:
            if word.startswith("skipped=") and int(word.partition("=")[2] or 0):
                count = int(word.partition("=")[2])
                notes.append(f"{count:,} folder{'s' if count != 1 else ''} could not be read")
        root = treemap.build(paths.leaf(self.folder) or self.folder, self._rows)
        self._rows = []
        self.ready.emit(root, "  ·  ".join(notes))

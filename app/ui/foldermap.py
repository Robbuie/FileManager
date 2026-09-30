"""The folder map window (0.43): where the space under a folder has gone.

Blocks sized by bytes, nested as the folders are, coloured by the family of
file that takes most of each -- the same colours as the badges in the
listing, so a teal block is Logix projects here as it is there. Click a
folder to go into it, Backspace or the Up button to come back out, double
click anything to take the pane there with that item under the cursor.

It paints what `core/treemap.py` lays out and nothing else, and it never
touches the filesystem: the walk is `core/foldermap.py`'s, in the folder's
own worker. Colours come from the theme's tokens (`kind_*`), never from
literals, so it follows the theme like the rest of the window.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from app.core import treemap
from app.core.filetypes import FAMILIES, LABELS
from app.core.listing import format_size
from app.ui.dialogs import Dialog
from app.ui.rows import parse_colour


class MapCanvas(QWidget):
    """The blocks. Emits a path (names from the map's root) for each gesture."""

    zoomRequested = Signal(tuple)
    goRequested = Signal(tuple)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumSize(480, 320)
        self._root: treemap.Node | None = None
        self._base: tuple[str, ...] = ()
        self._tiles: list[treemap.Tile] = []
        self._hover: treemap.Tile | None = None
        self._tokens: dict = {}

    def set_tokens(self, tokens: dict) -> None:
        self._tokens = dict(tokens)
        self.update()

    def show_node(self, node: treemap.Node | None, base: tuple[str, ...]) -> None:
        self._root = node
        self._base = base
        self._hover = None
        self._relayout()
        self.update()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self._relayout()

    def _relayout(self) -> None:
        if self._root is None:
            self._tiles = []
            return
        self._tiles = treemap.layout(self._root, (0.0, 0.0, float(self.width()),
                                                  float(self.height())), path=self._base)

    def _colour(self, name: str, fallback: str = "#7d8591") -> QColor:
        colour = parse_colour(self._tokens.get(name, fallback))
        return colour if colour.isValid() else QColor(fallback)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.fillRect(self.rect(), self._colour("bg_1"))
        ink = self._colour("txt_0")
        muted = self._colour("txt_1")
        edge = self._colour("bg_0")
        small = QFont(self.font())
        small.setPointSizeF(max(7.0, small.pointSizeF() - 1))
        for tile in self._tiles:
            x, y, w, h = tile.rect
            box = QRectF(x, y, w, h).adjusted(0.5, 0.5, -0.5, -0.5)
            hue = self._colour(f"kind_{tile.node.family}")
            fill = QColor(hue)
            # Deeper blocks a little brighter, so nesting reads without lines.
            fill.setAlphaF(min(1.0, 0.28 + 0.14 * tile.depth) if not tile.opened else 0.22)
            painter.fillRect(box, fill)
            painter.setPen(QPen(edge, 1))
            painter.drawRect(box)
            if tile is self._hover:
                painter.setPen(QPen(self._colour("accent", "#4a91ff"), 2))
                painter.drawRect(box.adjusted(1, 1, -1, -1))
            label = tile.node.name
            if tile.opened:
                if w > 40:
                    painter.setPen(ink)
                    painter.setFont(small)
                    painter.drawText(QRectF(x + 4, y, w - 8, treemap.HEADER),
                                     Qt.AlignVCenter | Qt.AlignLeft,
                                     self._elide(painter, f"{label}  {format_size(tile.node.size)}",
                                                 w - 8))
            elif w > 44 and h > 18:
                painter.setPen(ink)
                painter.setFont(small)
                text_box = QRectF(x + 4, y + 2, w - 8, h - 4)
                painter.drawText(text_box, Qt.AlignTop | Qt.AlignLeft,
                                 self._elide(painter, label, w - 8))
                if h > 34:
                    painter.setPen(muted)
                    painter.drawText(QRectF(x + 4, y + 17, w - 8, h - 19),
                                     Qt.AlignTop | Qt.AlignLeft,
                                     self._elide(painter, format_size(tile.node.size), w - 8))
        if not self._tiles:
            painter.setPen(muted)
            painter.drawText(self.rect(), Qt.AlignCenter,
                             "Nothing here takes any room" if self._root is not None else "")
        painter.end()

    @staticmethod
    def _elide(painter: QPainter, text: str, width: float) -> str:
        return painter.fontMetrics().elidedText(text, Qt.ElideRight, int(max(0, width)))

    def _at(self, point: QPointF) -> treemap.Tile | None:
        return treemap.hit(self._tiles, point.x(), point.y())

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        tile = self._at(event.position())
        if tile is not self._hover:
            self._hover = tile
            self.update()
        if tile is not None:
            node = tile.node
            kind = LABELS.get(node.family, node.family)
            what = (f"{node.files:,} files, mostly {kind}" if node.is_dir else kind)
            where = "\\".join(tile.path)
            QToolTip.showText(event.globalPosition().toPoint(),
                              f"{where}\n{format_size(node.size)}  ·  {what}", self)

    def leaveEvent(self, _event) -> None:  # noqa: N802
        self._hover = None
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.BackButton:
            self.zoomRequested.emit(self._base[:-1])
            return
        tile = self._at(event.position())
        if event.button() == Qt.LeftButton and tile is not None:
            # Into the outermost folder under the click below the current
            # level: one level at a time, like a folder in the listing.
            step = tile.path[:len(self._base) + 1]
            node = self._root.find(list(step[len(self._base):])) if self._root else None
            if node is not None and node.is_dir:
                self.zoomRequested.emit(step)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        tile = self._at(event.position())
        if tile is not None:
            self.goRequested.emit(tile.path)


class FolderMapWindow(Dialog):
    """Non-modal: it stays open beside the window while the pane moves."""

    def __init__(self, parent: QWidget | None, *, folder_label: str,
                 on_go: Callable[[tuple[str, ...], bool], None],
                 on_refresh: Callable[[], None]) -> None:
        super().__init__(parent)
        self.setModal(False)
        self.setWindowTitle(f"Folder map -- {folder_label}")
        self.resize(900, 620)
        self._root: treemap.Node | None = None
        self._zoom: tuple[str, ...] = ()
        self._on_go = on_go
        self._tokens: dict = {}

        self._where = QLabel(folder_label)
        self._where.setProperty("role", "title")
        self._summary = QLabel("walking...")
        self._summary.setProperty("role", "muted")
        self._up = QPushButton("Up")
        self._up.setToolTip("Out one level (Backspace)")
        self._up.clicked.connect(self.up)
        go = QPushButton("Go there")
        go.setToolTip("Take the pane to the folder shown here")
        go.clicked.connect(lambda: self._on_go(self._zoom, True))
        refresh = QPushButton("Walk again")
        refresh.clicked.connect(on_refresh)

        top = QHBoxLayout()
        top.addWidget(self._where, 1)
        top.addWidget(self._up)
        top.addWidget(go)
        top.addWidget(refresh)

        self._canvas = MapCanvas(self)
        self._canvas.zoomRequested.connect(self.zoom_to)
        self._canvas.goRequested.connect(lambda path: self._on_go(path, False))
        self._legend = QLabel("")
        self._legend.setTextFormat(Qt.RichText)
        self._legend.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.addLayout(top)
        layout.addWidget(self._summary)
        layout.addWidget(self._canvas, 1)
        layout.addWidget(self._legend)
        self._sync()

    def set_tokens(self, tokens: dict) -> None:
        self._tokens = dict(tokens)
        self._canvas.set_tokens(tokens)
        self._fill_legend()

    def walking(self, files: int) -> None:
        self._summary.setText(f"walking, {files:,} files so far")

    def failed(self, message: str) -> None:
        self._summary.setText(f"could not walk this folder: {message}")

    def show_tree(self, root: treemap.Node, note: str) -> None:
        self._root = root
        self._note = note
        node = root.find(list(self._zoom)) if self._zoom else root
        if node is None or not node.is_dir:
            self._zoom = ()
        self._sync()

    def zoom_to(self, path: tuple) -> None:
        if self._root is None:
            return
        node = self._root.find(list(path)) if path else self._root
        if node is None or not node.is_dir:
            return
        self._zoom = tuple(path)
        self._sync()

    def up(self) -> None:
        if self._zoom:
            self.zoom_to(self._zoom[:-1])

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key_Backspace:
            self.up()
            return
        super().keyPressEvent(event)

    def _sync(self) -> None:
        node = None
        if self._root is not None:
            node = self._root.find(list(self._zoom)) if self._zoom else self._root
        self._up.setEnabled(bool(self._zoom))
        if node is None:
            self._canvas.show_node(None, ())
            return
        where = "\\".join((self._root.name,) + self._zoom)
        self._where.setText(where)
        summary = f"{format_size(node.size)} in {node.files:,} files"
        if getattr(self, "_note", ""):
            summary += f"  ·  {self._note}"
        self._summary.setText(summary)
        self._canvas.show_node(node, self._zoom)
        self._fill_legend(node)

    def _fill_legend(self, node: treemap.Node | None = None) -> None:
        if node is None and self._root is not None:
            node = self._root.find(list(self._zoom)) if self._zoom else self._root
        if node is None or not node.size:
            self._legend.setText("")
            return
        parts = []
        for family in FAMILIES:
            share = node.families.get(family, 0)
            if not share:
                continue
            colour = parse_colour(self._tokens.get(f"kind_{family}", "#7d8591")).name()
            parts.append(f'<span style="color:{colour}">&#9632;</span> '
                         f'{LABELS.get(family, family)} {share * 100 / node.size:.0f}%')
        self._legend.setText("&nbsp;&nbsp;&nbsp;".join(parts))

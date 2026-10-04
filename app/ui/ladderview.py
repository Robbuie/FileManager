"""Rungs of ladder, drawn (0.55).

One widget paints a whole routine: each rung is laid out once when the
routine arrives (`app/core/ladder.py` reads its text into series and
branches), and the paint draws only the rungs inside the area being exposed,
so a routine of five thousand rungs scrolls like one of five.

The drawing is the familiar one: power rails left and right, contacts and
coils as their symbols with the tag above, every other instruction as a box
with its operands, branches as parallel paths joined at both ends. Tags that
match the search are drawn in the warning colour. A rung the parser cannot
read is shown as its text, never guessed at.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from app.core import ladder
from app.core.ladder import Branch, Instr

NUMBER_W = 44          # the rung number's column
RAIL_GAP = 10          # between a rail and the first or last element
WIRE = 16              # wire between two elements in series
BRANCH_PAD = 12        # wire inside a branch's joining verticals
ROW_GAP = 8            # between two paths of a branch
TAG_H = 16             # the tag line above a contact
SYMBOL_H = 22          # a contact or coil's symbol
ANCHOR = TAG_H + SYMBOL_H // 2      # where the wire meets every element
BOX_HEAD = 20
BOX_LINE = 15
RUNG_PAD = 12
COMMENT_H = 18


@dataclass
class _Laid:
    node: object
    size: QSize


class LadderView(QWidget):
    """A routine's rungs, painted. Set with `show_rungs`."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._rungs: list[tuple[int, str, str, object]] = []   # number, comment, text, layout
        self._tops: list[int] = []
        self._heights: list[int] = []
        self._needle = ""
        self._current = -1
        self._colours = {key: QColor(0, 0, 0, 0) for key in
                         ("wire", "rail", "tag", "text", "box", "boxbg", "comment", "hit",
                          "muted", "band")}
        self._font = QFont(self.font())
        self._mono = QFont(self.font())
        self._content_w = 400

    # ------------------------------------------------------------ setup

    def apply_tokens(self, tokens: dict[str, str]) -> None:
        pick = {"wire": "txt_2", "rail": "accent", "tag": "txt_1", "text": "txt_0",
                "box": "bg_4", "boxbg": "bg_2", "comment": "good", "hit": "warn",
                "muted": "txt_2", "band": "bg_3"}
        for key, token in pick.items():
            self._colours[key] = QColor(tokens.get(token, ""))
        mono = tokens.get("mono", "monospace")
        self._mono.setFamilies([part.strip().strip('"') for part in mono.split(",")])
        self._mono.setPointSizeF(max(8.0, self._font.pointSizeF() - 1))
        self._relayout()

    def show_rungs(self, rungs: list, needle: str = "") -> None:
        """`rungs` as the worker sends them: [number, comment, text] each."""
        self._needle = needle.lower().strip()
        self._rungs = [(int(r[0]), r[1] or "", r[2] or "", None) for r in rungs]
        self._current = -1
        self._relayout()

    def set_needle(self, needle: str) -> None:
        self._needle = needle.lower().strip()
        self.update()

    def rung_top(self, number: int) -> int:
        """The y of a rung by its number, for scrolling to a search hit."""
        for index, rung in enumerate(self._rungs):
            if rung[0] == number:
                self._current = index
                self.update()
                return self._tops[index]
        return 0

    # ------------------------------------------------------------ layout

    def _metrics(self, mono: bool = False) -> QFontMetrics:
        return QFontMetrics(self._mono if mono else self._font)

    def _measure(self, node) -> QSize:
        if isinstance(node, Instr):
            if node.symbol:
                tag = node.args[0] if node.args else ""
                width = max(70, self._metrics(True).horizontalAdvance(tag) + 12)
                return QSize(width, TAG_H + SYMBOL_H)
            lines = [node.name] + node.args
            width = max(self._metrics(True).horizontalAdvance(line) for line in lines) + 20
            return QSize(max(80, width), TAG_H + BOX_HEAD + BOX_LINE * len(node.args) + 4)
        if isinstance(node, Branch):
            sizes = [self._measure_series(path) for path in node.paths]
            width = max((s.width() for s in sizes), default=0) + 2 * BRANCH_PAD
            height = sum(s.height() for s in sizes) + ROW_GAP * (len(sizes) - 1)
            return QSize(width, height)
        return QSize(0, 0)

    def _measure_series(self, nodes: list) -> QSize:
        if not nodes:
            return QSize(WIRE, TAG_H + SYMBOL_H)
        sizes = [self._measure(node) for node in nodes]
        width = sum(s.width() for s in sizes) + WIRE * (len(sizes) - 1)
        return QSize(width, max(s.height() for s in sizes))

    def _relayout(self) -> None:
        tops, heights = [], []
        y = 0
        widest = 0
        laid = []
        for number, comment, text, _ in self._rungs:
            nodes = ladder.parse(text)
            if nodes is None:
                size = QSize(self._metrics(True).horizontalAdvance(text) + 20, 22)
            else:
                size = self._measure_series(nodes)
            height = RUNG_PAD * 2 + (COMMENT_H if comment else 0) + max(size.height(), 30)
            tops.append(y)
            heights.append(height)
            y += height
            widest = max(widest, size.width())
            laid.append((number, comment, text, nodes))
        self._rungs = laid
        self._tops = tops
        self._heights = heights
        self._content_w = NUMBER_W + 2 * RAIL_GAP + widest + 40
        self.setMinimumSize(self._content_w, max(1, y))
        self.resize(max(self.width(), self._content_w), max(1, y))
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        return QSize(self._content_w, self._tops[-1] + self._heights[-1] if self._tops else 1)

    # ------------------------------------------------------------ paint

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        exposed = event.rect()
        right_rail = self.width() - RAIL_GAP
        for index, (number, comment, text, nodes) in enumerate(self._rungs):
            top, height = self._tops[index], self._heights[index]
            if top > exposed.bottom() or top + height < exposed.top():
                continue
            if index == self._current:
                painter.fillRect(QRect(0, top, self.width(), height), self._colours["band"])
            y = top + RUNG_PAD
            painter.setFont(self._font)
            painter.setPen(self._colours["muted"])
            painter.drawText(QRect(0, y + (COMMENT_H if comment else 0), NUMBER_W - 8, ANCHOR + 8),
                             Qt.AlignRight | Qt.AlignVCenter, str(number))
            left = NUMBER_W
            if comment:
                painter.setPen(self._colours["comment"])
                painter.drawText(QRect(left + RAIL_GAP, y, right_rail - left, COMMENT_H),
                                 Qt.AlignLeft | Qt.AlignVCenter,
                                 self._metrics().elidedText(comment.replace("\n", " "),
                                                            Qt.ElideRight, right_rail - left - 20))
                y += COMMENT_H
            body = height - RUNG_PAD * 2 - (COMMENT_H if comment else 0)
            painter.setPen(QPen(self._colours["rail"], 2))
            painter.drawLine(left, y - 2, left, y + body + 2)
            painter.drawLine(right_rail, y - 2, right_rail, y + body + 2)
            if nodes is None:
                painter.setFont(self._mono)
                painter.setPen(self._colours["tag"])
                painter.drawText(QRect(left + RAIL_GAP, y, right_rail - left, 22),
                                 Qt.AlignLeft | Qt.AlignVCenter, text)
                continue
            # Coils at the end of a rung sit against the right rail, as Logix
            # Designer draws them; everything before them runs from the left.
            split = len(nodes)
            while split > 0 and isinstance(nodes[split - 1], Instr) \
                    and nodes[split - 1].name in ladder.COILS:
                split -= 1
            end = self._series(painter, nodes[:split], left, y) if split else left
            outputs = nodes[split:]
            if outputs:
                width = self._measure_series(outputs).width()
                start = max(end + WIRE, right_rail - RAIL_GAP - width)
                self._wire(painter, end, start, y + ANCHOR)
                end = self._series(painter, outputs, start, y)
            self._wire(painter, end, right_rail, y + ANCHOR)

    def _wire(self, painter: QPainter, x1: int, x2: int, y: int) -> None:
        if x2 > x1:
            painter.setPen(QPen(self._colours["wire"], 1))
            painter.drawLine(x1, y, x2, y)

    def _series(self, painter: QPainter, nodes: list, x: int, y: int) -> int:
        """Draw nodes in series from x, wire at y + ANCHOR. Returns the end x."""
        x_wire = x
        for index, node in enumerate(nodes):
            gap = RAIL_GAP if index == 0 and x == NUMBER_W else WIRE
            if index == 0 and x != NUMBER_W:
                gap = 0
            self._wire(painter, x_wire, x_wire + gap, y + ANCHOR)
            x_wire += gap
            size = self._measure(node)
            if isinstance(node, Branch):
                self._branch(painter, node, x_wire, y, size)
            else:
                self._instr(painter, node, x_wire, y, size)
            x_wire += size.width()
        if not nodes:
            self._wire(painter, x_wire, x_wire + WIRE, y + ANCHOR)
            x_wire += WIRE
        return x_wire

    def _branch(self, painter: QPainter, node: Branch, x: int, y: int, size: QSize) -> None:
        right = x + size.width()
        row = y
        last_anchor = y + ANCHOR
        for path in node.paths:
            path_size = self._measure_series(path)
            self._wire(painter, x, x + BRANCH_PAD, row + ANCHOR)
            end = self._series(painter, path, x + BRANCH_PAD, row)
            self._wire(painter, end, right, row + ANCHOR)
            last_anchor = row + ANCHOR
            row += path_size.height() + ROW_GAP
        painter.setPen(QPen(self._colours["wire"], 1))
        painter.drawLine(x, y + ANCHOR, x, last_anchor)
        painter.drawLine(right, y + ANCHOR, right, last_anchor)

    def _instr(self, painter: QPainter, node: Instr, x: int, y: int, size: QSize) -> None:
        painter.setFont(self._mono)
        if node.symbol:
            tag = node.args[0] if node.args else ""
            hit = bool(self._needle) and self._needle in tag.lower()
            painter.setPen(self._colours["hit" if hit else "tag"])
            painter.drawText(QRect(x, y, size.width(), TAG_H), Qt.AlignHCenter | Qt.AlignBottom,
                             tag)
            symbol = node.symbol
            metrics = self._metrics(True)
            sw = metrics.horizontalAdvance(symbol) + 8
            sx = x + (size.width() - sw) // 2
            self._wire(painter, x, sx, y + ANCHOR)
            self._wire(painter, sx + sw, x + size.width(), y + ANCHOR)
            painter.setPen(self._colours["text"])
            painter.drawText(QRect(sx, y + TAG_H, sw, SYMBOL_H), Qt.AlignCenter, symbol)
            return
        box = QRect(x, y + TAG_H - BOX_HEAD // 2 + SYMBOL_H // 2 - 1, size.width(),
                    BOX_HEAD + BOX_LINE * len(node.args) + 4)
        painter.fillRect(box, self._colours["boxbg"])
        painter.setPen(QPen(self._colours["box"], 1))
        painter.drawRect(box)
        painter.setPen(self._colours["text"])
        bold = QFont(self._mono)
        bold.setBold(True)
        painter.setFont(bold)
        painter.drawText(QRect(box.left(), box.top(), box.width(), BOX_HEAD),
                         Qt.AlignCenter, node.name)
        painter.setFont(self._mono)
        for line, arg in enumerate(node.args):
            hit = bool(self._needle) and self._needle in arg.lower()
            painter.setPen(self._colours["hit" if hit else "tag"])
            painter.drawText(QRect(box.left() + 8, box.top() + BOX_HEAD + line * BOX_LINE,
                                   box.width() - 12, BOX_LINE),
                             Qt.AlignLeft | Qt.AlignVCenter, arg)

    def hit_point(self) -> QPoint:
        return QPoint(0, self._tops[self._current] if 0 <= self._current < len(self._tops) else 0)

"""The Logix card (0.53): an export drawn as what it describes.

Until 0.53 the summary of an `.L5X` or `.L5K` was a block of text laid out
with spaces for a monospaced font and drawn in the interface's proportional
one, so the columns wandered and the whole thing read as a dump. This draws
the same facts as a card: the controller with its processor and firmware,
the counts as tiles, the tasks with the programs they run, the local chassis
slot by slot, the devices on the network with their addresses, and the
add-on instructions and types.

One widget, used by every surface that shows a preview -- the preview panel,
the peek card and quick view -- and it reflows to the width it is given: in
a panel three hundred pixels wide the columns stack and the tiles wrap; in
quick view they sit side by side. Everything it shows is worked out in
`app/core/logixview.py`; this only places labels.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QBoxLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.core import logixview

#: Below this width the two columns stack.
STACK_BELOW = 820
#: Below this width a row's detail folds under its name: the header's dates,
#: a program's counts, a device's catalog number and address.
FOLD_BELOW = 440
#: Widths the network's catalog and address columns are given while they sit
#: on one line, so the rows line up as a table.
CATALOG_W = 150
ADDRESS_W = 96
#: The narrowest a tile is allowed to get before the row wraps.
TILE_W = 96
#: The narrowest a chassis slot is allowed to get before the chassis wraps.
SLOT_W = 76


def _label(text: str, role: str, *, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    label.setProperty("role", role)
    label.setWordWrap(wrap)
    label.setTextInteractionFlags(Qt.NoTextInteraction)
    return label


def _chip(text: str, *, accent: bool = False, warn: bool = False) -> QLabel:
    chip = _label(text, "lxchip")
    if accent:
        chip.setProperty("tone", "accent")
    elif warn:
        chip.setProperty("tone", "warn")
    chip.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    return chip


class _Wrap(QWidget):
    """A row of equal cells that wraps onto more rows when it runs out of
    width. Qt has no flow layout; this is the one shape the card needs.

    It answers height-for-width itself, so the card's layout gives it the rows
    it will actually use at the width it is given, and its minimum width is one
    cell -- which is what lets the card shrink to a narrow panel at all.
    """

    def __init__(self, cells: list[QWidget], least: int, spacing: int = 8,
                 *, natural: bool = False) -> None:
        super().__init__()
        self._cells = cells
        self._least = least
        self._spacing = spacing
        #: Chips keep their own widths and flow like words; tiles and slots
        #: share the row equally.
        self._natural = natural
        for cell in cells:
            cell.setParent(self)
        policy = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def _columns(self, width: int) -> int:
        if not self._cells:
            return 1
        fits = max(1, (width + self._spacing) // (self._least + self._spacing))
        return min(fits, len(self._cells))

    def _row_height(self) -> int:
        return max((cell.sizeHint().height() for cell in self._cells), default=0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt naming
        return True

    def _flow(self, width: int) -> list[tuple[int, int, int]]:
        """(x, y, w) for each cell, laid out like words in a line."""
        x = y = 0
        row_h = self._row_height()
        out = []
        for cell in self._cells:
            w = min(cell.sizeHint().width(), max(1, width))
            if x and x + w > width:
                x = 0
                y += row_h + self._spacing
            out.append((x, y, w))
            x += w + self._spacing
        return out

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt naming
        if not self._cells:
            return 0
        if self._natural:
            return self._flow(width)[-1][1] + self._row_height()
        columns = self._columns(width)
        rows = -(-len(self._cells) // columns)
        return rows * self._row_height() + (rows - 1) * self._spacing

    def sizeHint(self):  # noqa: N802 - Qt naming
        from PySide6.QtCore import QSize

        width = len(self._cells) * (self._least + self._spacing)
        return QSize(width, self.heightForWidth(width))

    def minimumSizeHint(self):  # noqa: N802 - Qt naming
        from PySide6.QtCore import QSize

        return QSize(self._least, self.heightForWidth(self._least))

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self._lay()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().showEvent(event)
        self._lay()

    def _lay(self) -> None:
        if not self._cells:
            return
        width = self.width()
        if self._natural:
            row_h = self._row_height()
            for cell, (x, y, w) in zip(self._cells, self._flow(width)):
                cell.setGeometry(x, y, w, row_h)
            if self.height() != self.heightForWidth(width):
                self.updateGeometry()
            return
        columns = self._columns(width)
        cell_w = (width - (columns - 1) * self._spacing) // columns
        row_h = self._row_height()
        for index, cell in enumerate(self._cells):
            row, column = divmod(index, columns)
            cell.setGeometry(column * (cell_w + self._spacing), row * (row_h + self._spacing),
                             cell_w, row_h)
        if self.height() != self.heightForWidth(width):
            self.updateGeometry()


def _content() -> QWidget:
    """The card's scrolling body. Its width is ignored when the scroll area
    sizes it, so it is always exactly as wide as the panel: a label that
    cannot fold any further is clipped at the edge rather than pushing the
    whole card sideways under a scroll bar that is turned off."""
    content = QWidget()
    content.setProperty("role", "lxcontent")
    content.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    return content


class LogixCard(QScrollArea):
    """A scrolling card built from one Logix summary."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "lxcard")
        self.setFrameShape(QFrame.NoFrame)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.NoFocus)
        self._body: QBoxLayout | None = None
        self._summary: dict[str, Any] = {}
        #: Rows whose detail folds under the name in a narrow panel, and the
        #: labels whose fixed column width only applies while unfolded.
        self._folding: list[QBoxLayout] = []
        self._columns: list[tuple[QLabel, int]] = []

    @property
    def summary(self) -> dict[str, Any]:
        return self._summary

    def show_summary(self, summary: dict[str, Any], name: str = "") -> None:
        if summary.get("kind") == "acd":
            self.show_acd(summary, name)
            return
        self._summary = dict(summary)
        self._folding = []
        self._columns = []
        content = _content()
        outer = QVBoxLayout(content)
        outer.setContentsMargins(14, 12, 14, 14)
        outer.setSpacing(14)

        self._header(outer, summary, name)
        counts = [self._tile(number, label) for number, label in logixview.tiles(summary)]
        if counts:
            outer.addWidget(_Wrap(counts, TILE_W))
        if summary.get("partial"):
            outer.addWidget(_label("Counted as far as the time allowed -- at least this many.",
                                   "lxwarn", wrap=True))

        self._body = QBoxLayout(QBoxLayout.LeftToRight)
        self._body.setSpacing(22)
        left = QVBoxLayout()
        left.setSpacing(10)
        right = QVBoxLayout()
        right.setSpacing(10)
        self._tasks(left, summary)
        self._definitions(left, summary)
        self._chassis(right, summary)
        self._network(right, summary)
        left.addStretch(1)
        right.addStretch(1)
        self._body.addLayout(left, 1)
        self._body.addLayout(right, 1)
        outer.addLayout(self._body)
        outer.addStretch(1)
        self.setWidget(content)
        self._reflow()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self._reflow()

    def show_acd(self, header: dict[str, Any], name: str = "") -> None:
        """0.56: a project file -- only what its text header says."""
        self._summary = dict(header)
        self._body = None
        self._folding, self._columns = [], []
        content = _content()
        outer = QVBoxLayout(content)
        outer.setContentsMargins(14, 12, 14, 14)
        outer.setSpacing(12)
        if name:
            outer.addWidget(_label(name, "lxsub"))
        outer.addWidget(_label("Logix Designer project", "lxname", wrap=True))
        chips = []
        if header.get("needs"):
            chips.append(_chip(f"Last saved with {header['needs']}", accent=True))
        if header.get("first") and header.get("first") != header.get("needs"):
            chips.append(_chip(f"Earliest version listed {header['first']}"))
        history = header.get("history") or []
        if len(history) > 1:
            chips.append(_chip(f"{len(history)} versions listed"))
        if chips:
            outer.addWidget(_Wrap(chips, 150, spacing=6, natural=True))
        outer.addWidget(_label("WHAT THE FILE SAYS ABOUT ITSELF", "lxhead", wrap=True))
        box = QFrame()
        box.setProperty("role", "lxbox")
        lines = QVBoxLayout(box)
        lines.setContentsMargins(12, 10, 12, 10)
        lines.setSpacing(3)
        for line in header.get("lines") or []:
            label = _label(line, "lxmono", wrap=True)
            lines.addWidget(label)
        outer.addWidget(box)
        outer.addWidget(_label(
            "Only the plain-text header at the start of the file is read. Tags and "
            "logic are in Rockwell's own format and need Logix Designer; an .L5X "
            "export of the project shows them here.", "lxsub", wrap=True))
        outer.addStretch(1)
        self.setWidget(content)

    def _reflow(self) -> None:
        if self._body is None:
            return
        width = self.viewport().width()
        wide = width >= STACK_BELOW
        self._body.setDirection(QBoxLayout.LeftToRight if wide else QBoxLayout.TopToBottom)
        folded = width < FOLD_BELOW
        for row in self._folding:
            row.setDirection(QBoxLayout.TopToBottom if folded else QBoxLayout.LeftToRight)
            row.setSpacing(1 if folded else 10)
        for label, column in self._columns:
            label.setMinimumWidth(0 if folded else column)
            if label.property("role") == "lxfact":
                # Beside the name there is room for a date on one line; under
                # it, in a narrow panel, it may have to break.
                label.setWordWrap(folded)
            label.setAlignment(Qt.AlignLeft if folded else Qt.AlignRight)

    def _fold(self, *widgets: QWidget, indent: int = 0) -> QBoxLayout:
        """A row of a name and its details that folds into lines when narrow."""
        row = QBoxLayout(QBoxLayout.LeftToRight)
        row.setContentsMargins(indent, 0, 0, 0)
        row.setSpacing(10)
        row.addWidget(widgets[0], 1)
        for widget in widgets[1:]:
            row.addWidget(widget)
        self._folding.append(row)
        return row

    # ------------------------------------------------------------ parts

    def _header(self, outer: QVBoxLayout, summary: dict[str, Any], name: str) -> None:
        top = QBoxLayout(QBoxLayout.LeftToRight)
        top.setSpacing(16)
        self._folding.append(top)
        left = QVBoxLayout()
        left.setSpacing(4)
        if name:
            left.addWidget(_label(name, "lxsub"))
        what = summary.get("controller") or summary.get("target") or "Logix export"
        left.addWidget(_label(what, "lxname", wrap=True))
        chips = []
        if summary.get("processor"):
            chips.append(_chip(summary["processor"], accent=True))
        if summary.get("firmware"):
            chips.append(_chip(f"Firmware {summary['firmware']}"))
        if summary.get("software"):
            chips.append(_chip(f"Saved by Logix Designer {summary['software']}"))
        target = summary.get("target_type") or ""
        if target and target != "Controller":
            chips.append(_chip(f"Export of a {target.lower()}: {summary.get('target', '')}",
                               warn=True))
        if chips:
            left.addWidget(_Wrap(chips, 150, spacing=6, natural=True))
        top.addLayout(left, 1)

        facts = QVBoxLayout()
        facts.setSpacing(3)
        for label, value in (("Path", logixview.address(summary.get("comm_path", ""))),
                             ("Last edited", logixview.when(summary.get("modified", ""))),
                             ("Exported", logixview.when(summary.get("exported", "")))):
            if value:
                line = _label(f"{label}  {value}", "lxfact")
                self._columns.append((line, 0))
                facts.addWidget(line)
        facts.addStretch(1)
        top.addLayout(facts)
        outer.addLayout(top)

    def _tile(self, number: str, label: str) -> QWidget:
        tile = QFrame()
        tile.setProperty("role", "lxtile")
        layout = QVBoxLayout(tile)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(0)
        layout.addWidget(_label(number, "lxnumber"))
        layout.addWidget(_label(label, "lxsub"))
        return tile

    def _section(self, into: QVBoxLayout, title: str) -> QFrame:
        into.addWidget(_label(title.upper(), "lxhead", wrap=True))
        box = QFrame()
        box.setProperty("role", "lxbox")
        into.addWidget(box)
        return box

    def _tasks(self, into: QVBoxLayout, summary: dict[str, Any]) -> None:
        tasks = summary.get("tasks") or []
        programs = {p.get("name"): p for p in summary.get("program_list") or []}
        if not tasks and not programs:
            return
        box = self._section(into, "Tasks")
        rows = QVBoxLayout(box)
        rows.setContentsMargins(12, 10, 12, 10)
        rows.setSpacing(5)
        scheduled: set[str] = set()

        def program_row(name: str) -> None:
            detail = programs.get(name)
            line = _label(logixview.program_line(detail) if detail else "", "lxsub")
            self._columns.append((line, 0))
            rows.addLayout(self._fold(_label(name, "lxrow"), line, indent=14))

        for task in tasks:
            head = QHBoxLayout()
            head.setSpacing(8)
            head.addWidget(_label(task.get("name", ""), "lxstrong"))
            rate = logixview.task_rate(task)
            if rate:
                head.addWidget(_chip(rate, warn=rate.startswith("every")))
            head.addStretch(1)
            rows.addLayout(head)
            for name in task.get("programs", []):
                scheduled.add(name)
                program_row(name)
        loose = [name for name in programs if name not in scheduled]
        if loose:
            rows.addWidget(_label("Not scheduled", "lxstrong"))
            for name in loose:
                program_row(name)

    def _definitions(self, into: QVBoxLayout, summary: dict[str, Any]) -> None:
        aois = summary.get("aoi_list") or []
        udts = summary.get("udt_list") or []
        if not aois and not udts:
            return
        into.addWidget(_label("ADD-ON INSTRUCTIONS AND TYPES", "lxhead", wrap=True))
        chips = [_chip(f"{name}  v{revision}" if revision else name)
                 for name, revision in aois]
        chips += [_chip(name) for name in udts]
        more = (int(summary.get("aois") or 0) - len(aois)) \
            + (int(summary.get("udts") or 0) - len(udts))
        if more > 0:
            chips.append(_chip(f"and {more:,} more"))
        into.addWidget(_Wrap(chips, 120, spacing=6, natural=True))

    def _chassis(self, into: QVBoxLayout, summary: dict[str, Any]) -> None:
        slots = logixview.chassis(summary)
        if not slots:
            return
        into.addWidget(_label("I/O: LOCAL CHASSIS", "lxhead", wrap=True))
        cells = []
        for slot in slots:
            cell = QFrame()
            cell.setProperty("role", "lxslot")
            if slot.controller:
                cell.setProperty("tone", "accent")
            elif slot.empty:
                cell.setProperty("tone", "empty")
            layout = QVBoxLayout(cell)
            layout.setContentsMargins(4, 6, 4, 6)
            layout.setSpacing(2)
            for text, role in ((str(slot.number), "lxsub"),
                               ("empty" if slot.empty else logixview.short_catalog(slot.catalog),
                                "lxstrong"),
                               (slot.kind or " ", "lxsub")):
                label = _label(text, role)
                label.setAlignment(Qt.AlignHCenter)
                layout.addWidget(label)
            if not slot.empty:
                cell.setToolTip(f"{slot.name}\n{slot.catalog}"
                                + ("\ninhibited" if slot.inhibited else ""))
            cells.append(cell)
        into.addWidget(_Wrap(cells, SLOT_W, spacing=5))

    def _network(self, into: QVBoxLayout, summary: dict[str, Any]) -> None:
        for network in logixview.networks(summary):
            via = "the controller's own port" if network.via == "Local" else network.via
            box = self._section(into, f"On {via}")
            rows = QVBoxLayout(box)
            rows.setContentsMargins(12, 8, 12, 8)
            rows.setSpacing(6)
            for device in network.devices:
                name = device.name + (f"  ({device.children} modules)" if device.children else "")
                catalog = _label(device.catalog, "lxmono")
                address = _label(device.address, "lxmono")
                self._columns.append((catalog, CATALOG_W))
                self._columns.append((address, ADDRESS_W))
                rows.addLayout(self._fold(_label(name, "lxrow"), catalog, address))

"""F3 on a Logix export (0.55): browse its programs and read its rungs.

The viewer shows a Logix export as this rather than as a page of XML. On the
left, the programs and add-on instructions with their routines; on the right,
the card from the preview until a routine is picked, then that routine --
ladder drawn as ladder, structured text as text. A find box searches every
rung and line in the export, marks how many hits each routine has, and Enter
steps through them.

Read-only, like everything the viewer shows: nothing here can change the
file, and it says so.

All of the reading is the worker's (`Op.LOGIX`, asked through
`core.logixbrowse`); the export is loaded once and every question after the
first is answered from what that load kept.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.ui.ladderview import LadderView
from app.ui.logixcard import LogixCard

KIND_NAMES = {"RLL": "", "ST": "structured text", "FBD": "function block", "SFC": "SFC"}


class LogixBrowser(QWidget):
    def __init__(self, browse, tokens: dict[str, str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._browse = browse
        self._path = ""
        self._summary: dict[str, Any] = {}
        #: (program, routine) on screen, and the routine's reply.
        self._showing: tuple[str, str] | None = None
        self._routine: dict[str, Any] | None = None
        self._hits: list[dict] = []
        self._hit = -1
        self._searched = ""
        #: A hit to scroll to once its routine arrives.
        self._pending_at: int | None = None
        self._items: dict[tuple[str, str], QTreeWidgetItem] = {}

        self._find = QLineEdit()
        self._find.setPlaceholderText("Find a tag or text in this export")
        self._find.setClearButtonEnabled(True)
        self._find.returnPressed.connect(self._on_find)
        self._found = QLabel("")
        self._found.setProperty("role", "note")
        self._as_text = QToolButton()
        self._as_text.setText("Rung text")
        self._as_text.setCheckable(True)
        self._as_text.setToolTip("Show each rung as Logix's own text instead of drawn")
        self._as_text.toggled.connect(lambda _on: self._render_routine())
        self._where = QLabel("Read only. Nothing here can change the file.")
        self._where.setProperty("role", "note")
        top = QHBoxLayout()
        top.setSpacing(10)
        top.addWidget(self._find, 2)
        top.addWidget(self._found)
        top.addStretch(1)
        top.addWidget(self._where)
        top.addWidget(self._as_text)

        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setProperty("role", "lxtree")
        self._tree.currentItemChanged.connect(self._on_pick)

        self._card = LogixCard()
        self._ladder = LadderView()
        self._ladder_area = QScrollArea()
        self._ladder_area.setWidget(self._ladder)
        self._ladder_area.setWidgetResizable(True)
        self._ladder_area.setProperty("role", "lxladder")
        self._text = QPlainTextEdit()
        self._text.setReadOnly(True)
        self._text.setProperty("role", "preview")
        self._text.setLineWrapMode(QPlainTextEdit.NoWrap)
        self._note = QLabel("")
        self._note.setProperty("role", "note")
        self._note.setAlignment(Qt.AlignCenter)
        self._note.setWordWrap(True)
        self._right = QStackedWidget()
        for widget in (self._card, self._ladder_area, self._text, self._note):
            self._right.addWidget(widget)

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        split.addWidget(self._tree)
        split.addWidget(self._right)
        split.setStretchFactor(1, 1)
        split.setSizes([260, 800])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addLayout(top)
        layout.addWidget(split, 1)

        browse.outlineReady.connect(self._on_outline)
        browse.routineReady.connect(self._on_routine)
        browse.hitsReady.connect(self._on_hits)
        browse.failed.connect(self._on_failed)
        self.apply_tokens(tokens)

    # ------------------------------------------------------------ outside

    def apply_tokens(self, tokens: dict[str, str]) -> None:
        self._ladder.apply_tokens(tokens)
        mono = tokens.get("mono", "monospace")
        font = self._text.font()
        font.setFamilies([part.strip().strip('"') for part in mono.split(",")])
        self._text.setFont(font)

    @property
    def path(self) -> str:
        return self._path

    def open(self, path: str, summary: dict[str, Any]) -> None:
        """Show an export: its card at once, its outline when the worker has it."""
        if path == self._path and self._items:
            return
        self._browse.cancel()
        self._path = path
        self._summary = dict(summary or {})
        self._showing = None
        self._routine = None
        self._hits, self._hit, self._searched = [], -1, ""
        self._found.setText("")
        self._items.clear()
        self._tree.clear()
        loading = QTreeWidgetItem(["reading the export..."])
        loading.setFlags(Qt.NoItemFlags)
        self._tree.addTopLevelItem(loading)
        self._card.show_summary(self._summary, "")
        self._right.setCurrentWidget(self._card)
        self._browse.outline(path)

    def status(self) -> str:
        """A line for the viewer's footer."""
        if self._routine and self._showing:
            program, routine = self._showing
            count = len(self._routine.get("rungs") or self._routine.get("lines") or [])
            unit = "rungs" if self._routine.get("kind") == "RLL" else "lines"
            return f"{program} / {routine}   {count:,} {unit}"
        return "a Logix export: pick a routine on the left"

    # ------------------------------------------------------------ replies

    def _on_outline(self, path: str, outline: dict) -> None:
        if path != self._path:
            return
        self._tree.clear()
        self._items.clear()
        first = None
        for container in outline.get("containers", []):
            label = container["name"] + ("  (AOI)" if container.get("kind") == "aoi" else "")
            parent = QTreeWidgetItem([label])
            parent.setFlags(Qt.ItemIsEnabled)
            self._tree.addTopLevelItem(parent)
            for routine in container.get("routines", []):
                item = QTreeWidgetItem([self._routine_label(routine, 0)])
                item.setData(0, Qt.UserRole, (container["name"], routine["name"]))
                item.setData(0, Qt.UserRole + 1, routine)
                parent.addChild(item)
                self._items[(container["name"], routine["name"])] = item
                first = first or item
            parent.setExpanded(container.get("kind") == "program")
        if not self._items:
            empty = QTreeWidgetItem(["no routines in this export"])
            empty.setFlags(Qt.NoItemFlags)
            self._tree.addTopLevelItem(empty)

    def _routine_label(self, routine: dict, hits: int) -> str:
        kind = KIND_NAMES.get(routine.get("kind", "RLL"), routine.get("kind", ""))
        count = routine.get("count", 0)
        detail = f"{count} rung{'s' if count != 1 else ''}" if not kind else f"{kind}"
        if routine.get("protected"):
            detail = "protected"
        label = f"{routine['name']}   {detail}"
        if hits:
            label += f"   {hits} found"
        return label

    def _on_pick(self, item: QTreeWidgetItem | None, _previous=None) -> None:
        if item is None:
            return
        where = item.data(0, Qt.UserRole)
        if not where:
            return
        program, routine = where
        if self._showing == (program, routine) and self._routine is not None:
            return
        self._showing = (program, routine)
        self._routine = None
        self._note.setText("reading...")
        self._right.setCurrentWidget(self._note)
        self._browse.routine(self._path, program, routine)

    def _on_routine(self, path: str, routine: dict) -> None:
        if path != self._path or self._showing != (routine.get("program"), routine.get("routine")):
            return
        self._routine = routine
        self._render_routine()

    def _render_routine(self) -> None:
        routine = self._routine
        if routine is None:
            return
        if routine.get("missing"):
            self._say("this routine is not in the export any more")
            return
        if routine.get("protected"):
            self._say("This routine is source-protected: its logic is encrypted in the file "
                      "and only Logix Designer with the key can show it.")
            return
        kind = routine.get("kind", "RLL")
        if kind == "RLL":
            rungs = routine.get("rungs") or []
            if not rungs:
                self._say("no rungs")
                return
            if self._as_text.isChecked():
                lines = []
                for number, comment, text in rungs:
                    if comment:
                        lines.append(f"// {comment}".replace("\n", "\n// "))
                    lines.append(f"{number:>4}  {text}")
                self._text.setPlainText("\n".join(lines))
                self._right.setCurrentWidget(self._text)
            else:
                self._ladder.show_rungs(rungs, self._searched)
                self._right.setCurrentWidget(self._ladder_area)
        elif kind == "ST":
            self._text.setPlainText("\n".join(routine.get("lines") or []))
            self._right.setCurrentWidget(self._text)
        else:
            self._say(f"{KIND_NAMES.get(kind, kind).capitalize()} routines are listed but not "
                      "drawn here; open the project in Logix Designer to see this one.")
            return
        self._scroll_to_pending()

    def _say(self, text: str) -> None:
        self._note.setText(text)
        self._right.setCurrentWidget(self._note)

    # ------------------------------------------------------------ find

    def _on_find(self) -> None:
        text = self._find.text().strip()
        if not text:
            return
        if text.lower() != self._searched or not self._hits:
            self._searched = text.lower()
            self._found.setText("searching...")
            self._browse.search(self._path, text)
            return
        self._go_hit(self._hit + 1)

    def _on_hits(self, path: str, found: dict) -> None:
        if path != self._path:
            return
        self._hits = list(found.get("hits") or [])
        counts: dict[tuple[str, str], int] = {}
        for hit in self._hits:
            key = (hit["program"], hit["routine"])
            counts[key] = counts.get(key, 0) + 1
        for key, item in self._items.items():
            item.setText(0, self._routine_label(item.data(0, Qt.UserRole + 1), counts.get(key, 0)))
        routines = len(counts)
        more = "+" if found.get("more") else ""
        self._found.setText(
            f"{len(self._hits):,}{more} places in {routines} routine{'s' if routines != 1 else ''}"
            if self._hits else "not found")
        self._ladder.set_needle(self._searched)
        if self._hits:
            self._go_hit(0)

    def _go_hit(self, index: int) -> None:
        if not self._hits:
            return
        self._hit = index % len(self._hits)
        hit = self._hits[self._hit]
        self._found.setText(f"{self._hit + 1} of {len(self._hits):,}")
        self._pending_at = int(hit["at"])
        key = (hit["program"], hit["routine"])
        if self._showing == key and self._routine is not None:
            self._scroll_to_pending()
            return
        item = self._items.get(key)
        if item is not None:
            self._tree.setCurrentItem(item)

    def _scroll_to_pending(self) -> None:
        if self._pending_at is None:
            return
        at, self._pending_at = self._pending_at, None
        current = self._right.currentWidget()
        if current is self._ladder_area:
            self._ladder_area.verticalScrollBar().setValue(max(0, self._ladder.rung_top(at) - 20))
        elif current is self._text:
            block = self._text.document().findBlockByLineNumber(at)
            cursor = self._text.textCursor()
            cursor.setPosition(block.position())
            self._text.setTextCursor(cursor)
            self._text.centerCursor()

    def _on_failed(self, path: str, why: str) -> None:
        if path != self._path:
            return
        if not self._items:
            self._tree.clear()
            item = QTreeWidgetItem([f"could not read the logic: {why}"])
            item.setFlags(Qt.NoItemFlags)
            self._tree.addTopLevelItem(item)
        else:
            self._say(why)

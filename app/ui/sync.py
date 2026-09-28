"""The sync preview: what a one-way sync would do, before it does any of it.

Nothing here decides anything about files. `core/sync.py` walks and plans;
this shows the plan, lets somebody choose direction, mode and which kinds of
action to include, and hands the result to the queue. The dialog is the
confirmation -- the button says how many items it will remove, and nothing
is written or removed until it is pressed.
"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.core import sync as core_sync
from app.core.listing import count_of, format_size
from app.io.protocol import Conflict
from app.ui.dialogs import Dialog

#: Rows shown under one heading. A new tree of 80,000 files is one action and
#: one row, but a first sync into an empty folder of loose files is not, and a
#: list widget of 80,000 items takes longer to build than the walk did. The
#: heading's count is the whole of it either way, and the heading's checkbox
#: decides every item under it, shown or not.
SHOWN = 1000

#: Headings, in order, and whether a checkbox chooses them.
HEADINGS = (
    (core_sync.NEW_FOLDER, "New folders, copied whole", True),
    (core_sync.NEW_FILE, "New files", True),
    (core_sync.NEWER, "Newer here, replacing the older copy", True),
    (core_sync.EXTRA_FOLDER, "Folders only on the target, removed", True),
    (core_sync.EXTRA_FILE, "Files only on the target, removed", True),
    (core_sync.TARGET_NEWER, "Newer on the target -- left alone", False),
    (core_sync.DIFFERENT, "Same time, different size -- left alone", False),
    (core_sync.CLASH, "A folder on one side, a file on the other -- left alone", False),
    (core_sync.LINK, "Links and junctions -- not followed", False),
)


def _when(mtime: float) -> str:
    if not mtime:
        return ""
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime))


class SyncDialog(Dialog):
    def __init__(self, scanner, queue, left: str, right: str, *,
                 from_left: bool = True, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scanner = scanner
        self._queue = queue
        self._left, self._right = left, right
        self._from_left = from_left
        self._scan: core_sync.Scan | None = None
        self._plan: core_sync.Plan | None = None
        self._headings: dict[str, QTreeWidgetItem] = {}
        self.submitted = False

        self.setWindowTitle("Synchronize folders")
        self.setModal(False)
        self.resize(860, 560)

        self._from = QLabel()
        self._to = QLabel()
        for label in (self._from, self._to):
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        swap = QPushButton("Swap direction")
        swap.clicked.connect(self._swap)

        self._update = QRadioButton("Update: copy what is new or newer")
        self._mirror = QRadioButton("Mirror: also remove what the source does not have")
        self._update.setChecked(True)
        modes = QButtonGroup(self)
        modes.addButton(self._update)
        modes.addButton(self._mirror)
        self._update.toggled.connect(self._replan)

        self._state = QLabel("Reading both folders...")
        self._state.setProperty("role", "note")
        self._warning = QLabel()
        self._warning.setProperty("role", "warn")
        self._warning.setWordWrap(True)
        self._warning.setVisible(False)

        self._list = QTreeWidget()
        self._list.setHeaderLabels(["What would happen", "Size", "Source", "Target"])
        self._list.setUniformRowHeights(True)
        self._list.itemChanged.connect(self._chosen_changed)

        self._go = QPushButton("Synchronize")
        self._go.setDefault(True)
        self._go.setEnabled(False)
        self._go.clicked.connect(self._submit)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)

        ends = QVBoxLayout()
        ends.setSpacing(2)
        ends.addWidget(self._from)
        ends.addWidget(self._to)
        top = QHBoxLayout()
        top.addLayout(ends, 1)
        top.addWidget(swap)

        buttons = QHBoxLayout()
        buttons.addWidget(self._state, 1)
        buttons.addWidget(self._go)
        buttons.addWidget(cancel)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.addLayout(top)
        layout.addWidget(self._update)
        layout.addWidget(self._mirror)
        layout.addWidget(self._warning)
        layout.addWidget(self._list, 1)
        layout.addLayout(buttons)

        self._label_ends()
        scanner.progressed.connect(self._progress)
        scanner.finished.connect(self._scanned)
        scanner.start(left, right)

    # ------------------------------------------------------------- the ends

    @property
    def source(self) -> str:
        return self._left if self._from_left else self._right

    @property
    def target(self) -> str:
        return self._right if self._from_left else self._left

    def _label_ends(self) -> None:
        self._from.setText(f"From  {self.source}")
        self._to.setText(f"To      {self.target}")

    def _swap(self) -> None:
        self._from_left = not self._from_left
        self._label_ends()
        self._replan()

    # ----------------------------------------------------------- the walk

    def _progress(self, left: int, right: int) -> None:
        self._state.setText(f"Reading both folders...  {left + right:,} found")

    def _scanned(self, scan) -> None:
        self._scan = scan
        if scan.error:
            self._state.setText(f"Could not read {scan.error}")
            return
        if not scan.complete:
            self._mirror.setEnabled(False)
            self._update.setChecked(True)
        self._replan()

    def _replan(self, *_args) -> None:
        scan = self._scan
        if scan is None or scan.error:
            return
        source_rows = scan.left_rows if self._from_left else scan.right_rows
        target_rows = scan.right_rows if self._from_left else scan.left_rows
        self._plan = core_sync.plan(source_rows, target_rows,
                                    mirror=self._mirror.isChecked())
        self._fill()

    # ----------------------------------------------------------- the plan

    def _fill(self) -> None:
        plan = self._plan
        self._list.blockSignals(True)
        self._list.clear()
        self._headings.clear()
        for kind, title, choosable in HEADINGS:
            if kind in core_sync.REMOVALS and not plan.mirror:
                continue
            actions = plan.of(kind)
            if not actions:
                continue
            heading = QTreeWidgetItem([f"{title}  ({len(actions):,})"])
            heading.setData(0, Qt.UserRole, kind)
            if choosable:
                heading.setFlags(heading.flags() | Qt.ItemIsUserCheckable)
                heading.setCheckState(0, Qt.Checked)
            else:
                heading.setFlags(heading.flags() & ~Qt.ItemIsUserCheckable)
            size = sum(action.size for action in actions)
            if size:
                heading.setText(1, format_size(size))
            for action in actions[:SHOWN]:
                name = action.path
                if action.files > 1 or kind in (core_sync.NEW_FOLDER,
                                                core_sync.EXTRA_FOLDER):
                    name += f"   ({count_of(action.files, 'file') or 'empty'})"
                child = QTreeWidgetItem([name,
                                         format_size(action.size) if action.size else "",
                                         _when(action.source_mtime),
                                         _when(action.target_mtime)])
                child.setFlags(child.flags() & ~Qt.ItemIsUserCheckable)
                heading.addChild(child)
            if len(actions) > SHOWN:
                more = QTreeWidgetItem([f"... and {len(actions) - SHOWN:,} more"])
                more.setFlags(Qt.NoItemFlags)
                heading.addChild(more)
            self._list.addTopLevelItem(heading)
            heading.setExpanded(len(actions) <= 50)
            self._headings[kind] = heading
        for column in (1, 2, 3):
            self._list.resizeColumnToContents(column)
        self._list.setColumnWidth(0, max(380, self._list.columnWidth(0)))
        self._list.blockSignals(False)
        self._explain()

    def _chosen(self) -> list:
        plan = self._plan
        if plan is None:
            return []
        wanted = {kind for kind, heading in self._headings.items()
                  if heading.flags() & Qt.ItemIsUserCheckable
                  and heading.checkState(0) == Qt.Checked}
        return [action for action in plan.copies + plan.removals
                if action.kind in wanted]

    def _chosen_changed(self, *_args) -> None:
        self._explain()

    def _explain(self) -> None:
        """The line by the button, the warning, and what the button says."""
        scan, plan = self._scan, self._plan
        chosen = self._chosen()
        copies = [a for a in chosen if a.kind in core_sync.COPIES]
        removals = [a for a in chosen if a.kind in core_sync.REMOVALS]
        left_alone = plan.of(*core_sync.LEFT_ALONE) if plan else []

        warnings: list[str] = []
        if scan is not None and not scan.complete:
            for side, found in (("left", scan.left_gaps), ("right", scan.right_gaps)):
                if found:
                    where = scan.left if side == "left" else scan.right
                    warnings.append(f"{where}: {'; '.join(found)}.")
            warnings.append("Mirror needs to have seen everything, so only "
                            "update is offered.")
        if removals and core_sync.on_a_share(self.target):
            warnings.append("The target is a network folder: Windows deletes "
                            "files removed from it rather than moving them to "
                            "the Recycle Bin.")
        self._warning.setText("  ".join(warnings))
        self._warning.setVisible(bool(warnings))

        if plan is not None and plan.empty and not left_alone:
            self._state.setText("Already in step: nothing to copy or remove.")
        else:
            parts = []
            if copies:
                files = sum(a.files for a in copies)
                parts.append(f"copy {count_of(files, 'file')}, "
                             f"{format_size(sum(a.size for a in copies))}")
            if removals:
                parts.append(f"remove {count_of(len(removals), 'item')}")
            if left_alone:
                parts.append(f"{len(left_alone):,} left alone")
            self._state.setText("  ·  ".join(parts) if parts else "Nothing chosen.")
        self._go.setEnabled(bool(copies or removals))
        self._go.setText(f"Synchronize, removing {len(removals):,}" if removals
                         else "Synchronize")

    # ----------------------------------------------------------- doing it

    def _submit(self) -> None:
        chosen = self._chosen()
        sources, into = core_sync.copy_request(chosen, self.source, self.target)
        if sources:
            # Newer only, checked again as each file is written: the plan
            # was true when the folders were read, and this keeps it from
            # overwriting anything that has changed on the target since.
            self._queue.copy_into(sources, self.target, into,
                                  conflict=Conflict.NEWER)
        removing = core_sync.removal_request(chosen, self.target)
        if removing and self._plan is not None and self._plan.mirror:
            self._queue.recycle(removing)
        self.submitted = True
        self.accept()

    def done(self, result: int) -> None:  # noqa: D401 - Qt naming
        self._scanner.cancel()
        try:
            self._scanner.progressed.disconnect(self._progress)
            self._scanner.finished.disconnect(self._scanned)
        except (RuntimeError, TypeError):
            pass
        super().done(result)

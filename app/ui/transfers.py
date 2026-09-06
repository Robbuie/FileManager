"""The window's half of the transfer queue: a readout, a queue, and a question.

Three pieces, and the split between them is deliberate. Most transfers are
started and then ignored, so the one that is always visible is the smallest --
a line in the status bar. The queue is behind it, for when something needs
pausing or cancelling. The conflict dialog is the only one that interrupts,
because it is the only one that cannot proceed without an answer.

Nothing here decides anything about files. It shows what `core.TransferQueue`
holds and sends back what the user clicked.
"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QProgressBar,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.core.listing import format_size
from app.io.protocol import Conflict, Transfer


def _when(mtime: float | None) -> str:
    if not mtime:
        return "unknown"
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime))
    except (ValueError, OSError, OverflowError):
        return "unknown"


def _size(value: int | None) -> str:
    return "unknown" if value is None else format_size(value)


class TransferBar(QWidget):
    """One line in the status bar, and nothing at all when nothing is running.

    Chrome that is permanently present for something that is usually not
    happening is chrome that stops being read.
    """

    opened = Signal()   # the user wants the queue

    def __init__(self, queue, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._queue = queue

        self._label = QLabel("")
        self._label.setProperty("role", "transfer")
        self._bar = QProgressBar()
        self._bar.setTextVisible(False)
        self._bar.setFixedWidth(140)
        self._bar.setFixedHeight(10)
        self._bar.setRange(0, 100)

        self._pause = QToolButton()
        self._pause.setText("Pause")
        self._pause.setProperty("role", "status")
        self._pause.setFocusPolicy(Qt.NoFocus)
        self._pause.clicked.connect(self._toggle)

        self._more = QToolButton()
        self._more.setText("Queue")
        self._more.setProperty("role", "status")
        self._more.setFocusPolicy(Qt.NoFocus)
        self._more.clicked.connect(self.opened)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self._label)
        layout.addWidget(self._bar)
        layout.addWidget(self._pause)
        layout.addWidget(self._more)

        queue.changed.connect(self.refresh)
        self.refresh()

    def _toggle(self) -> None:
        self._queue.resume() if self._queue.paused else self._queue.pause()

    def refresh(self) -> None:
        job = self._queue.current()
        if job is None:
            self.setVisible(False)
            return
        self.setVisible(True)
        done, total = self._queue.totals()
        waiting = len(self._queue.active) - 1
        text = job.brief
        if total:
            text += f"  ·  {format_size(done)} of {format_size(total)}"
        if waiting > 0:
            text += f"  ·  {waiting} more queued"
        if self._queue.paused:
            text = "Paused  ·  " + text
        # Elided rather than allowed to push the bar and the buttons off the
        # end of the status bar. The middle goes first, because both ends of
        # this line carry the information -- what is happening, and how far.
        self._label.setText(
            QFontMetrics(self._label.font()).elidedText(text, Qt.ElideRight, 380)
        )
        self._label.setToolTip(text)
        self._bar.setValue(job.percent)
        self._pause.setText("Resume" if self._queue.paused else "Pause")


class QueueDialog(QDialog):
    """What is running and what is behind it. Not modal: work continues."""

    def __init__(self, queue, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._queue = queue
        self.setWindowTitle("Transfers")
        self.setModal(False)
        self.setMinimumWidth(520)

        self._list = QListWidget()
        self._list.setMinimumHeight(160)

        self._pause = QPushButton("Pause")
        self._pause.clicked.connect(self._toggle)
        self._cancel = QPushButton("Cancel selected")
        self._cancel.clicked.connect(self._cancel_selected)
        self._clear = QPushButton("Clear finished")
        self._clear.clicked.connect(queue.forget_finished)
        close = QPushButton("Close")
        close.clicked.connect(self.close)

        buttons = QHBoxLayout()
        buttons.addWidget(self._pause)
        buttons.addWidget(self._cancel)
        buttons.addWidget(self._clear)
        buttons.addStretch(1)
        buttons.addWidget(close)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.addWidget(self._list)
        layout.addLayout(buttons)

        queue.changed.connect(self.refresh)
        self.refresh()

    def _toggle(self) -> None:
        self._queue.resume() if self._queue.paused else self._queue.pause()

    def _cancel_selected(self) -> None:
        for item in self._list.selectedItems():
            job_id = item.data(Qt.UserRole)
            if job_id is not None:
                self._queue.cancel(int(job_id))

    def refresh(self) -> None:
        """Rebuilt rather than patched: the list is a handful of rows."""
        selected = {item.data(Qt.UserRole) for item in self._list.selectedItems()}
        self._list.clear()
        for job_id in self._queue.order:
            job = self._queue.jobs[job_id]
            verb = "Copy" if job.kind is Transfer.COPY else "Move"
            if job.state == "done":
                outcome = "cancelled" if job.cancelled else "done"
                detail = (f"{outcome}: {job.copied:,} copied, {job.skipped:,} skipped, "
                          f"{job.failed:,} failed")
            elif job.total:
                detail = (f"{job.percent}%  ·  {format_size(job.done)} of "
                          f"{format_size(job.total)}  ·  {job.current}")
            else:
                detail = job.label
            self._list.addItem(f"{verb} to {job.destination}\n    {detail}")
            item = self._list.item(self._list.count() - 1)
            item.setData(Qt.UserRole, job_id)
            if job_id in selected:
                item.setSelected(True)
        self._pause.setText("Resume" if self._queue.paused else "Pause")


class ConflictDialog(QDialog):
    """The one interruption: a name at the destination is already taken.

    Both files are described -- size and date, side by side -- because that is
    the information the decision is actually made on, and a dialog that only
    says the name forces the user to go and look.
    """

    def __init__(self, name: str, source: dict, target: dict,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("That name is taken")
        self.setModal(True)
        self.setMinimumWidth(520)
        self.action: Conflict | None = None

        headline = QLabel(f"{name} already exists in the destination.")
        headline.setWordWrap(True)

        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(4)
        for column, (title, info) in enumerate((("Already there", target),
                                                ("Being copied", source))):
            caption = QLabel(title)
            caption.setProperty("role", "note")
            grid.addWidget(caption, 0, column)
            grid.addWidget(QLabel(_size(info.get("size"))), 1, column)
            grid.addWidget(QLabel(_when(info.get("mtime"))), 2, column)

        self._rest = QCheckBox("Do this for everything else in this transfer")

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        for label, action in (("Replace", Conflict.OVERWRITE),
                              ("Replace if newer", Conflict.NEWER),
                              ("Keep both", Conflict.RENAME),
                              ("Skip", Conflict.SKIP)):
            button = QPushButton(label)
            button.clicked.connect(lambda _checked=False, a=action: self._choose(a))
            buttons.addWidget(button)
        buttons.addStretch(1)
        stop = QPushButton("Cancel transfer")
        stop.setDefault(True)
        stop.clicked.connect(self.reject)
        buttons.addWidget(stop)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.addWidget(headline)
        layout.addLayout(grid)
        layout.addWidget(self._rest)
        layout.addLayout(buttons)

    def _choose(self, action: Conflict) -> None:
        self.action = action
        self.accept()

    @property
    def apply_to_all(self) -> bool:
        return self._rest.isChecked()


class TransferPrompt(QDialog):
    """Where it is going, before anything moves.

    The destination is filled in from the other pane and can be edited, but it
    is never assumed: this dialog is the confirmation, and there is no path
    from a keystroke to a file being written that does not pass through it.
    """

    def __init__(self, kind: Transfer, names: list[str], destination: str,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        verb = "Copy" if kind is Transfer.COPY else "Move"
        self.setWindowTitle(verb)
        self.setModal(True)
        self.setMinimumWidth(520)

        what = names[0] if len(names) == 1 else f"{len(names):,} items"
        headline = QLabel(f"{verb} {what} to:")
        headline.setWordWrap(True)

        self._field = QLineEdit(destination)
        self._field.selectAll()

        listing = QListWidget()
        listing.setSelectionMode(QListWidget.NoSelection)
        listing.setFocusPolicy(Qt.NoFocus)
        listing.setUniformItemSizes(True)
        shown = names[:8]
        listing.addItems(shown)
        if len(names) > len(shown):
            listing.addItem(f"and {len(names) - len(shown):,} more")
        row = listing.sizeHintForRow(0) if listing.count() else 18
        listing.setFixedHeight(listing.count() * row + 8)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText(verb)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.addWidget(headline)
        if len(names) > 1:
            layout.addWidget(listing)
        layout.addWidget(self._field)
        layout.addWidget(buttons)

    def destination(self) -> str:
        return self._field.text().strip()

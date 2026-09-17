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

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QFontMetrics, QPainter
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.core.listing import format_size
from app.core.transfers import VERBS, shorten
from app.io.protocol import Conflict, JobKind
from app.ui.dialogs import Dialog
from app.ui.rows import parse_colour

#: Height of one row in the queue panel. Two lines of text, a bar, and air
#: under it -- named because the list has to hand the same number to every
#: item as a size hint, and a widget taller than its item is a widget clipped.
ROW_HEIGHT = 56


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
        waiting = len(self._queue.active) - 1
        text = job.brief
        if job.counts_items:
            # A delete's own numbers, not the queue's totals. The totals are
            # bytes, and this job is not measured in them.
            if job.total:
                text += f"  ·  {job.done:,} of {job.total:,}"
        else:
            done, total = self._queue.totals()
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
        # A shell recycle cannot be paused, so the button says nothing rather
        # than offering something that will not happen. Greyed rather than
        # hidden: a control that disappears and comes back is harder to read
        # than one that is plainly unavailable for a moment.
        self._pause.setEnabled(job.interruptible)


class JobRow(QWidget):
    """One job in the queue panel: what it is, where, and how far through.

    A painted widget rather than a list item with a stylesheet, for the reason
    `app/ui/rows.py` gives about the size bar: a second shape inside a row is
    not something QSS can be asked for. The progress bar here is the shape, and
    a `QProgressBar` per row would bring the platform's own drawing back into a
    window that has spent four versions getting rid of it.

    The row knows nothing about the queue. It is handed a `JobState` and
    redraws; the panel decides which rows exist and what the buttons do.
    """

    #: The bar under the text. Same two pixels as the size bar in a listing,
    #: and for the same reason -- at this height it reads as part of the line
    #: above it rather than as a control of its own.
    BAR_HEIGHT = 3
    PADDING = 8

    #: Air under the bar. Without it the bar sits on the bottom edge of the
    #: row, and a faint full-width line at the bottom of a row is a rule
    #: between rows -- which is what it read as in the first render, however
    #: obvious its purpose was in the code.
    BAR_LIFT = 8

    #: Room for the percentage at the right of the first line. Fixed rather
    #: than measured, so the titles of five rows all elide against the same
    #: edge instead of each one stopping wherever its own number happened to
    #: end.
    PERCENT_WIDTH = 44

    def __init__(self, job, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._job = job
        self._t: dict[str, str] = {}
        self.setMinimumHeight(ROW_HEIGHT)

    def apply_tokens(self, tokens: dict[str, str]) -> None:
        self._t = dict(tokens or {})
        self.update()

    def set_job(self, job) -> None:
        self._job = job
        self.update()

    @property
    def job(self):
        return self._job

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        job = self._job
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = self.rect().adjusted(self.PADDING, 4, -self.PADDING, -4)
        metrics = QFontMetrics(self.font())

        ink = parse_colour(self._t.get("txt_0"))
        muted = parse_colour(self._t.get("txt_2"))
        # A step that cannot report on itself gets no bar and no percentage.
        # A recycle is one shell call: it returns when it is finished and says
        # nothing on the way, so a trough sitting at 0% for two minutes would
        # be the panel inventing a number it does not have.
        drawing_bar = (job.state not in ("done", "queued")
                       and job.total > 0 and job.interruptible)

        title = f"{VERBS[job.kind][1]} {job.files:,} item(s)"
        if job.destination:
            title += f" to {job.destination}"
        width = rect.width() - (self.PERCENT_WIDTH if drawing_bar else 0)
        painter.setPen(ink)
        painter.drawText(rect.left(), rect.top() + 13,
                         metrics.elidedText(title, Qt.ElideMiddle, width))
        if drawing_bar:
            # The number and the bar are one readout in two forms. The bar is
            # the one that can be read at a glance and the number is the one
            # that can be read at all when the bar is nearly empty or nearly
            # full, which is most of a long copy.
            painter.setPen(muted)
            # Aligned to the title's own baseline rather than to a box of its
            # own, so the number sits on the line it belongs to.
            painter.drawText(rect.right() - self.PERCENT_WIDTH,
                             rect.top() + 13 - metrics.ascent(),
                             self.PERCENT_WIDTH, metrics.height(),
                             int(Qt.AlignRight | Qt.AlignVCenter),
                             f"{job.percent}%")

        painter.setPen(muted)
        painter.drawText(rect.left(), rect.top() + 28,
                         metrics.elidedText(_detail(job), Qt.ElideRight,
                                            rect.width()))

        # No bar for a job that has not started or has no total to draw
        # against. An empty trough sitting under a queued job reads as a job
        # stuck at zero rather than as one that has not begun.
        if not drawing_bar:
            painter.end()
            return
        trough = QRectF(rect.left(), rect.bottom() - self.BAR_LIFT,
                        rect.width(), self.BAR_HEIGHT)
        painter.setPen(Qt.NoPen)
        painter.setBrush(parse_colour(self._t.get("bg_4")))
        painter.drawRoundedRect(trough, 1.5, 1.5)
        if job.percent <= 0:
            # Nothing done yet, so nothing is drawn. A minimum-width stub at
            # zero is a dot on the left of the trough, which reads as a
            # rendering fault rather than as a job about to start.
            painter.end()
            return
        # A held job's bar is drawn muted rather than in the accent. The
        # accent is this application's way of saying "this is moving", and a
        # held job is the one case where that would be false while the bar
        # still has something in it.
        painter.setBrush(parse_colour(self._t.get("txt_2" if job.held else "sel")))
        filled = QRectF(trough)
        filled.setWidth(max(3.0, trough.width() * job.percent / 100.0))
        painter.drawRoundedRect(filled, 1.5, 1.5)
        painter.end()


def _detail(job) -> str:
    """The second line of a row: what it is doing, in its own units.

    A transfer counts bytes and a delete counts items, and this is the one
    place that difference shows up in words. Getting it wrong is not a
    cosmetic matter: "3.2 GB of 4.1 GB" against a recycle of small files would
    be a number nobody could act on.
    """
    if job.state == "done":
        outcome = "cancelled" if job.cancelled else "done"
        verb = "removed" if job.kind.removes else "copied"
        return (f"{outcome}: {job.copied:,} {verb}, {job.skipped:,} skipped, "
                f"{job.failed:,} failed")
    if job.held:
        return "held"
    if job.state == "queued":
        return "waiting its turn"
    if job.state == "scanning":
        return "counting what is there"
    if job.state == "waiting":
        return f"waiting for an answer about {shorten(job.current)}"
    if not job.interruptible:
        return "the shell is deleting these; it cannot be interrupted"
    if job.counts_items:
        where = f"{job.done:,} of {job.total:,} item(s)"
    elif job.total:
        where = f"{format_size(job.done)} of {format_size(job.total)}"
    else:
        where = "starting"
    return f"{where}  ·  {shorten(job.current)}" if job.current else where


class QueueDialog(Dialog):
    """Everything the queue holds: running, waiting and finished.

    Not modal -- work carries on behind it, which is the point of a queue --
    and not a dock either. A dock would take width from the panes permanently
    for something that is empty most of the time, and the status bar readout is
    already the always-visible half of this.

    The buttons act on the selection, and which of them are enabled is worked
    out from the selected jobs rather than from the queue as a whole. That is
    the difference between a panel and a list with buttons under it: cancel is
    offered for a job that can be cancelled, hold for one that can be held, and
    a recycle that the shell has already started offers neither, because
    neither would do anything.
    """

    def __init__(self, queue, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._queue = queue
        self._rows: dict[int, JobRow] = {}
        self._tokens: dict[str, str] = {}
        self.setWindowTitle("Queue")
        self.setModal(False)
        self.setMinimumWidth(560)

        self._list = QListWidget()
        self._list.setMinimumHeight(200)
        self._list.setSelectionMode(QListWidget.ExtendedSelection)
        self._list.setUniformItemSizes(False)
        self._list.itemSelectionChanged.connect(self._sync_buttons)

        self._hold = QPushButton("Hold")
        self._hold.clicked.connect(self._toggle_hold)
        self._up = QPushButton("Up")
        self._up.clicked.connect(lambda: self._move(-1))
        self._down = QPushButton("Down")
        self._down.clicked.connect(lambda: self._move(1))
        self._cancel = QPushButton("Cancel")
        self._cancel.clicked.connect(self._cancel_selected)
        self._pause = QPushButton("Pause all")
        self._pause.clicked.connect(self._toggle_pause)
        self._clear = QPushButton("Clear finished")
        self._clear.clicked.connect(queue.forget_finished)
        close = QPushButton("Close")
        close.clicked.connect(self.close)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        for button in (self._hold, self._up, self._down, self._cancel):
            button.setFocusPolicy(Qt.NoFocus)
            buttons.addWidget(button)
        buttons.addSpacing(12)
        for button in (self._pause, self._clear):
            button.setFocusPolicy(Qt.NoFocus)
            buttons.addWidget(button)
        buttons.addStretch(1)
        buttons.addWidget(close)

        self._empty = QLabel("Nothing running.")
        self._empty.setProperty("role", "note")

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.addWidget(self._list)
        layout.addWidget(self._empty)
        layout.addLayout(buttons)

        queue.changed.connect(self.refresh)
        self.refresh()

    def apply_tokens(self, tokens: dict[str, str]) -> None:
        """The rows paint themselves, so they need the same tokens the sheet
        was rendered from -- handed down rather than fetched, exactly as the
        panes get theirs."""
        self._tokens = dict(tokens or {})
        for row in self._rows.values():
            row.apply_tokens(self._tokens)

    # ------------------------------------------------------------- commands

    def _selected(self) -> list:
        jobs = []
        for item in self._list.selectedItems():
            job = self._queue.jobs.get(item.data(Qt.UserRole))
            if job is not None:
                jobs.append(job)
        return jobs

    def _toggle_pause(self) -> None:
        self._queue.resume() if self._queue.paused else self._queue.pause()

    def _toggle_hold(self) -> None:
        """One button for both, decided by the selection.

        With a mixed selection, holding wins: the user reached for a button
        labelled Hold, and releasing something they were trying to hold is the
        worse of the two ways to be wrong.
        """
        jobs = [job for job in self._selected() if job.state != "done"]
        if not jobs:
            return
        if all(job.held for job in jobs):
            for job in jobs:
                self._queue.release(job.id)
        else:
            for job in jobs:
                if not job.held:
                    self._queue.hold(job.id)

    def _move(self, delta: int) -> None:
        """Move the selection a place. Down goes back to front so that two
        adjacent rows moving together do not swap past each other."""
        jobs = [job for job in self._selected() if job.state == "queued"]
        for job in (reversed(jobs) if delta > 0 else jobs):
            self._queue.move_job(job.id, delta)

    def _cancel_selected(self) -> None:
        for job in self._selected():
            self._queue.cancel(job.id)

    # ---------------------------------------------------------------- state

    def refresh(self) -> None:
        """Rows are reused rather than rebuilt.

        A rebuild per event is what the old version did, and it was fine while
        the list was three lines of text. It stops being fine the moment a row
        can be selected: rebuilding drops the selection several times a second
        while a copy runs, so the buttons under it would keep going dead in the
        user's hand.
        """
        wanted = list(self._queue.order)
        for job_id in [i for i in self._rows if i not in wanted]:
            self._rows.pop(job_id)

        for position, job_id in enumerate(wanted):
            job = self._queue.jobs[job_id]
            row = self._rows.get(job_id)
            if row is None:
                row = JobRow(job)
                row.apply_tokens(self._tokens)
                self._rows[job_id] = row
                item = QListWidgetItem()
                item.setData(Qt.UserRole, job_id)
                item.setSizeHint(QSize(0, ROW_HEIGHT))
                self._list.insertItem(position, item)
                self._list.setItemWidget(item, row)
            else:
                row.set_job(job)
            current = self._index_of(job_id)
            if current not in (position, -1):
                self._reinsert(job_id, position)

        while self._list.count() > len(wanted):
            self._list.takeItem(self._list.count() - 1)

        self._empty.setVisible(not wanted)
        self._list.setVisible(bool(wanted))
        self._pause.setText("Resume all" if self._queue.paused else "Pause all")
        self._sync_buttons()

    def _index_of(self, job_id: int) -> int:
        for index in range(self._list.count()):
            if self._list.item(index).data(Qt.UserRole) == job_id:
                return index
        return -1

    def _reinsert(self, job_id: int, position: int) -> None:
        """Move a row that changed places, keeping its widget and selection.

        `takeItem` drops the widget the item was carrying, so the widget is put
        back explicitly. Without that a reorder leaves a blank row, which looks
        exactly like a crash and is not one.
        """
        index = self._index_of(job_id)
        if index < 0:
            return
        selected = self._list.item(index).isSelected()
        row = self._rows[job_id]
        self._list.takeItem(index)
        item = QListWidgetItem()
        item.setData(Qt.UserRole, job_id)
        item.setSizeHint(QSize(0, ROW_HEIGHT))
        self._list.insertItem(position, item)
        self._list.setItemWidget(item, row)
        item.setSelected(selected)

    def _sync_buttons(self) -> None:
        jobs = self._selected()
        live = [job for job in jobs if job.state != "done"]
        waiting = [job for job in jobs if job.state == "queued"]
        self._hold.setEnabled(bool([j for j in live if j.interruptible]))
        self._hold.setText("Release" if live and all(j.held for j in live) else "Hold")
        self._up.setEnabled(bool(waiting))
        self._down.setEnabled(bool(waiting))
        self._cancel.setEnabled(bool([j for j in live if j.interruptible]))
        self._clear.setEnabled(
            any(self._queue.jobs[i].state == "done" for i in self._queue.order))


class ConflictDialog(Dialog):
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


class TransferPrompt(Dialog):
    """Where it is going, before anything moves.

    The destination is filled in from the other pane and can be edited, but it
    is never assumed: this dialog is the confirmation, and there is no path
    from a keystroke to a file being written that does not pass through it.
    """

    def __init__(self, kind: JobKind, names: list[str], destination: str,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        verb = "Copy" if kind is JobKind.COPY else "Move"
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

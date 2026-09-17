"""The strip above a pane's listing: the folder's name, and what it is made of.

0.27. The path bar says where a folder is; this says what it is. A dated job
folder full of Logix and HMI files looks different at a glance from one of
drawings and PDFs, and a folder that has quietly filled with 40 MB archives
shows it before anybody sorts by size.

Everything it draws comes from rows the listing already holds -- names, sizes,
whether a row is a folder -- so it costs no reads and it is as current as the
listing is. It is recomputed on a short timer rather than on every batch, so a
50,000-row folder arriving in fifty batches is summed a handful of times, not
fifty.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath
from PySide6.QtWidgets import QSizePolicy, QWidget

from app.core import filetypes
from app.core.listing import format_size
from app.ui.rows import IDLE_OPACITY, parse_colour

HEIGHT = 62
#: How many families the legend names. The bar shows all of them; the legend
#: stops where the names would stop fitting in a pane half a screen wide.
LEGEND = 4
SETTLE_MS = 200


class FolderHeader(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self._tokens: dict[str, str] = {}
        self._title = ""
        self._model = None
        self._live = True
        self.parts: list[tuple[str, int, int]] = []
        self.folders = 0
        self.files = 0
        self.bytes = 0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(SETTLE_MS)
        self._timer.timeout.connect(self.recount)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        return QSize(0, HEIGHT)

    def apply_tokens(self, tokens: dict[str, str]) -> None:
        self._tokens = tokens
        self.update()

    def follow(self, model, title: str) -> None:
        """Watch this listing. Connections are made once per model."""
        self._title = title
        if model is not self._model:
            self._model = model
            if model is not None and not getattr(model, "_header_watched", False):
                for signal in (model.modelReset, model.layoutChanged,
                               model.rowsInserted, model.rowsRemoved):
                    signal.connect(lambda *_a, m=model: self._soon(m))
                model._header_watched = True  # noqa: SLF001 - a marker, not state
        self.recount()

    def set_live(self, live: bool) -> None:
        """The idle pane's header steps back with its rows (IDLE_OPACITY)."""
        if live != self._live:
            self._live = live
            self.update()

    def set_title(self, title: str) -> None:
        if title != self._title:
            self._title = title
            self.update()

    def _soon(self, model) -> None:
        # Only the listing on screen matters; a background tab's batches are
        # ignored and it is counted when it comes to the front.
        if model is self._model and not self._timer.isActive():
            self._timer.start()

    def recount(self) -> None:
        entries = self._model.entries() if self._model is not None else []
        self.folders = sum(1 for entry in entries if entry.is_dir)
        self.files = len(entries) - self.folders
        self.bytes = sum(int(entry.size or 0) for entry in entries if not entry.is_dir)
        self.parts = filetypes.composition(entries)
        self.update()

    def summary(self) -> str:
        bits = []
        if self.folders:
            bits.append(f"{self.folders:,} folder{'s' if self.folders != 1 else ''}")
        bits.append(f"{self.files:,} file{'s' if self.files != 1 else ''}")
        if self.bytes:
            bits.append(format_size(self.bytes))
        return "  ·  ".join(bits)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if not self._tokens:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        if not self._live:
            painter.setOpacity(IDLE_OPACITY)
        left, right = 12.0, float(self.width() - 12)

        title_font = QFont(self.font())
        title_font.setPixelSize(20)
        title_font.setWeight(QFont.DemiBold)
        small = QFont(self.font())
        small.setPixelSize(12)

        summary = self.summary()
        small_metrics = QFontMetricsF(small)
        summary_w = small_metrics.horizontalAdvance(summary)
        painter.setFont(small)
        painter.setPen(parse_colour(self._tokens.get("txt_2")))
        painter.drawText(QRectF(right - summary_w, 4, summary_w, 28),
                         int(Qt.AlignRight | Qt.AlignVCenter), summary)

        painter.setFont(title_font)
        painter.setPen(parse_colour(self._tokens.get("txt_0")))
        room = max(0.0, right - left - summary_w - 16)
        title = QFontMetricsF(title_font).elidedText(self._title, Qt.ElideMiddle, room)
        painter.drawText(QRectF(left, 2, room, 30),
                         int(Qt.AlignLeft | Qt.AlignVCenter), title)

        bar_top = 36.0
        width = right - left
        track = QPainterPath()
        track.addRoundedRect(QRectF(left, bar_top, width, 5), 2.5, 2.5)
        painter.fillPath(track, parse_colour(self._tokens.get("bg_3")))
        weights = [part[1] for part in self.parts]
        if not any(weights):
            weights = [part[2] for part in self.parts]
        total = float(sum(weights)) or 1.0
        painter.save()
        painter.setClipPath(track)
        x = left
        for (kind, _bytes, _count), weight in zip(self.parts, weights):
            span = width * weight / total
            if span <= 0:
                continue
            painter.fillRect(QRectF(x, bar_top, max(1.0, span - 1.5), 5),
                             parse_colour(self._tokens.get(f"kind_{kind}")))
            x += span
        painter.restore()

        painter.setFont(small)
        x = left
        for (kind, _bytes, _count), weight in list(zip(self.parts, weights))[:LEGEND]:
            share = f"{round(100 * weight / total)}%"
            label = filetypes.LABELS.get(kind, kind)
            label_w = small_metrics.horizontalAdvance(label)
            share_w = small_metrics.horizontalAdvance(share)
            needed = 12 + label_w + 5 + share_w + 16
            if x + needed > right:
                break
            dot = QPainterPath()
            dot.addRoundedRect(QRectF(x, 49, 7, 7), 2, 2)
            painter.fillPath(dot, parse_colour(self._tokens.get(f"kind_{kind}")))
            painter.setPen(parse_colour(self._tokens.get("txt_1")))
            painter.drawText(QRectF(x + 12, 44, label_w + 1, 17),
                             int(Qt.AlignLeft | Qt.AlignVCenter), label)
            painter.setPen(parse_colour(self._tokens.get("txt_2")))
            painter.drawText(QRectF(x + 12 + label_w + 5, 44, share_w + 1, 17),
                             int(Qt.AlignLeft | Qt.AlignVCenter), share)
            x += needed
        painter.end()

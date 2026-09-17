"""The splitter the rail and the two panes sit in, and the light around the active pane.

0.26 draws the panes as cards on the window's backdrop, and says which one has
the keyboard with a soft accent glow around its edge. The glow cannot be a
`QGraphicsDropShadowEffect` on the pane: an effect renders its whole widget
into an offscreen picture on every repaint, and the widget is a listing that
repaints on every step of a scroll through 50,000 rows. So the glow is painted
*behind* the panes, by the splitter they sit in, into the margin and the
handle gap around them -- which are the only places it is visible anyway.

The glow moves between panes with a short ease rather than jumping, so the eye
follows Tab to the other side. It interpolates between the two panes' current
geometries on every frame rather than between remembered rectangles, so a
resize in the middle of the move still ends on the right pane.
"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QRectF, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QSplitter, QWidget

#: How far the light reaches past the card's edge, in logical pixels. It has
#: to fit in the margin around the splitter's children.
SPREAD = 10
#: The card's corner, which the glow's rings follow outwards.
RADIUS = 12.0
DURATION_MS = 180


class Deck(QSplitter):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(Qt.Horizontal, parent)
        self.setProperty("role", "deck")
        self.setContentsMargins(SPREAD, 6, SPREAD, 6)
        self.setHandleWidth(SPREAD)
        self._colour = QColor(0, 0, 0, 0)
        self._strength = 0.3
        self._target: QWidget | None = None
        self._source: QWidget | None = None
        self._progress = 1.0
        self._motion = QVariantAnimation(self)
        self._motion.setDuration(DURATION_MS)
        self._motion.setStartValue(0.0)
        self._motion.setEndValue(1.0)
        self._motion.setEasingCurve(QEasingCurve.OutCubic)
        self._motion.valueChanged.connect(self._step)

    def set_motion(self, on: bool) -> None:
        self._motion.setDuration(DURATION_MS if on else 0)

    def set_glow_colour(self, colour: str, strength: float = 0.3) -> None:
        self._colour = QColor(colour)
        self._strength = strength
        self.update()

    def glow_on(self, widget: QWidget | None, animate: bool = True) -> None:
        if widget is self._target:
            return
        self._source = self._target if animate else None
        self._target = widget
        self._motion.stop()
        if self._source is None or not self.isVisible():
            self._progress = 1.0
            self.update()
            return
        self._progress = 0.0
        self._motion.start()

    @property
    def target(self) -> QWidget | None:
        return self._target

    def glow_rect(self) -> QRectF | None:
        """Where the card being lit is, part way through a move included."""
        if self._target is None or self._target.isHidden():
            return None
        end = QRectF(self._target.geometry())
        if self._source is None or self._progress >= 1.0 or self._source.isHidden():
            return end
        start = QRectF(self._source.geometry())
        t = self._progress
        return QRectF(start.x() + (end.x() - start.x()) * t,
                      start.y() + (end.y() - start.y()) * t,
                      start.width() + (end.width() - start.width()) * t,
                      start.height() + (end.height() - start.height()) * t)

    def _step(self, value) -> None:
        self._progress = float(value)
        if self._progress >= 1.0:
            self._source = None
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().paintEvent(event)
        rect = self.glow_rect()
        if rect is None or self._colour.alpha() == 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setBrush(Qt.NoBrush)
        # Rings from the edge outwards, each fainter than the last. A quadratic
        # fall-off reads as light; a linear one reads as a border drawn thick.
        for ring in range(1, SPREAD + 1):
            fade = (1.0 - (ring - 1) / SPREAD) ** 2
            colour = QColor(self._colour)
            colour.setAlphaF(max(0.0, min(1.0, self._strength * fade)))
            pen = QPen(colour)
            pen.setWidthF(1.3)
            painter.setPen(pen)
            grown = rect.adjusted(-ring + 0.5, -ring + 0.5, ring - 0.5, ring - 0.5)
            painter.drawRoundedRect(grown, RADIUS + ring, RADIUS + ring)
        painter.end()

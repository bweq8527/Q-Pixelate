from PySide6.QtCore import QAbstractAnimation, QEasingCurve, QPointF, QRect, QRectF, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QPen

from .palette import INK, MUSTARD
from .ui_theme import motion_enabled


def tint(color, alpha):
    result = QColor(color)
    result.setAlpha(alpha)
    return result


class GestureFeedback:
    """Draw a short accent at the input point without moving the hit target."""

    def __init__(self, widget):
        self._widget = widget
        widget.setProperty('customInteractionFeedback', True)
        self._point = QPointF()
        self._area = QRect()
        self._color = MUSTARD
        self._flash = QVariantAnimation(widget)
        self._flash.setDuration(190)
        self._flash.setStartValue(0.0)
        self._flash.setEndValue(1.0)
        self._flash.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._flash.valueChanged.connect(self._repaint)

    def trigger(self, point, color=MUSTARD):
        if not self._widget.isVisible() or not self._widget.isEnabled() or not motion_enabled():
            return
        self._flash.stop()
        self._repaint()
        self._point = QPointF(
            max(0, min(self._widget.width()-1, point.x())),
            max(0, min(self._widget.height()-1, point.y())),
        )
        self._color = color
        self._area = QRectF(self._point.x()-22, self._point.y()-22, 44, 44).toAlignedRect()
        self._flash.start()
        self._repaint()

    def _repaint(self, *_):
        if not self._area.isEmpty():
            self._widget.update(self._area)

    def paint(self, painter):
        if self._flash.state() != QAbstractAnimation.State.Running:
            return
        progress = self._flash.currentValue()
        radius = 7 + 11 * progress
        border = QRectF(self._point.x()-radius, self._point.y()-radius, 2*radius, 2*radius)
        painter.save()
        painter.setOpacity(1-progress)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(INK), 4))
        painter.drawRect(border)
        painter.setPen(QPen(QColor(self._color), 2))
        painter.drawRect(border)
        painter.restore()

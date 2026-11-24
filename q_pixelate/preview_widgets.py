from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QSlider

from .gesture_feedback import GestureFeedback
from .palette import (
    BLUR_TONE, BRICK, INK, MOSAIC_TONE, MUSTARD, MUTED, PAPER, SPEED_TONE, STICKER_TONE,
    SURFACE, TRANSITION_TONE,
)


EDIT_COLORS = {
    'blur': ('模糊', BLUR_TONE),
    'sticker': ('贴纸', STICKER_TONE),
    'mosaic': ('马赛克', MOSAIC_TONE),
    'speed': ('倍速', SPEED_TONE),
    'transition': ('过渡', TRANSITION_TONE),
    'cut': ('删段拼接点', BRICK),
}


class PreviewProgress(QSlider):
    HANDLE_RADIUS = 6

    def __init__(self, spans=(), parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setAccessibleName('预览进度')
        self.setToolTip('点击或拖动定位，方向键微调')
        self.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.setRange(0, 0)
        self.setSingleStep(1000)
        self.setPageStep(10000)
        self.spans = tuple(spans)
        kinds = {span.kind for span in self.spans}
        self.kinds = tuple(kind for kind in EDIT_COLORS if kind in kinds)
        self.lanes = {kind: i for i, kind in enumerate(kind for kind in self.kinds if kind != 'cut')}
        self.setMinimumHeight(max(28, 12 + self.track_height))
        self._feedback = GestureFeedback(self)

    @property
    def track_height(self):
        return max(4, 5 * len(self.lanes) - 1)

    def _track(self):
        margin = self.HANDLE_RADIUS + 2
        return QRectF(margin, (self.height() - self.track_height) / 2,
                      max(1, self.width() - 2 * margin), self.track_height)

    def _drag_to(self, position):
        track = self._track()
        fraction = (position.x() - track.left()) / track.width()
        self.setSliderPosition(round(self.maximum() * max(0, min(1, fraction))))

    def _slider_point(self, track):
        x = track.left() + track.width() * self.sliderPosition() / max(1, self.maximum())
        return QPointF(x, track.center().y())

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or not self.maximum():
            event.ignore()
            return
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self.setSliderDown(True)
        self._drag_to(event.position())
        self._feedback.trigger(self._slider_point(self._track()))
        self.update()
        event.accept()

    def mouseMoveEvent(self, event):
        if self.isSliderDown():
            self._drag_to(event.position())
            event.accept()

    def mouseReleaseEvent(self, event):
        if self.isSliderDown() and event.button() == Qt.MouseButton.LeftButton:
            self._drag_to(event.position())
            self.setSliderDown(False)
            if self.maximum():
                self._feedback.trigger(self._slider_point(self._track()))
            self.update()
            event.accept()

    def keyPressEvent(self, event):
        super().keyPressEvent(event)
        if self.maximum() and event.key() in (
            Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down,
            Qt.Key.Key_PageUp, Qt.Key.Key_PageDown, Qt.Key.Key_Home, Qt.Key.Key_End,
        ):
            self._feedback.trigger(self._slider_point(self._track()))

    def wheelEvent(self, event):
        super().wheelEvent(event)
        if self.maximum() and event.isAccepted():
            self._feedback.trigger(self._slider_point(self._track()))

    def enterEvent(self, event):
        super().enterEvent(event)
        if self.maximum():
            self._feedback.trigger(self._slider_point(self._track()), BRICK)
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.update()

    def focusInEvent(self, event):
        super().focusInEvent(event)
        if self.maximum() and event.reason() in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason):
            self._feedback.trigger(self._slider_point(self._track()))

    def paintEvent(self, event):
        painter = QPainter(self)
        track = self._track()
        painter.setPen(QPen(QColor(INK if self.isEnabled() else MUTED), 1))
        painter.setBrush(QColor(INK if self.maximum() else PAPER))
        painter.drawRect(track.adjusted(0, -1, 0, 1))
        if not self.maximum():
            return

        def x_at(position):
            return track.left() + track.width() * min(1, position / self.maximum())

        for span in self.spans:
            if span.kind == 'cut':
                continue
            left = min(track.right() - 2, x_at(span.start_ms))
            right = max(left + 2, x_at(span.end_ms))
            y = track.top() + 5 * self.lanes[span.kind]
            painter.fillRect(QRectF(left, y, right - left, 4), QColor(EDIT_COLORS[span.kind][1]))
        painter.setPen(QPen(QColor(EDIT_COLORS['cut'][1]), 2))
        for span in self.spans:
            if span.kind == 'cut':
                x = x_at(span.start_ms)
                painter.drawLine(QPointF(x, track.top() - 2), QPointF(x, track.bottom() + 2))

        handle = self._slider_point(track)
        x = handle.x()
        painter.setPen(QPen(QColor(SURFACE), 2))
        painter.drawLine(QPointF(x, track.top() - 2), QPointF(x, track.bottom() + 2))
        self._feedback.paint(painter)
        radius = self.HANDLE_RADIUS - (1 if self.isSliderDown() else 0)
        knob = QRectF(x-radius, handle.y()-radius, 2*radius, 2*radius)
        painter.fillRect(knob.translated(2, 2), QColor(INK))
        painter.setPen(QPen(QColor(INK), 2))
        active = self.isEnabled() and (self.hasFocus() or self.underMouse() or self.isSliderDown())
        painter.setBrush(QColor(MUSTARD if active else SURFACE))
        painter.drawRect(knob)

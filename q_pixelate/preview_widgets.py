from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QSlider


EDIT_COLORS = {
    'blur': ('模糊', '#66d8b4'),
    'sticker': ('贴纸', '#ba93ef'),
    'mosaic': ('马赛克', '#76b6ff'),
    'speed': ('倍速', '#f4cb70'),
    'transition': ('过渡', '#f3a2d4'),
    'cut': ('删段拼接点', '#fb8288'),
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

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or not self.maximum():
            event.ignore()
            return
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self.setSliderDown(True)
        self._drag_to(event.position())
        event.accept()

    def mouseMoveEvent(self, event):
        if self.isSliderDown():
            self._drag_to(event.position())
            event.accept()

    def mouseReleaseEvent(self, event):
        if self.isSliderDown() and event.button() == Qt.MouseButton.LeftButton:
            self._drag_to(event.position())
            self.setSliderDown(False)
            event.accept()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = self._track()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor('#354359'))
        painter.drawRoundedRect(track, 2, 2)
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

        x = x_at(self.sliderPosition())
        painter.setPen(QPen(QColor('#e4f4fc'), 2))
        painter.drawLine(QPointF(x, track.top() - 2), QPointF(x, track.bottom() + 2))
        painter.setPen(QPen(QColor('#73dfbc' if self.hasFocus() else '#182333'), 2))
        painter.setBrush(QColor('#e4f4fc'))
        painter.drawEllipse(QPointF(x, track.center().y()), self.HANDLE_RADIUS, self.HANDLE_RADIUS)

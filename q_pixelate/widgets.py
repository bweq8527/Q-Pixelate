from __future__ import annotations

import math
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QValidator
from PySide6.QtWidgets import QDoubleSpinBox, QWidget

from .model import Rect


def time_text(seconds):
    milliseconds = max(0, round(seconds * 1000))
    h, remainder = divmod(milliseconds, 3600000)
    m, remainder = divmod(remainder, 60000)
    s, ms = divmod(remainder, 1000)
    return f'{h:02}:{m:02}:{s:02}.{ms:03}'


class TimeSpinBox(QDoubleSpinBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDecimals(3)
        self.setRange(0, 360000)
        self.setSingleStep(.1)
        self.setKeyboardTracking(False)
        self.setMinimumWidth(145)

    def textFromValue(self, value):
        return time_text(value)

    def valueFromText(self, text):
        try:
            parts = [float(part) for part in text.strip().split(':')]
            if not 1 <= len(parts) <= 3 or any(not math.isfinite(v) or v < 0 for v in parts):
                return self.value()
            value = 0
            for part in parts:
                value = value * 60 + part
            return value
        except ValueError:
            return self.value()

    def validate(self, text, pos):
        if all(c in '0123456789:.' for c in text) and text.count(':') <= 2 and text.count('.') <= 1:
            return QValidator.State.Acceptable, text, pos
        return QValidator.State.Invalid, text, pos


class VideoCanvas(QWidget):
    rectChanged = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 180)
        self.setMouseTracking(True)
        self.image = QImage()
        self.dimensions = (1920, 1080)
        self.auto_dimensions = False
        self.rect = Rect()
        self.editable = False
        self.fullscreen_mask = False
        self.show_mask = False
        self.effect_label = ''
        self.anchor = None
        self.drag_mode = None
        self.original_rect = None

    def set_dimensions(self, width, height):
        self.dimensions = width, height
        self.update()

    def set_frame(self, frame):
        image = frame.toImage()
        if not image.isNull():
            self.image = image
            if self.auto_dimensions:
                self.dimensions = image.width(), image.height()
            self.update()

    def video_rect(self):
        width, height = self.dimensions
        if not width or not height:
            return QRectF(self.rectGeometry())
        scale = min(self.width()/width, self.height()/height)
        w, h = width*scale, height*scale
        return QRectF((self.width()-w)/2, (self.height()-h)/2, w, h)

    def normalized_point(self, point):
        box = self.video_rect()
        return QPointF(max(0, min(1, (point.x()-box.x())/max(1, box.width()))),
                       max(0, min(1, (point.y()-box.y())/max(1, box.height()))))

    def mask_rect(self):
        box = self.video_rect()
        if self.fullscreen_mask:
            return box
        r = self.rect
        return QRectF(box.x()+r.x*box.width(), box.y()+r.y*box.height(), r.w*box.width(), r.h*box.height())

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rectGeometry(), QColor('#0a0c10'))
        box = self.video_rect()
        if not self.image.isNull():
            p.drawImage(box, self.image)
        else:
            p.setPen(QColor('#69768b'))
            p.drawText(self.rectGeometry(), Qt.AlignmentFlag.AlignCenter, '打开视频，开始局部修订\n\nMP4 / MKV · 本地处理')
        if self.show_mask:
            mask = self.mask_rect()
            p.fillRect(mask, QColor(94, 196, 175, 35))
            p.setPen(QPen(QColor('#6ee7c0'), 2, Qt.PenStyle.DashLine))
            p.drawRect(mask)
            if not self.fullscreen_mask:
                p.fillRect(QRectF(mask.bottomRight()-QPointF(5, 5), mask.bottomRight()+QPointF(5, 5)), QColor('#6ee7c0'))
            p.setPen(QColor('#b5f5df'))
            p.drawText(box.adjusted(12, 12, -12, -12), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
                       self.effect_label or '编辑区域 · 拖动画框，内部移动，右下角缩放')

    def rectGeometry(self):
        return QRectF(0, 0, self.width(), self.height()).toRect()

    def mousePressEvent(self, event):
        if not self.editable or event.button() != Qt.MouseButton.LeftButton or not self.video_rect().contains(event.position()):
            return
        self.anchor = self.normalized_point(event.position())
        self.original_rect = Rect(self.rect.x, self.rect.y, self.rect.w, self.rect.h)
        box = self.mask_rect()
        if (event.position()-box.bottomRight()).manhattanLength() < 18:
            self.drag_mode = 'resize'
        elif box.contains(event.position()):
            self.drag_mode = 'move'
        else:
            self.drag_mode = 'draw'

    def mouseMoveEvent(self, event):
        if self.anchor is None:
            return
        current = self.normalized_point(event.position())
        a, r = self.anchor, self.original_rect
        if self.drag_mode == 'move':
            self.rect = Rect(max(0, min(1-r.w, r.x+current.x()-a.x())),
                             max(0, min(1-r.h, r.y+current.y()-a.y())), r.w, r.h)
        elif self.drag_mode == 'resize':
            self.rect = Rect(r.x, r.y, max(.01, min(1-r.x, current.x()-r.x)),
                             max(.01, min(1-r.y, current.y()-r.y)))
        else:
            self.rect = Rect(min(a.x(), current.x()), min(a.y(), current.y()),
                             max(.001, abs(current.x()-a.x())), max(.001, abs(current.y()-a.y())))
        self.rectChanged.emit(self.rect)
        self.update()

    def mouseReleaseEvent(self, event):
        if self.anchor is not None:
            self.mouseMoveEvent(event)
        self.anchor = None
        self.drag_mode = None


class Timeline(QWidget):
    seekRequested = Signal(float)
    selectionChanged = Signal(float, float)
    viewChanged = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(116)
        self.duration = 0
        self.position = 0
        self.selection = (0, 0)
        self.edits = []
        self.zoom = 1
        self.view_start = 0
        self.drag = None
        self.anchor = 0
        self.setMouseTracking(True)

    @property
    def visible(self):
        return self.duration/max(1, self.zoom)

    def set_duration(self, duration):
        self.duration = duration
        self.view_start = 0
        self.update()

    def set_zoom(self, zoom):
        self.zoom = zoom
        self.view_start = max(0, min(self.duration-self.visible, self.position-self.visible/2))
        self.viewChanged.emit(self.view_start)
        self.update()

    def point_for_time(self, time):
        return QPoint(round(20+(time-self.view_start)/max(.001, self.visible)*(self.width()-40)), 76)

    def time_at(self, x):
        return max(0, min(self.duration, self.view_start+(x-20)/max(1, self.width()-40)*self.visible))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor('#131923'))
        p.fillRect(QRectF(20, 54, self.width()-40, 42), QColor('#202b3a'))
        if not self.duration:
            return
        steps = [0.1, .2, .5, 1, 2, 5, 10, 20, 30, 60, 120, 300, 600, 1800, 3600, 10800]
        target = self.visible/max(2, self.width()/100)
        step = next((s for s in steps if s >= target), steps[-1])
        t = math.ceil(self.view_start/step)*step
        p.setFont(QFont('Segoe UI', 9))
        while t <= self.view_start+self.visible:
            x = self.point_for_time(t).x()
            p.setPen(QColor('#526078'))
            p.drawLine(x, 39, x, 102)
            p.setPen(QColor('#99a8be'))
            p.drawText(x+4, 28, time_text(t).split('.')[0] if step >= 1 else time_text(t))
            t += step
        p.setClipRect(19, 42, self.width()-38, 62)
        for edit in self.edits:
            x1, x2 = self.point_for_time(edit.start).x(), self.point_for_time(edit.end).x()
            color = QColor('#296f69') if edit.effect != 'image' else QColor('#7460a0')
            p.fillRect(QRectF(x1, 58, max(2, x2-x1), 34), color)
        left, right = (self.point_for_time(t).x() for t in self.selection)
        p.fillRect(QRectF(left, 48, max(0, right-left), 49), QColor(110, 231, 192, 28))
        p.setPen(QPen(QColor('#6ee7c0'), 2))
        p.drawRect(QRectF(left, 48, max(0, right-left), 49))
        for x in (left, right):
            p.fillRect(QRectF(x-3, 48, 6, 49), QColor('#6ee7c0'))
        x = self.point_for_time(self.position).x()
        p.setPen(QPen(QColor('#ffb86b'), 2))
        p.drawLine(x, 43, x, 104)
        p.setClipping(False)

    def mousePressEvent(self, event):
        if not self.duration or event.button() != Qt.MouseButton.LeftButton:
            return
        self.anchor = self.time_at(event.position().x())
        left, right = (self.point_for_time(t).x() for t in self.selection)
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.drag = 'range'
        elif abs(event.position().x()-left) < 9:
            self.drag = 'left'
        elif abs(event.position().x()-right) < 9:
            self.drag = 'right'
        else:
            self.drag = 'seek'
            self.seekRequested.emit(self.anchor)

    def mouseMoveEvent(self, event):
        if not self.drag:
            return
        t = self.time_at(event.position().x())
        a, b = self.selection
        if self.drag == 'range':
            a, b = sorted((self.anchor, t))
        elif self.drag == 'left':
            a = min(t, b)
        elif self.drag == 'right':
            b = max(a, t)
        else:
            self.seekRequested.emit(t)
            return
        self.selection = a, b
        self.selectionChanged.emit(a, b)
        self.update()

    def mouseReleaseEvent(self, event):
        self.mouseMoveEvent(event)
        self.drag = None

    def wheelEvent(self, event):
        if self.duration:
            self.set_zoom(max(1, min(100, self.zoom*(1.3 if event.angleDelta().y()>0 else 1/1.3))))

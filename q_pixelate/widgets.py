from __future__ import annotations

import math
from dataclasses import replace
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QValidator
from PySide6.QtWidgets import QDoubleSpinBox, QWidget

from .gesture_feedback import GestureFeedback, tint
from .model import Mask, Rect, Transform
from .masks import layer_mask_path, mask_path, paint_sticker, transform_geometry
from .stickers import STICKER_MIME
from .palette import BLUR_TONE, BRICK, CANVAS, INK, MUSTARD, MUTED, PAPER, STICKER_TONE, SURFACE, TEAL_WASH


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
    pointsChanged = Signal(object)
    stickerDropped = Signal(str, object)
    layerPicked = Signal(int)
    transformChanged = Signal(object)
    maskDrawn = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 180)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.image = QImage()
        self.frame_time = None
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
        self.shape = 'rect'
        self.points = []
        self.draw_new = True
        self.setAcceptDrops(True)
        self.layers = []
        self.layer_source_time = 0
        self.detection_boxes = []
        self.layer_target = None
        self.free_transform = False
        self.layer_gesture = None
        self.mask_shape = None
        self.mask_stroke = []
        self.sticker_images = {}
        self._feedback = GestureFeedback(self)
        self._drop_target = False
        self._hover_canvas = False

    def redraw(self):
        self.draw_new = True

    def set_dimensions(self, width, height):
        self.dimensions = width, height
        self.update()

    def set_frame(self, frame):
        image = frame.toImage()
        if not image.isNull():
            self.image = image
            timestamp = frame.startTime()
            self.frame_time = timestamp / 1_000_000 if timestamp >= 0 else None
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
        p.fillRect(self.rectGeometry(), QColor(CANVAS))
        box = self.video_rect()
        if not self.image.isNull():
            p.drawImage(box, self.image)
        else:
            self.paint_empty_canvas(p)
        if self.show_mask:
            mask = self.mask_rect()
            path = mask_path('rect' if self.fullscreen_mask else self.shape,
                             Rect(0, 0, 1, 1) if self.fullscreen_mask else self.rect, self.points, box)
            p.fillPath(path, tint(MUSTARD, 50 if self.anchor is not None else 24))
            p.setPen(QPen(QColor(INK), 4))
            p.drawPath(path)
            p.setPen(QPen(QColor(MUSTARD), 2, Qt.PenStyle.DashLine))
            p.drawPath(path)
            if not self.fullscreen_mask and self.shape != 'lasso':
                handle = QRectF(mask.bottomRight()-QPointF(5, 5), mask.bottomRight()+QPointF(5, 5))
                p.fillRect(handle, QColor(INK))
                p.fillRect(handle.adjusted(2, 2, -2, -2), QColor(MUSTARD))
            self.paint_mask_label(p, box)
        self.paint_layers(p, box)
        if self._drop_target:
            p.setPen(QPen(QColor(INK), 6))
            p.drawRect(box.adjusted(3, 3, -3, -3))
            p.setPen(QPen(QColor(MUSTARD), 3))
            p.drawRect(box.adjusted(3, 3, -3, -3))
        self._feedback.paint(p)

    def paint_empty_canvas(self, painter):
        painter.save()
        center_y = self.height() / 2
        painter.fillRect(QRectF(self.width()/2-23, center_y-33, 46, 4), QColor(MUSTARD))
        heading = painter.font()
        heading.setPixelSize(18)
        heading.setBold(True)
        painter.setFont(heading)
        painter.setPen(QColor(SURFACE))
        painter.drawText(QRectF(8, center_y-22, self.width()-16, 28),
                         Qt.AlignmentFlag.AlignCenter, '打开视频，开始局部修订')
        caption = painter.font()
        caption.setPixelSize(12)
        caption.setBold(False)
        painter.setFont(caption)
        painter.setPen(QColor('#C3BFAE'))
        painter.drawText(QRectF(8, center_y+9, self.width()-16, 20),
                         Qt.AlignmentFlag.AlignCenter, 'MP4 / MKV · 本地处理')
        painter.restore()

    def paint_mask_label(self, painter, box):
        caption = self.effect_label or '编辑区域 · 拖动画框，内部移动，右下角缩放'
        metrics = painter.fontMetrics()
        caption = metrics.elidedText(caption, Qt.TextElideMode.ElideRight, max(0, int(box.width())-40))
        badge = QRectF(box.left()+12, box.top()+12, metrics.horizontalAdvance(caption)+16, metrics.height()+8)
        painter.fillRect(badge.translated(3, 3), QColor(INK))
        painter.fillRect(badge, QColor(MUSTARD))
        painter.setPen(QColor(INK))
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, caption)

    def paint_layers(self, painter, box):
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        for _, layer in self.layers:
            if layer.kind == 'sticker':
                if layer.image not in self.sticker_images:
                    self.sticker_images[layer.image] = QImage(layer.image)
                displayed = layer.sticker_at(self.layer_source_time-layer.start) if layer.motion else layer
                paint_sticker(painter, displayed, self.sticker_images[layer.image], box)
            elif layer.kind == 'mosaic':
                rect = layer.mosaic_rect_at(self.layer_source_time-layer.start)
                self.paint_detection_box(painter, box, rect)
            elif layer.kind == 'blur' and layer.motion:
                rect = layer.mosaic_rect_at(self.layer_source_time-layer.start)
                area = QRectF(box.x()+rect.x*box.width(), box.y()+rect.y*box.height(),
                              rect.w*box.width(), rect.h*box.height()).intersected(box)
                painter.fillRect(area, tint(MUSTARD, 30))
                painter.setPen(QPen(QColor(MUSTARD), 1, Qt.PenStyle.DashLine))
                painter.drawRect(area)
            elif layer.mask:
                painter.save()
                painter.setClipRect(box)
                path = layer_mask_path(layer.mask, box)
                painter.fillPath(path, tint(MUSTARD, 30))
                painter.setPen(QPen(QColor(MUSTARD), 1, Qt.PenStyle.DashLine))
                painter.drawPath(path)
                painter.restore()
        for rect in self.detection_boxes:
            self.paint_detection_box(painter, box, rect)
        if self.layer_target is not None and self.mask_shape is None:
            transform, mask = self.layer_target
            matrix, local = transform_geometry(transform, box)
            if mask:
                painter.setPen(QPen(QColor(MUSTARD), 1, Qt.PenStyle.DashLine))
                painter.drawPath(layer_mask_path(mask, box))
            painter.save()
            painter.setTransform(matrix, True)
            handle_color = MUSTARD if self.layer_gesture else BRICK
            painter.setPen(QPen(QColor(INK), 4))
            painter.drawRect(local)
            painter.setPen(QPen(QColor(handle_color), 2))
            painter.drawRect(local)
            if self.free_transform:
                for _, _, handle in self.handles(local):
                    grip = QRectF(handle.x()-4, handle.y()-4, 8, 8)
                    painter.fillRect(grip, QColor(INK))
                    painter.fillRect(grip.adjusted(1, 1, -1, -1), QColor(handle_color))
                knob = QPointF(0, local.top()-22)
                painter.drawLine(QPointF(0, local.top()), knob)
                painter.fillRect(QRectF(knob.x()-4, knob.y()-4, 8, 8), QColor(handle_color))
            painter.restore()
        if self.mask_stroke:
            points = [QPointF(box.x()+x*box.width(), box.y()+y*box.height()) for x, y in self.mask_stroke]
            painter.setPen(QPen(QColor(MUSTARD), 2, Qt.PenStyle.DashLine))
            if self.mask_shape == 'lasso':
                for a, b in zip(points, points[1:]):
                    painter.drawLine(a, b)
            elif len(points) == 2:
                area = QRectF(*points).normalized()
                if self.mask_shape == 'ellipse':
                    painter.drawEllipse(area)
                else:
                    painter.drawRect(area)

    @staticmethod
    def paint_detection_box(painter, box, rect):
        painter.save()
        painter.setClipRect(box)
        path = mask_path('rect', rect, [], box)
        painter.fillPath(path, tint(BRICK, 28))
        painter.setPen(QPen(QColor(BRICK), 2, Qt.PenStyle.DashLine))
        painter.drawPath(path)
        painter.restore()

    @staticmethod
    def handles(local):
        return [(x, y, QPointF(x*local.width()/2, y*local.height()/2))
                for x in (-1, 0, 1) for y in (-1, 0, 1) if x or y]

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(STICKER_MIME):
            self._set_drop_target(self.video_rect().contains(event.position()), event.position())
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        valid = event.mimeData().hasFormat(STICKER_MIME) and self.video_rect().contains(event.position())
        self._set_drop_target(valid, event.position())
        if valid:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self._set_drop_target(False)
        super().dragLeaveEvent(event)

    def _set_drop_target(self, active, position=None):
        if self._drop_target != active:
            self._drop_target = active
            if active:
                self._feedback.trigger(position)
            self.update()

    def dropEvent(self, event):
        self._set_drop_target(False)
        if not event.mimeData().hasFormat(STICKER_MIME) or not self.video_rect().contains(event.position()):
            return
        try:
            path = bytes(event.mimeData().data(STICKER_MIME)).decode('utf-8')
        except UnicodeDecodeError:
            return
        self._feedback.trigger(event.position())
        self.stickerDropped.emit(path, self.normalized_point(event.position()))
        event.acceptProposedAction()

    def layer_press(self, event):
        if event.button() != Qt.LeftButton:
            return False
        position = event.position()
        box = self.video_rect()
        if self.mask_shape is not None:
            if box.contains(position):
                self._feedback.trigger(position)
                point = self.normalized_point(position)
                self.mask_stroke = [(point.x(), point.y())]
                self.update()
            return True
        if self.layer_target is not None:
            transform, _ = self.layer_target
            matrix, local = transform_geometry(transform, box)
            inverse, _ = matrix.inverted()
            point = inverse.map(position)
            axes, mode = (0, 0), None
            if self.free_transform:
                closest = min(self.handles(local), key=lambda h: (point-h[2]).manhattanLength())
                if (point-closest[2]).manhattanLength() <= 11:
                    axes, mode = closest[:2], 'scale'
                elif (not local.contains(point)
                      and local.adjusted(-26, -30, 26, 26).contains(point)):
                    mode = 'rotate'
            if mode is None and local.contains(point):
                mode = 'move'
            if mode:
                self.layer_gesture = mode, axes, transform, matrix, local, point
                self._feedback.trigger(position)
                self.update()
                return True
        if not box.contains(position):
            return False
        for index, layer in reversed(self.layers):
            if layer.kind == 'sticker':
                displayed = layer.sticker_at(self.layer_source_time-layer.start) if layer.motion else layer
                matrix, local = transform_geometry(displayed.transform, box)
                inside = local.contains(matrix.inverted()[0].map(position))
            elif layer.kind == 'mosaic':
                inside = mask_path('rect', layer.mosaic_rect_at(self.layer_source_time-layer.start), [], box).contains(position)
            else:
                inside = bool(layer.mask and layer_mask_path(layer.mask, box).contains(position))
            if inside:
                self._feedback.trigger(position)
                self.layerPicked.emit(index)
                return True
        return False

    def update_layer_gesture(self, event):
        mode, axes, original, matrix, local, press = self.layer_gesture
        point = matrix.inverted()[0].map(event.position())
        box = self.video_rect()
        if mode == 'rotate':
            radians = math.atan2(point.y(), point.x())-math.atan2(press.y(), press.x())
            angle = (original.angle+math.degrees(radians)+180)%360-180
            self.transformChanged.emit(replace(original, angle=angle))
            return
        if mode == 'move':
            delta = event.position()-matrix.map(press)
            self.transformChanged.emit(replace(original, x=max(-4, min(5, original.x+delta.x()/box.width())),
                                              y=max(-4, min(5, original.y+delta.y()/box.height()))))
            return
        ax, ay = axes
        delta = point-press
        w = max(box.width()*.0001, min(box.width()*8, local.width()+ax*delta.x()))
        h = max(box.height()*.0001, min(box.height()*8, local.height()+ay*delta.y()))
        if event.modifiers() & Qt.ShiftModifier:
            ratios = [w/local.width()] if ax else []
            if ay:
                ratios.append(h/local.height())
            ratio = max(ratios, key=lambda value: abs(value-1))
            ratio = max(.0001/min(original.w, original.h), min(8/max(original.w, original.h), ratio))
            w, h = local.width()*ratio, local.height()*ratio
        center = matrix.map(QPointF(ax*(w-local.width())/2, ay*(h-local.height())/2))
        self.transformChanged.emit(Transform(max(-4, min(5, (center.x()-box.x())/box.width())),
                                             max(-4, min(5, (center.y()-box.y())/box.height())),
                                             w/box.width(), h/box.height(), original.angle))

    def update_mask_stroke(self, position):
        p = self.normalized_point(position)
        point = p.x(), p.y()
        if self.mask_shape == 'lasso':
            a, b = self.mask_stroke[-1]
            if abs(a-point[0])+abs(b-point[1]) >= .002:
                self.mask_stroke.append(point)
        else:
            self.mask_stroke[1:] = [point]
        self.update()

    def finish_mask_stroke(self):
        points, self.mask_stroke = self.mask_stroke, []
        xs, ys = zip(*points)
        w, h = max(xs)-min(xs), max(ys)-min(ys)
        if min(w, h) < .001:
            self.update()
            return
        rect = Rect(min(xs), min(ys), w, h)
        local = [((x-rect.x)/w, (y-rect.y)/h) for x, y in points] if self.mask_shape == 'lasso' else []
        mask = Mask(self.mask_shape, Transform.from_rect(rect), local)
        try:
            mask.validate()
        except ValueError:
            self.update()
            return
        self.mask_shape = None
        self.maskDrawn.emit(mask)
        self.update()

    def rectGeometry(self):
        return QRectF(0, 0, self.width(), self.height()).toRect()

    def mousePressEvent(self, event):
        if self.layer_press(event):
            return
        if not self.editable or event.button() != Qt.MouseButton.LeftButton or not self.video_rect().contains(event.position()):
            return
        self._feedback.trigger(event.position())
        self.anchor = self.normalized_point(event.position())
        if self.shape == 'lasso':
            self.points = [(self.anchor.x(), self.anchor.y())]
            self.drag_mode = 'lasso'
            self.update()
            return
        self.original_rect = Rect(self.rect.x, self.rect.y, self.rect.w, self.rect.h)
        box = self.mask_rect()
        if self.draw_new:
            self.drag_mode = 'draw'
        elif (event.position()-box.bottomRight()).manhattanLength() < 18:
            self.drag_mode = 'resize'
        elif box.contains(event.position()):
            self.drag_mode = 'move'
        else:
            self.drag_mode = 'draw'
        self.update()

    def mouseMoveEvent(self, event):
        if self.layer_gesture:
            self.update_layer_gesture(event)
            return
        if self.mask_stroke:
            self.update_mask_stroke(event.position())
            return
        if self.anchor is None:
            interactive = self.editable or self.mask_shape or self.layer_target or self.layers
            hovered = bool(interactive and self.video_rect().contains(event.position()))
            if hovered and not self._hover_canvas:
                self._feedback.trigger(event.position(), BRICK)
            self._hover_canvas = hovered
            return
        current = self.normalized_point(event.position())
        a, r = self.anchor, self.original_rect
        if self.drag_mode == 'lasso':
            point = (current.x(), current.y())
            if not self.points or abs(point[0]-self.points[-1][0])+abs(point[1]-self.points[-1][1]) > .003:
                self.points.append(point)
            self.update()
            return
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
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self.layer_gesture:
            self.update_layer_gesture(event)
            self.layer_gesture = None
            self._feedback.trigger(event.position())
            self.update()
            return
        if self.mask_stroke:
            self.update_mask_stroke(event.position())
            self.finish_mask_stroke()
            self._feedback.trigger(event.position())
            return
        if self.anchor is not None:
            self.mouseMoveEvent(event)
            if self.drag_mode == 'lasso' and len(self.points) >= 3:
                xs, ys = zip(*self.points)
                self.rect = Rect(min(xs), min(ys), max(.001, max(xs)-min(xs)), max(.001, max(ys)-min(ys)))
                finished_rect = self.rect
                self.pointsChanged.emit(list(self.points))
                self.rect = finished_rect
                self.rectChanged.emit(self.rect)
            self.draw_new = False
            self._feedback.trigger(event.position())
            self.update()
        self.anchor = None
        self.drag_mode = None

    def leaveEvent(self, event):
        self._hover_canvas = False
        super().leaveEvent(event)


class Timeline(QWidget):
    seekRequested = Signal(float)
    selectionChanged = Signal(float, float)
    viewChanged = Signal(float)
    editActivated = Signal(int)
    selectionModified = Signal(int, Qt.KeyboardModifier)
    editDragged = Signal(int, float, float, str)
    rangeCreated = Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(116)
        self.duration = 0
        self.position = 0
        self.selection = (0, 0)
        self.edits = []
        self.detection_spans = []
        self.zoom = 1
        self.view_start = 0
        self.drag = None
        self.anchor = 0
        self.selection_locked = False
        self.active_row = -1
        self.selected_rows = set()
        self.minimum_span = .001
        self.drag_bounds = None
        self.candidate = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self._feedback = GestureFeedback(self)
        self._hover_lane = None

    @property
    def visible(self):
        return self.duration/max(1, self.zoom)

    def set_duration(self, duration):
        self.duration = duration
        self.view_start = 0
        self.update()

    def set_zoom(self, zoom, *, anchor_x=None):
        anchor_time = self.position
        fraction = .5
        if anchor_x is not None:
            fraction = max(0, min(1, (anchor_x-20)/max(1, self.width()-40)))
            anchor_time = self.view_start+fraction*self.visible
        self.zoom = zoom
        self.set_view_start(anchor_time-fraction*self.visible)

    def point_for_time(self, time):
        return QPoint(round(20+(time-self.view_start)/max(.001, self.visible)*(self.width()-40)), 76)

    def time_at(self, x):
        return max(0, min(self.duration, self.view_start+(x-20)/max(1, self.width()-40)*self.visible))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(SURFACE))
        p.setPen(QPen(QColor(INK), 2))
        for lane, top, height in (('seek', 34, 18), ('edit', 60, 42)):
            highlighted = self._hover_lane == lane and self.duration
            if self.drag:
                highlighted = lane == ('seek' if self.drag in ('seek', 'pan') else 'edit')
            p.setBrush(QColor(TEAL_WASH if highlighted else PAPER))
            p.drawRect(QRectF(20, top, self.width()-40, height))
        if not self.duration:
            return
        steps = [0.1, .2, .5, 1, 2, 5, 10, 20, 30, 60, 120, 300, 600, 1800, 3600, 10800]
        target = self.visible/max(2, self.width()/100)
        step = next((s for s in steps if s >= target), steps[-1])
        t = math.ceil(self.view_start/step)*step
        p.setFont(QFont('Consolas', 9))
        while t <= self.view_start+self.visible:
            x = self.point_for_time(t).x()
            p.setPen(QColor(MUTED))
            p.drawLine(x, 39, x, 49)
            p.drawLine(x, 61, x, 101)
            caption = time_text(t).split('.')[0] if step >= 1 else time_text(t)
            if x+4+p.fontMetrics().horizontalAdvance(caption) <= self.width()-12:
                p.drawText(x+4, 25, caption)
            t += step
        p.setClipRect(19, 57, self.width()-38, 47)
        for row, edit in enumerate(self.edits):
            x1, x2 = self.point_for_time(edit.start).x(), self.point_for_time(edit.end).x()
            color = BRICK if edit.omit else '#AAA598' if edit.locked else (
                BLUR_TONE if edit.effect != 'image' else STICKER_TONE)
            block = QRectF(x1, 63, max(2, x2-x1), 29)
            p.fillRect(block, QColor(color))
            p.setPen(QPen(QColor(INK), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(block)
            if row in self.selected_rows and row != self.active_row:
                p.setPen(QPen(QColor(INK), 1, Qt.PenStyle.DashLine))
                p.drawRect(QRectF(x1, 60, max(2, x2-x1), 42))
        first, last = self.candidate or self.selection
        left, right = self.point_for_time(first).x(), self.point_for_time(last).x()
        if first < last or self.candidate:
            outline = QRectF(left, 60, max(2, right-left), 42)
            p.fillRect(outline, tint(MUSTARD, 104 if self.candidate else 50))
            p.setPen(QPen(QColor(INK), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(outline)
            if not self.selection_locked or self.candidate:
                for x in (left, right):
                    p.fillRect(QRectF(x-3, 60, 6, 42), QColor(INK))
                    p.fillRect(QRectF(x-1, 64, 2, 34), QColor(MUSTARD))
        for start, end in self.detection_spans:
            x1, x2 = self.point_for_time(start).x(), self.point_for_time(end).x()
            p.fillRect(QRectF(x1, 96, max(2, x2-x1), 4), QColor(BRICK))
        p.setClipping(False)
        p.setClipRect(15, 28, self.width()-30, 27)
        x = self.point_for_time(self.position).x()
        p.setPen(QPen(QColor(INK), 3))
        p.drawLine(x, 34, x, 52)
        p.setBrush(QColor(MUSTARD if self.drag == 'seek' else BRICK))
        p.drawRect(QRectF(x-4, 32, 8, 10))
        p.setClipping(False)
        p.setFont(self.font())
        p.setPen(QColor(MUTED))
        p.drawText(20, 113, '上轨：播放定位    下轨：右键拖出选区 · Ctrl 加选 · Shift 连选    中键 / Alt 拖动：平移')
        self._feedback.paint(p)

    def mousePressEvent(self, event):
        if not self.duration:
            return
        self.setFocus()
        button = event.button()
        x, y = event.position().x(), event.position().y()
        if button == Qt.MouseButton.MiddleButton or (button == Qt.MouseButton.LeftButton and event.modifiers() & Qt.AltModifier):
            self.drag = 'pan'
            self.drag_button = button
            self.pan_x = x
            self.pan_start = self.view_start
            self._feedback.trigger(event.position(), BRICK)
            self.update()
            return
        if not 19 <= x <= self.width()-19:
            return
        self.anchor = self.time_at(x)
        self.drag_button = button
        if 34 <= y <= 52 and button == Qt.MouseButton.LeftButton:
            self.drag = 'seek'
            self._feedback.trigger(event.position(), BRICK)
            self.update()
            self.seekRequested.emit(self.anchor)
            return
        if not 58 <= y <= 102:
            return
        if button == Qt.MouseButton.RightButton:
            self.drag = 'create'
            self.candidate = (self.anchor, self.anchor)
            self._feedback.trigger(event.position())
            self.update()
            return
        if button != Qt.MouseButton.LeftButton:
            return
        if not self.edits and not self.selection_locked and event.modifiers() & Qt.ShiftModifier:
            self.drag = 'range'
            self._feedback.trigger(event.position())
            self.update()
            return
        ranges = [(e.start, e.end) for e in self.edits] or [self.selection]
        edges, interiors = [], []
        for index, (start, end) in enumerate(ranges):
            if start >= end:
                continue
            left, right = (self.point_for_time(t).x() for t in (start, end))
            # Tolerance shrinks on short clips so they still have a move area.
            tolerance = min(8, max(1, (right-left)*.23))
            for edge, mode in ((left, 'left'), (right, 'right')):
                if abs(x-edge) <= tolerance:
                    edges.append((abs(x-edge), index != self.active_row, index, mode))
            if left <= x <= right:
                interiors.append(index)
        if edges:
            _, _, index, mode = min(edges)
        elif interiors:
            index, mode = interiors[0], 'move'
        else:
            return
        a, b = ranges[index]
        lo, hi = 0, self.duration
        self._feedback.trigger(event.position())
        if self.edits:
            modifiers = event.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier)
            if modifiers:
                self.selectionModified.emit(index, modifiers)
                return
            self.editActivated.emit(index)
            edit = self.edits[index]
            if self.active_row != index or edit.locked or edit.range_locked:
                return
            lo = max((e.end for e in self.edits if e is not edit and e.end <= a+1e-8), default=0)
            hi = min((e.start for e in self.edits if e is not edit and e.start >= b-1e-8), default=self.duration)
        elif self.selection_locked:
            return
        self.drag = mode
        self.drag_row = index if self.edits else -1
        self.drag_bounds = (a, b, lo, hi)
        self.update()

    def mouseMoveEvent(self, event):
        if not self.drag:
            lane = self._lane_at(event.position())
            if self._hover_lane != lane:
                self._hover_lane = lane
                if lane:
                    self._feedback.trigger(event.position(), BRICK)
                self.update()
            return
        if self.drag == 'pan':
            self.set_view_start(self.pan_start + (self.pan_x-event.position().x())/max(1, self.width()-40)*self.visible)
            return
        t = self.time_at(event.position().x())
        if self.drag == 'range':
            self.selection = tuple(sorted((self.anchor, t)))
            self.selectionChanged.emit(*self.selection)
        elif self.drag == 'seek':
            self.seekRequested.emit(t)
        elif self.drag == 'create':
            self.candidate = tuple(sorted((self.anchor, t)))
        else:
            a, b, lo, hi = self.drag_bounds
            delta = t-self.anchor
            minimum = min(self.minimum_span, b-a)
            if self.drag == 'left':
                a = max(lo, min(a+delta, b-minimum))
            elif self.drag == 'right':
                b = min(hi, max(b+delta, a+minimum))
            else:
                delta = max(lo-a, min(delta, hi-b))
                a, b = a+delta, b+delta
            self.candidate = (a, b)
        self.update()

    def leaveEvent(self, event):
        self._hover_lane = None
        self.update()
        super().leaveEvent(event)

    def _lane_at(self, position):
        x, y = position.x(), position.y()
        if self.duration and 19 <= x <= self.width()-19:
            if 34 <= y <= 52:
                return 'seek'
            if 58 <= y <= 102:
                return 'edit'
        return None

    def mouseReleaseEvent(self, event):
        if not self.drag or event.button() != self.drag_button:
            return
        self.mouseMoveEvent(event)
        mode, candidate = self.drag, self.candidate
        self.drag = None
        self.candidate = None
        self._hover_lane = self._lane_at(event.position())
        self._feedback.trigger(event.position(), BRICK if mode in ('seek', 'pan') else MUSTARD)
        if mode == 'create':
            self.rangeCreated.emit(*candidate)
        elif mode in ('left', 'right', 'move') and candidate != self.drag_bounds[:2]:
            if self.drag_row < 0:
                self.selection = candidate
                self.selectionChanged.emit(*candidate)
            else:
                self.editDragged.emit(self.drag_row, *candidate, mode)
        self.update()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if self.duration and delta:
            zoom = self.zoom*(1.3 if delta > 0 else 1/1.3)
            self.set_zoom(max(1, min(100, zoom)), anchor_x=event.position().x())
            self._feedback.trigger(event.position(), BRICK)

    def set_view_start(self, value):
        self.view_start = max(0, min(self.duration-self.visible, value))
        self.viewChanged.emit(self.view_start)
        self.update()

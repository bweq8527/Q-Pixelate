from PySide6.QtCore import QMimeData, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QDrag, QIcon, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget,
)

from .gesture_feedback import GestureFeedback, tint
from .model import AUDIO_TRANSITIONS, VIDEO_TRANSITIONS
from .parameter_widgets import ParamControl, WheelSafeComboBox
from .stickers import STICKER_MIME
from .palette import (
    BLUR_TONE, BRICK, BRICK_WASH, INK, MOSAIC_TONE, MUSTARD, MUTED, PAPER, SPEED_TONE,
    STICKER_TONE, SURFACE, TEAL_WASH,
)


OMIT_LAYER = '@omit'
SPEED_LAYER = '@speed'


def effect_rows(edit):
    if not edit:
        return []
    rows = [(layer.id, layer.name or {'sticker': '贴纸', 'mosaic': '马赛克', 'blur': '模糊'}[layer.kind])
            for layer in edit.layers or ()]
    if edit.omit:
        rows.append((OMIT_LAYER, '直接删除段落'))
    if edit.speed_effect or edit.speed != 1:
        rows.append((SPEED_LAYER, f'倍速段落 · {edit.speed:g}×'))
    return rows


class StickerList(QListWidget):
    asset_role = Qt.ItemDataRole.UserRole

    def __init__(self):
        super().__init__()
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setIconSize(QSize(42, 42))
        self.setGridSize(QSize(92, 66))
        self.setMinimumHeight(92)
        self.setToolTip('将缩略图拖到视频画面，或用于当前遮罩')

    def add_asset(self, path, name):
        item = QListWidgetItem(QIcon(path), name)
        item.setToolTip(name)
        item.setData(self.asset_role, path)
        self.addItem(item)

    def find_asset(self, path):
        return next((self.item(i) for i in range(self.count()) if self.item(i).data(self.asset_role) == path), None)

    def startDrag(self, actions):
        item = self.currentItem()
        if item is None:
            return
        mime = QMimeData()
        mime.setData(STICKER_MIME, item.data(self.asset_role).encode('utf-8'))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(item.icon().pixmap(54, 54))
        drag.exec(Qt.DropAction.CopyAction)


class LayerPicker(QListWidget):
    def __init__(self):
        super().__init__()
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setMinimumHeight(92)
        self.setMaximumHeight(112)
        self.setToolTip('单击选择 · Ctrl 加选 · Shift 连选')

class LayerInspector(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.picker = LayerPicker()
        layout.addWidget(self.picker)
        self.shape = WheelSafeComboBox()
        for label, value in [('方框', 'rect'), ('椭圆', 'ellipse'), ('套索', 'lasso')]:
            self.shape.addItem(label, value)
        self.redraw = QPushButton('绘制 / 重绘本层遮罩')
        row = QHBoxLayout()
        row.addWidget(self.redraw, 1)
        row.addWidget(self.shape)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.track_end = QPushButton('查看追踪结束')
        row.addWidget(self.track_end)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.lower = QPushButton('下移')
        self.raise_ = QPushButton('上移')
        self.delete = QPushButton('删除图层')
        for widget in (self.lower, self.raise_, self.delete):
            row.addWidget(widget)
        layout.addLayout(row)
        self.details = QWidget()
        form = self.form = QFormLayout(self.details)
        form.setContentsMargins(0, 0, 0, 0)
        self.name = QLineEdit()
        form.addRow('图层名称', self.name)
        self.start, self.end = ParamControl(time=True), ParamControl(time=True)
        form.addRow('图层入点', self.start)
        form.addRow('图层出点', self.end)
        self.kind = WheelSafeComboBox()
        self.kind.addItem('模糊', 'blur')
        self.kind.addItem('贴纸', 'sticker')
        self.kind.addItem('马赛克', 'mosaic')
        form.addRow('遮罩用途', self.kind)
        self.asset = QPushButton('使用素材库选中的贴纸')
        form.addRow(self.asset)
        self.target = WheelSafeComboBox()
        self.target.addItem('贴纸', 'sticker')
        self.target.addItem('遮罩', 'mask')
        form.addRow('变换对象', self.target)
        self.remove_mask = QPushButton('移除贴纸遮罩')
        form.addRow(self.remove_mask)
        self.strength = self.spin(1, 100, 0)
        self.feather = self.spin(0, 20)
        self.feather.setSuffix(' %')
        form.addRow('模糊强度', self.strength)
        self.strength_label = form.labelForField(self.strength)
        form.addRow('向内羽化', self.feather)
        self.geometry = []
        for name, lo, hi in [('中心 X %', -400, 500), ('中心 Y %', -400, 500),
                             ('宽度 %', .01, 800), ('高度 %', .01, 800), ('角度 °', -180, 180)]:
            spin = self.spin(lo, hi)
            form.addRow(name, spin)
            self.geometry.append(spin)
        self.transform = QPushButton('自由变换 Ctrl+T')
        form.addRow(self.transform)
        layout.addWidget(self.details)

        self.paragraph = QWidget()
        paragraph_form = QFormLayout(self.paragraph)
        paragraph_form.setContentsMargins(0, 0, 0, 0)
        self.speed = self.spin(.25, 4)
        self.speed.setSuffix(' ×')
        paragraph_form.addRow('片段速度', self.speed)
        self.paragraph_form = paragraph_form
        self.video_transition = WheelSafeComboBox()
        self.audio_transition = WheelSafeComboBox()
        for combo, options in ((self.video_transition, VIDEO_TRANSITIONS),
                               (self.audio_transition, AUDIO_TRANSITIONS)):
            for kind, caption in options.items():
                combo.addItem(caption, kind)
        self.transition_duration = self.spin(.05, 1)
        self.transition_duration.setSuffix(' 秒')
        self.transition_duration.setToolTip('接缝前后各使用该时长，保留片段较短时会自动缩短。')
        paragraph_form.addRow('视频过渡', self.video_transition)
        paragraph_form.addRow('音频过渡', self.audio_transition)
        paragraph_form.addRow('单侧过渡时长', self.transition_duration)
        self.paragraph_note = QLabel()
        self.paragraph_note.setWordWrap(True)
        paragraph_form.addRow(self.paragraph_note)
        layout.addWidget(self.paragraph)

    @staticmethod
    def spin(lo, hi, decimals=2):
        spin = ParamControl(decimals=decimals)
        spin.setRange(lo, hi)
        return spin


class LayerTimeline(QWidget):
    activated = Signal(int)
    selectionModified = Signal(int, object)
    dragged = Signal(int, float, float)
    row_height = 24

    def __init__(self, timeline):
        super().__init__()
        self.timeline = timeline
        self.edit = None
        self.selected = set()
        self.drag = None
        self.candidate = None
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.setToolTip('拖动图层内部移动时间，拖动两端调整时长')
        self._feedback = GestureFeedback(self)
        self._hover_row = -1

    def set_edit(self, edit, selected):
        self.edit, self.selected = edit, selected
        self.setMinimumHeight(max(1, len(effect_rows(edit)))*self.row_height)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(SURFACE))
        rows = effect_rows(self.edit)
        if not rows:
            painter.setPen(QColor(MUTED))
            painter.drawText(20, 17, '图层轨 · 将贴纸拖入画面，或绘制遮罩')
            return
        for index in range(max(0, event.rect().top()//self.row_height),
                           min(len(rows), event.rect().bottom()//self.row_height+1)):
            layer = self.edit.layers[index] if index < len(self.edit.layers) else None
            layer_id, title = rows[index]
            selected = index in self.selected
            dragging = self.drag and self.drag[0] == index
            y = index*self.row_height
            area = QRectF(20, y+2, self.width()-39, self.row_height-3)
            highlighted = selected or index == self._hover_row
            painter.fillRect(area, QColor(TEAL_WASH if highlighted else PAPER))
            painter.setPen(QPen(QColor(INK), 1))
            painter.drawRect(area)
            painter.save()
            painter.setClipRect(area)
            if dragging:
                start, end = self.candidate
            elif layer:
                start, end = self.edit.layer_span(layer)
            else:
                start, end = self.edit.start, self.edit.end
            if start < end:
                left, right = (self.timeline.point_for_time(t).x() for t in (start, end))
                kind = layer.kind if layer else layer_id
                color = {'sticker': STICKER_TONE, 'mosaic': MOSAIC_TONE, OMIT_LAYER: BRICK_WASH,
                         SPEED_LAYER: SPEED_TONE}.get(kind, BLUR_TONE)
                segment = QRectF(left, y+3, max(2, right-left), self.row_height-5)
                painter.fillRect(segment, QColor(MUSTARD if dragging else color))
                painter.setPen(QPen(QColor(INK), 2 if selected else 1))
                painter.drawRect(segment)
                if selected and layer and not self.edit.locked and right-left > 12:
                    for x in (left, right-3):
                        painter.fillRect(QRectF(x, y+5, 3, self.row_height-9), QColor(INK))
            painter.setPen(QColor(INK if start < end else MUTED))
            painter.drawText(26, y+17, f'{index+1:02}  {title}' + (' · 范围外' if start >= end else ''))
            painter.restore()
        x = self.timeline.point_for_time(self.timeline.position).x()
        if 20 <= x <= self.width()-19:
            painter.setPen(QPen(tint(INK, 170), 1, Qt.PenStyle.DotLine))
            painter.drawLine(x, 0, x, len(rows)*self.row_height)
            painter.fillRect(QRectF(x-2, 0, 4, 4), QColor(BRICK))
        self._feedback.paint(painter)

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton or not self.edit:
            return
        index = int(event.position().y())//self.row_height
        if not 0 <= index < len(effect_rows(self.edit)):
            return
        self.setFocus()
        self._feedback.trigger(event.position())
        modifiers = event.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier)
        if modifiers:
            self.selectionModified.emit(index, modifiers)
            return
        self.activated.emit(index)
        if self.edit.locked or index >= len(self.edit.layers):
            return
        a, b = self.edit.layer_span(self.edit.layers[index])
        if a >= b:
            return
        x = event.position().x()
        left, right = (self.timeline.point_for_time(t).x() for t in (a, b))
        tolerance = min(7, max(1, (right-left)*.23))
        if abs(x-left) <= tolerance:
            mode = 'left'
        elif abs(x-right) <= tolerance:
            mode = 'right'
        elif left < x < right:
            mode = 'move'
        else:
            return
        self.drag = (index, mode, self.timeline.time_at(x), a, b)
        self.candidate = (a, b)
        self.update()

    def mouseMoveEvent(self, event):
        if not self.drag:
            index = self._row_at(event.position())
            if self._hover_row != index:
                self._hover_row = index
                if index >= 0:
                    self._feedback.trigger(event.position(), BRICK)
                self.update()
            return
        _, mode, anchor, a, b = self.drag
        delta = self.timeline.time_at(event.position().x())-anchor
        lo, hi = self.edit.start, self.edit.end
        minimum = min(b-a, self.timeline.minimum_span)
        if mode == 'left':
            a = max(lo, min(b-minimum, a+delta))
        elif mode == 'right':
            b = min(hi, max(a+minimum, b+delta))
        else:
            delta = max(lo-a, min(hi-b, delta))
            a, b = a+delta, b+delta
        self.candidate = a, b
        self.update()

    def leaveEvent(self, event):
        self._hover_row = -1
        self.update()
        super().leaveEvent(event)

    def _row_at(self, position):
        index = int(position.y())//self.row_height
        if self.rect().contains(position.toPoint()) and 0 <= index < len(effect_rows(self.edit)):
            return index
        return -1

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton or not self.drag:
            return
        self.mouseMoveEvent(event)
        index, _, _, a, b = self.drag
        candidate = self.candidate
        self.drag = self.candidate = None
        self._hover_row = self._row_at(event.position())
        self._feedback.trigger(event.position())
        if candidate != (a, b):
            self.dragged.emit(index, *candidate)
        self.update()

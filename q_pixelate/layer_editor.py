"""Layer editing operations used by the main window."""
import math
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QSignalBlocker
from PySide6.QtGui import QImage

from .model import Layer, Transform, frame_bounds


class LayerEditing:
    def connect_layer_editor(self):
        panel = self.layer_panel
        panel.picker.currentIndexChanged.connect(self.select_layer)
        panel.draw.clicked.connect(lambda: self.begin_mask(False))
        panel.redraw.clicked.connect(lambda: self.begin_mask(True))
        panel.full_blur.clicked.connect(self.add_full_blur)
        panel.raise_.clicked.connect(lambda: self.reorder_layer(1))
        panel.lower.clicked.connect(lambda: self.reorder_layer(-1))
        panel.delete.clicked.connect(self.delete_layer)
        panel.name.editingFinished.connect(lambda: self.update_layer(name=panel.name.text().strip()))
        panel.start.valueChanged.connect(self.set_layer_times)
        panel.end.valueChanged.connect(self.set_layer_times)
        panel.kind.activated.connect(self.set_layer_kind)
        panel.asset.clicked.connect(self.use_library_asset)
        panel.target.currentIndexChanged.connect(self.change_layer_target)
        panel.remove_mask.clicked.connect(self.remove_layer_mask)
        panel.strength.valueChanged.connect(lambda value: self.update_layer(strength=int(value)))
        panel.feather.valueChanged.connect(self.set_layer_feather)
        for spin in panel.geometry:
            spin.valueChanged.connect(self.geometry_changed)
        panel.transform.clicked.connect(self.begin_transform)
        self.layer_timeline.activated.connect(self.select_layer)
        self.layer_timeline.dragged.connect(self.set_layer_span)
        self.canvas.stickerDropped.connect(self.drop_sticker)
        self.canvas.layerPicked.connect(self.pick_canvas_layer)
        self.canvas.maskDrawn.connect(self.finish_mask)
        self.canvas.transformChanged.connect(self.set_layer_transform)

    def layer_edit(self):
        if self.project and 0 <= self._editing_row < len(self.project.edits):
            edit = self.current_edit()
            return edit if edit.layers is not None else None

    def selected_layer(self, edit):
        if edit:
            return next((layer for layer in edit.layers if layer.id == self._layer_id), None)

    def sync_layer_editor(self):
        edit = self.layer_edit()
        layer = self.selected_layer(edit)
        selected = edit.layers.index(layer) if layer else -1
        panel = self.layer_panel
        panel.setVisible(bool(edit))
        self.layer_empty.setVisible(not edit)
        self.layer_timeline.set_edit(edit, selected)
        if not edit:
            return
        editable = not edit.locked
        widgets = (panel.picker, panel.name, panel.kind, panel.target, panel.start, panel.end,
                   panel.strength, panel.feather, *panel.geometry)
        blockers = [QSignalBlocker(widget) for widget in widgets]
        captions = [f'{index+1:02}  {item.name or ("贴纸" if item.kind == "sticker" else "模糊")}'
                    for index, item in enumerate(edit.layers)]
        if [panel.picker.itemText(i) for i in range(panel.picker.count())] != captions:
            panel.picker.clear()
            panel.picker.addItems(captions)
        panel.picker.setCurrentIndex(selected)
        panel.draw.setEnabled(editable)
        panel.shape.setEnabled(editable)
        panel.full_blur.setEnabled(editable)
        panel.lower.setEnabled(editable and selected > 0)
        panel.raise_.setEnabled(editable and 0 <= selected < len(edit.layers)-1)
        panel.delete.setEnabled(editable and layer is not None)
        panel.details.setVisible(layer is not None)
        panel.details.setEnabled(editable)
        panel.redraw.setEnabled(editable and bool(layer))
        if not layer:
            return
        panel.name.setText(layer.name)
        start, end = edit.layer_span(layer)
        panel.start.setRange(edit.start, edit.end)
        panel.end.setRange(edit.start, edit.end)
        panel.start.setValue(start if start < end else edit.start)
        panel.end.setValue(end if start < end else edit.end)
        panel.start.setToolTip('图层超出选区，修改时间可恢复显示' if start >= end else '')
        panel.kind.setCurrentIndex(0 if layer.kind == 'blur' else 1)
        panel.kind.setEnabled(layer.mask is not None)
        sticker = layer.kind == 'sticker'
        panel.target.setEnabled(sticker and layer.mask is not None)
        panel.target.setCurrentIndex(1 if self._layer_target == 'mask' else 0)
        panel.remove_mask.setEnabled(sticker and layer.mask is not None)
        panel.asset.setEnabled(sticker or layer.mask is not None)
        panel.strength.setEnabled(not sticker)
        panel.strength.setValue(layer.strength)
        panel.feather.setEnabled(layer.mask is not None)
        panel.feather.setValue(layer.mask.feather if layer.mask else 0)
        transform = self.target_transform(layer)
        panel.transform.setEnabled(transform is not None)
        for spin in panel.geometry:
            spin.setEnabled(transform is not None)
        if transform:
            for spin, value in zip(panel.geometry, (transform.x*100, transform.y*100, transform.w*100,
                                                    transform.h*100, (transform.angle+180)%360-180)):
                spin.setValue(value)
        del blockers

    def target_transform(self, layer):
        if layer is None:
            return None
        if self._layer_target == 'mask' or layer.kind == 'blur':
            return layer.mask.transform if layer.mask else None
        return layer.transform

    def cancel_layer_interaction(self):
        self._transform_before = None
        self.canvas.free_transform = False
        self.canvas.layer_gesture = None
        self._mask_context = None
        self.canvas.mask_shape = None
        self.canvas.mask_stroke = []

    def select_layer(self, index):
        edit = self.layer_edit()
        if not edit or not 0 <= index < len(edit.layers):
            return
        layer = edit.layers[index]
        if layer.id != self._layer_id:
            self.cancel_layer_interaction()
            self._layer_id = layer.id
            self._layer_target = 'mask' if layer.kind == 'blur' else 'sticker'
        self.sync_layer_editor()
        self.update_canvas_mask()

    def pick_canvas_layer(self, index):
        if self._canvas_layer_row >= 0:
            self.activate_from_timeline(self._canvas_layer_row)
            self.select_layer(index)

    def store_layer_edit(self, edit):
        row = self._editing_row
        if edit != self.project.edits[row]:
            self._drafts[row] = edit
        else:
            self._drafts.pop(row, None)
        self.timeline.edits[row] = edit
        self.clip_list.item(row).setText(self.edit_caption(row, edit))
        self.timeline.update()
        self.changed()
        self.sync_layer_editor()
        self.update_canvas_mask()

    def update_layer(self, **kwargs):
        edit = self.layer_edit()
        layer = self.selected_layer(edit)
        if not layer or edit.locked:
            return
        updated = replace(layer, **kwargs)
        if updated != layer:
            self.store_layer_edit(replace(edit, layers=[updated if item is layer else item for item in edit.layers]))

    def add_layer(self, edit, layer, target):
        self.cancel_layer_interaction()
        self._layer_id, self._layer_target = layer.id, target
        self.store_layer_edit(replace(edit, layers=[*edit.layers, layer]))

    def promote_selected_edit(self):
        edit = self.layer_edit()
        if edit is None and self._editing_row >= 0 and not self.selected_locked():
            edit = self.current_edit()
            edit.promote_layers(self.info.width, self.info.height)
        return edit

    def drop_sticker(self, path, point):
        if not self.project or self.worker or self.sticker_library_list.find_asset(path) is None:
            return
        seconds = self.timeline.position
        self.stash_edit()
        row = next((i for i, stored in enumerate(self.project.edits)
                    if (edit := self._drafts.get(i, stored)).start <= seconds < edit.end), -1)
        if row < 0:
            self.status.setText('先创建选区，并把播放位置移到选区内，再拖入贴纸。')
            return
        if self.project.edits[row].locked:
            self.status.setText('播放位置所在的选区已锁定，请先解锁。')
            return
        image = QImage(path)
        if image.isNull():
            self.status.setText('无法读取贴纸，请重新导入图片。')
            return
        self.activate_from_timeline(row)
        edit = self.promote_selected_edit()
        video_width, video_height = self.canvas.dimensions
        ratio = image.width()/image.height()/(video_width/video_height)
        transform = Transform(point.x(), point.y(), .28*min(1, ratio), .28*min(1, 1/ratio))
        start = self.default_layer_start(edit)
        layer = Layer(start, min(start+2, edit.end-edit.start), 'sticker',
                      Path(path).stem.rsplit('-', 1)[0], path, transform)
        first, last = frame_bounds(replace(layer, start=edit.start+layer.start, end=edit.start+layer.end),
                                   self.info.fps_float)
        if first >= last:
            self.status.setText('当前选区没有可放置贴纸的视频帧，请调整选区边界。')
            return
        self.add_layer(edit, layer, 'sticker')
        self.update_edit_controls()
        self.begin_transform()

    def default_layer_start(self, edit):
        seconds = self.timeline.position
        if edit.start <= seconds < edit.end:
            fps = self.info.fps_float
            frame_time = math.floor(seconds*fps+1e-6)/fps
            return max(0, frame_time-edit.start)
        return 0

    def add_full_blur(self):
        edit = self.layer_edit()
        if edit and not edit.locked:
            self.add_layer(edit, Layer(self.default_layer_start(edit), edit.end-edit.start, name='全屏模糊'), 'mask')

    def begin_mask(self, redraw):
        edit = self.layer_edit()
        if not edit or edit.locked:
            return
        self.player.pause()
        self.cancel_layer_interaction()
        self._mask_context = self._editing_row, self._layer_id if redraw else None
        self.canvas.mask_shape = self.layer_panel.shape.currentData()
        self.canvas.setFocus()
        self.status.setText('在视频上按住左键绘制遮罩，Esc 取消。')
        self.update_canvas_mask()

    def finish_mask(self, mask):
        context, self._mask_context = self._mask_context, None
        edit = self.layer_edit()
        if not context or not edit or edit.locked or context[0] != self._editing_row:
            return
        if context[1]:
            layer = self.selected_layer(edit)
            if layer and layer.id == context[1]:
                self._layer_target = 'mask'
                self.update_layer(mask=replace(mask, feather=layer.mask.feather if layer.mask else 0))
        else:
            self.add_layer(edit, Layer(self.default_layer_start(edit), edit.end-edit.start,
                                       name='区域遮罩', mask=mask), 'mask')
        self.status.setText('遮罩已绘制。可保持模糊，或选中素材并将遮罩用于贴纸。')

    def use_library_asset(self):
        item = self.sticker_library_list.currentItem()
        edit = self.layer_edit()
        layer = self.selected_layer(edit)
        if not layer or edit.locked:
            return
        if not item:
            self.status.setText('请先在素材库中点选一张贴纸。')
            return
        self.set_sticker_asset(item.data(self.sticker_library_list.asset_role), layer)

    def set_sticker_asset(self, path, layer):
        self.cancel_layer_interaction()
        self._layer_target = 'sticker'
        transform = layer.mask.transform if layer.kind == 'blur' and not layer.image and layer.mask else layer.transform
        self.update_layer(kind='sticker', image=path, transform=replace(transform))

    def set_layer_kind(self, _):
        edit = self.layer_edit()
        layer = self.selected_layer(edit)
        if not layer or edit.locked:
            return
        kind = self.layer_panel.kind.currentData()
        if kind == layer.kind:
            return
        if kind == 'sticker':
            if layer.image:
                self.set_sticker_asset(layer.image, layer)
            else:
                self.use_library_asset()
                self.sync_layer_editor()
        else:
            self.cancel_layer_interaction()
            self._layer_target = 'mask'
            self.update_layer(kind='blur')

    def change_layer_target(self, _):
        self.cancel_layer_interaction()
        self._layer_target = self.layer_panel.target.currentData()
        self.sync_layer_editor()
        self.update_canvas_mask()

    def remove_layer_mask(self):
        self.cancel_layer_interaction()
        self._layer_target = 'sticker'
        self.update_layer(mask=None)

    def set_layer_feather(self, value):
        layer = self.selected_layer(self.layer_edit())
        if layer and layer.mask:
            self.update_layer(mask=replace(layer.mask, feather=value))

    def begin_transform(self):
        edit = self.layer_edit()
        transform = self.target_transform(self.selected_layer(edit))
        if not transform or edit.locked:
            return False
        if self.canvas.free_transform:
            self.canvas.setFocus()
            return True
        self.player.pause()
        self.cancel_layer_interaction()
        self._transform_before = transform
        self.canvas.free_transform = True
        self.canvas.setFocus()
        self.status.setText('框内移动，控制点缩放，框外旋转。按住 Shift 等比缩放，Enter 提交，Esc 撤销变换。')
        self.update_canvas_mask()
        return True

    def finish_transform(self, cancel):
        if cancel and self._transform_before:
            self.set_layer_transform(self._transform_before)
        self.cancel_layer_interaction()
        self.update_canvas_mask()
        self.status.setText('已取消变换。' if cancel else '变换已应用到图层。')

    def geometry_changed(self, _):
        x, y, w, h, angle = (spin.value() for spin in self.layer_panel.geometry)
        self.set_layer_transform(Transform(x/100, y/100, w/100, h/100, angle))

    def set_layer_transform(self, transform):
        layer = self.selected_layer(self.layer_edit())
        if not layer:
            return
        if self._layer_target == 'mask' or layer.kind == 'blur':
            if layer.mask:
                self.update_layer(mask=replace(layer.mask, transform=transform))
        else:
            self.update_layer(transform=transform)

    def set_layer_times(self, _):
        edit = self.layer_edit()
        layer = self.selected_layer(edit)
        if layer:
            self.set_layer_span(edit.layers.index(layer), self.layer_panel.start.value(), self.layer_panel.end.value())

    def set_layer_span(self, index, start, end):
        edit = self.layer_edit()
        if not edit or edit.locked or not 0 <= index < len(edit.layers):
            return
        layer = edit.layers[index]
        start, end = max(edit.start, start), min(end, edit.end)
        # Time controls display millisecond precision. Preserve sub-ms source
        # boundaries if the user merely focuses and leaves an unchanged field.
        a, b = edit.layer_span(layer)
        if abs(start-a) <= .0005 and abs(end-b) <= .0005:
            return
        if end-start < min(self.timeline.minimum_span, layer.end-layer.start)-.0005:
            self.status.setText('请选择至少一帧的有效图层时间。')
            self.sync_layer_editor()
            return
        self.select_layer(index)
        self.update_layer(start=start-edit.start, end=end-edit.start)

    def reorder_layer(self, offset):
        edit = self.layer_edit()
        layer = self.selected_layer(edit)
        if not layer or edit.locked:
            return
        layers = list(edit.layers)
        old = layers.index(layer)
        new = old+offset
        if 0 <= new < len(layers):
            layers[old], layers[new] = layers[new], layers[old]
            self.store_layer_edit(replace(edit, layers=layers))

    def delete_layer(self):
        edit = self.layer_edit()
        layer = self.selected_layer(edit)
        if not layer or edit.locked:
            return
        self.cancel_layer_interaction()
        layers = [item for item in edit.layers if item is not layer]
        next_layer = layers[min(edit.layers.index(layer), len(layers)-1)] if layers else None
        self._layer_id = next_layer.id if next_layer else None
        self._layer_target = 'sticker' if next_layer and next_layer.kind == 'sticker' else 'mask'
        self.store_layer_edit(replace(edit, layers=layers))

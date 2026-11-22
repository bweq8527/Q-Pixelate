"""Layer editing operations used by the main window."""
import math
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QItemSelectionModel, QSignalBlocker, Qt
from PySide6.QtGui import QImage

from .layer_widgets import OMIT_LAYER, SPEED_LAYER, effect_rows
from .model import DEFAULT_TRANSITION_DURATION, Layer, Transform, frame_bounds
from .sticker_tracking import StickerTracking


class LayerEditing(StickerTracking):
    def connect_layer_editor(self):
        panel = self.layer_panel
        self._layer_ids = set()
        self._layer_selection_primary = None
        self._layer_anchor = None
        panel.picker.itemSelectionChanged.connect(self.pick_layer_rows)
        panel.redraw.clicked.connect(lambda: self.begin_mask(True))
        panel.track_end.clicked.connect(self.review_tracking_end)
        panel.raise_.clicked.connect(lambda: self.reorder_layer(1))
        panel.lower.clicked.connect(lambda: self.reorder_layer(-1))
        panel.delete.clicked.connect(self.delete_layer)
        panel.name.editingFinished.connect(self.set_layer_name)
        panel.start.valueChanged.connect(lambda value: self.set_layer_time('start', value))
        panel.end.valueChanged.connect(lambda value: self.set_layer_time('end', value))
        panel.speed.valueChanged.connect(self.set_paragraph_speed)
        panel.video_transition.currentIndexChanged.connect(
            lambda: self.set_paragraph_transition(video_transition=panel.video_transition.currentData()))
        panel.audio_transition.currentIndexChanged.connect(
            lambda: self.set_paragraph_transition(audio_transition=panel.audio_transition.currentData()))
        panel.transition_duration.valueChanged.connect(
            lambda value: self.set_paragraph_transition(transition_duration=value))
        panel.kind.activated.connect(self.set_layer_kind)
        panel.asset.clicked.connect(self.use_library_asset)
        panel.target.currentIndexChanged.connect(self.change_layer_target)
        panel.remove_mask.clicked.connect(self.remove_layer_mask)
        panel.strength.valueChanged.connect(self.set_layer_strength)
        panel.feather.valueChanged.connect(self.set_layer_feather)
        for field, spin in zip(("x", "y", "w", "h", "angle"), panel.geometry):
            spin.valueChanged.connect(lambda value, field=field: self.geometry_changed(field, value))
        panel.transform.clicked.connect(self.begin_transform)
        self.layer_timeline.activated.connect(self.select_layer)
        self.layer_timeline.selectionModified.connect(self.select_layer_modified)
        self.layer_timeline.dragged.connect(self.set_layer_span)
        self.canvas.stickerDropped.connect(self.drop_sticker)
        self.canvas.layerPicked.connect(self.pick_canvas_layer)
        self.canvas.maskDrawn.connect(self.finish_mask)
        self.canvas.transformChanged.connect(self.set_layer_transform)

    def layer_edit(self):
        if self.project and 0 <= self._editing_row < len(self.project.edits):
            edit = self.current_edit()
            return edit if edit.layers is not None else None

    def reset_layer_selection(self):
        self._layer_selection_primary = self._layer_id
        self._layer_ids = {self._layer_id} if self._layer_id else set()
        self._layer_anchor = self._layer_id

    def layer_selection(self, edit):
        if self._layer_id != self._layer_selection_primary:
            # Some actions, such as applying detector results, set the primary layer.
            self.reset_layer_selection()
        return self._layer_ids & {layer_id for layer_id, _ in effect_rows(edit)}

    def selected_layer(self, edit):
        selected = self.layer_selection(edit)
        if edit and len(selected) == 1:
            return next((layer for layer in edit.layers if layer.id in selected), None)

    @staticmethod
    def set_values(control, values):
        values = list(values)
        control.setMixed(False)
        if values:
            control.setValue(values[0])
            control.setMixed(any(value != values[0] for value in values[1:]))

    def sync_layer_editor(self):
        edit = self.layer_edit()
        chosen = self.layer_selection(edit)
        rows = effect_rows(edit)
        selected = {i for i, (layer_id, _) in enumerate(rows) if layer_id in chosen}
        panel = self.layer_panel
        panel.setVisible(bool(edit))
        self.layer_empty.setVisible(not edit)
        self.layer_timeline.set_edit(edit, selected)
        if not edit:
            return

        editable = not edit.locked
        captions = [f'{index+1:02}  {name}' for index, (_, name) in enumerate(rows)]
        layer = self.selected_layer(edit)
        visual = [item for item in edit.layers if item.id in chosen]
        compatible = bool(visual) and len(visual) == len(chosen)
        widgets = (panel.picker, panel.name, panel.kind, panel.target, panel.start, panel.end,
                   panel.strength, panel.feather, panel.speed, panel.video_transition,
                   panel.audio_transition, panel.transition_duration, *panel.geometry)
        blockers = [QSignalBlocker(widget) for widget in widgets]
        picker = panel.picker
        rebuilt = [picker.item(i).text() for i in range(picker.count())] != captions
        if rebuilt:
            picker.clear()
            picker.addItems(captions)
        for i in range(picker.count()):
            item = picker.item(i)
            if item.isSelected() != (i in selected):
                item.setSelected(i in selected)
        if selected and (rebuilt or picker.currentRow() < 0):
            picker.setCurrentRow(min(selected), QItemSelectionModel.SelectionFlag.NoUpdate)

        panel.track_end.setVisible(bool(layer and layer.motion))
        index = edit.layers.index(layer) if layer else -1
        panel.lower.setVisible(index >= 0 and len(edit.layers) > 1)
        panel.raise_.setVisible(index >= 0 and len(edit.layers) > 1)
        panel.lower.setEnabled(editable and index > 0)
        panel.raise_.setEnabled(editable and 0 <= index < len(edit.layers)-1)
        panel.delete.setEnabled(editable and bool(chosen))
        can_redraw = bool(layer and not layer.motion)
        for widget in (panel.redraw, panel.shape):
            widget.setVisible(can_redraw)
            widget.setEnabled(editable and can_redraw)

        panel.paragraph.setVisible(bool(chosen) and not compatible)
        speed_only = chosen == {SPEED_LAYER}
        omit_only = chosen == {OMIT_LAYER}
        panel.paragraph_form.setRowVisible(panel.speed, speed_only)
        panel.speed.setEnabled(editable)
        panel.speed.setValue(edit.speed)
        for widget in (panel.video_transition, panel.audio_transition, panel.transition_duration):
            panel.paragraph_form.setRowVisible(widget, omit_only)
            widget.setEnabled(editable)
        panel.video_transition.setCurrentIndex(panel.video_transition.findData(edit.video_transition))
        panel.audio_transition.setCurrentIndex(panel.audio_transition.findData(edit.audio_transition))
        panel.transition_duration.setValue(edit.transition_duration)
        panel.transition_duration.setEnabled(editable and (
            edit.video_transition != 'cut' or edit.audio_transition != 'cut'))
        if omit_only:
            panel.paragraph_note.setText('预览和导出跳过整个选区，并对接缝两侧的保留内容应用过渡。移除此效果可恢复该段。')
        elif speed_only:
            panel.paragraph_note.setText('整个选区的画面和声音同步变速。当前会优先跳过该段。' if edit.omit
                                         else '整个选区的画面和声音同步变速。')
        else:
            panel.paragraph_note.setText('所选效果没有可统一设置的参数。')
        panel.details.setVisible(compatible)
        panel.details.setEnabled(editable)
        if not compatible:
            return

        names = {item.name for item in visual}
        panel.name.setText(visual[0].name if len(names) == 1 else '')
        panel.name.setPlaceholderText('多值' if len(names) > 1 else '')
        spans = [edit.layer_span(item) for item in visual]
        panel.start.setRange(edit.start, edit.end)
        panel.end.setRange(edit.start, edit.end)
        self.set_values(panel.start, (a if a < b else edit.start for a, b in spans))
        self.set_values(panel.end, (b if a < b else edit.end for a, b in spans))
        panel.start.setToolTip('图层超出选区，修改时间可恢复显示' if any(a >= b for a, b in spans) else '')

        one = layer is not None
        has_mask = one and layer.mask is not None
        masked_sticker = has_mask and layer.kind == 'sticker'
        panel.kind.setCurrentIndex(panel.kind.findData(layer.kind) if one else -1)
        panel.kind.model().item(panel.kind.findData('mosaic')).setEnabled(one and layer.kind == 'mosaic')
        for widget, show in ((panel.kind, has_mask and not layer.motion),
                             (panel.target, masked_sticker),
                             (panel.remove_mask, masked_sticker),
                             (panel.asset, one and (layer.kind == 'sticker' or has_mask))):
            panel.form.setRowVisible(widget, bool(show))
        panel.target.setCurrentIndex(1 if self._layer_target == 'mask' else 0)

        kinds = {item.kind for item in visual}
        strength = kinds in ({'blur'}, {'mosaic'})
        mosaic = kinds == {'mosaic'}
        panel.form.setRowVisible(panel.strength, strength)
        panel.strength_label.setText('马赛克块大小' if mosaic else '模糊强度')
        panel.strength.setSuffix(' px' if mosaic else '')
        if strength:
            self.set_values(panel.strength, (item.strength for item in visual))
        feather = all(item.mask is not None for item in visual)
        panel.form.setRowVisible(panel.feather, feather)
        if feather:
            self.set_values(panel.feather, (item.mask.feather for item in visual))

        transforms = [self.target_transform(item) for item in visual]
        transformable = len(kinds) == 1 and all(t is not None for t in transforms)
        panel.form.setRowVisible(panel.transform, bool(one and transformable and not layer.motion))
        for field, control in zip(('x', 'y', 'w', 'h', 'angle'), panel.geometry):
            visible = transformable and (field != 'angle' or not any(
                item.motion and item.kind != 'sticker' for item in visual))
            panel.form.setRowVisible(control, visible)
            if visible:
                self.set_values(control, (getattr(t, field)*100 if field != 'angle'
                                          else (t.angle+180)%360-180 for t in transforms))
        del blockers

    def target_transform(self, layer):
        if layer is None:
            return None
        if layer.motion and layer.kind in ('mosaic', 'blur'):
            return layer.transform
        if self._layer_target == 'mask' or layer.kind == 'blur':
            return layer.mask.transform if layer.mask else None
        return layer.transform

    def cancel_layer_interaction(self):
        self._transform_before = None
        self.canvas.free_transform = False
        self.canvas.layer_gesture = None
        self._mask_context = None
        self._tracking_request = None
        self.canvas.mask_shape = None
        self.canvas.mask_stroke = []

    def pick_layer_rows(self):
        edit = self.layer_edit()
        rows = effect_rows(edit)
        picker = self.layer_panel.picker
        indices = [picker.row(item) for item in picker.selectedItems()]
        current = picker.currentRow()
        primary = current if current in indices else (indices[0] if indices else None)
        self.set_layer_selection(rows, indices, primary, anchor=primary)

    def set_layer_selection(self, rows, indices, primary, *, anchor=None):
        indices = [i for i in indices if 0 <= i < len(rows)]
        new = {rows[i][0] for i in indices}
        primary = rows[primary][0] if primary in indices else next((rows[i][0] for i in indices), None)
        if new != self._layer_ids or primary != self._layer_id:
            self.cancel_layer_interaction()
        if primary != self._layer_id:
            edit = self.layer_edit()
            layer = next((item for item in edit.layers if item.id == primary), None) if edit else None
            self._layer_target = 'mask' if layer and layer.kind == 'blur' else 'sticker'
        self._layer_id = self._layer_selection_primary = primary
        self._layer_ids = new
        if anchor is not None:
            self._layer_anchor = rows[anchor][0]
        self.sync_layer_editor()
        self.update_canvas_mask()

    def select_layer(self, index):
        rows = effect_rows(self.layer_edit())
        if 0 <= index < len(rows):
            self.set_layer_selection(rows, [index], index, anchor=index)

    def select_effect_layer(self, layer_id):
        rows = effect_rows(self.layer_edit())
        index = next((i for i, (key, _) in enumerate(rows) if key == layer_id), -1)
        self.select_layer(index)

    def select_layer_modified(self, index, modifiers):
        edit = self.layer_edit()
        rows = effect_rows(edit)
        previous = self.layer_selection(edit)
        indices = {i for i, (layer_id, _) in enumerate(rows) if layer_id in previous}
        if modifiers & Qt.ShiftModifier:
            anchor = next((i for i, (layer_id, _) in enumerate(rows) if layer_id == self._layer_anchor), index)
            first, last = sorted((anchor, index))
            addition = set(range(first, last+1))
            indices = indices | addition if modifiers & Qt.ControlModifier else addition
            self.set_layer_selection(rows, sorted(indices), index)
        elif modifiers & Qt.ControlModifier:
            self.set_layer_selection(rows, sorted(indices ^ {index}), index, anchor=index)

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
        if layer and not edit.locked:
            self.update_visual_layers(edit, [(layer, replace(layer, **kwargs))])

    def update_visual_layers(self, edit, pairs):
        updates = {original.id: updated for original, updated in pairs if original != updated}
        if updates:
            self.store_layer_edit(replace(edit, layers=[updates.get(item.id, item) for item in edit.layers]))

    def selected_visual_layers(self, edit):
        chosen = self.layer_selection(edit)
        layers = [layer for layer in edit.layers if layer.id in chosen] if edit and not edit.locked else []
        return layers if len(layers) == len(chosen) else []

    def set_layer_name(self):
        name = self.layer_panel.name
        if not name.isModified():
            return
        edit = self.layer_edit()
        self.update_visual_layers(edit, ((layer, replace(layer, name=name.text().strip()))
                                         for layer in self.selected_visual_layers(edit)))

    def set_layer_strength(self, value):
        edit = self.layer_edit()
        layers = self.selected_visual_layers(edit)
        if {layer.kind for layer in layers} in ({'blur'}, {'mosaic'}):
            self.update_visual_layers(edit, ((layer, replace(layer, strength=int(value))) for layer in layers))

    def set_paragraph_speed(self, value):
        edit = self.layer_edit()
        if edit and not edit.locked and self.layer_selection(edit) == {SPEED_LAYER} and edit.speed != value:
            self.store_layer_edit(replace(edit, speed=value, speed_effect=True))

    def set_paragraph_transition(self, **changes):
        edit = self.layer_edit()
        if edit and not edit.locked and self.layer_selection(edit) == {OMIT_LAYER}:
            updated = replace(edit, **changes)
            if updated != edit:
                self.store_layer_edit(updated)

    def add_layer(self, edit, layer, target):
        self.cancel_layer_interaction()
        self._layer_id, self._layer_target = layer.id, target
        self.reset_layer_selection()
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
        self.activate_from_timeline(row)
        if self.add_static_sticker(path, point):
            self.begin_transform()

    def add_static_sticker(self, path, point):
        edit = self.promote_selected_edit()
        if not edit or edit.locked:
            return False
        image = QImage(path)
        if image.isNull():
            self.status.setText('无法读取贴纸，请重新导入图片。')
            return False
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
            return False
        self.add_layer(edit, layer, 'sticker')
        self.update_edit_controls()
        return True

    def default_layer_start(self, edit):
        seconds = self.timeline.position
        if edit.start <= seconds < edit.end:
            fps = self.info.fps_float
            frame_time = math.floor(seconds*fps+1e-6)/fps
            return max(0, frame_time-edit.start)
        return 0

    def add_full_blur(self):
        edit = self.promote_selected_edit()
        if edit and not edit.locked:
            self.add_layer(edit, Layer(self.default_layer_start(edit), edit.end-edit.start, name='全屏模糊'), 'mask')

    def begin_mask(self, redraw, shape=None):
        edit = self.layer_edit() if redraw else self.promote_selected_edit()
        layer = self.selected_layer(edit) if redraw else None
        if not edit or edit.locked or (redraw and (not layer or layer.motion)):
            return
        self.player.pause()
        self.cancel_layer_interaction()
        self._mask_context = self._editing_row, edit.start, edit.end, self._layer_id if redraw else None
        self.canvas.mask_shape = shape or self.layer_panel.shape.currentData()
        self.canvas.setFocus()
        self.status.setText('在视频上按住左键绘制遮罩，Esc 取消。')
        self.update_canvas_mask()

    def finish_mask(self, mask):
        if self._tracking_request:
            self.finish_tracking(mask)
            return
        context, self._mask_context = self._mask_context, None
        edit = self.promote_selected_edit() if context else None
        if not context or not edit or edit.locked or context[:3] != (self._editing_row, edit.start, edit.end):
            return
        if context[3]:
            layer = self.selected_layer(edit)
            if layer and layer.id == context[3]:
                self._layer_target = 'mask'
                self.update_layer(mask=replace(mask, feather=layer.mask.feather if layer.mask else 0))
        else:
            self.add_layer(edit, Layer(self.default_layer_start(edit), edit.end-edit.start,
                                       name='区域遮罩', mask=mask), 'mask')
            self.open_layer_properties()
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
        if not layer or edit.locked or layer.motion:
            return
        kind = self.layer_panel.kind.currentData()
        if kind == layer.kind or kind == 'mosaic':
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
        edit = self.layer_edit()
        layers = self.selected_visual_layers(edit)
        if all(layer.mask is not None for layer in layers):
            self.update_visual_layers(edit, ((layer, replace(layer, mask=replace(layer.mask, feather=value)))
                                             for layer in layers))

    def open_layer_properties(self):
        if hasattr(self, 'layer_properties_tab'):
            self.editor_tabs.setCurrentWidget(self.layer_properties_tab)

    def begin_transform(self):
        edit = self.layer_edit()
        layer = self.selected_layer(edit)
        transform = self.target_transform(layer)
        if not transform or edit.locked or layer.motion:
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

    def geometry_changed(self, field, value):
        edit = self.layer_edit()
        layers = self.selected_visual_layers(edit)
        if len({layer.kind for layer in layers}) != 1:
            return
        transforms = [self.target_transform(layer) for layer in layers]
        if any(t is None for t in transforms):
            return
        value = value if field == 'angle' else value/100
        self.update_visual_layers(edit, ((layer, self.with_transform(layer, replace(transform, **{field: value})))
                                         for layer, transform in zip(layers, transforms)))

    def with_transform(self, layer, transform):
        if layer.motion and layer.kind in ('blur', 'mosaic'):
            return replace(layer, transform=replace(transform, angle=0))
        if layer.mask and (self._layer_target == 'mask' or layer.kind == 'blur'):
            return replace(layer, mask=replace(layer.mask, transform=transform))
        return replace(layer, transform=transform)

    def set_layer_transform(self, transform):
        edit = self.layer_edit()
        layer = self.selected_layer(edit)
        if layer and not edit.locked and self.target_transform(layer):
            self.update_visual_layers(edit, [(layer, self.with_transform(layer, transform))])

    def set_layer_time(self, side, value):
        edit = self.layer_edit()
        pairs = []
        for layer in self.selected_visual_layers(edit):
            a, b = edit.layer_span(layer)
            if a >= b:
                a, b = edit.start, edit.end
            span = (value, b) if side == 'start' else (a, value)
            updated = self.layer_with_span(edit, layer, *span)
            if updated is None:
                self.reject_layer_span()
                return
            pairs.append((layer, updated))
        self.update_visual_layers(edit, pairs)

    def set_layer_span(self, index, start, end):
        edit = self.layer_edit()
        if not edit or edit.locked or not 0 <= index < len(edit.layers):
            return
        layer = edit.layers[index]
        updated = self.layer_with_span(edit, layer, start, end)
        if updated is None:
            self.reject_layer_span()
        elif updated != layer:
            self.select_layer(index)
            self.update_visual_layers(edit, [(layer, updated)])

    def reject_layer_span(self):
        self.status.setText('请选择至少一帧的有效图层时间。')
        self.sync_layer_editor()

    def layer_with_span(self, edit, layer, start, end):
        start, end = max(edit.start, start), min(end, edit.end)
        target_track = layer.kind in ('sticker', 'blur') and bool(layer.motion)
        if target_track:
            origin = edit.start + layer.start
            start = max(start, origin + layer.motion[0][0])
            end = min(end, origin + layer.motion[-1][0] + 1/self.info.fps_float)
        # Preserve sub-ms source boundaries through unrelated edits and focus changes.
        a, b = edit.layer_span(layer)
        if abs(start-a) <= .0005 and abs(end-b) <= .0005:
            return layer
        if end-start < min(self.timeline.minimum_span, layer.end-layer.start)-.0005:
            return None
        new_start = start-edit.start
        motion = layer.motion
        if target_track or (motion and abs((end-start)-(layer.end-layer.start)) > .0005):
            motion = [(time+layer.start-new_start, box) for time, box in motion]
        return replace(layer, start=new_start, end=end-edit.start, motion=motion)

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
        chosen = self.layer_selection(edit)
        if not edit or edit.locked or not chosen:
            return
        self.cancel_layer_interaction()
        layers = [item for item in edit.layers if item.id not in chosen]
        updated = replace(edit, layers=layers, omit=edit.omit and OMIT_LAYER not in chosen,
                          speed=1 if SPEED_LAYER in chosen else edit.speed,
                          speed_effect=edit.speed_effect and SPEED_LAYER not in chosen)
        if OMIT_LAYER in chosen:
            updated = replace(updated, video_transition='cut', audio_transition='cut',
                              transition_duration=DEFAULT_TRANSITION_DURATION)
        old_rows = effect_rows(edit)
        first_removed = next(i for i, (layer_id, _) in enumerate(old_rows) if layer_id in chosen)
        rows = effect_rows(updated)
        self._layer_id = rows[min(first_removed, len(rows)-1)][0] if rows else None
        self._layer_target = 'mask' if any(layer.id == self._layer_id and layer.kind == 'blur'
                                           for layer in layers) else 'sticker'
        self.reset_layer_selection()
        self.store_layer_edit(updated)

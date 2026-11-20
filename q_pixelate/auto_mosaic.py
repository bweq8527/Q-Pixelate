"""Review detections on the source timeline and convert them to draft layers."""

import sys
from dataclasses import dataclass, replace

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QPushButton, QSpinBox, QVBoxLayout, QWidget)

from .model import Layer, Transform
from .widgets import time_text


LABELS = {
    'FEMALE_BREAST_EXPOSED': '胸部裸露', 'MALE_BREAST_EXPOSED': '胸部裸露',
    'FEMALE_GENITALIA_EXPOSED': '私密部位裸露', 'MALE_GENITALIA_EXPOSED': '私密部位裸露',
    'ANUS_EXPOSED': '私密部位裸露', 'BUTTOCKS_EXPOSED': '臀部裸露',
}


@dataclass
class DetectionBatch:
    mode: str
    tracks: list
    layers: list[Layer]
    checked: set[str]


class AutomaticMosaic:
    def setup_detection_tab(self):
        self.detection_batches = {}
        self._listed_batch = None
        self._active_detection_key = None
        self.detection_tab = QWidget()
        layout = QVBoxLayout(self.detection_tab)
        self.detection_label = QLabel()
        self.detection_label.setWordWrap(True)
        layout.addWidget(self.detection_label)
        row = QHBoxLayout()
        self.scan_all_frames = QCheckBox('逐帧扫描')
        self.scan_fps = QSpinBox()
        self.scan_fps.setRange(1, 30)
        self.scan_fps.setValue(6)
        self.scan_fps.setSuffix(' 帧/秒')
        self.scan_all_frames.toggled.connect(self.scan_fps.setDisabled)
        row.addWidget(self.scan_all_frames)
        row.addWidget(self.scan_fps)
        row.addStretch()
        layout.addLayout(row)
        self.scan_button = QPushButton('识别裸露内容')
        self.scan_button.clicked.connect(self.scan_selection)
        layout.addWidget(self.scan_button)
        self.detection_results = QListWidget()
        self.detection_results.currentRowChanged.connect(self.jump_to_detection)
        self.detection_results.itemChanged.connect(self.toggle_detection)
        layout.addWidget(self.detection_results, 1)
        self.detection_position = QLabel()
        self.detection_position.setWordWrap(True)
        layout.addWidget(self.detection_position)
        self.mosaic_button = QPushButton('全部马赛克')
        self.mosaic_button.setObjectName('primary')
        self.mosaic_button.setToolTip('将所有勾选的检测结果转成可编辑的马赛克图层')
        self.mosaic_button.clicked.connect(self.apply_detection_results)
        layout.addWidget(self.mosaic_button)
        self.editor_tabs.addTab(self.detection_tab, '自动打码')
        self.editor_tabs.currentChanged.connect(lambda _: self.update_canvas_mask())

    def detection_key(self):
        if self.project and 0 <= self._editing_row < len(self.project.edits):
            edit = self.current_edit()
            return edit.start, edit.end
        return None

    def scan_selection(self):
        key = self.detection_key()
        if key is None or self.selected_locked():
            return
        manager = self.components
        settings = manager.preferences()
        model = settings['model']
        device = settings['device']
        if any(not manager.status(item)['ready'] for item in (model, device)):
            self.status.setText('请先安装所选模型和识别环境。')
            self.open_components()
            return
        python = manager.path('base') / 'python.exe'
        if not manager.status('base')['ready']:
            python = sys.executable
        rate = None if self.scan_all_frames.isChecked() else self.scan_fps.value()
        mode = '逐帧' if rate is None else f'快速 · {rate} 帧/秒'
        edit, info = self.current_edit(), self.info

        def task(runner):
            from .detection import scan
            return scan(info, edit, runner, python=python, site_packages=manager.path(device)/'site-packages',
                        model=manager.path(model)/'weights.onnx', device=device, sample_fps=rate)

        self.start_job(task, lambda tracks: self.show_detection_results(key, tracks, mode))

    def show_detection_results(self, key, tracks, mode):
        if key != self.detection_key():
            return
        layers = []
        for track in tracks:
            label = LABELS.get(track.label, track.label)
            layers.append(Layer(track.start-key[0], track.end-key[0], 'mosaic', f'自动马赛克 · {label}',
                                transform=Transform.from_rect(track.points[0][1]), strength=24, id=track.id,
                                motion=[(t-track.start, box) for t, box in track.points]))
        self.detection_batches[key] = DetectionBatch(mode, tracks, layers, {track.id for track in tracks})
        self.sync_detection()
        self.status.setText(f'{mode}扫描完成 · {len(tracks)} 个候选区域' if tracks else f'{mode}扫描完成 · 未检测到')

    def sync_detection(self):
        key = self.detection_key()
        self._active_detection_key = key
        current_ranges = {(edit.start, edit.end) for edit in self.timeline.edits}
        current_ranges.add(key)
        for retired in self.detection_batches.keys() - current_ranges:
            del self.detection_batches[retired]
        batch = self.detection_batches.get(key)
        editable = key is not None and not self.selected_locked()
        self.scan_button.setEnabled(editable)
        self.mosaic_button.setEnabled(editable and bool(batch and batch.checked))
        if batch is not self._listed_batch:
            self._listed_batch = batch
            with QSignalBlocker(self.detection_results):
                self.detection_results.clear()
                for track in batch.tracks if batch else ():
                    box = track.points[0][1]
                    label = LABELS.get(track.label, track.label)
                    item = QListWidgetItem(f'{time_text(track.start)} – {time_text(track.end)}\n'
                                           f'{label} · {track.score:.0%}')
                    item.setToolTip(f'首帧位置 · X {box.x:.1%}，Y {box.y:.1%}，宽 {box.w:.1%}，高 {box.h:.1%}')
                    item.setData(Qt.ItemDataRole.UserRole, track.id)
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    item.setCheckState(Qt.CheckState.Checked if track.id in batch.checked else Qt.CheckState.Unchecked)
                    self.detection_results.addItem(item)
        if batch:
            summary = f'{batch.mode} · {len(batch.tracks)} 个候选区域' if batch.tracks else f'{batch.mode} · 未检测到'
        elif key is None:
            summary = '请先建立或选中一个选区。'
        elif not editable:
            summary = '解锁当前选区后可扫描或应用检测结果。'
        else:
            summary = '扫描当前选区。快速扫描可能漏掉短暂画面。'
        preferences = self.components.preferences()
        device = 'NVIDIA GPU' if preferences['device'] == 'gpu' else 'CPU'
        summary += f"\nNudeNet {preferences['model']} · {device}"
        self.detection_label.setText(summary)
        checked = batch.checked if batch else ()
        self.timeline.detection_spans = [(t.start, t.end) for t in batch.tracks if t.id in checked] if batch else []
        self.timeline.update()
        self.update_canvas_mask()

    def toggle_detection(self, item):
        batch = self._listed_batch
        if batch is None:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        if item.checkState() == Qt.CheckState.Checked:
            batch.checked.add(key)
        else:
            batch.checked.discard(key)
        self.sync_detection()

    def jump_to_detection(self, row):
        batch = self._listed_batch
        if batch and 0 <= row < len(batch.tracks):
            self.player.pause()
            self.seek(batch.tracks[row].start)
            self.update_canvas_mask(batch.tracks[row].start)

    def detection_boxes_at(self, seconds):
        if self.editor_tabs.currentWidget() != self.detection_tab:
            return []
        key = self.detection_key()
        batch = self.detection_batches.get(key)
        if not batch:
            self.detection_position.clear()
            return []
        # QMediaPlayer reports milliseconds. Snap to the nearest source frame.
        seconds = round(seconds*self.info.fps_float)/self.info.fps_float
        visible = {track.id: layer.mosaic_rect_at(seconds-track.start)
                   for track, layer in zip(batch.tracks, batch.layers)
                   if track.id in batch.checked and track.start <= seconds < track.end}
        item = self.detection_results.currentItem()
        box = visible.get(item.data(Qt.ItemDataRole.UserRole)) if item else None
        if box:
            self.detection_position.setText(f'当前位置 · X {box.x:.1%}，Y {box.y:.1%}，宽 {box.w:.1%}，高 {box.h:.1%}')
        else:
            self.detection_position.clear()
        return list(visible.values())

    def apply_detection_results(self):
        batch = self.detection_batches.get(self.detection_key())
        if not batch or not batch.checked or self.selected_locked():
            return
        edit = self.promote_selected_edit()
        if not edit:
            return
        applied = {layer.id for layer in edit.layers}
        additions = [layer for layer in batch.layers if layer.id in batch.checked and layer.id not in applied]
        if not additions:
            self.status.setText('勾选的结果已全部应用。')
            return
        self.cancel_layer_interaction()
        self._layer_id, self._layer_target = additions[0].id, 'mask'
        self.store_layer_edit(replace(edit, layers=[*edit.layers, *additions]))
        self.status.setText(f'已添加 {len(additions)} 个马赛克图层，可在图层属性调整。')

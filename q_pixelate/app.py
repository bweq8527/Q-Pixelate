from __future__ import annotations

import copy
import os
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, QSignalBlocker
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout,
    QFrame, QHBoxLayout, QLabel, QListWidget, QMainWindow, QMessageBox, QPushButton,
    QProgressBar, QScrollArea, QScrollBar, QSlider, QSplitter, QTextEdit,
    QVBoxLayout, QWidget,
)

from .jobs import Worker
from .media import probe
from .model import Edit, Project, Rect
from .render import Exporter, render_preview
from .widgets import Timeline, TimeSpinBox, VideoCanvas, time_text


STYLE = '''
QMainWindow, QDialog { background: #0f141c; color: #dce6f3; }
QWidget { color: #dce6f3; font-family: "Microsoft YaHei UI", "Segoe UI"; font-size: 12px; }
QFrame#panel { background: #171f2b; border: 1px solid #283548; border-radius: 10px; }
QLabel#brand { color: #72e6c3; font-size: 22px; font-weight: 700; }
QLabel#muted { color: #91a0b6; }
QLabel#section { color: #96a9c1; font-weight: 600; }
QPushButton { background: #253247; border: 1px solid #35475f; border-radius: 6px; padding: 8px 12px; }
QPushButton:hover { background: #32445c; border-color: #6ee7c0; }
QPushButton:disabled { color: #607086; background: #1a2330; border-color: #283344; }
QPushButton#primary { background: #73dfbc; color: #10251f; border: none; font-weight: 700; }
QPushButton#primary:hover { background: #9ff3d6; }
QPushButton#primary:disabled { background: #33564f; color: #738d85; }
QPushButton#danger { color: #f1acac; }
QListWidget, QTextEdit { background: #111923; border: 1px solid #304057; border-radius: 6px; padding: 5px; }
QListWidget::item { padding: 9px 5px; border-radius: 4px; }
QListWidget::item:selected { background: #284d49; color: #b8ffe7; }
QDoubleSpinBox, QComboBox { background: #101923; border: 1px solid #35465c; border-radius: 5px; padding: 5px; }
QComboBox QAbstractItemView { background: #1b2939; selection-background-color: #315f56; }
QSlider::groove:horizontal { background: #2e3c50; height: 5px; border-radius: 2px; }
QSlider::handle:horizontal { background: #73dfbc; width: 12px; margin: -4px 0; border-radius: 6px; }
QProgressBar { border: none; background: #202e40; border-radius: 4px; height: 10px; text-align: center; }
QProgressBar::chunk { background: #59ba9d; border-radius: 4px; }
QScrollArea { border: none; background: transparent; }
QScrollBar:horizontal { height: 10px; background: #172231; }
QScrollBar::handle:horizontal { background: #42566f; min-width: 25px; border-radius: 4px; }
QSplitter::handle { background: #263246; }
QToolTip { background: #25374b; color: #e6f2ff; border: 1px solid #52708e; }
'''


def button(text, callback, primary=False):
    widget = QPushButton(text)
    if primary:
        widget.setObjectName('primary')
    widget.clicked.connect(callback)
    return widget


def label(text, name='muted'):
    widget = QLabel(text)
    widget.setObjectName(name)
    return widget


class PreviewDialog(QDialog):
    def __init__(self, path, parent):
        super().__init__(parent)
        self.setWindowTitle('真实效果预览 · Q-Pixelate')
        self.resize(960, 650)
        layout = QVBoxLayout(self)
        layout.addWidget(label('已渲染的片段效果 · 与导出使用相同的滤镜，预览分辨率最高 960px'))
        self.canvas = VideoCanvas()
        self.canvas.auto_dimensions = True
        layout.addWidget(self.canvas, 1)
        self.audio = QAudioOutput(self)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio)
        self.sink = QVideoSink(self)
        self.player.setVideoSink(self.sink)
        self.sink.videoFrameChanged.connect(self.canvas.set_frame)
        self.player.setLoops(QMediaPlayer.Loops.Infinite)
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        row = QHBoxLayout()
        row.addWidget(button('播放 / 暂停', self.toggle))
        row.addWidget(button('重新播放', lambda: (self.player.setPosition(0), self.player.play())))
        row.addStretch()
        row.addWidget(button('关闭预览', self.close))
        layout.addLayout(row)
        self.player.play()

    def toggle(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def closeEvent(self, event):
        self.player.stop()
        self.player.setSource(QUrl())
        event.accept()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Q-Pixelate · 长视频局部打码')
        self.resize(1390, 960)
        self.setMinimumSize(1040, 760)
        self.setStyleSheet(STYLE)
        self.project = None
        self.info = None
        self.project_path = None
        self.dirty = False
        self.worker = None
        self.last_export = None
        self.image_path = ''
        self._syncing = False
        self._closing = False
        self._pause_on_frame = False
        self._editing_row = -1
        self._loading_form = False
        self.preview_temp = tempfile.TemporaryDirectory(prefix='qpixel-ui-')
        self.preview_dialogs = []
        self.setAcceptDrops(True)
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(22, 18, 22, 16)
        layout.setSpacing(12)
        header = QHBoxLayout()
        header.addWidget(label('Q-PIXELATE', 'brand'))
        header.addWidget(label('  局部修订 · 快速缝合'))
        header.addStretch()
        self.open_button = button('打开视频', self.choose_video)
        self.load_button = button('载入工程', self.choose_project)
        self.save_button = button('保存工程', self.save_project)
        self.export_button = button('缝合导出  ↗', self.choose_export, True)
        for widget in (self.open_button, self.load_button, self.save_button, self.export_button):
            header.addWidget(widget)
        layout.addLayout(header)
        self.source_label = label('选择源视频；文件全程在本机处理。')
        self.source_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.source_label)
        self.body = QWidget()
        body = QVBoxLayout(self.body)
        body.setContentsMargins(0, 0, 0, 0)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        body.addWidget(splitter, 1)
        preview_panel = QFrame()
        preview_panel.setObjectName('panel')
        preview_layout = QVBoxLayout(preview_panel)
        self.mode_label = label('原画编辑 · 遮挡框表示位置；点击“预览效果”查看实际模糊。')
        preview_layout.addWidget(self.mode_label)
        self.canvas = VideoCanvas()
        preview_layout.addWidget(self.canvas, 1)
        playback = QHBoxLayout()
        playback.addWidget(button('◀ 一帧', lambda: self.step_frame(-1)))
        self.play_button = button('▶ 播放', self.toggle_play)
        playback.addWidget(self.play_button)
        playback.addWidget(button('一帧 ▶', lambda: self.step_frame(1)))
        self.position_label = label('00:00:00.000 / 00:00:00.000')
        playback.addStretch()
        playback.addWidget(self.position_label)
        preview_layout.addLayout(playback)
        splitter.addWidget(preview_panel)
        side = QFrame()
        side.setObjectName('panel')
        side.setMinimumWidth(350)
        side_layout = QVBoxLayout(side)
        side_layout.setSpacing(9)
        title = QHBoxLayout()
        title.addWidget(label('修订片段', 'section'))
        title.addStretch()
        self.count_label = label('0 段')
        title.addWidget(self.count_label)
        side_layout.addLayout(title)
        self.clip_list = QListWidget()
        self.clip_list.setMinimumHeight(100)
        self.clip_list.setMaximumHeight(200)
        self.clip_list.currentRowChanged.connect(self.select_edit)
        side_layout.addWidget(self.clip_list, 1)
        row = QHBoxLayout()
        row.addWidget(button('＋ 添加选区', self.add_edit, True))
        remove = button('删除片段', self.delete_edit)
        remove.setObjectName('danger')
        row.addWidget(remove)
        side_layout.addLayout(row)
        form = QFormLayout()
        form.setSpacing(9)
        self.in_time = TimeSpinBox()
        self.out_time = TimeSpinBox()
        form.addRow('入点', self.in_time)
        form.addRow('出点', self.out_time)
        self.in_time.valueChanged.connect(self.selection_inputs_changed)
        self.out_time.valueChanged.connect(self.selection_inputs_changed)
        self.effect = QComboBox()
        self.effect.addItems(['全屏模糊', '区域模糊', '图片素材遮挡'])
        self.effect.currentIndexChanged.connect(self.effect_changed)
        form.addRow('效果', self.effect)
        self.strength = QSlider(Qt.Orientation.Horizontal)
        self.strength.setRange(1, 100)
        self.strength.setValue(20)
        self.strength.valueChanged.connect(self.form_changed)
        form.addRow('模糊强度', self.strength)
        side_layout.addLayout(form)
        self.region_panel = QWidget()
        region_layout = QFormLayout(self.region_panel)
        region_layout.setContentsMargins(0, 0, 0, 0)
        self.coordinates = []
        for name, value in [('左侧 %', 25), ('顶部 %', 25), ('宽度 %', 50), ('高度 %', 50)]:
            spin = QDoubleSpinBox()
            spin.setDecimals(2)
            spin.setRange(0 if len(self.coordinates)<2 else .1, 100)
            spin.setValue(value)
            spin.valueChanged.connect(self.coordinate_changed)
            self.coordinates.append(spin)
            region_layout.addRow(name, spin)
        side_layout.addWidget(self.region_panel)
        self.image_button = button('选择遮挡图片…', self.choose_image)
        self.image_label = label('尚未选择图片')
        self.image_label.setWordWrap(True)
        side_layout.addWidget(self.image_button)
        side_layout.addWidget(self.image_label)
        row = QHBoxLayout()
        row.addWidget(button('应用修改', self.apply_edit))
        row.addWidget(button('预览效果', self.preview_edit))
        side_layout.addLayout(row)
        note = label('参数调整后点击“应用修改”。\n在画面中拖出区域；内部可移动，右下角可缩放。')
        note.setWordWrap(True)
        side_layout.addWidget(note)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(side)
        splitter.addWidget(scroll)
        splitter.setSizes([950, 380])
        timeline_header = QHBoxLayout()
        timeline_header.addWidget(label('源视频时间轴', 'section'))
        timeline_header.addWidget(label('  I 入点 · O 出点 · Shift 拖动选区 · 空格播放'))
        timeline_header.addStretch()
        timeline_header.addWidget(button('设为入点 I', self.set_in))
        timeline_header.addWidget(button('设为出点 O', self.set_out))
        timeline_header.addWidget(label('缩放'))
        self.zoom = QSlider(Qt.Orientation.Horizontal)
        self.zoom.setRange(1, 100)
        self.zoom.setMaximumWidth(130)
        timeline_header.addWidget(self.zoom)
        body.addLayout(timeline_header)
        self.timeline = Timeline()
        body.addWidget(self.timeline)
        self.scrollbar = QScrollBar(Qt.Orientation.Horizontal)
        self.scrollbar.setRange(0, 10000)
        self.scrollbar.setPageStep(10000)
        body.addWidget(self.scrollbar)
        self.zoom.valueChanged.connect(self.timeline.set_zoom)
        self.timeline.viewChanged.connect(self.sync_scrollbar)
        self.scrollbar.valueChanged.connect(self.scroll_timeline)
        self.timeline.selectionChanged.connect(self.set_selection)
        self.timeline.seekRequested.connect(self.seek)
        layout.addWidget(self.body, 1)
        footer = QHBoxLayout()
        self.status = label('就绪 · 首版快速导出优先支持 H.264 / SDR / 固定帧率录屏')
        self.status.setWordWrap(True)
        footer.addWidget(self.status, 1)
        self.cancel_button = button('取消任务', self.cancel_job)
        self.cancel_button.setVisible(False)
        footer.addWidget(self.cancel_button)
        self.folder_button = button('打开输出目录', self.open_output_folder)
        self.folder_button.setVisible(False)
        footer.addWidget(self.folder_button)
        log_button = button('处理日志', self.toggle_logs)
        footer.addWidget(log_button)
        layout.addLayout(footer)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)
        self.logs = QTextEdit()
        self.logs.setReadOnly(True)
        self.logs.document().setMaximumBlockCount(1500)
        self.logs.setMaximumHeight(160)
        self.logs.setVisible(False)
        layout.addWidget(self.logs)
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.video_sink = QVideoSink(self)
        self.player.setVideoSink(self.video_sink)
        self.video_sink.videoFrameChanged.connect(self.on_frame)
        self.player.positionChanged.connect(self.position_changed)
        self.player.playbackStateChanged.connect(self.playback_state_changed)
        self.player.errorOccurred.connect(lambda err, text: self.status.setText(f'播放提示：{text}'))
        self.canvas.rectChanged.connect(self.canvas_rect_changed)
        self.effect_changed()
        self.update_enabled()

    def update_enabled(self):
        busy = self.worker is not None and self.worker.isRunning()
        self.body.setEnabled(not busy and self.project is not None)
        self.open_button.setEnabled(not busy)
        self.load_button.setEnabled(not busy)
        self.save_button.setEnabled(not busy and self.project is not None)
        self.export_button.setEnabled(not busy and self.project is not None)
        self.cancel_button.setVisible(busy)
        self.progress.setVisible(busy)

    def error(self, text):
        self.status.setText(text.splitlines()[0])
        if text != '任务已取消。':
            QMessageBox.warning(self, 'Q-Pixelate', text)

    def start_job(self, task, callback):
        if self.worker is not None and self.worker.isRunning():
            return
        self.player.pause()
        worker = Worker(task, self)
        self.worker = worker
        worker.succeeded.connect(callback)
        worker.failed.connect(self.error)
        worker.progressed.connect(self.on_progress)
        worker.logged.connect(self.logs.append)
        worker.finished.connect(self.job_finished)
        worker.start()
        self.progress.setValue(0)
        self.update_enabled()

    def job_finished(self):
        worker = self.worker
        self.worker = None
        self.update_enabled()
        if worker:
            worker.deleteLater()
        if self._closing:
            self.close()

    def on_progress(self, value, text):
        self.progress.setValue(value)
        self.status.setText(text)

    def cancel_job(self):
        if self.worker:
            self.worker.cancel()
            self.status.setText('正在取消并清理本次临时文件…')

    def confirm_discard(self):
        if not self.dirty:
            return True
        reply = QMessageBox.question(self, '未保存的工程', '当前修订尚未保存，是否保存工程？',
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
        if reply == QMessageBox.StandardButton.Save:
            return self.save_project()
        return reply == QMessageBox.StandardButton.Discard

    def choose_video(self):
        path, _ = QFileDialog.getOpenFileName(self, '打开源视频', '', '视频 (*.mp4 *.mkv *.mov *.ts *.avi *.webm);;所有文件 (*)')
        if path:
            self.open_video(path)

    def open_video(self, path):
        if not self.confirm_discard():
            return
        self.status.setText('正在读取视频信息…')
        self.start_job(lambda runner: probe(path, runner),
                       lambda info: self.set_project(Project(str(Path(path).resolve())), info))

    def set_project(self, project, info):
        project.check_source()
        project.validate(info.duration)
        self._loading_form = True
        self._editing_row = -1
        self.project, self.info = project, info
        self.project_path = None
        self.player.stop()
        self.canvas.image = self.canvas.image.__class__()
        sar = info.video.get('sample_aspect_ratio', '1:1')
        try:
            numerator, denominator = map(int, sar.split(':'))
            pixel_aspect = numerator/denominator if numerator > 0 and denominator > 0 else 1
        except (ValueError, ZeroDivisionError):
            pixel_aspect = 1
        self.canvas.set_dimensions(info.width*pixel_aspect, info.height)
        self.player.setSource(QUrl.fromLocalFile(project.source))
        self.timeline.set_duration(info.duration)
        self.timeline.position = 0
        self.zoom.setValue(1)
        self.in_time.setMaximum(info.duration)
        self.out_time.setMaximum(info.duration)
        self.set_selection(0, min(5, info.duration))
        self.source_label.setText(f'{Path(project.source).name}   ·   {info.width} × {info.height}   ·   {info.fps_float:.3f} fps   ·   {time_text(info.duration)}   ·   {len(info.audio)} 条音轨')
        self.source_label.setToolTip(project.source)
        self.refresh_edits(0 if project.edits else -1)
        self._loading_form = False
        self.dirty = False
        self.mark_title()
        error = info.fast_mode_error()
        self.status.setText(error or '视频已打开 · 选择时间范围，再添加修订片段')
        self.position_changed(0)
        self.seek(0)
        self.update_enabled()

    def choose_project(self):
        path, _ = QFileDialog.getOpenFileName(self, '载入工程', '', 'Q-Pixelate 工程 (*.qpixel);;JSON (*.json)')
        if not path or not self.confirm_discard():
            return
        def load(runner):
            project = Project.load(path)
            return project, probe(project.source, runner)
        def done(result):
            self.set_project(*result)
            self.project_path = path
        self.start_job(load, done)

    def save_project(self):
        if not self.project:
            return False
        if not self.commit_form():
            return False
        path = self.project_path
        if not path:
            path, _ = QFileDialog.getSaveFileName(self, '保存工程', str(Path(self.project.source).with_suffix('.qpixel')), 'Q-Pixelate 工程 (*.qpixel)')
        if not path:
            return False
        try:
            if not Path(path).suffix:
                path += '.qpixel'
            protected = [Path(self.project.source).resolve()] + [Path(e.image).resolve() for e in self.project.edits if e.image]
            if Path(path).resolve() in protected:
                raise ValueError('工程文件不能覆盖源视频或图片素材。')
            self.project.save(path)
            self.project_path = path
            self.dirty = False
            self.mark_title()
            self.status.setText('工程已保存')
            return True
        except (ValueError, OSError) as exc:
            self.error(str(exc))
            return False

    def mark_title(self):
        self.setWindowTitle(f'{"* " if self.dirty else ""}Q-Pixelate · {Path(self.project.source).name if self.project else "长视频局部打码"}')

    def changed(self):
        self.dirty = True
        self.mark_title()

    def form_changed(self, *args):
        row = self._editing_row
        if (not self._loading_form and not self._syncing and self.project and
                0 <= row < len(self.project.edits) and self.current_edit() != self.project.edits[row]):
            self.changed()

    def set_selection(self, start, end):
        blockers = [QSignalBlocker(self.in_time), QSignalBlocker(self.out_time)]
        self.in_time.setValue(start)
        self.out_time.setValue(end)
        self.timeline.selection = (start, end)
        self.timeline.update()

    def selection_inputs_changed(self):
        self.timeline.selection = self.in_time.value(), self.out_time.value()
        self.timeline.update()
        self.form_changed()

    def current_edit(self):
        return Edit(self.in_time.value(), self.out_time.value(), ['blur', 'region', 'image'][self.effect.currentIndex()],
                    Rect(*(s.value()/100 for s in self.coordinates)), self.strength.value(), self.image_path)

    def add_edit(self):
        if not self.project:
            return
        edit = self.current_edit()
        candidate = copy.deepcopy(self.project)
        candidate.edits.append(edit)
        try:
            candidate.validate(self.info.duration)
        except ValueError as exc:
            self.error(str(exc))
            return
        self.project.edits.append(edit)
        self.refresh_edits(len(self.project.edits)-1)
        self.changed()

    def apply_edit(self):
        row = self.clip_list.currentRow()
        if not self.project or row < 0:
            self.status.setText('请先添加片段或在列表中选择要修改的片段。')
            return False
        edit = self.current_edit()
        candidate = copy.deepcopy(self.project)
        candidate.edits[row] = edit
        try:
            candidate.validate(self.info.duration)
        except ValueError as exc:
            self.error(str(exc))
            return False
        self.project.edits[row] = edit
        self.refresh_edits(row)
        self.changed()
        self.status.setText('片段修改已应用')
        return True

    def commit_form(self):
        row = self.clip_list.currentRow()
        if self.project and row >= 0 and self.current_edit() != self.project.edits[row]:
            return self.apply_edit()
        return True

    def refresh_edits(self, selected=-1):
        self._editing_row = -1
        blocker = QSignalBlocker(self.clip_list)
        self.clip_list.clear()
        names = {'blur': '全屏模糊', 'region': '区域模糊', 'image': '图片遮挡'}
        for i, edit in enumerate(self.project.edits):
            self.clip_list.addItem(f'{i+1:02}  {time_text(edit.start)} → {time_text(edit.end)}\n      {names[edit.effect]}  ·  {edit.end-edit.start:.3f}s')
        self.clip_list.setCurrentRow(selected)
        self.count_label.setText(f'{len(self.project.edits)} 段')
        self.timeline.edits = self.project.edits
        self.timeline.update()
        del blocker
        if selected >= 0:
            self.select_edit(selected)

    def select_edit(self, row):
        if not self.project or row < 0 or row >= len(self.project.edits):
            return
        previous = self._editing_row
        if 0 <= previous < len(self.project.edits) and previous != row and not self._loading_form:
            pending = self.current_edit()
            if pending != self.project.edits[previous]:
                candidate = copy.deepcopy(self.project)
                candidate.edits[previous] = pending
                try:
                    candidate.validate(self.info.duration)
                except ValueError as exc:
                    blocker = QSignalBlocker(self.clip_list)
                    self.clip_list.setCurrentRow(previous)
                    self.error(str(exc))
                    return
                self.project.edits[previous] = pending
                item = self.clip_list.item(previous)
                names = {'blur': '全屏模糊', 'region': '区域模糊', 'image': '图片遮挡'}
                item.setText(f'{previous+1:02}  {time_text(pending.start)} → {time_text(pending.end)}\n      {names[pending.effect]}  ·  {pending.end-pending.start:.3f}s')
                self.timeline.update()
                self.changed()
        edit = self.project.edits[row]
        was_loading = self._loading_form
        self._loading_form = True
        self._editing_row = row
        self.set_selection(edit.start, edit.end)
        self.effect.setCurrentIndex(['blur', 'region', 'image'].index(edit.effect))
        self.strength.setValue(edit.strength)
        self.image_path = edit.image
        self.image_label.setText(Path(edit.image).name if edit.image else '尚未选择图片')
        self.canvas_rect_changed(edit.rect)
        self._loading_form = was_loading
        self.update_canvas_mask()
        self.seek(edit.start)

    def delete_edit(self):
        row = self.clip_list.currentRow()
        if self.project and row >= 0:
            del self.project.edits[row]
            self.refresh_edits(min(row, len(self.project.edits)-1))
            self.changed()

    def effect_changed(self, index=None):
        index = self.effect.currentIndex()
        self.region_panel.setVisible(index != 0)
        self.image_button.setVisible(index == 2)
        self.image_label.setVisible(index == 2)
        self.strength.setEnabled(index != 2)
        self.update_canvas_mask()
        self.form_changed()

    def canvas_rect_changed(self, rect):
        self._syncing = True
        for spin, value in zip(self.coordinates, (rect.x, rect.y, rect.w, rect.h)):
            spin.setValue(value*100)
        self._syncing = False
        self.coordinate_changed()

    def coordinate_changed(self):
        if self._syncing:
            return
        x, y, w, h = [s.value()/100 for s in self.coordinates]
        self.canvas.rect = Rect(x, y, w, h)
        self.update_canvas_mask()
        self.form_changed()

    def choose_image(self):
        path, _ = QFileDialog.getOpenFileName(self, '选择遮挡素材', '', '图片 (*.png *.jpg *.jpeg *.webp *.bmp)')
        if path:
            self.image_path = path
            self.image_label.setText(Path(path).name)
            self.image_label.setToolTip(path)
            self.form_changed()

    def seek(self, seconds):
        paused = self.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState
        self.player.setPosition(round(seconds*1000))
        if paused and self.project:
            self._pause_on_frame = True
            self.player.play()

    def on_frame(self, frame):
        self.canvas.set_frame(frame)
        if self._pause_on_frame and frame.isValid():
            self._pause_on_frame = False
            self.player.pause()

    def set_in(self):
        if self.info:
            self.in_time.setValue(self.player.position()/1000)

    def set_out(self):
        if self.info:
            self.out_time.setValue(self.player.position()/1000)

    def toggle_play(self):
        if not self.project:
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def step_frame(self, direction):
        if self.info:
            self.player.pause()
            n = round(self.player.position()/1000*self.info.fps_float)+direction
            self.seek(max(0, min(self.info.duration, n/self.info.fps_float)))

    def position_changed(self, milliseconds):
        if not self.info:
            return
        seconds = milliseconds/1000
        self.position_label.setText(f'{time_text(seconds)} / {time_text(self.info.duration)}')
        self.timeline.position = seconds
        if seconds > self.timeline.view_start+self.timeline.visible or seconds < self.timeline.view_start:
            self.timeline.view_start = max(0, min(self.info.duration-self.timeline.visible, seconds-self.timeline.visible*.1))
            self.sync_scrollbar(self.timeline.view_start)
        self.timeline.update()
        self.update_canvas_mask(seconds)

    def playback_state_changed(self, state):
        self.play_button.setText('❚❚ 暂停' if state == QMediaPlayer.PlaybackState.PlayingState else '▶ 播放')
        self.update_canvas_mask()

    def update_canvas_mask(self, seconds=None):
        # Playback follows the active saved edit; paused editing follows the form.
        playing = hasattr(self, 'player') and self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState and not self._pause_on_frame
        if playing and self.project:
            seconds = self.player.position()/1000 if seconds is None else seconds
            edit = next((e for e in self.project.edits if e.start <= seconds < e.end), None)
            self.canvas.show_mask = edit is not None
            self.canvas.editable = False
            if edit:
                self.canvas.rect = copy.copy(edit.rect)
                self.canvas.fullscreen_mask = edit.effect == 'blur'
                self.canvas.effect_label = '播放定位 · 当前片段遮挡范围（原画）'
        else:
            index = self.effect.currentIndex()
            self.canvas.rect = Rect(*(s.value()/100 for s in self.coordinates))
            self.canvas.editable = index != 0
            self.canvas.fullscreen_mask = index == 0
            self.canvas.show_mask = self.project is not None
            self.canvas.effect_label = '原画编辑 · 全屏模糊区域' if index == 0 else '原画编辑 · 拖动画框 / 内部移动 / 右下角缩放'
        self.canvas.update()

    def sync_scrollbar(self, start):
        blocker = QSignalBlocker(self.scrollbar)
        maximum = max(.001, self.timeline.duration-self.timeline.visible)
        self.scrollbar.setValue(round(start/maximum*10000))
        self.scrollbar.setPageStep(max(1, round(10000/max(1, self.timeline.zoom))))

    def scroll_timeline(self, value):
        self.timeline.view_start = value/10000*max(0, self.timeline.duration-self.timeline.visible)
        self.timeline.update()

    def preview_edit(self):
        if not self.commit_form():
            return
        row = self.clip_list.currentRow()
        if not self.project or row < 0:
            self.status.setText('请先添加并选择一个修订片段。')
            return
        project = copy.deepcopy(self.project)
        project.edits[row] = self.current_edit()
        try:
            project.validate(self.info.duration)
        except ValueError as exc:
            self.error(str(exc))
            return
        path = Path(self.preview_temp.name) / f'preview-{len(self.preview_dialogs)}.mp4'
        def done(result):
            dialog = PreviewDialog(result, self)
            self.preview_dialogs.append(dialog)
            dialog.show()
            self.status.setText('真实效果预览已生成；参数修改后请重新预览。')
        self.start_job(lambda runner: render_preview(project, row, path, runner), done)

    def choose_export(self):
        if not self.project:
            return
        source = Path(self.project.source)
        path, _ = QFileDialog.getSaveFileName(self, '缝合导出完整视频', str(source.with_name(source.stem+'-打码.mp4')),
                                             'MP4 视频 (*.mp4);;Matroska 视频 (*.mkv)')
        if path:
            if not Path(path).suffix:
                path += '.mp4'
            self.export_to(path, overwrite=Path(path).exists())

    def export_to(self, path, overwrite=False):
        if not self.commit_form():
            return
        project = copy.deepcopy(self.project)
        self.folder_button.setVisible(False)
        def done(report):
            self.last_export = report
            self.status.setText(f'导出完成 · 重编码 {report.encoded_seconds:.2f}s / 直接复制 {report.copied_seconds:.2f}s · {report.audio_tracks} 条原音轨')
            self.folder_button.setVisible(True)
        self.start_job(lambda runner: Exporter().export(project, path, runner, overwrite), done)

    def open_output_folder(self):
        if self.last_export:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self.last_export.path).parent)))

    def toggle_logs(self):
        self.logs.setVisible(not self.logs.isVisible())

    def keyPressEvent(self, event):
        if self.worker or not self.project:
            return super().keyPressEvent(event)
        actions = {Qt.Key.Key_Space: self.toggle_play, Qt.Key.Key_I: self.set_in, Qt.Key.Key_O: self.set_out,
                   Qt.Key.Key_Left: lambda: self.step_frame(-1), Qt.Key.Key_Right: lambda: self.step_frame(1),
                   Qt.Key.Key_Delete: self.delete_edit}
        if event.key() in actions:
            actions[event.key()]()
            event.accept()
        else:
            super().keyPressEvent(event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and not self.worker:
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls and urls[0].isLocalFile():
            self.open_video(urls[0].toLocalFile())

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            reply = QMessageBox.question(self, '任务仍在运行', '取消当前任务并退出？')
            if reply == QMessageBox.StandardButton.Yes:
                self._closing = True
                self.worker.cancel()
            event.ignore()
            return
        if not self.confirm_discard():
            self._closing = False
            event.ignore()
            return
        self.player.stop()
        self.player.setSource(QUrl())
        for dialog in self.preview_dialogs:
            dialog.close()
        self.preview_temp.cleanup()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName('Q-Pixelate')
    app.setFont(QFont('Microsoft YaHei UI', 10))
    window = MainWindow()
    window.show()
    if len(sys.argv)>1 and Path(sys.argv[1]).is_file():
        window.open_video(sys.argv[1])
    return app.exec()

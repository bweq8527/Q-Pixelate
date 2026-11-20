from __future__ import annotations

import copy
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, QSignalBlocker, QTimer, QEvent
from PySide6.QtGui import QDesktopServices, QFont, QIcon
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout,
    QFrame, QHBoxLayout, QLabel, QListWidget, QMainWindow, QMessageBox, QPushButton,
    QProgressBar, QScrollArea, QScrollBar, QSlider, QSplitter, QTabWidget, QTextEdit,
    QVBoxLayout, QWidget, QAbstractSpinBox, QLineEdit,
)

from .jobs import Worker
from .auto_mosaic import AutomaticMosaic
from .component_dialog import ComponentDialog
from .components import ComponentManager
from .layer_editor import LayerEditing
from .layer_widgets import LayerInspector, LayerTimeline, StickerList
from .media import probe
from .model import Edit, Project, Rect
from .render import Exporter, render_preview
from .widgets import Timeline, TimeSpinBox, VideoCanvas, time_text
from .stickers import StickerLibrary


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
QDoubleSpinBox, QComboBox, QLineEdit { background: #101923; border: 1px solid #35465c; border-radius: 5px; padding: 5px; }
QComboBox QAbstractItemView { background: #1b2939; selection-background-color: #315f56; }
QSlider::groove:horizontal { background: #2e3c50; height: 5px; border-radius: 2px; }
QSlider::handle:horizontal { background: #73dfbc; width: 12px; margin: -4px 0; border-radius: 6px; }
QProgressBar { border: none; background: #202e40; border-radius: 4px; height: 10px; text-align: center; }
QProgressBar::chunk { background: #59ba9d; border-radius: 4px; }
QScrollArea { border: none; background: transparent; }
QScrollBar:horizontal { height: 10px; background: #172231; }
QScrollBar::handle:horizontal { background: #42566f; min-width: 25px; border-radius: 4px; }
QScrollBar:vertical { width: 10px; background: #172231; }
QScrollBar::handle:vertical { background: #42566f; min-height: 25px; border-radius: 4px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; background: transparent; }
QTabWidget::pane { border: 1px solid #283548; background: #171f2b; border-radius: 6px; }
QTabBar::tab { background: #202b3a; padding: 7px 12px; }
QTabBar::tab:selected { background: #31534f; color: #b8ffe7; }
QWidget#layerProperties { background: #171f2b; }
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


class MainWindow(QMainWindow, LayerEditing, AutomaticMosaic):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Q-Pixelate · 长视频局部打码')
        self.resize(1390, 960)
        self.setMinimumSize(1040, 760)
        self.setStyleSheet(STYLE)
        self.components = ComponentManager()
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
        self._drafts = {}
        self._layer_id = None
        self._layer_target = 'sticker'
        self._transform_before = None
        self._mask_context = None
        self._loading_form = False
        self._range_locked = False
        self.mask_points = []
        self.sticker_library = StickerLibrary()
        self._arrow_key = None
        self._shuttling = False
        self.hold_timer = QTimer(self)
        self.hold_timer.setSingleShot(True)
        self.hold_timer.setInterval(300)
        self.hold_timer.timeout.connect(self.begin_shuttle)
        self.shuttle_timer = QTimer(self)
        self.shuttle_timer.setInterval(60)
        self.shuttle_timer.timeout.connect(self.shuttle_tick)
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
        self.settings_button = button('设置', self.open_components)
        self.settings_button.setToolTip('环境与组件')
        for widget in (self.open_button, self.load_button, self.save_button, self.settings_button, self.export_button):
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
        self.mode_label = label('将素材库贴纸拖到画面。遮罩轮廓可在画面编辑，实际模糊和羽化可通过效果预览检查。')
        self.mode_label.setWordWrap(True)
        preview_layout.addWidget(self.mode_label)
        self.canvas = VideoCanvas()
        preview_layout.addWidget(self.canvas, 1)
        playback = QHBoxLayout()
        playback.addWidget(button('◀ 一帧', lambda: self.step_frame(-1)))
        playback.addWidget(button('− N 秒', lambda: self.skip_seconds(-1)))
        self.play_button = button('▶ 播放', self.toggle_play)
        playback.addWidget(self.play_button)
        playback.addWidget(button('一帧 ▶', lambda: self.step_frame(1)))
        playback.addWidget(button('＋ N 秒', lambda: self.skip_seconds(1)))
        self.seek_seconds = QDoubleSpinBox()
        self.seek_seconds.setRange(.1, 600)
        self.seek_seconds.setValue(5)
        self.seek_seconds.setSuffix(' 秒')
        self.seek_seconds.setMaximumWidth(100)
        playback.addWidget(self.seek_seconds)
        self.reverse_button = button('按住 ◀ 3×', lambda: None)
        self.forward_button = button('按住 ▶ 3×', lambda: None)
        shuttle_controls = QHBoxLayout()
        for widget, direction in ((self.reverse_button, -1), (self.forward_button, 1)):
            widget.pressed.connect(lambda d=direction: self.start_shuttle(d))
            widget.released.connect(self.stop_shuttle)
            shuttle_controls.addWidget(widget)
        self.position_label = label('00:00:00.000 / 00:00:00.000')
        playback.addStretch()
        preview_layout.addLayout(playback)
        shuttle_controls.addStretch()
        shuttle_controls.addWidget(self.position_label)
        preview_layout.addLayout(shuttle_controls)
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
        self.clip_list.setMaximumHeight(140)
        self.clip_list.currentRowChanged.connect(self.select_edit)
        side_layout.addWidget(self.clip_list, 1)
        row = QHBoxLayout()
        row.addWidget(button('＋ 添加选区', self.add_selection, True))
        remove = button('删除片段', self.delete_edit)
        remove.setObjectName('danger')
        row.addWidget(remove)
        side_layout.addLayout(row)
        workflow = QHBoxLayout()
        self.new_button = button('选择新片段', self.new_selection)
        self.range_button = button('锁定片段长度', self.toggle_range_lock)
        workflow.addWidget(self.new_button)
        workflow.addWidget(self.range_button)
        side_layout.addLayout(workflow)
        self.workflow_label = label('选定片段 → 锁定长度 → 修改 → 确认退出')
        self.workflow_label.setWordWrap(True)
        side_layout.addWidget(self.workflow_label)
        form = QFormLayout()
        self.clip_form = form
        form.setSpacing(9)
        self.in_time = TimeSpinBox()
        self.out_time = TimeSpinBox()
        form.addRow('入点', self.in_time)
        form.addRow('出点', self.out_time)
        self.in_time.valueChanged.connect(self.selection_inputs_changed)
        self.out_time.valueChanged.connect(self.selection_inputs_changed)
        self.effect = QComboBox()
        self.effect.addItems(['全屏模糊', '区域模糊', '贴纸遮挡', '不遮挡（仅变速）'])
        self.effect.currentIndexChanged.connect(self.effect_changed)
        form.addRow('效果', self.effect)
        self.strength = QSlider(Qt.Orientation.Horizontal)
        self.strength.setRange(1, 100)
        self.strength.setValue(20)
        self.strength.valueChanged.connect(self.form_changed)
        form.addRow('模糊强度', self.strength)
        self.speed = QDoubleSpinBox()
        self.speed.setRange(.25, 4)
        self.speed.setSingleStep(.25)
        self.speed.setSuffix(' ×')
        self.speed.setValue(1)
        self.speed.valueChanged.connect(self.form_changed)
        form.addRow('片段速度', self.speed)
        self.shape = QComboBox()
        self.shape.addItems(['方框', '圆形 / 椭圆', '套索'])
        self.shape.currentIndexChanged.connect(self.shape_changed)
        form.addRow('遮罩形状', self.shape)
        self.feather = QDoubleSpinBox()
        self.feather.setRange(0, 20)
        self.feather.setSuffix(' %')
        self.feather.setToolTip('按原画短边百分比向遮罩内部羽化；0 为硬边。')
        self.feather.valueChanged.connect(self.form_changed)
        form.addRow('羽化范围', self.feather)
        self.redraw_button = button('重新绘制遮罩', self.canvas.redraw)
        form.addRow(self.redraw_button)
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
        self.image_button = button('导入贴纸到素材库…', self.choose_image)
        self.sticker_library_list = StickerList()
        library = QWidget()
        library_layout = QVBoxLayout(library)
        library_layout.addWidget(self.image_button)
        library_layout.addWidget(label('从这里拖到视频画面。'))
        library_layout.addWidget(self.sticker_library_list, 1)
        self.layer_panel = LayerInspector()
        self.layer_tab = QWidget()
        self.layer_tab.setObjectName('layerProperties')
        layer_layout = QVBoxLayout(self.layer_tab)
        layer_layout.addWidget(self.layer_panel)
        self.layer_empty = label('先在选区轨创建或选中一个选区。')
        layer_layout.addWidget(self.layer_empty)
        layer_layout.addStretch()
        self.sticker_combo = QComboBox()
        self.sticker_combo.currentIndexChanged.connect(self.sticker_selected)
        side_layout.addWidget(self.sticker_combo)
        self.image_label = label('尚未选择贴纸')
        self.image_label.setWordWrap(True)
        side_layout.addWidget(self.image_label)
        actions_panel = QWidget()
        actions_layout = QVBoxLayout(actions_panel)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.confirm_button = button('确认并锁定片段', self.apply_edit, True)
        self.unlock_button = button('解锁并编辑', self.unlock_edit)
        row.addWidget(self.confirm_button)
        row.addWidget(self.unlock_button)
        actions_layout.addLayout(row)
        actions_layout.addWidget(button('预览当前片段效果', self.preview_edit))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(side)
        layer_properties = QScrollArea()
        layer_properties.setWidgetResizable(True)
        layer_properties.setWidget(self.layer_tab)
        self.editor_tabs = QTabWidget()
        self.editor_tabs.addTab(scroll, '选区')
        self.editor_tabs.addTab(library, '贴纸素材')
        self.editor_tabs.addTab(layer_properties, '图层属性')
        self.editor_tabs.setCurrentWidget(library)
        self.setup_detection_tab()
        editor_panel = QWidget()
        editor_panel.setMinimumWidth(375)
        editor_layout = QVBoxLayout(editor_panel)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.addWidget(self.editor_tabs, 1)
        editor_layout.addWidget(actions_panel)
        splitter.addWidget(editor_panel)
        splitter.setSizes([950, 380])
        timeline_header = QHBoxLayout()
        timeline_header.addWidget(label('源视频时间轴', 'section'))
        timeline_header.addWidget(label('  I / O 设入出点 · 空格播放 / 暂停'))
        timeline_header.addStretch()
        self.in_button = button('设为入点 I', self.set_in)
        self.out_button = button('设为出点 O', self.set_out)
        timeline_header.addWidget(self.in_button)
        timeline_header.addWidget(self.out_button)
        timeline_header.addWidget(label('缩放'))
        self.zoom = QSlider(Qt.Orientation.Horizontal)
        self.zoom.setRange(1, 100)
        self.zoom.setMaximumWidth(130)
        timeline_header.addWidget(self.zoom)
        body.addLayout(timeline_header)
        self.timeline = Timeline()
        body.addWidget(self.timeline)
        layer_scroll = QScrollArea()
        layer_scroll.setWidgetResizable(True)
        layer_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        layer_scroll.setFixedHeight(76)
        self.layer_timeline = LayerTimeline(self.timeline)
        layer_scroll.setWidget(self.layer_timeline)
        body.addWidget(layer_scroll)
        self.scrollbar = QScrollBar(Qt.Orientation.Horizontal)
        self.scrollbar.setRange(0, 10000)
        self.scrollbar.setPageStep(10000)
        body.addWidget(self.scrollbar)
        self.zoom.valueChanged.connect(self.timeline.set_zoom)
        self.timeline.viewChanged.connect(self.sync_scrollbar)
        self.scrollbar.valueChanged.connect(self.scroll_timeline)
        self.timeline.selectionChanged.connect(self.set_selection)
        self.timeline.seekRequested.connect(self.seek)
        self.timeline.editActivated.connect(self.activate_from_timeline)
        self.timeline.editDragged.connect(self.drag_selection)
        self.timeline.rangeCreated.connect(self.create_ranges)
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
        self.canvas.pointsChanged.connect(self.canvas_points_changed)
        self.connect_layer_editor()
        QApplication.instance().installEventFilter(self)
        self.refresh_stickers()
        self.effect_changed()
        self.update_edit_controls()
        self.update_enabled()

    def update_enabled(self):
        busy = self.worker is not None and self.worker.isRunning()
        self.body.setEnabled(not busy and self.project is not None)
        self.open_button.setEnabled(not busy)
        self.load_button.setEnabled(not busy)
        self.settings_button.setEnabled(not busy)
        self.save_button.setEnabled(not busy and self.project is not None)
        self.export_button.setEnabled(not busy and self.project is not None)
        self.cancel_button.setVisible(busy)
        self.progress.setVisible(busy)

    def error(self, text):
        self.status.setText(text.splitlines()[0])
        if text != '任务已取消。':
            QMessageBox.warning(self, 'Q-Pixelate', text)

    def open_components(self):
        dialog = ComponentDialog(self.components, self)
        dialog.handoff.connect(self.handoff_component_action)
        dialog.exec()
        dialog.deleteLater()
        if not self._closing:
            self.sync_detection()

    def handoff_component_action(self, action, key, archive):
        launcher = os.environ.get('QPIXELATE_LAUNCHER')
        if not launcher or not Path(launcher).is_file():
            self.error('找不到启动器，请重新启动 Q-Pixelate。')
            return
        arguments = [launcher]
        if action == 'move-components':
            arguments.extend(['--move-components', archive])
            if key == 'cleanup':
                arguments.append('--remove-old-copy')
        elif action == 'move-program':
            arguments.append('--relocate-program')
        elif action == 'open-installer':
            arguments.extend(['--start-installer', archive])
        else:
            arguments.extend(['--component-action', action, key])
            if archive:
                arguments.append(archive)
        arguments.extend(['--wait-pid', str(os.getpid()), '--'])
        if self.close():
            try:
                subprocess.Popen(arguments)
            except OSError as exc:
                QMessageBox.warning(None, 'Q-Pixelate', f'无法打开组件启动器：{exc}')

    def start_job(self, task, callback):
        if self.worker is not None and self.worker.isRunning():
            return
        self.stop_shuttle()
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
        self.stop_shuttle()
        project.check_source()
        project.validate(info.duration)
        self._loading_form = True
        self._editing_row = -1
        self._drafts.clear()
        self.detection_batches.clear()
        self._range_locked = False
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
        self.timeline.minimum_span = 1/info.fps_float
        self.timeline.position = 0
        self.zoom.setValue(1)
        self.sync_scrollbar(0)
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
        self.status.setText(error or '视频已打开 · 在选区轨上右键拖动创建选区')
        self.position_changed(0)
        self.seek(0)
        self.update_edit_controls()
        self.update_enabled()

    def choose_project(self):
        path, _ = QFileDialog.getOpenFileName(self, '载入工程', '', 'Q-Pixelate 工程 (*.qpixel);;JSON (*.json)')
        if not path or not self.confirm_discard():
            return
        def load(runner):
            project = Project.load(path)
            info = probe(project.source, runner)
            for edit in project.edits:
                edit.promote_layers(info.width, info.height)
            return project, info
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
            if Path(path).resolve() in self.project.asset_paths():
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
        if (self._loading_form or self._syncing or not self.project
                or not 0 <= row < len(self.project.edits) or self.project.edits[row].locked):
            return
        current = self.current_edit()
        stored = self.project.edits[row]
        if self._active_detection_key != (current.start, current.end):
            self.detection_batches.pop(self._active_detection_key, None)
        if current != stored:
            self.changed()
        if current.layers is not None:
            self.stash_edit()
            edit = self._drafts.get(row, stored)
            self.timeline.edits[row] = edit
            self.clip_list.item(row).setText(self.edit_caption(row, edit))
            self.layer_timeline.update()
            self.sync_layer_editor()
        self.sync_detection()

    def set_selection(self, start, end):
        if self._range_locked and not self._loading_form:
            return
        blockers = [QSignalBlocker(self.in_time), QSignalBlocker(self.out_time)]
        self.in_time.setValue(start)
        self.out_time.setValue(end)
        self.timeline.selection = (start, end)
        self.timeline.update()
        self.form_changed()

    def selection_inputs_changed(self):
        if self._range_locked and not self._loading_form:
            blockers = [QSignalBlocker(self.in_time), QSignalBlocker(self.out_time)]
            self.in_time.setValue(self.timeline.selection[0])
            self.out_time.setValue(self.timeline.selection[1])
            return
        start, end = self.in_time.value(), self.out_time.value()
        if self.layer_edit() is not None:
            if (end-start < self.timeline.minimum_span-.0005 or any(
                    index != self._editing_row and start < edit.end-1e-8 and end > edit.start+1e-8
                    for index, edit in enumerate(self.timeline.edits))):
                with QSignalBlocker(self.in_time), QSignalBlocker(self.out_time):
                    self.in_time.setValue(self.timeline.selection[0])
                    self.out_time.setValue(self.timeline.selection[1])
                self.status.setText('选区至少需要一帧，且不能与其他选区重叠。')
                return
        self.timeline.selection = start, end
        self.timeline.update()
        self.form_changed()

    def current_edit(self):
        row = self._editing_row
        base = self._drafts.get(row, self.project.edits[row]) if self.project and 0 <= row < len(self.project.edits) else None
        locked = bool(base and base.locked)
        if base and base.layers is not None:
            start, end = self.timeline.selection
            delta = base.start-start
            layers = [replace(layer, start=layer.start+delta, end=layer.end+delta) for layer in base.layers] if delta else base.layers
            return replace(base, start=start, end=end, speed=self.speed.value(), range_locked=self._range_locked, layers=layers)
        return Edit(self.in_time.value(), self.out_time.value(), ['blur', 'region', 'image', 'none'][self.effect.currentIndex()],
                    Rect(*(s.value()/100 for s in self.coordinates)), self.strength.value(), self.image_path,
                    self.speed.value(), ['rect', 'ellipse', 'lasso'][self.shape.currentIndex()],
                    list(self.mask_points), self.feather.value(), self._range_locked, locked)

    def stash_edit(self):
        row = self._editing_row
        if row >= 0 and not self.selected_locked():
            edit = self.current_edit()
            if edit != self.project.edits[row]:
                self._drafts[row] = edit
            else:
                self._drafts.pop(row, None)
            self.timeline.edits[row] = self._drafts.get(row, self.project.edits[row])
            self.clip_list.item(row).setText(self.edit_caption(row, self.timeline.edits[row]))

    def activate_from_timeline(self, row):
        if row != self._editing_row:
            with QSignalBlocker(self.clip_list):
                self.clip_list.setCurrentRow(row)
            self.select_edit(row, keep_draft=True)

    def create_ranges(self, start, end):
        if not self.project or start >= end:
            return
        self.stash_edit()
        cursor, gaps = start, []
        for edit in sorted(self.timeline.edits, key=lambda edit: edit.start):
            if edit.end <= cursor:
                continue
            if edit.start >= end:
                break
            if cursor < edit.start:
                gaps.append((cursor, edit.start))
            cursor = max(cursor, edit.end)
        if cursor < end:
            gaps.append((cursor, end))
        minimum = self.timeline.minimum_span
        new = [Edit(a, b, 'none', layers=[]) for a, b in gaps if b-a >= minimum-1e-8]
        if not new:
            self.status.setText('拖动范围内没有至少一帧的空白时间。')
            return
        first = len(self.project.edits)
        self.project.edits.extend(new)
        self.refresh_edits(first)
        self.changed()

    def add_selection(self):
        self.create_ranges(*self.timeline.selection)

    def drag_selection(self, row, start, end, mode):
        if row != self._editing_row or self.selected_locked() or self._range_locked:
            return
        edit = self.current_edit()
        if edit.layers is not None and mode == 'left':
            delta = edit.start-start
            edit = replace(edit, layers=[replace(layer, start=layer.start+delta, end=layer.end+delta)
                                         for layer in edit.layers])
        self._drafts[row] = replace(edit, start=start, end=end)
        self.refresh_edits(row)
        self.changed()

    def add_edit(self):
        if not self.project:
            return
        if not self.commit_form():
            return
        edit = self.current_edit()
        edit.locked = False
        edit.range_locked = True
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
        if self.project.edits[row].locked:
            self.status.setText('片段已锁定；请先点击“解锁并编辑”。')
            return False
        edit = self.current_edit()
        edit.locked = True
        edit.range_locked = True
        try:
            edit.validate(self.info.duration)
            if any(index != row and edit.start < other.end-1e-8 and edit.end > other.start+1e-8
                   for index, other in enumerate(self.timeline.edits)):
                raise ValueError('修订片段重叠，请调整入点和出点。')
        except ValueError as exc:
            self.error(str(exc))
            return False
        self.project.edits[row] = edit
        self._drafts.pop(row, None)
        self.refresh_edits(row)
        self.changed()
        self.status.setText('修改已确认，已退出编辑并锁定片段。')
        return True

    def commit_form(self):
        self.stash_edit()
        if self._drafts:
            self.status.setText('有未确认的片段，请先选择带 * 的片段并确认。')
            return False
        return True

    def refresh_edits(self, selected=-1):
        self._editing_row = -1
        blocker = QSignalBlocker(self.clip_list)
        self.clip_list.clear()
        edits = [self._drafts.get(i, edit) for i, edit in enumerate(self.project.edits)]
        for i, edit in enumerate(edits):
            self.clip_list.addItem(self.edit_caption(i, edit))
        self.clip_list.setCurrentRow(selected)
        self.count_label.setText(f'{len(self.project.edits)} 段')
        self.timeline.edits = edits
        self.timeline.active_row = selected
        self.timeline.update()
        del blocker
        if selected >= 0:
            self.select_edit(selected)
        else:
            self.sync_detection()

    def edit_caption(self, row, edit):
        names = {'blur': '全屏模糊', 'region': '区域模糊', 'image': '贴纸遮挡', 'none': '仅变速'}
        summary = names[edit.effect] if edit.layers is None else f'{len(edit.layers)} 个图层'
        pending = ' *' if row in self._drafts else ''
        return (f'{row+1:02}{pending}  {time_text(edit.start)} → {time_text(edit.end)}\n'
                f'      {summary} · {edit.speed:g}× · {"已锁定" if edit.locked else "编辑中"}')

    def select_edit(self, row, keep_draft=False):
        if not self.project or row < 0 or row >= len(self.project.edits):
            return
        previous = self._editing_row
        if 0 <= previous < len(self.project.edits) and previous != row and not self._loading_form:
            pending = self.current_edit()
            if keep_draft or pending.layers is not None:
                self.stash_edit()
            elif not self.project.edits[previous].locked and pending != self.project.edits[previous]:
                blocker = QSignalBlocker(self.clip_list)
                self.clip_list.setCurrentRow(previous)
                self.status.setText('请先确认当前片段，再切换片段。')
                return
        edit = self._drafts.get(row, self.project.edits[row])
        was_loading = self._loading_form
        self._loading_form = True
        if previous != row or edit.locked:
            self.cancel_layer_interaction()
            self._layer_id = edit.layers[0].id if edit.layers else None
            self._layer_target = 'mask' if edit.layers and edit.layers[0].kind == 'blur' else 'sticker'
        self._editing_row = row
        self.timeline.active_row = row
        self._range_locked = edit.range_locked
        self.set_selection(edit.start, edit.end)
        self.effect.setCurrentIndex(['blur', 'region', 'image', 'none'].index(edit.effect))
        self.strength.setValue(edit.strength)
        self.speed.setValue(edit.speed)
        self.shape.setCurrentIndex(['rect', 'ellipse', 'lasso'].index(edit.shape))
        self.mask_points = list(edit.points)
        self.feather.setValue(edit.feather)
        self.image_path = edit.image
        self.image_label.setText(Path(edit.image).name if edit.image else '尚未选择贴纸')
        self.canvas_rect_changed(edit.rect)
        self.canvas.draw_new = False
        self._loading_form = was_loading
        self.update_edit_controls()

    def delete_edit(self):
        row = self.clip_list.currentRow()
        if self.project and row >= 0 and not self.project.edits[row].locked:
            del self.project.edits[row]
            self._drafts = {i-(i > row): edit for i, edit in self._drafts.items() if i != row}
            self.refresh_edits(min(row, len(self.project.edits)-1))
            if not self.project.edits:
                self._range_locked = False
                self.update_edit_controls()
            self.changed()

    def effect_changed(self, index=None):
        index = self.effect.currentIndex()
        self.region_panel.setVisible(index in (1, 2))
        self.image_label.setVisible(index == 2)
        self.sticker_combo.setVisible(index == 2)
        self.shape.setEnabled(index == 1)
        self.feather.setEnabled(index == 1)
        self.redraw_button.setEnabled(index in (1, 2))
        self.strength.setEnabled(index != 2)
        self.canvas.redraw()
        self.update_canvas_mask()
        self.form_changed()
        if hasattr(self, 'timeline'):
            self.update_edit_controls()

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
        paths, _ = QFileDialog.getOpenFileNames(self, '导入贴纸（可多选）', '', '图片 (*.png *.jpg *.jpeg *.webp *.bmp)')
        try:
            saved = [self.sticker_library.add(path) for path in paths]
            if saved:
                self.refresh_stickers(saved[-1])
        except (OSError, ValueError) as exc:
            self.error(str(exc))

    def seek(self, seconds):
        paused = self.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState
        self.player.setPosition(round(seconds*1000))
        if paused and self.project:
            self._pause_on_frame = True
            self.player.play()

    def skip_seconds(self, direction):
        if self.info:
            self.player.pause()
            self.seek(max(0, min(self.info.duration, self.player.position()/1000+direction*self.seek_seconds.value())))

    def start_shuttle(self, direction):
        if not self.info or self.worker:
            return
        self._shuttling = True
        self._shuttle_volume = self.audio.volume()
        self.audio.setVolume(0)
        self._shuttle_direction = direction
        self._shuttle_start = self.player.position()/1000
        self._shuttle_time = time.monotonic()
        self.player.pause()
        self.shuttle_timer.start()

    def begin_shuttle(self):
        if self._arrow_key is not None:
            self.start_shuttle(-1 if self._arrow_key == Qt.Key_Left else 1)

    def shuttle_tick(self):
        if self.info:
            target = self._shuttle_start + self._shuttle_direction*3*(time.monotonic()-self._shuttle_time)
            self.seek(max(0, min(self.info.duration, target)))

    def stop_shuttle(self):
        self.hold_timer.stop()
        self.shuttle_timer.stop()
        if self._shuttling and hasattr(self, 'player'):
            self.player.pause()
            self.audio.setVolume(self._shuttle_volume)
        self._arrow_key = None
        self._shuttling = False

    def selected_locked(self):
        return bool(self.project and 0 <= self._editing_row < len(self.project.edits)
                    and self.project.edits[self._editing_row].locked)

    def update_edit_controls(self):
        locked = self.selected_locked()
        layered = self.layer_edit() is not None
        legacy = self._editing_row >= 0 and not layered
        self.timeline.selection_locked = self._range_locked or locked
        for widget in (self.in_time, self.out_time, self.in_button, self.out_button):
            widget.setEnabled(not self.timeline.selection_locked)
        self.range_button.setEnabled(not locked)
        self.range_button.setText('解锁片段长度' if self._range_locked else '锁定片段长度')
        self.confirm_button.setEnabled(not locked and self._editing_row >= 0 and (layered or self._range_locked))
        self.unlock_button.setEnabled(locked)
        editable = not locked and self._range_locked
        index = self.effect.currentIndex()
        for widget in (self.effect, self.strength, self.shape, self.feather, self.redraw_button):
            self.clip_form.setRowVisible(widget, legacy)
        self.effect.setEnabled(editable)
        self.speed.setEnabled(not locked and (layered or editable))
        self.strength.setEnabled(editable and index in (0, 1))
        self.shape.setEnabled(editable and index == 1)
        self.feather.setEnabled(editable and index == 1)
        self.redraw_button.setEnabled(editable and index in (1, 2))
        self.region_panel.setEnabled(editable)
        self.region_panel.setVisible(legacy and index in (1, 2) and not (index == 1 and self.shape.currentIndex() == 2))
        self.image_button.setEnabled(True)
        self.sticker_combo.setVisible(legacy and index == 2)
        self.image_label.setVisible(legacy and index == 2)
        self.sticker_combo.setEnabled(editable)
        self.workflow_label.setText('片段已确认并锁定 · 需要修改时先解锁' if locked else
                                    '编辑图层后可确认并锁定' if layered else
                                    '长度已锁定 → 修改 → 确认并锁定片段' if self._range_locked else
                                    '在选区轨右键拖动，或输入入点和出点后添加选区')
        self.timeline.update()
        self.sync_layer_editor()
        self.sync_detection()

    def toggle_range_lock(self):
        if self.selected_locked():
            return
        if self.in_time.value() >= self.out_time.value():
            self.status.setText('请先选择有效的片段范围。')
            return
        self._range_locked = not self._range_locked
        self.update_edit_controls()
        self.form_changed()

    def unlock_edit(self):
        if self.selected_locked():
            self.project.edits[self._editing_row].locked = False
            if self.project.edits[self._editing_row].layers is not None:
                self.project.edits[self._editing_row].range_locked = False
            self.refresh_edits(self._editing_row)
            self.changed()

    def new_selection(self):
        self.stash_edit()
        blocker = QSignalBlocker(self.clip_list)
        self.clip_list.setCurrentRow(-1)
        self._editing_row = -1
        self.cancel_layer_interaction()
        self._layer_id = None
        self.timeline.active_row = -1
        self._range_locked = False
        self._loading_form = True
        self.effect.setCurrentIndex(0)
        self.speed.setValue(1)
        self.shape.setCurrentIndex(0)
        self.mask_points = []
        self.feather.setValue(0)
        self._loading_form = False
        start = min(self.player.position()/1000, max(0, self.info.duration-1/self.info.fps_float))
        self.set_selection(start, min(self.info.duration, start+5))
        self.update_edit_controls()

    def shape_changed(self, *args):
        self.mask_points = []
        self.canvas.redraw()
        self.update_canvas_mask()
        self.form_changed()
        if hasattr(self, 'timeline'):
            self.update_edit_controls()

    def canvas_points_changed(self, points):
        self.mask_points = points
        self.update_canvas_mask()
        self.form_changed()

    def refresh_stickers(self, selected=None):
        blocker = QSignalBlocker(self.sticker_combo)
        self.sticker_combo.clear()
        self.sticker_combo.addItem('选择素材库贴纸…', '')
        self.sticker_library_list.clear()
        for path in self.sticker_library.items():
            name = Path(path).stem.rsplit('-', 1)[0]
            self.sticker_combo.addItem(QIcon(path), name, path)
            self.sticker_library_list.add_asset(path, name)
        if selected:
            self.sticker_combo.setCurrentIndex(self.sticker_combo.findData(selected))
            self.sticker_library_list.setCurrentItem(self.sticker_library_list.find_asset(selected))
        del blocker
        if selected:
            self.sticker_selected()

    def sticker_selected(self, *args):
        path = self.sticker_combo.currentData()
        if path:
            self.image_path = path
            self.image_label.setText(Path(path).stem.rsplit('-', 1)[0])
            self.image_label.setToolTip(path)
            self.form_changed()

    def on_frame(self, frame):
        self.canvas.set_frame(frame)
        if self._pause_on_frame and frame.isValid():
            self._pause_on_frame = False
            self.player.pause()

    def set_in(self):
        if self.info and not self._range_locked:
            self.in_time.setValue(self.player.position()/1000)

    def set_out(self):
        if self.info and not self._range_locked:
            self.out_time.setValue(self.player.position()/1000)

    def toggle_play(self):
        if not self.project:
            return
        self._pause_on_frame = False
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
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState and not self._pause_on_frame and (seconds > self.timeline.view_start+self.timeline.visible or seconds < self.timeline.view_start):
            self.timeline.view_start = max(0, min(self.info.duration-self.timeline.visible, seconds-self.timeline.visible*.1))
            self.sync_scrollbar(self.timeline.view_start)
        self.timeline.update()
        self.layer_timeline.update()
        self.update_canvas_mask(seconds)

    def playback_state_changed(self, state):
        self.play_button.setText('❚❚ 暂停' if state == QMediaPlayer.PlaybackState.PlayingState else '▶ 播放')
        self.update_canvas_mask()

    def update_canvas_mask(self, seconds=None):
        # Playback follows the active saved edit; paused editing follows the form.
        playing = hasattr(self, 'player') and self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState and not self._pause_on_frame
        seconds = self.timeline.position if seconds is None else seconds
        selected = self.layer_edit()
        frame_time = round(seconds*self.info.fps_float)/self.info.fps_float if self.info else seconds
        active_row = next((i for i, edit in enumerate(self.timeline.edits) if edit.start <= seconds < edit.end), -1)
        if 0 < abs(frame_time-seconds) < .00051:
            active_row = next((i for i, edit in enumerate(self.timeline.edits)
                               if edit.start <= frame_time < edit.end and edit.layers and
                               any(layer.kind == 'mosaic' for layer in edit.layers)), active_row)
        active = self.timeline.edits[active_row] if self.project and active_row >= 0 else None
        self._canvas_layer_row = active_row
        self.canvas.layers = []
        self.canvas.layer_target = None
        self.canvas.detection_boxes = self.detection_boxes_at(seconds)
        if active and active.layers is not None:
            self.canvas.layer_source_time = frame_time-active.start
            self.canvas.layers = [(i, layer) for i, layer in enumerate(active.layers)
                                  if (span := active.layer_span(layer))[0] <= (
                                      frame_time if layer.kind == 'mosaic' else seconds) < span[1]]
        if selected:
            if not playing and not selected.locked:
                layer = self.selected_layer(selected)
                transform = self.target_transform(layer)
                if transform and layer.kind != 'mosaic':
                    mask = layer.mask if self._layer_target == 'mask' else None
                    self.canvas.layer_target = transform, mask
            self.canvas.show_mask = False
            self.canvas.editable = False
            self.canvas.update()
            return
        if playing and self.project:
            seconds = self.player.position()/1000 if seconds is None else seconds
            edit = next((e for e in self.project.edits if e.start <= seconds < e.end), None)
            self.canvas.show_mask = edit is not None and edit.layers is None and edit.effect != 'none'
            self.canvas.editable = False
            if edit:
                self.canvas.rect = copy.copy(edit.rect)
                self.canvas.shape = edit.shape if edit.effect == 'region' else 'rect'
                self.canvas.points = list(edit.points)
                self.canvas.fullscreen_mask = edit.effect == 'blur'
                self.canvas.effect_label = '播放定位 · 当前片段遮挡范围（原画）'
        else:
            index = self.effect.currentIndex()
            self.canvas.rect = Rect(*(s.value()/100 for s in self.coordinates))
            self.canvas.editable = index in (1, 2) and self._range_locked and not self.selected_locked()
            self.canvas.shape = ['rect', 'ellipse', 'lasso'][self.shape.currentIndex()] if index == 1 else 'rect'
            self.canvas.points = list(self.mask_points)
            self.canvas.fullscreen_mask = index == 0
            self.canvas.show_mask = self.project is not None and self._editing_row >= 0 and index != 3
            self.canvas.effect_label = '原画编辑 · 全屏模糊区域' if index == 0 else '原画编辑 · 拖动画框 / 内部移动 / 右下角缩放'
        self.canvas.update()

    def sync_scrollbar(self, start):
        blocker = QSignalBlocker(self.scrollbar)
        available = max(0, self.timeline.duration-self.timeline.visible)
        self.scrollbar.setRange(0, 10000 if available > .000001 else 0)
        self.scrollbar.setEnabled(available > .000001)
        maximum = max(.001, available)
        self.scrollbar.setValue(round(start/maximum*10000))
        self.scrollbar.setPageStep(max(1, round(10000/max(.001, self.timeline.zoom-1))))
        self.layer_timeline.update()

    def scroll_timeline(self, value):
        self.timeline.view_start = value/10000*max(0, self.timeline.duration-self.timeline.visible)
        self.timeline.update()
        self.layer_timeline.update()

    def preview_edit(self):
        row = self.clip_list.currentRow()
        if not self.project or row < 0:
            self.status.setText('请先添加并选择一个修订片段。')
            return
        project = copy.deepcopy(Project(self.project.source, self.timeline.edits, self.project.fingerprint))
        if not project.edits[row].locked:
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
        super().keyPressEvent(event)

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.WindowDeactivate, QEvent.Type.ApplicationDeactivate) and (obj is self or obj is QApplication.instance()):
            self.stop_shuttle()
        if not isinstance(obj, QWidget) or obj.window() is not self:
            return False
        if event.type() not in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            return False
        if not self.project or self.worker:
            return False
        key = event.key()
        if event.type() == QEvent.Type.KeyRelease and key == self._arrow_key:
            if not event.isAutoRepeat():
                self.stop_shuttle()
            return True
        focus = QApplication.focusWidget()
        if event.type() == QEvent.Type.KeyPress:
            if key == Qt.Key_T and event.modifiers() == Qt.ControlModifier and self.begin_transform():
                return True
            if focus is self.canvas and key == Qt.Key_Escape and self.canvas.mask_shape is not None:
                self.cancel_layer_interaction()
                self.update_canvas_mask()
                self.status.setText('已取消绘制遮罩。')
                return True
            if self.canvas.free_transform and key in (Qt.Key_Escape, Qt.Key_Return, Qt.Key_Enter):
                geometry_focus = next((spin for spin in self.layer_panel.geometry
                                       if spin is focus or (focus and spin.isAncestorOf(focus))), None)
                if focus is self.canvas or geometry_focus:
                    if geometry_focus and key != Qt.Key_Escape:
                        geometry_focus.interpretText()
                    self.finish_transform(key == Qt.Key_Escape)
                    return True
        if isinstance(focus, (QLineEdit, QAbstractSpinBox, QTextEdit, QComboBox)):
            return False
        if key not in (Qt.Key_Space, Qt.Key_Left, Qt.Key_Right, Qt.Key_I, Qt.Key_O, Qt.Key_Delete):
            return False
        if event.modifiers() & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier):
            return False
        if event.type() == QEvent.Type.KeyPress and not event.isAutoRepeat():
            if key == Qt.Key_Space:
                self.stop_shuttle()
                self.toggle_play()
            elif key in (Qt.Key_Left, Qt.Key_Right):
                direction = -1 if key == Qt.Key_Left else 1
                if event.modifiers() & Qt.ShiftModifier:
                    self.skip_seconds(direction)
                else:
                    self.stop_shuttle()
                    self.step_frame(direction)
                    self._arrow_key = key
                    self.hold_timer.start()
            elif key == Qt.Key_I:
                self.set_in()
            elif key == Qt.Key_O:
                self.set_out()
            elif key == Qt.Key_Delete:
                in_layers = focus in (self.canvas, self.layer_timeline) or (
                    focus is not None and self.layer_panel.isAncestorOf(focus))
                if in_layers and self.layer_edit() is not None:
                    self.delete_layer()
                elif focus in (self.clip_list, self.timeline):
                    self.delete_edit()
        return True

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and not self.worker:
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls and urls[0].isLocalFile():
            self.open_video(urls[0].toLocalFile())

    def closeEvent(self, event):
        self.stop_shuttle()
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

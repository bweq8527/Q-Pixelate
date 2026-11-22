from __future__ import annotations

import copy
import os
import subprocess
import sys
import time
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, QSignalBlocker, QTimer, QEvent, QPointF, QItemSelectionModel
from PySide6.QtGui import QDesktopServices, QFont, QIcon
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFileDialog, QFormLayout,
    QFrame, QHBoxLayout, QLabel, QListWidget, QMainWindow, QMessageBox, QPushButton,
    QProgressBar, QScrollArea, QScrollBar, QSplitter, QTabWidget, QTextEdit,
    QVBoxLayout, QWidget, QAbstractSpinBox, QLineEdit, QAbstractItemView, QMenu,
)

from .jobs import Worker
from .auto_mosaic import AutomaticMosaic
from .component_dialog import ComponentDialog
from .components import ComponentManager
from .layer_editor import LayerEditing
from .layer_widgets import LayerInspector, LayerTimeline, StickerList
from .media import probe
from .model import Edit, Project, Rect, frame_bounds
from .render import Exporter, render_preview
from .parameter_widgets import ParamControl, WheelSafeComboBox
from .preview_widgets import EDIT_COLORS, PreviewProgress
from .widgets import Timeline, VideoCanvas, time_text
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
QMenu { background: #1b2939; border: 1px solid #35475f; padding: 4px; }
QMenu::item { padding: 6px 20px; }
QMenu::item:selected { background: #315f56; }
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
QWidget#layerProperties, QWidget#effectsPanel { background: #171f2b; }
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
    def __init__(self, path, parent, *, whole=False, spans=()):
        super().__init__(parent)
        self.setWindowTitle(('成片预览' if whole else '片段预览') + ' · Q-Pixelate')
        self.resize(960, 650)
        layout = QVBoxLayout(self)
        layout.addWidget(label('已渲染预览 · 包含画面效果、删段与倍速，预览宽度最高 960px'))
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
        self.progress = PreviewProgress(spans)
        self.progress.setEnabled(False)
        self.position_label = label('')
        self._resume_after_scrub = False
        self._scrub_timer = QTimer(self)
        self._scrub_timer.setSingleShot(True)
        self._scrub_timer.setInterval(40)
        self._scrub_timer.timeout.connect(self._seek_to_progress)
        self.progress.sliderPressed.connect(self._start_scrub)
        self.progress.sliderReleased.connect(self._finish_scrub)
        self.progress.valueChanged.connect(self._progress_changed)
        self.player.durationChanged.connect(self._update_duration)
        self.player.seekableChanged.connect(self._update_duration)
        self.player.positionChanged.connect(self._update_position)
        layout.addWidget(self.progress)
        if self.progress.kinds:
            legend = '　'.join(
                f'<span style="color: {color}">■</span> {name}'
                for name, color in (EDIT_COLORS[kind] for kind in self.progress.kinds))
            legend_label = label(legend)
            legend_label.setTextFormat(Qt.TextFormat.RichText)
            legend_label.setWordWrap(True)
            layout.addWidget(legend_label)
        row = QHBoxLayout()
        row.addWidget(button('播放 / 暂停', self.toggle))
        row.addWidget(button('重新播放', lambda: (self.player.setPosition(0), self.player.play())))
        row.addStretch()
        row.addWidget(self.position_label)
        row.addWidget(button('关闭预览', self.close))
        layout.addLayout(row)
        self._update_duration()
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.player.play()

    def _update_duration(self):
        duration = self.player.duration()
        with QSignalBlocker(self.progress):
            self.progress.setRange(0, duration)
        self.progress.setEnabled(duration > 0 and self.player.isSeekable())
        self._update_position(self.player.position())

    def _show_time(self, position):
        self.position_label.setText(f'{time_text(position / 1000)} / {time_text(self.progress.maximum() / 1000)}')

    def _update_position(self, position):
        if not self.progress.isSliderDown():
            with QSignalBlocker(self.progress):
                self.progress.setValue(position)
            self._show_time(position)

    def _seek_to_progress(self):
        self.player.setPosition(self.progress.value())

    def _progress_changed(self, position):
        self._show_time(position)
        if not self.progress.isSliderDown():
            self._seek_to_progress()
        elif not self._scrub_timer.isActive():
            self._scrub_timer.start()

    def _start_scrub(self):
        self._resume_after_scrub = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        if self._resume_after_scrub:
            self.player.pause()

    def _finish_scrub(self):
        self._scrub_timer.stop()
        self._seek_to_progress()
        if self._resume_after_scrub:
            self.player.play()
        self._resume_after_scrub = False

    def toggle(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def done(self, result):
        self._scrub_timer.stop()
        self.player.stop()
        self.player.setSource(QUrl())
        super().done(result)


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
        self._clip_selection_anchor = -1
        self._drafts = {}
        self._layer_id = None
        self._layer_target = 'sticker'
        self._transform_before = None
        self._mask_context = None
        self._tracking_request = None
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
        self.preview_workspaces = {}
        self.preview_cleanup = ExitStack()
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
        self.library_button = button('贴纸素材', self.show_sticker_library)
        self.export_button = button('缝合导出  ↗', self.choose_export, True)
        self.settings_button = button('设置', self.open_components)
        self.settings_button.setToolTip('环境与组件')
        for widget in (self.open_button, self.load_button, self.save_button, self.library_button,
                       self.settings_button, self.export_button):
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
        self.seek_seconds = ParamControl()
        self.seek_seconds.setRange(.1, 600)
        self.seek_seconds.setValue(5)
        self.seek_seconds.setSuffix(' 秒')
        self.seek_seconds.setMaximumWidth(235)
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
        title.addWidget(label('随时添加选区', 'section'))
        title.addStretch()
        self.count_label = label('0 段')
        title.addWidget(self.count_label)
        side_layout.addLayout(title)
        self.clip_list = QListWidget()
        self.clip_list.setMinimumHeight(100)
        self.clip_list.setMaximumHeight(140)
        self.clip_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.clip_list.setToolTip('Ctrl 加选或取消，Shift 连选。')
        self.clip_list.itemSelectionChanged.connect(self.sync_clip_selection)
        self.clip_list.currentRowChanged.connect(self.sync_clip_selection)
        side_layout.addWidget(self.clip_list, 1)
        row = QHBoxLayout()
        row.addWidget(button('＋ 添加选区', self.add_selection, True))
        self.remove_clips_button = button('移除选区设置', self.delete_edit)
        self.remove_clips_button.setToolTip('移除选中的选区设置。要从成片剪掉音画，请使用「直接删除段落」。')
        self.remove_clips_button.setObjectName('danger')
        self.remove_clips_button.setEnabled(False)
        row.addWidget(self.remove_clips_button)
        side_layout.addLayout(row)
        workflow = QHBoxLayout()
        self.new_button = button('选择新片段', self.new_selection)
        self.range_button = button('锁定片段长度', self.toggle_range_lock)
        workflow.addWidget(self.new_button)
        workflow.addWidget(self.range_button)
        side_layout.addLayout(workflow)
        self.workflow_label = label('在时间轴右键拖动，或输入入点和出点。')
        self.workflow_label.setWordWrap(True)
        side_layout.addWidget(self.workflow_label)
        form = QFormLayout()
        self.clip_form = form
        form.setSpacing(9)
        self.in_time = ParamControl(time=True)
        self.out_time = ParamControl(time=True)
        form.addRow('入点', self.in_time)
        form.addRow('出点', self.out_time)
        self.in_time.valueChanged.connect(lambda _: self.selection_inputs_changed(self.in_time))
        self.out_time.valueChanged.connect(lambda _: self.selection_inputs_changed(self.out_time))
        self.effect = WheelSafeComboBox()
        self.effect.addItems(['全屏模糊', '区域模糊', '贴纸遮挡', '不遮挡（仅变速）'])
        self.effect.currentIndexChanged.connect(self.effect_changed)
        form.addRow('效果', self.effect)
        self.strength = ParamControl(decimals=0)
        self.strength.setRange(1, 100)
        self.strength.setValue(20)
        self.strength.valueChanged.connect(self.form_changed)
        form.addRow('模糊强度', self.strength)
        self.speed = ParamControl()
        self.speed.setRange(.25, 4)
        self.speed.setSuffix(' ×')
        self.speed.setValue(1)
        self.speed.valueChanged.connect(self.form_changed)
        form.addRow('片段速度', self.speed)
        self.shape = WheelSafeComboBox()
        self.shape.addItems(['方框', '圆形 / 椭圆', '套索'])
        self.shape.currentIndexChanged.connect(self.shape_changed)
        form.addRow('遮罩形状', self.shape)
        self.feather = ParamControl()
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
            spin = ParamControl()
            spin.setRange(0 if len(self.coordinates)<2 else .1, 100)
            spin.setValue(value)
            spin.valueChanged.connect(self.coordinate_changed)
            self.coordinates.append(spin)
            region_layout.addRow(name, spin)
        side_layout.addWidget(self.region_panel)
        self.image_button = button('导入贴纸到素材库…', self.choose_image)
        self.sticker_library_list = StickerList()
        self.library_dialog = QDialog(self)
        self.library_dialog.setWindowTitle('贴纸素材 · Q-Pixelate')
        self.library_dialog.resize(420, 440)
        library_layout = QVBoxLayout(self.library_dialog)
        library_layout.addWidget(self.image_button)
        library_layout.addWidget(label('选中素材后可添加贴纸，也可拖到视频画面。'))
        library_layout.addWidget(self.sticker_library_list, 1)
        self.layer_panel = LayerInspector()
        self.layer_tab = QWidget()
        self.layer_tab.setObjectName('layerProperties')
        layer_layout = QVBoxLayout(self.layer_tab)
        layer_layout.addWidget(self.layer_panel)
        self.layer_empty = label('先在选区轨创建或选中一个选区。')
        layer_layout.addWidget(self.layer_empty)
        layer_layout.addStretch()
        self.sticker_combo = WheelSafeComboBox()
        self.sticker_combo.currentIndexChanged.connect(self.sticker_selected)
        side_layout.addWidget(self.sticker_combo)
        self.image_label = label('尚未选择贴纸')
        self.image_label.setWordWrap(True)
        side_layout.addWidget(self.image_label)
        actions_panel = QWidget()
        actions_layout = QVBoxLayout(actions_panel)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.confirm_button = button('确认修改', self.apply_edit, True)
        self.unlock_button = button('解锁旧片段', self.unlock_edit)
        row.addWidget(self.confirm_button)
        row.addWidget(self.unlock_button)
        actions_layout.addLayout(row)
        row = QHBoxLayout()
        self.preview_selection_button = button('预览所选片段', self.preview_edit)
        row.addWidget(self.preview_selection_button)
        row.addWidget(button('预览成片', self.preview_project))
        actions_layout.addLayout(row)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(side)
        self.layer_properties_tab = QScrollArea()
        self.layer_properties_tab.setWidgetResizable(True)
        self.layer_properties_tab.setWidget(self.layer_tab)
        self.editor_tabs = QTabWidget()
        self.editor_tabs.addTab(scroll, '选择区间')
        self.editor_tabs.addTab(self.setup_effects_tab(), '添加效果')
        self.editor_tabs.addTab(self.layer_properties_tab, '图层属性')
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
        timeline_header.addWidget(label('  I / O 设入出点 · Ctrl+L 锁定长度 · 空格播放 / 暂停'))
        timeline_header.addStretch()
        clip_choices = QPushButton('选区操作')
        clip_menu = QMenu(clip_choices)
        clip_menu.addAction('选择全部已有选区', self.select_all_clips)
        clip_menu.addAction('反选已有选区', self.invert_clip_selection)
        clip_menu.addAction('为剩余片段创建选区并选中', self.select_uncovered_ranges)
        clip_choices.setMenu(clip_menu)
        timeline_header.addWidget(clip_choices)
        self.in_button = button('设为入点 I', self.set_in)
        self.out_button = button('设为出点 O', self.set_out)
        timeline_header.addWidget(self.in_button)
        timeline_header.addWidget(self.out_button)
        timeline_header.addWidget(label('缩放'))
        self.zoom = ParamControl()
        self.zoom.setRange(1, 100)
        self.zoom.setMaximumWidth(240)
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
        self.timeline.selectionModified.connect(self.modify_timeline_selection)
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

    def setup_effects_tab(self):
        self.effects_tab = QScrollArea()
        self.effects_tab.setWidgetResizable(True)
        effects = QWidget()
        effects.setObjectName('effectsPanel')
        self.effects_tab.setWidget(effects)
        layout = QVBoxLayout(effects)
        layout.addWidget(label('画面效果', 'section'))
        static = button('普通静态贴纸', self.add_static_effect)
        masked = button('模糊遮罩层', lambda: self.begin_mask(False, shape=shape.currentData()))
        shape = WheelSafeComboBox()
        for title, value in (('方框', 'rect'), ('椭圆', 'ellipse'), ('套索', 'lasso')):
            shape.addItem(title, value)
        self.track_sticker_button = button('追踪贴纸', self.add_tracking_sticker_effect)
        self.track_blur_button = button('追踪模糊遮罩层', self.begin_blur_tracking)
        for widget in (self.track_sticker_button, self.track_blur_button):
            widget.setToolTip('请单独选中一段，在该段画面上框选追踪目标。')
        full = button('全屏模糊', self.add_full_blur_effect)
        self.auto_effect_button = button('自动打码', self.show_auto_mosaic)
        self.auto_effect_button.setCheckable(True)
        for left, right in ((static, self.track_sticker_button), (full, self.auto_effect_button)):
            row = QHBoxLayout()
            row.addWidget(left, 1)
            row.addWidget(right, 1)
            layout.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(masked, 1)
        row.addWidget(shape)
        row.addWidget(self.track_blur_button, 1)
        layout.addLayout(row)
        layout.addWidget(label('段落效果', 'section'))
        omit = button('直接删除段落', lambda: self.add_paragraph_effect('omit'))
        omit.setToolTip('预览和导出时跳过整段音画。可在图层属性撤销。')
        speed = button('倍速段落', lambda: self.add_paragraph_effect('speed'))
        row = QHBoxLayout()
        row.addWidget(omit, 1)
        row.addWidget(speed, 1)
        layout.addLayout(row)
        self.setup_detection_panel()
        layout.addWidget(self.detection_tab)
        self.detection_tab.hide()
        layout.addStretch()
        self.effect_buttons = (static, masked, shape, self.track_sticker_button, self.track_blur_button, full,
                               self.auto_effect_button, omit, speed)
        self.editor_tabs.currentChanged.connect(lambda _: self.update_canvas_mask())
        return self.effects_tab

    def show_auto_mosaic(self, visible=True):
        self.auto_effect_button.setChecked(visible)
        self.detection_tab.setVisible(visible)
        if visible:
            self.editor_tabs.setCurrentWidget(self.effects_tab)
        self.sync_detection()

    def show_sticker_library(self):
        self.library_dialog.show()
        self.library_dialog.raise_()
        self.library_dialog.activateWindow()

    def add_static_effect(self):
        item = self.sticker_library_list.currentItem()
        if item is None:
            self.status.setText('请在贴纸素材库选中图片，再添加静态贴纸。')
            self.show_sticker_library()
        elif self.add_static_sticker(item.data(self.sticker_library_list.asset_role), QPointF(.5, .5)):
            self.editor_tabs.setCurrentWidget(self.layer_properties_tab)

    def add_tracking_sticker_effect(self):
        self.begin_sticker_tracking()
        if self._tracking_request is None and self.sticker_library_list.currentItem() is None:
            self.show_sticker_library()

    def add_full_blur_effect(self):
        self.add_full_blur()
        self.editor_tabs.setCurrentWidget(self.layer_properties_tab)

    def add_paragraph_effect(self, kind):
        rows = self.editable_clip_rows()
        if not rows:
            return
        self.cancel_layer_interaction()
        changed = {}
        for row in rows:
            edit = self.promote_clip(self.clip_edit(row))
            if kind == 'omit' and not edit.omit:
                changed[row] = replace(edit, omit=True)
            elif kind == 'speed' and not edit.speed_effect:
                changed[row] = replace(edit, speed_effect=True, speed=edit.speed if edit.speed != 1 else 2)
        if changed:
            self.store_layer_edits(changed)
        if kind == 'omit':
            self.status.setText(f'预览和导出将跳过 {len(rows)} 个选区。移除「直接删除段落」效果可恢复。')
        if self._editing_row not in rows:
            self.set_clip_selection(self.timeline.selected_rows, rows[0])
        self.select_effect_layer('@omit' if kind == 'omit' else '@speed')
        self.update_edit_controls()
        self.editor_tabs.setCurrentWidget(self.layer_properties_tab)

    def update_enabled(self):
        busy = self.worker is not None and self.worker.isRunning()
        self.body.setEnabled(not busy and self.project is not None)
        self.open_button.setEnabled(not busy)
        self.load_button.setEnabled(not busy)
        self.settings_button.setEnabled(not busy)
        self.library_button.setEnabled(not busy)
        self.library_dialog.setEnabled(not busy)
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
        worker = Worker(task, self, process_files=self.components.process_files)
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
        self.canvas.frame_time = None
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
                edit.range_locked |= edit.locked
                edit.locked = False
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

    def selection_inputs_changed(self, control):
        if self._range_locked and not self._loading_form:
            blockers = [QSignalBlocker(self.in_time), QSignalBlocker(self.out_time)]
            self.in_time.setValue(self.timeline.selection[0])
            self.out_time.setValue(self.timeline.selection[1])
            return
        value = control.value()
        if self.info and value == control.maximum():
            value = self.info.duration
        start, end = self.timeline.selection
        if control is self.in_time:
            start = value
        else:
            end = value
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
        start, end = self.timeline.selection
        if base and base.layers is not None:
            delta = base.start-start
            layers = [replace(layer, start=layer.start+delta, end=layer.end+delta) for layer in base.layers] if delta else base.layers
            return replace(base, start=start, end=end, range_locked=self._range_locked, layers=layers)
        return Edit(start, end, ['blur', 'region', 'image', 'none'][self.effect.currentIndex()],
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
        self.select_edit(row, keep_draft=True)

    def modify_timeline_selection(self, row, modifiers):
        current = self.timeline.selected_rows
        if modifiers & Qt.ShiftModifier:
            ordered = sorted(range(len(self.timeline.edits)), key=lambda i: self.timeline.edits[i].start)
            anchor = self._clip_selection_anchor if self._clip_selection_anchor in ordered else row
            a, b = sorted((ordered.index(anchor), ordered.index(row)))
            rows = set(ordered[a:b+1])
            if modifiers & Qt.ControlModifier:
                rows |= current
            self.set_clip_selection(rows, row)
        else:
            self.set_clip_selection(current ^ {row}, row)
            self._clip_selection_anchor = row

    def select_all_clips(self):
        self.set_clip_selection(set(range(self.clip_list.count())), self._editing_row)
        self._clip_selection_anchor = self._editing_row

    def invert_clip_selection(self):
        self.set_clip_selection(set(range(self.clip_list.count())) - self.timeline.selected_rows)
        self._clip_selection_anchor = self._editing_row

    def sync_clip_selection(self, *args):
        rows = {self.clip_list.row(item) for item in self.clip_list.selectedItems()}
        row = self.clip_list.currentRow()
        if row not in rows:
            row = self._editing_row if self._editing_row in rows else min(rows, default=-1)
        if row != self._editing_row:
            if row >= 0 and not self.load_edit_form(row):
                self.set_clip_selection(self.timeline.selected_rows, self._editing_row)
                return
            if row < 0:
                self.stash_edit()
                self.clear_active_edit()
            self._clip_selection_anchor = row
        self.show_clip_selection(rows)

    def set_clip_selection(self, rows, preferred=-1):
        row = preferred if preferred in rows else (
            self._editing_row if self._editing_row in rows else min(rows, default=-1))
        if row != self._editing_row:
            if row >= 0:
                self.load_edit_form(row, keep_draft=True)
            else:
                self.stash_edit()
                self.clear_active_edit()
        with QSignalBlocker(self.clip_list):
            self.clip_list.setCurrentRow(row, QItemSelectionModel.SelectionFlag.NoUpdate)
            old = {self.clip_list.row(item) for item in self.clip_list.selectedItems()}
            for index in old ^ rows:
                self.clip_list.item(index).setSelected(index in rows)
        self.show_clip_selection(rows)

    def show_clip_selection(self, rows):
        if rows != self.timeline.selected_rows and (self._mask_context or self._tracking_request):
            self.cancel_layer_interaction()
        self.timeline.selected_rows = rows
        count = self.clip_list.count()
        self.count_label.setText(f'{count} 段 · 已选 {len(rows)} 段' if rows else f'{count} 段')
        self.remove_clips_button.setEnabled(any(not self.project.edits[row].locked for row in rows))
        self.preview_selection_button.setText('预览活动选区' if len(rows) > 1 else '预览所选片段')
        self.confirm_button.setText('确认所选选区' if len(rows) > 1 else '确认修改')
        self.update_effect_buttons()
        self.sync_detection()

    def editable_clip_rows(self):
        return sorted(row for row in self.timeline.selected_rows
                      if 0 <= row < len(self.project.edits) and not self.project.edits[row].locked)

    def clip_edit(self, row):
        return self.current_edit() if row == self._editing_row else self.timeline.edits[row]

    def select_uncovered_ranges(self):
        if self.info:
            self.create_ranges(0, self.info.duration, select_all=True)

    def create_ranges(self, start, end, *, select_all=False):
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
        new = []
        for a, b in gaps:
            edit = Edit(a, b, 'none', layers=[])
            if select_all:
                first, last = frame_bounds(edit, self.info.fps_float)
                if first >= last:
                    continue
            elif b-a < self.timeline.minimum_span-1e-8:
                continue
            new.append(edit)
        if not new:
            self.status.setText('范围内没有至少一帧的空白时间。')
            return
        first = len(self.project.edits)
        self.project.edits.extend(new)
        rows = set(range(first, first+len(new))) if select_all else None
        self.refresh_edits(first, rows)
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
        self.refresh_edits(row, self.timeline.selected_rows)
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
        row = self._editing_row
        if not self.project or row < 0:
            self.status.setText('请先添加片段或在列表中选择要修改的片段。')
            return False
        if len(self.timeline.selected_rows) > 1:
            self.stash_edit()
            pending = {i: self._drafts[i] for i in self.editable_clip_rows() if i in self._drafts}
            if not pending:
                self.status.setText('所选选区没有待确认的修改。')
                return False
        elif self.project.edits[row].locked:
            self.status.setText('请先点击「解锁旧片段」。')
            return False
        else:
            pending = {row: self.current_edit()}
        for i, edit in pending.items():
            if edit.layers is None:
                pending[i] = replace(edit, locked=True, range_locked=True)
        try:
            for i, edit in pending.items():
                edit.validate(self.info.duration)
                if any(j != i and edit.start < other.end-1e-8 and edit.end > other.start+1e-8
                       for j, other in enumerate(self.timeline.edits)):
                    raise ValueError('修订片段重叠，请调整入点和出点。')
        except ValueError as exc:
            self.error(str(exc))
            return False
        for index, edit in pending.items():
            self.project.edits[index] = self.timeline.edits[index] = edit
            self._drafts.pop(index, None)
            self.clip_list.item(index).setText(self.edit_caption(index, edit))
        self._range_locked = self.project.edits[row].range_locked
        self.update_edit_controls()
        self.show_clip_selection(self.timeline.selected_rows)
        self.changed()
        self.status.setText('修改已确认。')
        return True

    def commit_form(self):
        self.stash_edit()
        if self._drafts:
            self.status.setText('有未确认的片段，请先选择带 * 的片段并确认。')
            return False
        return True

    def refresh_edits(self, selected=-1, selected_rows=None):
        self._editing_row = -1
        self._clip_selection_anchor = selected
        edits = [self._drafts.get(i, edit) for i, edit in enumerate(self.project.edits)]
        with QSignalBlocker(self.clip_list):
            self.clip_list.clear()
            for i, edit in enumerate(edits):
                self.clip_list.addItem(self.edit_caption(i, edit))
        self.timeline.edits = edits
        self.timeline.active_row = -1
        if selected_rows is None:
            selected_rows = {selected} if selected >= 0 else set()
        self.set_clip_selection(selected_rows, selected)
        if not selected_rows:
            self.sync_detection()

    def edit_caption(self, row, edit):
        names = {'blur': '全屏模糊', 'region': '区域模糊', 'image': '贴纸遮挡', 'none': '仅变速'}
        summary = names[edit.effect] if edit.layers is None else f'{len(edit.layers)} 个图层'
        if edit.omit:
            summary = '跳过整段 · ' + summary
        lock = '长度已锁定' if edit.range_locked or edit.locked else '长度可调整'
        pending = ' *' if row in self._drafts else ''
        return (f'{row+1:02}{pending}  {time_text(edit.start)} → {time_text(edit.end)}\n'
                f'      {summary} · {edit.speed:g}× · {lock}')

    def select_edit(self, row, keep_draft=False):
        if row == self._editing_row or self.load_edit_form(row, keep_draft):
            if row >= 0:
                self.set_clip_selection({row}, row)
                self._clip_selection_anchor = row

    def load_edit_form(self, row, keep_draft=False):
        if not self.project or row < 0 or row >= len(self.project.edits):
            return False
        previous = self._editing_row
        if 0 <= previous < len(self.project.edits) and previous != row and not self._loading_form:
            pending = self.current_edit()
            if keep_draft or pending.layers is not None:
                self.stash_edit()
            elif not self.project.edits[previous].locked and pending != self.project.edits[previous]:
                self.status.setText('请先确认当前片段，再切换片段。')
                return False
        edit = self._drafts.get(row, self.project.edits[row])
        was_loading = self._loading_form
        self._loading_form = True
        if previous != row or edit.locked:
            self.cancel_layer_interaction()
            self._layer_id = edit.layers[0].id if edit.layers else None
            self.reset_layer_selection()
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
        return True

    def delete_edit(self):
        selected = self.timeline.selected_rows
        remove = {row for row in selected if not self.project.edits[row].locked}
        if not remove:
            return
        if self._editing_row not in remove:
            self.stash_edit()
        kept = [row for row in range(len(self.project.edits)) if row not in remove]
        self.project.edits[:] = [self.project.edits[row] for row in kept]
        self._drafts = {index: self._drafts[row] for index, row in enumerate(kept) if row in self._drafts}
        retained_selection = {index for index, row in enumerate(kept) if row in selected}
        next_row = min(retained_selection, default=min(min(remove), len(kept)-1))
        self.refresh_edits(next_row, retained_selection or None)
        if not kept:
            self.clear_active_edit()
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
        selected = self._editing_row >= 0
        layered = self.layer_edit() is not None
        legacy = self._editing_row >= 0 and not layered
        self.timeline.selection_locked = self._range_locked or locked
        for widget in (self.in_time, self.out_time, self.in_button, self.out_button):
            widget.setEnabled(not self.timeline.selection_locked)
        self.range_button.setEnabled(selected and not locked)
        self.range_button.setText('解锁片段长度' if self._range_locked else '锁定片段长度')
        self.range_button.setToolTip('Ctrl+L · 仅固定当前选区的入点和出点')
        self.unlock_button.setEnabled(locked)
        self.unlock_button.setVisible(locked)
        self.preview_selection_button.setEnabled(selected)
        self.update_effect_buttons()
        editable = not locked and self._range_locked
        index = self.effect.currentIndex()
        for widget in (self.effect, self.strength, self.shape, self.feather, self.redraw_button):
            self.clip_form.setRowVisible(widget, legacy)
        self.clip_form.setRowVisible(self.speed, legacy)
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
        self.workflow_label.setText('解锁旧片段后可编辑。' if locked else
                                    '长度已锁定，仍可添加和编辑效果。' if self._range_locked else
                                    '在选区轨右键拖动，或输入入点和出点后添加选区。')
        self.timeline.update()
        self.sync_layer_editor()
        self.sync_detection()

    def update_effect_buttons(self):
        multiple = len(self.timeline.selected_rows) > 1
        editable = bool(self.editable_clip_rows())
        for widget in self.effect_buttons:
            widget.setEnabled(editable)
        for widget in (self.track_sticker_button, self.track_blur_button):
            widget.setEnabled(editable and not multiple)
        if not multiple:
            editable = self._editing_row >= 0 and not self.selected_locked() and (
                self.layer_edit() is not None or self._range_locked)
        self.confirm_button.setEnabled(editable)

    def toggle_range_lock(self):
        if self._editing_row < 0 or self.selected_locked():
            return
        if not self._range_locked:
            for control in (self.in_time, self.out_time):
                if control.editor.isModified():
                    control.interpretText()
        start, end = self.timeline.selection
        if start >= end:
            self.status.setText('请先选择有效的片段范围。')
            return
        self._range_locked = not self._range_locked
        self.form_changed()
        self.update_edit_controls()

    def unlock_edit(self):
        if self.selected_locked():
            self.project.edits[self._editing_row].locked = False
            if self.project.edits[self._editing_row].layers is not None:
                self.project.edits[self._editing_row].range_locked = False
            self.refresh_edits(self._editing_row, self.timeline.selected_rows)
            self.changed()

    def clear_active_edit(self):
        self._editing_row = -1
        self.cancel_layer_interaction()
        self._layer_id = None
        self.reset_layer_selection()
        self.timeline.active_row = -1
        self._range_locked = False
        self.set_selection(0, 0)
        self.update_edit_controls()

    def new_selection(self):
        if not self.info:
            return
        self.set_clip_selection(set())
        self._clip_selection_anchor = -1
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
        if self.project and frame.isValid() and self.canvas.frame_time is not None:
            self.update_canvas_mask(self.canvas.frame_time)
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
                               any(layer.motion for layer in edit.layers)), active_row)
        active = self.timeline.edits[active_row] if self.project and active_row >= 0 else None
        self._canvas_layer_row = active_row
        self.canvas.layers = []
        self.canvas.layer_target = None
        self.canvas.detection_boxes = self.detection_boxes_at(seconds)
        if active and active.layers is not None:
            self.canvas.layer_source_time = frame_time-active.start
            self.canvas.layers = [(i, layer) for i, layer in enumerate(active.layers)
                                  if (span := active.layer_span(layer))[0] <= (
                                      frame_time if layer.motion else seconds) < span[1]]
        if selected:
            if not playing and not selected.locked:
                layer = self.selected_layer(selected)
                transform = self.target_transform(layer)
                if transform and not layer.motion:
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
        zoom = round(self.timeline.zoom, 2)
        if self.zoom.value() != zoom:
            with QSignalBlocker(self.zoom):
                self.zoom.setValue(zoom)
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
        if self._editing_row < 0:
            self.status.setText('请先添加并选择一个修订片段。')
            return
        self.start_preview(self._editing_row)

    def preview_project(self):
        self.start_preview()

    def start_preview(self, row=None):
        if not self.project:
            return
        from .render import render_project_preview

        self.stash_edit()
        project = copy.deepcopy(Project(self.project.source, self.timeline.edits, self.project.fingerprint))
        try:
            project.validate(self.info.duration)
        except ValueError as exc:
            self.error(str(exc))
            return
        self.player.pause()
        files = self.components.process_files
        try:
            root = files.directory
            if root not in self.preview_workspaces:
                self.preview_workspaces[root] = self.preview_cleanup.enter_context(files.workspace())
            path = self.preview_workspaces[root] / f'preview-{len(self.preview_dialogs)}.mp4'
        except (OSError, ValueError, RuntimeError) as error:
            self.error(str(error))
            return
        def done(result):
            dialog = PreviewDialog(result.path, self, whole=row is None, spans=result.spans)
            self.preview_dialogs.append(dialog)
            dialog.show()
            self.status.setText('真实效果预览已生成；参数修改后请重新预览。')
        if row is None:
            self.start_job(lambda runner: render_project_preview(project, path, runner, with_timeline=True), done)
        else:
            self.start_job(lambda runner: render_preview(project, row, path, runner, with_timeline=True), done)

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
            if key == Qt.Key_L and event.modifiers() == Qt.ControlModifier:
                if not event.isAutoRepeat():
                    self.toggle_range_lock()
                return True
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
        if isinstance(focus, (QLineEdit, QAbstractSpinBox, QTextEdit, QComboBox, ParamControl)) or (
                focus and isinstance(focus.parentWidget(), ParamControl)):
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
        self.library_dialog.close()
        for dialog in self.preview_dialogs:
            dialog.close()
        self.preview_cleanup.close()
        self.preview_workspaces.clear()
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

"""Settings for the editor's portable and optional components."""

import os
import sys
from pathlib import Path

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QMessageBox, QProgressBar, QPushButton,
                               QVBoxLayout)

from .jobs import Worker


COMPONENTS = {
    'base': '基础编辑器环境', 'ffmpeg': 'FFmpeg 视频工具', 'cpu': 'CPU 识别环境',
    'gpu': 'NVIDIA 加速环境', '320n': 'NudeNet 320n', '640m': 'NudeNet 640m',
}


def byte_text(size):
    if not size:
        return '—'
    return f'{size/1024**3:.2f} GB' if size >= 1024**3 else f'{size/1024**2:.1f} MB'


class ComponentDialog(QDialog):
    handoff = Signal(str, str, str)

    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.worker = None
        self.problem = ''
        self.states = {}
        self.launcher = os.environ.get('QPIXELATE_LAUNCHER', '')
        self.setWindowTitle('设置 · 环境与组件')
        self.resize(760, 625)
        layout = QVBoxLayout(self)
        locations = QFormLayout()
        self.location_controls = []
        for label, directory, caption, callback in (
            ('程序安装位置', manager.root,
             '更改位置…' if manager.installer else '打开安装包…', self.choose_program_location),
            ('组件安装位置', manager.component_directory, '更改位置…', self.choose_component_location),
        ):
            row = QHBoxLayout()
            field = QLineEdit(str(directory))
            field.setReadOnly(True)
            row.addWidget(field, 1)
            self.add_button(row, '打开', lambda _checked=False, path=directory: self.open_directory(path))
            self.location_controls.append(self.add_button(row, caption, callback))
            locations.addRow(label, row)
        row = QHBoxLayout()
        self.process_path = QLineEdit(str(manager.process_files.directory))
        self.process_path.setReadOnly(True)
        self.process_path.setToolTip('预览、导出和识别生成的过程文件使用此目录。')
        row.addWidget(self.process_path, 1)
        self.process_controls = [
            self.add_button(row, '打开', lambda: self.open_directory(self.manager.process_files.directory)),
            self.add_button(row, '更改位置…', self.choose_process_directory),
            self.add_button(row, '恢复默认', lambda: self.set_process_directory(None)),
        ]
        locations.addRow('过程文件位置', row)
        layout.addLayout(locations)
        self.items = QListWidget()
        self.items.addItems(list(COMPONENTS.values()))
        layout.addWidget(self.items, 2)
        self.detail = QLabel()
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)
        row = QHBoxLayout()
        self.refresh = self.add_button(row, '重新检测', self.refresh_states)
        self.install = self.add_button(row, '下载安装', self.install_selected)
        self.offline = self.add_button(row, '导入离线包', self.import_archive)
        self.remove = self.add_button(row, '卸载', self.uninstall_selected)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.external = self.add_button(row, '指定 FFmpeg 目录', self.choose_ffmpeg)
        self.reset = self.add_button(row, '解除外部引用', self.reset_ffmpeg)
        row.addStretch()
        layout.addLayout(row)
        form = QFormLayout()
        self.model = QComboBox()
        self.model.addItems(['320n', '640m'])
        self.device = QComboBox()
        self.device.addItem('CPU', 'cpu')
        self.device.addItem('NVIDIA GPU', 'gpu')
        preferences = self.manager.preferences()
        self.model.setCurrentText(preferences['model'])
        self.device.setCurrentIndex(self.device.findData(preferences['device']))
        self.model.currentTextChanged.connect(lambda model: self.set_preference(model=model))
        self.device.currentIndexChanged.connect(lambda _: self.set_preference(device=self.device.currentData()))
        form.addRow('检测模型', self.model)
        form.addRow('执行设备', self.device)
        layout.addLayout(form)
        self.status = QLabel('检测和视频处理均在本机执行。')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setProperty('feedbackPanel', True)
        self.progress.hide()
        layout.addWidget(self.progress)
        row = QHBoxLayout()
        row.addStretch()
        self.cancel = self.add_button(row, '取消任务', self.cancel_task)
        self.done_button = self.add_button(row, '关闭', self.close)
        layout.addLayout(row)
        self.items.currentRowChanged.connect(self.update_controls)
        self.items.setCurrentRow(0)
        self.refresh_states()

    @staticmethod
    def add_button(layout, caption, action):
        item = QPushButton(caption)
        item.clicked.connect(action)
        layout.addWidget(item)
        return item

    def selection(self):
        row = self.items.currentRow()
        return tuple(COMPONENTS)[max(0, row)]

    def update_controls(self):
        busy = self.worker is not None
        identifier = self.selection()
        state = self.states.get(identifier, {})
        entry = self.manager.catalog.get(identifier, {})
        local = self.manager.local_archive(identifier)
        can_manage = identifier != 'base' or bool(self.launcher)
        for button in (self.refresh, self.external, self.model, self.device, self.done_button):
            button.setEnabled(not busy)
        for button in self.location_controls:
            button.setEnabled(not busy and bool(self.launcher))
        for button in self.process_controls:
            button.setEnabled(not busy)
        self.reset.setEnabled(not busy and bool(self.manager.preferences().get('ffmpeg_directory')))
        self.install.setEnabled(not busy and can_manage and bool(local or entry.get('url')))
        self.install.setText('重新安装' if state.get('installed') else '安装本地包' if local else '下载安装')
        self.offline.setEnabled(not busy and can_manage and bool(entry))
        self.remove.setEnabled(not busy and can_manage and bool(state.get('installed')))
        self.cancel.setVisible(busy)
        self.progress.setVisible(busy)
        lines = [state.get('message', '正在读取组件状态。')]
        if identifier == 'base' and not self.launcher:
            lines = [f'正在使用外部 Python · {sys.executable}']
        elif state.get('path'):
            lines.append(str(state['path']))
        if state.get('version') or entry:
            lines.append(f"版本 {state.get('version') or entry.get('version', '—')}   ·   "
                         f"下载 {byte_text(entry.get('size'))}   ·   已占用 {byte_text(state.get('installed_size'))}")
        if local:
            lines.append(f'本地组件包 · {local.name}')
        elif entry.get('url'):
            lines.append(f"下载来源 · {entry['url']}")
        elif entry:
            lines.append('当前组件可通过离线包安装。')
        self.detail.setText('\n'.join(filter(None, lines)))

    def refresh_states(self, checked=False, *, verify=True):
        self.start_task(lambda runner: self.read_states(runner, verify))

    def read_states(self, runner, verify=False):
        states = {}
        for index, key in enumerate(COMPONENTS):
            runner.report(100 * index / len(COMPONENTS), f'检测 {COMPONENTS[key]}…')
            states[key] = self.manager.status(key, verify=verify, runner=runner)
        return states

    def start_task(self, task):
        if self.worker is not None:
            return
        self.problem = ''
        self.worker = Worker(task, self)
        self.worker.progressed.connect(self.set_progress)
        self.worker.succeeded.connect(self.show_states)
        self.worker.failed.connect(self.show_failure)
        self.worker.finished.connect(self.finish_task)
        self.progress.setValue(0)
        self.worker.start()
        self.update_controls()

    def set_progress(self, percent, detail):
        self.progress.setValue(percent)
        self.status.setText(detail)

    def show_states(self, states):
        self.states = states
        for row, (key, name) in enumerate(COMPONENTS.items()):
            state = states[key]
            tag = ('外部环境' if key == 'base' and not self.launcher else
                   '可用' if state['ready'] else '需修复' if state['installed'] else '未安装')
            self.items.item(row).setText(f'{name}  ·  {tag}')

    def show_failure(self, detail):
        self.problem = detail

    def finish_task(self):
        self.worker.deleteLater()
        self.worker = None
        self.status.setText(self.problem or '组件状态已更新。')
        self.update_controls()

    def cancel_task(self):
        if self.worker:
            self.worker.cancel()

    def closeEvent(self, event):
        if self.worker is not None:
            event.ignore()
            return
        super().closeEvent(event)

    def reject(self):
        if self.worker is None:
            super().reject()

    def set_preference(self, **changes):
        try:
            self.manager.set_preferences(**changes)
            self.status.setText('识别设置已保存。')
        except (OSError, ValueError, RuntimeError) as exc:
            self.status.setText(str(exc))

    def choose_ffmpeg(self):
        directory = QFileDialog.getExistingDirectory(self, '选择包含 ffmpeg.exe 和 ffprobe.exe 的文件夹')
        if directory:
            self.set_ffmpeg(directory)

    def reset_ffmpeg(self):
        self.set_ffmpeg(None)

    def set_ffmpeg(self, directory):
        def task(runner):
            self.manager.set_ffmpeg_directory(directory)
            return self.read_states(runner)
        self.start_task(task)

    def open_directory(self, directory):
        if directory.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))
        else:
            self.status.setText('目录尚未创建。')

    def choose_process_directory(self):
        folder = QFileDialog.getExistingDirectory(
            self, '选择过程文件的存放位置', str(self.manager.process_files.directory.parent))
        if folder:
            self.set_process_directory(folder)

    def set_process_directory(self, folder):
        try:
            self.manager.process_files.set_directory(folder)
        except (OSError, ValueError, RuntimeError) as error:
            self.status.setText(str(error))
            return
        self.process_path.setText(str(self.manager.process_files.directory))
        self.status.setText('过程文件位置已保存，下次预览、导出或识别时生效。')

    def choose_program_location(self):
        if self.manager.installer:
            if not self.manager.installer.is_file():
                self.status.setText('找不到维护安装包，请重新运行 Q-Pixelate-Setup.exe。')
                return
            action, path = 'move-program', ''
        else:
            path, _ = QFileDialog.getOpenFileName(self, '选择 Q-Pixelate-Setup.exe', str(self.manager.root),
                                                  'Q-Pixelate 安装包 (Q-Pixelate-Setup.exe)')
            if not path:
                return
            action = 'open-installer'
        self.accept()
        self.handoff.emit(action, '', path)

    def choose_component_location(self):
        folder = QFileDialog.getExistingDirectory(self, '选择组件的新安装位置', str(self.manager.component_directory))
        if not folder or Path(folder).resolve() == self.manager.component_directory:
            return
        cleanup = QMessageBox.question(
            self, '迁移组件', '迁移成功后，是否清理旧位置中的 Q-Pixelate 组件副本？',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.No)
        if cleanup != QMessageBox.StandardButton.Cancel:
            self.accept()
            self.handoff.emit('move-components', 'cleanup' if cleanup == QMessageBox.StandardButton.Yes else '', folder)

    def install_selected(self):
        self.change_component('install')

    def import_archive(self):
        archive, _ = QFileDialog.getOpenFileName(self, '选择组件离线包', str(self.manager.root), 'ZIP 包 (*.zip)')
        if archive:
            self.change_component('install', Path(archive))

    def uninstall_selected(self):
        self.change_component('uninstall')

    def change_component(self, action, archive=None):
        key = self.selection()
        if key == 'base':
            if self.launcher:
                self.accept()
                self.handoff.emit(action, key, str(archive or ''))
            return

        def task(runner):
            if action == 'install':
                self.manager.install(key, runner, archive=archive)
            else:
                self.manager.uninstall(key)
            return self.read_states(runner)
        self.start_task(task)

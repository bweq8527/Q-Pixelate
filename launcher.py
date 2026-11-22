"""Windows portable setup and editor launcher."""

import argparse
import ctypes
import os
from pathlib import Path
from queue import Empty, Queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import NamedTuple


TITLES = {
    'base': '基础运行环境',
    'ffmpeg': 'FFmpeg / ffprobe',
    'cpu': 'CPU 识别环境',
    'gpu': 'NVIDIA 加速环境',
    '320n': 'NudeNet 320n',
    '640m': 'NudeNet 640m',
}
REQUIRED = ('base', 'ffmpeg')


class LaunchOptions(NamedTuple):
    action: tuple[str, str, Path | None] | None
    wait_pid: int | None
    editor_args: list[str]
    setup: bool
    component_directory: Path | None
    cleanup: bool
    relocate_program: bool
    start_installer: Path | None
    remove_components: bool
    verify_installation: bool
    remove_process_files: bool

    @property
    def maintenance(self):
        return bool(self.action or self.component_directory or self.relocate_program
                    or self.start_installer or self.remove_components or self.remove_process_files)


def parse_options(arguments, working_directory):
    parser = argparse.ArgumentParser(prog='Q-Pixelate.exe')
    parser.add_argument('--setup', action='store_true')
    parser.add_argument('--wait-pid', type=int)
    operations = parser.add_mutually_exclusive_group()
    operations.add_argument('--component-action', nargs='+', metavar='ACTION')
    operations.add_argument('--move-components', metavar='DIRECTORY')
    operations.add_argument('--relocate-program', action='store_true')
    operations.add_argument('--start-installer', metavar='FILE')
    operations.add_argument('--remove-installed-components', action='store_true')
    operations.add_argument('--remove-process-files', action='store_true')
    operations.add_argument('--verify-installation', action='store_true')
    parser.add_argument('--remove-old-copy', action='store_true')
    if '--' in arguments:
        boundary = arguments.index('--')
        options, editor_args = parser.parse_known_args(arguments[:boundary])
        editor_args.extend(arguments[boundary + 1:])
    else:
        options, editor_args = parser.parse_known_args(arguments)
    action = None
    if options.component_action:
        kind, *details = options.component_action
        if (kind not in {'install', 'uninstall'} or not details or details[0] not in TITLES
                or len(details) > (2 if kind == 'install' else 1)):
            parser.error('expected --component-action {install,uninstall} COMPONENT [ARCHIVE]')
        component, *archives = details
        archive = (working_directory / archives[0]).resolve() if archives else None
        action = (kind, component, archive)
    if options.wait_pid is not None and not 0 < options.wait_pid <= 0xFFFFFFFF:
        parser.error('--wait-pid must be a Windows process ID')
    if options.remove_old_copy and not options.move_components:
        parser.error('--remove-old-copy requires --move-components')
    if editor_args and not editor_args[0].startswith('-'):
        editor_args[0] = str((working_directory / editor_args[0]).resolve())
    component_directory = (working_directory / options.move_components).resolve() if options.move_components else None
    installer = (working_directory / options.start_installer).resolve() if options.start_installer else None
    return LaunchOptions(action, options.wait_pid, editor_args, options.setup,
                         component_directory, options.remove_old_copy, options.relocate_program,
                         installer, options.remove_installed_components, options.verify_installation,
                         options.remove_process_files)


def wait_for_editor(pid, runner):
    from ctypes import wintypes

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    process = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
    if not process:
        error = ctypes.get_last_error()
        if error == 87:  # ERROR_INVALID_PARAMETER means the PID has already exited.
            return
        raise ctypes.WinError(error)
    try:
        runner.report(0, '正在等待编辑器关闭…')
        while True:
            runner.check()
            result = kernel.WaitForSingleObject(process, 100)
            if result == 0:  # WAIT_OBJECT_0
                return
            if result != 258:  # WAIT_TIMEOUT
                raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel.CloseHandle(process)


def prepare_frozen_environment():
    if not getattr(sys, 'frozen', False):
        return
    if not ctypes.windll.kernel32.SetDllDirectoryW(None):
        raise ctypes.WinError()
    # Tk's runtime hook sets these before Python starts. Its files vanish on exit.
    os.environ.pop('TCL_LIBRARY', None)
    os.environ.pop('TK_LIBRARY', None)


def start_editor(manager, editor_args):
    base = manager.path('base')
    env = os.environ.copy()
    for variable in ('QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH', 'PYTHONHOME', 'PYTHONPATH'):
        env.pop(variable, None)
    env['QPIXELATE_HOME'] = str(manager.root)
    if getattr(sys, 'frozen', False):
        for variable in set(env):
            if variable.startswith('_PYI_'):
                del env[variable]
        env['QPIXELATE_LAUNCHER'] = str(Path(sys.executable).resolve())
    subprocess.Popen([str(base / 'pythonw.exe'), '-m', 'q_pixelate', *editor_args],
                     cwd=manager.root, env=env)


class SetupWindow:
    def __init__(self, window, manager, options):
        self.window = window
        self.manager = manager
        self.options = options
        self.messages = Queue()
        self.cancel = threading.Event()
        self.busy = False
        self.closing = False
        self.statuses = {}
        self.rows = {}
        self.window.title('Q-Pixelate · 环境与组件')
        self.window.minsize(630, 350)
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        container = ttk.Frame(window, padding=18)
        container.pack(fill='both', expand=True)
        container.columnconfigure(1, weight=1)
        ttk.Label(container, text='环境与组件', font=('Segoe UI', 15, 'bold')).grid(
            row=0, column=0, columnspan=2, sticky='w', pady=(0, 10))
        for row, (component, name) in enumerate(TITLES.items(), 1):
            checked = tk.BooleanVar(window, False)
            check = ttk.Checkbutton(container, variable=checked, text=name, command=self.update_controls)
            check.grid(row=row, column=0, sticky='w', pady=3, padx=(0, 18))
            state = ttk.Label(container, text='正在检测…', wraplength=400)
            state.grid(row=row, column=1, sticky='w', pady=3)
            self.rows[component] = checked, check, state
        self.message = ttk.Label(container, wraplength=575)
        self.message.grid(row=7, column=0, columnspan=2, sticky='w', pady=(12, 3))
        self.progress = ttk.Progressbar(container, maximum=100)
        self.progress.grid(row=8, column=0, columnspan=2, sticky='ew')
        buttons = ttk.Frame(container)
        buttons.grid(row=9, column=0, columnspan=2, sticky='ew', pady=(12, 0))
        self.install_button = ttk.Button(buttons, text='下载安装所选项', command=self.install_selected)
        self.offline_button = ttk.Button(buttons, text='导入离线包', command=self.import_offline)
        self.refresh_button = ttk.Button(buttons, text='重新检测', command=self.check_statuses)
        self.cancel_button = ttk.Button(buttons, text='取消任务', command=self.cancel.set)
        self.open_button = ttk.Button(buttons, text='打开编辑器', command=self.open_editor)
        for button in (self.install_button, self.offline_button, self.refresh_button, self.cancel_button):
            button.pack(side='left', padx=(0, 6))
        self.open_button.pack(side='right')
        self.location = ttk.Label(container, wraplength=455)
        self.location.grid(row=10, column=0, columnspan=2, sticky='w', pady=(12, 0))
        self.location_button = ttk.Button(container, text='更改组件位置…', command=self.choose_location)
        self.location_button.grid(row=11, column=0, sticky='w', pady=(5, 0))
        window.after(60, self.poll)
        if options.maintenance:
            self.start_task(self.apply_handoff, auto_open=True)
        else:
            self.start_task(lambda runner: self.verify_components(runner, REQUIRED), auto_open=not options.setup)

    def selected(self):
        return [key for key, (checked, _, _) in self.rows.items() if checked.get()]

    def editor_ready(self):
        return all(self.statuses.get(component, {}).get('ready') for component in REQUIRED)

    def update_controls(self):
        selected = self.selected()
        adjacent = {component for component in selected if self.manager.local_archive(component)}
        can_install = all(component in adjacent or self.manager.catalog[component].get('url')
                          for component in selected)
        for component, (_, check, _) in self.rows.items():
            check.configure(state='normal' if not self.busy and component in self.manager.catalog else 'disabled')
        self.install_button.configure(
            text='安装所选项' if selected and len(adjacent) == len(selected) else '下载安装所选项',
            state='normal' if selected and can_install and not self.busy else 'disabled')
        self.offline_button.configure(state='normal' if len(selected) == 1 and not self.busy else 'disabled')
        self.refresh_button.configure(state='disabled' if self.busy else 'normal')
        self.cancel_button.configure(state='normal' if self.busy else 'disabled')
        self.open_button.configure(state='normal' if self.editor_ready() and not self.busy else 'disabled')
        self.location_button.configure(state='disabled' if self.busy else 'normal')
        self.location.configure(text=f'组件安装位置  {self.manager.component_directory}')

    def choose_location(self):
        folder = filedialog.askdirectory(parent=self.window, title='选择组件的新安装位置',
                                         initialdir=str(self.manager.component_directory))
        if folder and Path(folder).resolve() != self.manager.component_directory:
            self.start_task(lambda runner: self.manager.relocate_components(Path(folder), runner))

    def start_task(self, operation, *, auto_open=False):
        from q_pixelate.process import Runner

        self.cancel.clear()
        self.busy = True
        self.progress.configure(value=0)
        self.message.configure(text='正在检查组件…')
        self.update_controls()

        def do_work():
            last_report = None

            def report(value, text):
                nonlocal last_report
                if last_report != (value, text):
                    last_report = (value, text)
                    self.messages.put(('progress', last_report))

            runner = Runner(self.cancel, report)
            failure = None
            try:
                operation(runner)
            except Exception as error:
                failure = str(error) or type(error).__name__
            try:
                statuses = {key: self.manager.status(key) for key in TITLES}
            except Exception as error:
                statuses = {}
                failure = failure or str(error)
            self.messages.put(('done', (statuses, failure, auto_open)))

        threading.Thread(target=do_work, daemon=True).start()

    def poll(self):
        try:
            while True:
                event, arguments = self.messages.get_nowait()
                if event == 'progress':
                    value, text = arguments
                    self.progress.configure(value=value)
                    self.message.configure(text=text)
                elif self.finish_task(*arguments):
                    return
        except Empty:
            pass
        self.window.after(60, self.poll)

    def finish_task(self, statuses, failure, auto_open):
        if self.closing:
            self.window.destroy()
            return True
        self.busy = False
        self.statuses = statuses
        for component, (checked, _, label) in self.rows.items():
            status = statuses.get(component, {})
            details = status.get('message') or ('就绪' if status.get('ready') else '未安装')
            size = status.get('download_size')
            if size and not status.get('ready'):
                details += f' · {size / 1024**2:.1f} MB'
            if component not in self.manager.catalog and not status.get('ready'):
                details += ' · 发行清单未提供此组件'
            label.configure(text=details)
            checked.set(component in REQUIRED and component in self.manager.catalog and not status.get('ready'))
        self.message.configure(text=failure or ('环境就绪。' if self.editor_ready() else '请安装缺少的基础组件。'))
        self.progress.configure(value=0 if failure else 100)
        self.update_controls()
        if auto_open and not failure and self.editor_ready():
            return self.open_editor()
        return False

    def verify_components(self, runner, components=TITLES):
        if self.options.wait_pid:
            wait_for_editor(self.options.wait_pid, runner)
        for index, component in enumerate(components):
            runner.report(100 * index / len(components), f'正在检测 {TITLES[component]}…')
            self.manager.status(component, verify=True, runner=runner)

    def check_statuses(self):
        self.start_task(self.verify_components)

    def apply_handoff(self, runner):
        if self.options.wait_pid:
            wait_for_editor(self.options.wait_pid, runner)
        if self.options.component_directory:
            self.manager.relocate_components(self.options.component_directory, runner, cleanup=self.options.cleanup)
            return
        kind, component, archive = self.options.action
        if kind == 'uninstall':
            runner.report(0, f'正在卸载 {TITLES[component]}…')
            self.manager.uninstall(component)
        else:
            self.manager.install(component, runner, archive)

    def install_selected(self):
        from q_pixelate.process import Runner

        selected = self.selected()

        def install_all(runner):
            for index, component in enumerate(selected):
                def report(value, text):
                    runner.report((index * 100 + value) / len(selected), f'{TITLES[component]} · {text}')

                self.manager.install(component, Runner(self.cancel, report))

        self.start_task(install_all)

    def import_offline(self):
        component, = self.selected()
        archive = filedialog.askopenfilename(parent=self.window, title=f'选择 {TITLES[component]} 离线包',
                                             filetypes=[('组件 ZIP 包', '*.zip')])
        if archive:
            self.start_task(lambda runner: self.manager.install(component, runner, Path(archive)))

    def open_editor(self):
        try:
            start_editor(self.manager, self.options.editor_args)
        except OSError as error:
            self.message.configure(text=f'无法启动编辑器。{error}')
            return False
        self.window.destroy()
        return True

    def close(self):
        if self.busy:
            self.closing = True
            self.cancel.set()
        else:
            self.window.destroy()


def main():
    from q_pixelate.components import ComponentManager
    from q_pixelate.process import Runner

    options = parse_options(sys.argv[1:], Path.cwd())
    frozen = getattr(sys, 'frozen', False)
    launcher_path = Path(sys.executable if frozen else __file__).resolve()
    root = launcher_path.parent if frozen else os.environ.get('QPIXELATE_HOME', launcher_path.parent)
    if options.verify_installation or options.remove_components or options.remove_process_files:
        try:
            manager = ComponentManager(root)
            if not manager.installer or (options.verify_installation and not manager.installer.is_file()):
                return 1
            if options.verify_installation:
                return 0 if all(component in manager.catalog for component in REQUIRED) else 1
            prepare_frozen_environment()
            if options.wait_pid:
                wait_for_editor(options.wait_pid, Runner())
            if options.remove_process_files:
                manager.process_files.remove_abandoned()
                return 0
            for component in reversed(TITLES):
                if manager.status(component)['installed']:
                    manager.uninstall(component)
            return 0
        except (OSError, ValueError, RuntimeError):
            return 1
    window = tk.Tk()
    window.withdraw()
    try:
        prepare_frozen_environment()
        manager = ComponentManager(root)
        if options.relocate_program or options.start_installer:
            if options.wait_pid:
                wait_for_editor(options.wait_pid, Runner())
            installer = manager.installer if options.relocate_program else options.start_installer
            if not installer or not installer.is_file():
                raise ValueError('找不到 Q-Pixelate 安装包。')
            if not options.relocate_program and installer.name.casefold() != 'q-pixelate-setup.exe':
                raise ValueError('请选择 Q-Pixelate-Setup.exe 安装包。')
            command = [str(installer)]
            if options.relocate_program:
                command.append('/RELOCATE=1')
            subprocess.Popen(command, cwd=installer.parent)
            window.destroy()
            return 0
        SetupWindow(window, manager, options)
    except (OSError, ValueError, RuntimeError) as error:
        messagebox.showerror('Q-Pixelate', str(error), parent=window)
        window.destroy()
        return 1
    window.deiconify()
    window.mainloop()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

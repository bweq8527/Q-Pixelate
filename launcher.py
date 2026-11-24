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

from q_pixelate.palette import (
    BRICK, BRICK_HOVER, BRICK_PRESS, BRICK_WASH, INK, MUSTARD, MUSTARD_WASH,
    MUTED, PAPER, SURFACE, TEAL, TEAL_WASH,
)


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


def motion_enabled():
    if sys.platform != 'win32':
        return True
    from ctypes import wintypes

    enabled = wintypes.BOOL()
    query_client_area_animation = 0x1042
    success = ctypes.windll.user32.SystemParametersInfoW(query_client_area_animation, 0, ctypes.byref(enabled), 0)
    return not success or bool(enabled.value)


class SetupTheme:
    """Render the controls using ttk states so their hit targets stay fixed."""

    def __init__(self, window):
        self.window = window
        self.style = ttk.Style(window)
        self.style.theme_use('clam')
        self.scale = window.winfo_fpixels('1p') * 0.75
        self.motion = motion_enabled()
        self.images = []

        for variant, text, face, hover, pressed in (
            ('Secondary', INK, SURFACE, TEAL_WASH, MUSTARD_WASH),
            ('Primary', SURFACE, BRICK, BRICK_HOVER, BRICK_PRESS),
            ('Danger', INK, BRICK_WASH, '#e8b5a4', '#db9e8b'),
        ):
            self.style_button(variant, text, face, hover, pressed)
        self.style_checkbox()
        self.style.configure('Setup.Horizontal.TProgressbar', background=TEAL,
                             troughcolor=PAPER, bordercolor=INK, lightcolor=TEAL,
                             darkcolor=TEAL, thickness=self.pixels(12))

    def pixels(self, length):
        return round(length * self.scale)

    def image(self, width, height):
        image = tk.PhotoImage(master=self.window, width=self.pixels(width), height=self.pixels(height))
        self.images.append(image)
        return image

    def rectangle(self, image, color, *bounds):
        image.put(color, to=tuple(self.pixels(value) for value in bounds))

    def button_face(self, color, offset=0, edge=INK):
        image = self.image(18, 18)
        self.rectangle(image, edge, 4, 4, 18, 18)
        self.rectangle(image, edge, offset, offset, 14 + offset, 14 + offset)
        self.rectangle(image, color, 2 + offset, 2 + offset, 12 + offset, 12 + offset)
        return image

    def style_button(self, variant, text, face, hover, pressed):
        element = f'SetupButton.{variant}'
        border = self.pixels(6)
        self.style.element_create(
            element, 'image', self.button_face(face),
            ('disabled', self.button_face('#E7E1D4', edge='#9E9787')),
            ('pressed', self.button_face(pressed, 4 if self.motion else 0)),
            ('user2', self.button_face(pressed, 3)),
            ('user1', self.button_face(hover, 1)),
            ('active', self.button_face(hover)),
            border=(border, border, border + self.pixels(1), border + self.pixels(1)), sticky='nsew',
        )
        name = f'{variant}.Setup.TButton'
        self.style.layout(name, [(element, {'sticky': 'nsew', 'children': [
            ('Button.padding', {'sticky': 'nsew', 'children': [
                ('Button.focus', {'sticky': 'nsew', 'children': [
                    ('Button.label', {'sticky': 'nsew'}),
                ]}),
            ]}),
        ]})])
        horizontal, vertical = self.pixels(5), self.pixels(3)
        one, two = self.pixels(1), self.pixels(2)
        self.style.configure(name, font=('Segoe UI', 9, 'bold'), foreground=text, focuscolor=text,
                             padding=(horizontal, vertical), width=0, shiftrelief=0)
        self.style.map(name, foreground=[('disabled', MUTED)])
        if self.motion:
            self.style.map(name, padding=[
                ('pressed', (horizontal + two, vertical + two, horizontal - two, vertical - two)),
                ('user2', (horizontal + one, vertical + one, horizontal - one, vertical - one)),
            ])

    def checkbox_mark(self, fill, checked=False, edge=INK):
        image = self.image(26, 17)
        self.rectangle(image, edge, 0, 0, 17, 17)
        self.rectangle(image, fill, 2, 2, 15, 15)
        if checked:
            for x, y in ((4, 8), (5, 9), (6, 10), (7, 9), (8, 8), (9, 7), (10, 6), (11, 5)):
                self.rectangle(image, edge, x, y, x + 2, y + 2)
        return image

    def style_checkbox(self):
        pressed = self.checkbox_mark(MUSTARD_WASH)
        selected_pressed = self.checkbox_mark(MUSTARD, True)
        hover = self.checkbox_mark(PAPER)
        selected_hover = self.checkbox_mark(TEAL_WASH, True, TEAL)
        self.style.element_create(
            'SetupCheck.mark', 'image', self.checkbox_mark(SURFACE),
            ('disabled', 'selected', self.checkbox_mark('#E7E1D4', True, '#9E9787')),
            ('disabled', self.checkbox_mark('#E7E1D4', edge='#9E9787')),
            ('pressed', 'selected', selected_pressed), ('pressed', pressed),
            ('user2', 'selected', selected_pressed), ('user2', pressed),
            ('user1', 'selected', selected_hover), ('user1', hover),
            ('selected', 'active', selected_hover),
            ('selected', self.checkbox_mark(TEAL, True, SURFACE)), ('active', hover),
        )
        name = 'Setup.TCheckbutton'
        self.style.layout(name, [('Checkbutton.padding', {'sticky': 'nsew', 'children': [
            ('SetupCheck.mark', {'side': 'left', 'sticky': ''}),
            ('Checkbutton.focus', {'side': 'left', 'sticky': 'w', 'children': [
                ('Checkbutton.label', {'sticky': 'nsew'}),
            ]}),
        ]})])
        self.style.configure(name, background=SURFACE, foreground=INK, focuscolor=INK,
                             font=('Segoe UI', 10, 'bold'), padding=(12, 7, 10, 7))
        self.style.map(name, foreground=[('disabled', MUTED)],
                       background=[('disabled', SURFACE), ('pressed', PAPER), ('active', PAPER)])

    def button(self, parent, text, command, variant='Secondary'):
        button = ttk.Button(parent, text=text, command=command, style=f'{variant}.Setup.TButton')
        self.add_feedback(button)
        return button

    def add_feedback(self, widget):
        # Ttk handles activation and focus. The user states only keep the press visible as it settles.
        if not self.motion:
            return
        timer = None

        def clear_timer(_event=None):
            nonlocal timer
            if timer is not None:
                widget.after_cancel(timer)
                timer = None

        def reset():
            nonlocal timer
            timer = None
            widget.state(('!user1', '!user2'))

        def settle():
            nonlocal timer
            widget.state(('user1', '!user2'))
            timer = widget.after(50, reset)

        def start(delay):
            nonlocal timer
            clear_timer()
            widget.state(('!user1', 'user2'))
            timer = widget.after(delay, settle)

        def release(_event):
            if widget.instate(('pressed', '!disabled')):
                start(45)

        def activate(_event):
            if widget.instate(('!disabled',)):
                start(140)  # Includes the standard ttk keyboard press interval.

        widget.bind('<ButtonRelease-1>', release, add='+')
        widget.bind('<space>', activate, add='+')
        widget.bind('<<Invoke>>', activate, add='+')
        widget.bind('<Destroy>', clear_timer, add='+')


class SetupProgress(ttk.Progressbar):
    def __init__(self, parent, motion):
        super().__init__(parent, maximum=100, style='Setup.Horizontal.TProgressbar')
        self.motion = motion
        self.animation = None
        self.bind('<Destroy>', self.cancel_animation, add='+')

    def cancel_animation(self, _event=None):
        if self.animation is not None:
            self.after_cancel(self.animation)
            self.animation = None

    def show_value(self, value, *, immediate=False):
        self.cancel_animation()
        start = float(self.cget('value'))
        if immediate or not self.motion or start == value:
            self.configure(value=value)
            return

        def advance(step):
            self.animation = None
            fraction = 1 - (1 - step / 6) ** 2
            self.configure(value=start + (value - start) * fraction)
            if step < 6:
                self.animation = self.after(16, advance, step + 1)

        self.animation = self.after(16, advance, 1)


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
        self.window.configure(background=PAPER)
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        self.theme = SetupTheme(window)
        self.build_controls()
        window.update_idletasks()
        window.minsize(max(window.winfo_reqwidth(), round(window.winfo_fpixels('510p'))),
                       window.winfo_reqheight())
        window.after(60, self.poll)
        if options.maintenance:
            self.start_task(self.apply_handoff, auto_open=True)
        else:
            self.start_task(lambda runner: self.verify_components(runner, REQUIRED), auto_open=not options.setup)

    def build_controls(self):
        container = tk.Frame(self.window, background=PAPER, padx=18, pady=18)
        container.pack(fill='both', expand=True)
        container.columnconfigure(0, weight=1)

        header = tk.Frame(container, background=INK, padx=14, pady=10)
        header.grid(row=0, column=0, sticky='ew', pady=(0, 13))
        header.columnconfigure(0, weight=1)
        tk.Label(header, text='Q-PIXELATE', background=INK, foreground=MUSTARD, font=('Consolas', 11, 'bold')).grid(
            row=0, column=0, sticky='w')
        tk.Label(header, text='环境与组件', background=INK, foreground=SURFACE, font=('Segoe UI', 22, 'bold')).grid(
            row=1, column=0, sticky='w')
        unit = self.theme.pixels(11)
        mark = tk.Canvas(header, background=INK, highlightthickness=0, width=unit * 5, height=unit * 4)
        mark.grid(row=0, column=1, rowspan=2, padx=(10, 0))
        for x, y, color in ((0, 0, MUSTARD), (1, 0, MUSTARD), (2, 0, MUSTARD), (0, 1, MUSTARD), (2, 1, MUSTARD),
                            (0, 2, MUSTARD), (1, 2, MUSTARD), (2, 2, MUSTARD), (3, 3, BRICK), (4, 3, TEAL)):
            mark.create_rectangle(x * unit, y * unit, (x + 1) * unit - 2, (y + 1) * unit - 2,
                                  fill=color, width=0)

        table_edge = tk.Frame(container, background=INK, padx=2, pady=2)
        table_edge.grid(row=1, column=0, sticky='ew')
        table = tk.Frame(table_edge, background='#D9D4C6')
        table.pack(fill='x', expand=True)
        table.columnconfigure(1, weight=1)
        for row, (component, name) in enumerate(TITLES.items()):
            checked = tk.BooleanVar(self.window, False)
            check = ttk.Checkbutton(table, variable=checked, text=name, command=self.update_controls,
                                    style='Setup.TCheckbutton')
            self.theme.add_feedback(check)
            divider = 0 if row == len(TITLES) - 1 else 1
            check.grid(row=row, column=0, sticky='nsew', pady=(0, divider))
            state = tk.Label(table, text='正在检测…', foreground=MUTED, background=SURFACE,
                             font=('Segoe UI', 9), wraplength='295p', padx=12, pady=6, anchor='w', justify='left')
            state.grid(row=row, column=1, sticky='nsew', pady=(0, divider))
            self.rows[component] = checked, check, state

        status = tk.Frame(container, background=INK, padx=2, pady=2)
        status.grid(row=2, column=0, sticky='ew', pady=(13, 0))
        self.message = tk.Label(status, background=SURFACE, foreground=INK, font=('Segoe UI', 10, 'bold'),
                                wraplength='435p', padx=10, pady=5, anchor='w', justify='left')
        self.message.pack(fill='x')
        self.progress = SetupProgress(status, self.theme.motion)
        self.progress.pack(fill='x')

        buttons = tk.Frame(container, background=PAPER)
        buttons.grid(row=3, column=0, sticky='ew', pady=(13, 0))
        self.install_button = self.theme.button(buttons, '下载安装所选项', self.install_selected, 'Primary')
        self.offline_button = self.theme.button(buttons, '导入离线包', self.import_offline)
        self.refresh_button = self.theme.button(buttons, '重新检测', self.check_statuses)
        self.cancel_button = self.theme.button(buttons, '取消任务', self.cancel.set, 'Danger')
        self.open_button = self.theme.button(buttons, '打开编辑器', self.open_editor, 'Primary')
        for column, button in enumerate((self.install_button, self.offline_button, self.refresh_button,
                                         self.cancel_button, self.open_button)):
            buttons.columnconfigure(column, weight=1)
            button.grid(row=0, column=column, sticky='ew', padx=(0, 7 if column < 4 else 0))

        location = tk.Frame(container, background=PAPER)
        location.grid(row=4, column=0, sticky='ew', pady=(12, 0))
        location.columnconfigure(0, weight=1)
        self.location = tk.Label(location, background=PAPER, foreground=MUTED, font=('Segoe UI', 9),
                                 wraplength='315p', anchor='w', justify='left')
        self.location.grid(row=0, column=0, sticky='ew', padx=(0, 10))
        self.location_button = self.theme.button(location, '更改组件位置…', self.choose_location)
        self.location_button.grid(row=0, column=1, sticky='e')

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
        self.progress.show_value(0, immediate=True)
        self.message.configure(text='正在检查组件…', background=SURFACE)
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
                    self.progress.show_value(value)
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
            label.configure(text=details, foreground=INK if status.get('ready') else MUTED)
            checked.set(component in REQUIRED and component in self.manager.catalog and not status.get('ready'))
        ready = self.editor_ready()
        self.message.configure(text=failure or ('环境就绪。' if ready else '请安装缺少的基础组件。'),
                               background=BRICK_WASH if failure else TEAL_WASH if ready else SURFACE)
        self.progress.show_value(0 if failure else 100)
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
            self.message.configure(text=f'无法启动编辑器。{error}', background=BRICK_WASH)
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

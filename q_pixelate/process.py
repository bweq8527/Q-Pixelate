from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
import time
from collections import deque
from contextlib import contextmanager
from pathlib import Path


class Cancelled(Exception):
    pass


def retry_windows_file_operation(operation):
    """Give newly exited Windows processes time to release inherited handles."""
    deadline = time.monotonic() + 1.5
    while True:
        try:
            return operation()
        except OSError as error:
            if getattr(error, 'winerror', None) not in (5, 32) or time.monotonic() >= deadline:
                raise
            time.sleep(.05)


class Runner:
    def __init__(self, cancel_event=None, progress=None, log=None, *, process_files=None):
        self.cancel_event = cancel_event or threading.Event()
        self.progress = progress or (lambda value, text: None)
        self.log = log or (lambda text: None)
        self._video_tools = None
        self._process_files = process_files

    @contextmanager
    def workspace(self, prefix, *, fallback=None):
        if self._process_files is not None:
            with self._process_files.workspace() as path:
                yield path
        else:
            directory = tempfile.TemporaryDirectory(prefix=prefix, dir=fallback)
            try:
                yield Path(directory.name)
            finally:
                retry_windows_file_operation(directory.cleanup)

    def check(self):
        if self.cancel_event.is_set():
            raise Cancelled('任务已取消。')

    def report(self, value, text):
        self.check()
        self.progress(max(0, min(100, int(value))), text)

    def executable(self, name):
        if str(name) in ('ffmpeg', 'ffprobe'):
            if self._video_tools is None:
                from .components import ComponentManager
                self._video_tools = ComponentManager().ffmpeg_tools()
            return self._video_tools.get(name)
        return shutil.which(str(name))

    def run(self, args, duration=None, base=0, weight=0, *, input_chunks=None):
        self.check()
        args = [str(arg) for arg in args]
        executable = self.executable(args[0])
        if not executable:
            raise ValueError(f'找不到 {args[0]}。请从「设置 → 环境与组件」安装或指定 FFmpeg。')
        args[0] = executable
        self.log(subprocess.list2cmdline(args))
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   stdin=subprocess.PIPE if input_chunks is not None else subprocess.DEVNULL,
                                   text=True, encoding='utf-8', errors='replace',
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        output = []
        errors = deque(maxlen=200)
        def read_stdout():
            for line in process.stdout:
                output.append(line)
                if duration and line.startswith('out_time_us='):
                    try:
                        elapsed = float(line.split('=', 1)[1]) / 1_000_000
                        self.progress(int(min(99, base + weight * elapsed / duration)), '正在处理视频…')
                    except ValueError:
                        pass
        def read_stderr():
            for line in process.stderr:
                errors.append(line)
        threads = [threading.Thread(target=read_stdout, daemon=True),
                   threading.Thread(target=read_stderr, daemon=True)]
        input_errors = []

        def write_stdin():
            try:
                for chunk in input_chunks:
                    self.check()
                    process.stdin.buffer.write(chunk)
            except (BrokenPipeError, Cancelled):
                pass
            except Exception as exc:
                input_errors.append(exc)
            finally:
                try:
                    process.stdin.close()
                except BrokenPipeError:
                    pass

        if input_chunks is not None:
            threads.append(threading.Thread(target=write_stdin, daemon=True))
        for thread in threads:
            thread.start()
        try:
            while process.poll() is None:
                self.check()
                time.sleep(.05)
            self.check()
        except BaseException:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            raise
        finally:
            for thread in threads:
                thread.join(timeout=3)
            process.stdout.close()
            process.stderr.close()
        error_text = ''.join(errors)
        if error_text:
            self.log(error_text)
        if input_errors:
            raise input_errors[0]
        if process.returncode:
            raise ValueError(f'FFmpeg 操作失败（{process.returncode}）：\n{error_text[-5000:]}')
        return ''.join(output)

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from collections import deque


class Cancelled(Exception):
    pass


class Runner:
    def __init__(self, cancel_event=None, progress=None, log=None):
        self.cancel_event = cancel_event or threading.Event()
        self.progress = progress or (lambda value, text: None)
        self.log = log or (lambda text: None)

    def check(self):
        if self.cancel_event.is_set():
            raise Cancelled('任务已取消。')

    def report(self, value, text):
        self.check()
        self.progress(max(0, min(100, int(value))), text)

    def run(self, args, duration=None, base=0, weight=0):
        self.check()
        args = [str(arg) for arg in args]
        executable = shutil.which(args[0])
        if not executable:
            raise ValueError(f'找不到 {args[0]}。请安装 FFmpeg 并将 bin 目录加入 PATH。')
        args[0] = executable
        self.log(subprocess.list2cmdline(args))
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
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
        if process.returncode:
            raise ValueError(f'FFmpeg 操作失败（{process.returncode}）：\n{error_text[-5000:]}')
        return ''.join(output)

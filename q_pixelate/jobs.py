import threading
from PySide6.QtCore import QThread, Signal
from .process import Runner, Cancelled


class Worker(QThread):
    succeeded = Signal(object)
    failed = Signal(str)
    progressed = Signal(int, str)
    logged = Signal(str)

    def __init__(self, task, parent=None):
        super().__init__(parent)
        self.task = task
        self.cancel_event = threading.Event()

    def cancel(self):
        self.cancel_event.set()

    def run(self):
        runner = Runner(self.cancel_event, self.progressed.emit, self.logged.emit)
        try:
            result = self.task(runner)
            runner.check()
            self.succeeded.emit(result)
        except Cancelled:
            self.failed.emit('任务已取消。')
        except Exception as exc:
            self.failed.emit(str(exc))

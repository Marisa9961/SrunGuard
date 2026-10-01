"""All HTTP runs in one worker; GUI requests are communicated by thread-safe events."""

import logging
from threading import Event, Lock

from PySide6.QtCore import QThread, Signal

from .monitor import ReconnectMonitor, Update
from .network import Cancelled, SrunClient
from .settings import Settings


class NetworkWorker(QThread):
    update = Signal(object)
    diagnostic = Signal(str, str)

    def __init__(self, settings: Settings, password: str, once: bool = False, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.password = password
        self.once = once
        self.stop_event = Event()
        self.wake = Event()
        self.manual = Event()
        self.command_lock = Lock()
        if once:
            self.manual.set()

    def check_now(self):
        with self.command_lock:
            self.manual.set()
            self.wake.set()

    def stop(self):
        self.stop_event.set()
        self.wake.set()

    def run(self):
        client = None
        try:
            client = SrunClient(self.settings, self.password, self.stop_event)
            client.diagnostic = self.diagnostic.emit
            monitor = ReconnectMonitor(client, self.settings, self.update.emit)
            while not self.stop_event.is_set():
                with self.command_lock:
                    self.wake.clear()
                    manual = self.manual.is_set()
                    self.manual.clear()
                result = monitor.tick(manual)
                self.update.emit(result)
                if self.once:
                    break
                self.wake.wait(result.delay)
        except Cancelled:
            pass
        except Exception as exc:
            # Log the exception class only: arguments/tracebacks may expose login URLs.
            self.diagnostic.emit("worker.exception", f"Worker exception type: {type(exc).__name__}.")
            self.update.emit(Update("error", "后台任务异常，已停止。请检查配置后重试。", 0,
                                    "worker.error", "Worker failed; monitoring stopped. Check diagnostic log.",
                                    True, logging.ERROR))
        finally:
            self.password = ""
            if client is not None:
                client.password = ""

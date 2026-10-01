"""Bounded log storage and event-key throttling shared by the GUI and file output."""

from collections import OrderedDict
from dataclasses import dataclass
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import time


@dataclass
class _Entry:
    emitted_at: float
    suppressed: int = 0


class LogLimiter:
    """Throttle by stable event key, not by changing counters/countdown text."""

    def __init__(self, interval: int = 300, clock=time.monotonic):
        self.interval = interval
        self.clock = clock
        self.entries: OrderedDict[str, _Entry] = OrderedDict()

    def accept(self, key: str, message: str, force: bool = False) -> str | None:
        now = self.clock()
        entry = self.entries.get(key)
        if entry is not None and not force and now - entry.emitted_at < self.interval:
            entry.suppressed += 1
            return None
        if entry is not None and entry.suppressed:
            message += f" ({entry.suppressed} repeats suppressed.)"
        self.entries[key] = _Entry(now)
        self.entries.move_to_end(key)
        if len(self.entries) > 128:
            self.entries.popitem(last=False)
        return message

    def flush(self) -> str | None:
        count = sum(entry.suppressed for entry in self.entries.values())
        self.entries.clear()
        return f"Suppressed {count} repeated messages." if count else None


def log_path(directory: str, default: Path) -> Path:
    return (Path(directory).expanduser() if directory else default).resolve() / "guard.log"


def diagnostic_path(default: Path) -> Path:
    return default.resolve() / "logs" / "diagnostics.log"


def open_log_handler(path: Path, *, detailed: bool = False) -> RotatingFileHandler:
    """Open eagerly: a failed destination must not replace the current working handler."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(path, maxBytes=2_000_000 if detailed else 1_000_000,
                                  backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                                           datefmt="%Y-%m-%d %H:%M:%S"))
    return handler

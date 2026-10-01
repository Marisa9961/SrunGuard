"""Reconnect state machine with important events separated from diagnostic detail."""

from dataclasses import dataclass
import logging
import time
from typing import Callable

from .durations import format_duration, short_duration
from .network import Cancelled, NetworkError, SrunClient
from .protocol import ProtocolError
from .settings import Settings


@dataclass(frozen=True)
class Update:
    state: str
    message: str
    delay: float
    log_key: str | None = None
    log_message: str | None = None
    important: bool = True
    level: int = logging.INFO


class ReconnectMonitor:
    def __init__(self, client: SrunClient, settings: Settings,
                 notify: Callable[[Update], None], clock=time.monotonic):
        self.client = client
        self.settings = settings
        self.notify = notify
        self.clock = clock
        self.missed = 0
        self.failures = 0
        self.retry_at = 0.0
        self.was_online = False
        self.outage_active = False

    def _online(self, after_login: bool = False) -> Update:
        recovered = self.outage_active
        first_check = not self.was_online
        self.missed = self.failures = 0
        self.retry_at = 0.0
        self.was_online = True
        self.outage_active = False
        if recovered:
            text = "Login success, network restored." if after_login else "Network restored without login."
            return Update("online", "互联网连接正常", self.settings.interval,
                          "network.restored", text)
        return Update("online", "互联网连接正常", self.settings.interval,
                      "network.online" if first_check else None, "Connectivity check passed.",
                      important=False, level=logging.DEBUG)

    def tick(self, manual: bool = False) -> Update:
        self.client.check_cancelled()
        self.notify(Update("checking", "正在检查互联网连通性…", 0,
                           "check.start", "Connectivity check started.", False, logging.DEBUG))
        if self.client.online():
            return self._online()
        self.was_online = False
        self.missed += 1
        if not manual and self.missed < self.settings.threshold:
            return Update("suspect", f"检测未通过（{self.missed}/{self.settings.threshold}），等待再次确认",
                          self.settings.interval, "network.suspect",
                          f"Connectivity check failed; confirmation={self.missed}/{self.settings.threshold}.",
                          False, logging.DEBUG)
        remaining = self.retry_at - self.clock()
        if not manual and remaining > 0:
            return Update("cooldown", f"仍未联网；约 {format_duration(remaining)}后重试认证",
                          min(self.settings.interval, remaining), "retry.wait",
                          f"Cooldown active; remaining={short_duration(remaining)}. Login skipped.",
                          False, logging.DEBUG)
        new_outage = not self.outage_active
        self.outage_active = True
        self.notify(Update("reconnecting", "已确认断网，正在向校园网认证…", 0,
                           "network.down" if new_outage else "auth.retry",
                           "Network down, try login once." if new_outage else
                           f"Retrying login; attempt={self.failures + 1}, manual={manual}.",
                           new_outage, logging.WARNING if new_outage else logging.DEBUG))
        try:
            self.client.login()
            if self.client.stop.wait(2):
                raise Cancelled()
            if self.client.online():
                return self._online(after_login=True)
            reason = "Internet still unavailable."
        except (NetworkError, ProtocolError) as exc:
            reason = str(exc)  # Our sanitized errors only, never a raw HTTP exception.
        self.failures += 1
        backoff = min(self.settings.max_backoff,
                      self.settings.retry_interval * 2 ** min(self.failures - 1, 20))
        self.retry_at = self.clock() + backoff
        return Update("cooldown", f"{reason}；第 {self.failures} 次失败，{format_duration(backoff)}后重试",
                      min(self.settings.interval, backoff), "auth.failure:" + reason,
                      f"Login failed: {reason} Consecutive failures={self.failures}; "
                      f"enter cooldown for {short_duration(backoff)}.", True, logging.WARNING)

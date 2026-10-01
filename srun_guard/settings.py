"""Non-secret settings on disk; passwords only in the native OS credential vault."""

from dataclasses import asdict, dataclass, fields
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    username: str = ""
    portal: str = ""
    ac_id: str = "0"
    interval: int = 1800
    retry_interval: int = 10800
    timeout: int = 6
    threshold: int = 2
    max_backoff: int = 10800
    password_hmac: bool = False
    remember: bool = False
    auto_start: bool = False
    minimize_to_tray: bool = True
    log_directory: str = ""
    log_repeat_interval: int = 300
    schema_version: int = 2

    def validate(self, credentials: bool = True) -> None:
        if type(self.schema_version) is not int or self.schema_version != 2:
            raise ValueError("不支持的配置版本")
        if credentials and not self.portal.strip():
            raise ValueError("请输入认证网址")
        if credentials and not self.username.strip():
            raise ValueError("请输入校园网账号")
        try:
            url = urlsplit(self.portal)
            valid = (url.scheme in ("https", "http") and url.hostname and
                     not url.username and not url.password and
                     url.path in ("", "/") and not url.query and not url.fragment)
            _ = url.port
        except ValueError:
            valid = False
        if (self.portal or credentials) and not valid:
            raise ValueError("认证地址应为 http(s)://主机[:端口]，不要附带路径或参数")
        if not self.ac_id.isascii() or not self.ac_id.isdigit():
            raise ValueError("AC ID 必须为非负整数")
        for label, value, low, high in (
            ("检测间隔（秒）", self.interval, 5, 86400),
            ("初始重试间隔（秒）", self.retry_interval, 5, 86400),
            ("网络超时", self.timeout, 2, 30), ("断网确认次数", self.threshold, 1, 10),
            ("最大退避（秒）", self.max_backoff, 30, 604800),
            ("重复日志间隔（秒）", self.log_repeat_interval, 30, 86400),
        ):
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"{label}必须在 {low}–{high} 之间")
        if self.max_backoff < self.retry_interval:
            raise ValueError("最大退避不能小于初始重试间隔")
        if self.log_directory and ("\x00" in self.log_directory or
                                   not Path(self.log_directory).expanduser().is_absolute()):
            raise ValueError("日志目录必须是绝对路径，或留空使用默认目录")


def data_directory() -> Path:
    if sys.platform == "win32":
        root = Path(os.environ.get("APPDATA", str(Path.home())))
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    path = root / "SrunGuard"
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_settings(path: Path) -> Settings:
    if not path.exists():
        return Settings()
    values = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(values, dict):
        raise ValueError("配置文件不是 JSON 对象")
    defaults = asdict(Settings())
    version = values.get("schema_version", 1)
    if type(version) is not int or version not in (1, 2):
        raise ValueError("不支持的配置版本")
    # Migrate only known legacy defaults, never every user-selected short interval.
    legacy_timing = (values.get("interval"), values.get("retry_interval", values.get("interval")),
                     values.get("max_backoff", 300))
    if version == 1 and all(type(value) is int for value in legacy_timing) and legacy_timing in (
            (30, 30, 300), (30, 30, 10800), (30, 10800, 10800)):
        for name in ("interval", "retry_interval", "max_backoff"):
            values[name] = defaults[name]
    elif "retry_interval" not in values and "interval" in values:
        values["retry_interval"] = values["interval"]
    values["schema_version"] = 2
    clean = {}
    for item in fields(Settings):
        value = values.get(item.name, defaults[item.name])
        if type(value) is not type(defaults[item.name]):
            raise ValueError("配置字段类型不正确")
        clean[item.name] = value
    result = Settings(**clean)
    result.validate(credentials=False)
    return result


def save_settings(path: Path, settings: Settings) -> None:
    settings.validate(credentials=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(asdict(settings), ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


class CredentialStore:
    """Reject plaintext/third-party fallback backends rather than silently downgrade."""

    def _backend(self):
        import keyring
        if sys.platform == "win32":
            # Explicit import also works in compiled builds without entry-point discovery.
            from keyring.backends.Windows import WinVaultKeyring
            backend = WinVaultKeyring()
        else:
            backend = keyring.get_keyring()
        module = type(backend).__module__
        if module not in {"keyring.backends.Windows", "keyring.backends.macOS",
                          "keyring.backends.SecretService", "keyring.backends.kwallet"}:
            raise RuntimeError("没有可用的系统凭据库；请取消记住密码并手动输入")
        return backend

    @staticmethod
    def _service(settings: Settings) -> str:
        return "SrunGuard:" + settings.portal.rstrip("/").lower()

    def get(self, settings: Settings) -> str:
        return self._backend().get_password(self._service(settings), settings.username) or ""

    def set(self, settings: Settings, password: str) -> None:
        self._backend().set_password(self._service(settings), settings.username, password)

    def delete(self, settings: Settings) -> None:
        from keyring.errors import PasswordDeleteError
        try:
            self._backend().delete_password(self._service(settings), settings.username)
        except PasswordDeleteError:
            pass

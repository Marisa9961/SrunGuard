"""Windows per-user startup registration; no elevation or shell commands needed."""

from contextlib import contextmanager
from pathlib import Path
import subprocess
import sys

if sys.platform == "win32":
    import winreg


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "SrunGuard"


class StartupError(OSError):
    """A startup registration could not be applied or restored."""


def startup_command() -> str:
    if "__compiled__" in globals():
        # Nuitka onefile keeps the original executable in argv[0]. Its
        # sys.executable/__file__ may point into the temporary extraction.
        args = [str(Path(sys.argv[0]).resolve()), "--startup"]
    else:
        interpreter = Path(sys.executable)
        pythonw = interpreter.with_name("pythonw.exe")
        if pythonw.is_file():
            interpreter = pythonw
        args = [str(interpreter.resolve()),
                str(Path(__file__).resolve().parent.parent / "main.py"), "--startup"]
    command = subprocess.list2cmdline(args)
    if len(command.encode("utf-16-le")) // 2 > 260:
        raise StartupError("启动路径过长，请将程序移到较短的目录后重新启用开机自启动。")
    return command


def _read_value() -> tuple[str, int] | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return winreg.QueryValueEx(key, VALUE_NAME)
    except FileNotFoundError:
        return None


def _write_value(value: tuple[str, int] | None) -> None:
    if value is None:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, VALUE_NAME)
        except FileNotFoundError:
            pass
    else:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, value[1], value[0])


@contextmanager
def configure_startup(enabled: bool):
    """Roll back the Run value if saving the corresponding settings fails."""
    if sys.platform != "win32":
        if enabled:
            raise StartupError("开机自启动目前仅支持 Windows。")
        yield
        return
    try:
        previous = _read_value()
        desired = (startup_command(), winreg.REG_SZ) if enabled else None
        changed = previous != desired
        if changed:
            _write_value(desired)
    except StartupError:
        raise
    except OSError as exc:
        raise StartupError("无法更新开机自启动，请检查当前用户的启动项权限。") from exc
    try:
        yield
    except Exception:
        if changed:
            try:
                _write_value(previous)
            except OSError as exc:
                raise StartupError("保存失败，且无法恢复原开机自启动设置，请检查 Windows 启动项。") from exc
        raise

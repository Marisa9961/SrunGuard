from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from srun_guard import startup


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.platform = patch.object(startup.sys, "platform", "win32")
        self.platform.start()
        self.addCleanup(self.platform.stop)
        self.registry = SimpleNamespace(REG_SZ=1)
        self.winreg = patch.object(startup, "winreg", self.registry, create=True)
        self.winreg.start()
        self.addCleanup(self.winreg.stop)

    def test_compiled_command_uses_original_exe_with_spaces_and_unicode(self):
        executable = Path.cwd() / "Program Files" / "校园网" / "SrunGuard.exe"
        with patch.object(startup, "__compiled__", True, create=True), \
                patch.object(startup.sys, "argv", [str(executable), "--self-test", "report.json"]):
            command = startup.startup_command()
        self.assertEqual(command, subprocess.list2cmdline([str(executable.resolve()), "--startup"]))
        self.assertNotIn("--self-test", command)

    def test_source_command_uses_pythonw_and_absolute_entry_point(self):
        interpreter = Path.cwd() / "Python Environment" / "python.exe"
        with patch.object(startup.sys, "executable", str(interpreter)), \
                patch.object(Path, "is_file", return_value=True):
            command = startup.startup_command()
        entry = Path(startup.__file__).resolve().parent.parent / "main.py"
        self.assertEqual(command, subprocess.list2cmdline([
            str(interpreter.with_name("pythonw.exe").resolve()), str(entry), "--startup"]))

    def test_source_command_falls_back_when_pythonw_is_missing(self):
        with patch.object(Path, "is_file", return_value=False):
            command = startup.startup_command()
        self.assertTrue(command.startswith(subprocess.list2cmdline([str(Path(startup.sys.executable).resolve())])))

    def test_long_command_is_rejected(self):
        executable = str(Path.cwd() / ("x" * 250) / "SrunGuard.exe")
        with patch.object(startup, "__compiled__", True, create=True), \
                patch.object(startup.sys, "argv", [executable]), self.assertRaises(startup.StartupError):
            startup.startup_command()

    def test_enable_disable_and_no_op(self):
        for enabled, previous, expected in (
                (True, None, [("app.exe --startup", 1)]),
                (False, ("app.exe --startup", 1), [None]),
                (True, ("app.exe --startup", 1), []),
                (False, None, [])):
            with self.subTest(enabled=enabled, previous=previous), \
                    patch.object(startup, "_read_value", return_value=previous), \
                    patch.object(startup, "startup_command", return_value="app.exe --startup"), \
                    patch.object(startup, "_write_value") as write:
                with startup.configure_startup(enabled):
                    pass
                self.assertEqual([call.args[0] for call in write.call_args_list], expected)

    def test_failed_config_save_restores_exact_previous_registration(self):
        for enabled, previous in ((True, None), (True, ("old.exe", 2)), (False, ("old.exe", 2))):
            with self.subTest(enabled=enabled, previous=previous), \
                    patch.object(startup, "_read_value", return_value=previous), \
                    patch.object(startup, "startup_command", return_value="new.exe --startup"), \
                    patch.object(startup, "_write_value") as write:
                with self.assertRaises(OSError):
                    with startup.configure_startup(enabled):
                        raise OSError("config write failed")
                self.assertEqual(write.call_args_list[-1].args, (previous,))

    def test_permission_error_prevents_config_save(self):
        saved = False
        with patch.object(startup, "_read_value", side_effect=PermissionError), \
                self.assertRaises(startup.StartupError):
            with startup.configure_startup(True):
                saved = True
        self.assertFalse(saved)

    def test_non_windows_enable_is_rejected_and_disable_is_harmless(self):
        with patch.object(startup.sys, "platform", "linux"):
            with self.assertRaises(startup.StartupError):
                with startup.configure_startup(True):
                    pass
            with startup.configure_startup(False):
                pass

    def test_registry_writes_only_current_user_app_value(self):
        registry = MagicMock(HKEY_CURRENT_USER=1, KEY_SET_VALUE=2, REG_SZ=1)
        key = registry.CreateKeyEx.return_value.__enter__.return_value
        with patch.object(startup, "winreg", registry):
            startup._write_value(("app.exe --startup", 1))
        registry.CreateKeyEx.assert_called_once_with(1, startup.RUN_KEY, 0, 2)
        registry.SetValueEx.assert_called_once_with(key, "SrunGuard", 0, 1, "app.exe --startup")

    def test_missing_registry_entry_is_harmless_but_access_denied_is_not(self):
        registry = MagicMock()
        with patch.object(startup, "winreg", registry):
            registry.OpenKey.side_effect = FileNotFoundError
            self.assertIsNone(startup._read_value())
            startup._write_value(None)
            registry.OpenKey.side_effect = PermissionError
            with self.assertRaises(PermissionError):
                startup._read_value()
            with self.assertRaises(PermissionError):
                startup._write_value(None)


if __name__ == "__main__":
    unittest.main()

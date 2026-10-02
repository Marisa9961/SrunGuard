"""Offscreen GUI/worker smoke tests: no campus access or real credentials needed."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import Mock, patch

from PySide6.QtGui import QCloseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from srun_guard.gui import MainWindow, STYLE
from srun_guard.monitor import ReconnectMonitor, Update
from srun_guard.network import Cancelled
from srun_guard.settings import Settings, save_settings


class OnlineClient:
    def __init__(self, settings, password, stop):
        self.stop = stop
        self.password = password

    def check_cancelled(self):
        if self.stop.is_set():
            raise Cancelled()

    def online(self):
        self.check_cancelled()
        return True

    def login(self):
        raise AssertionError("online clients must not attempt login")


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        cls.app.setStyleSheet(STYLE)

    def setUp(self):
        self.directory = TemporaryDirectory()
        self.window = MainWindow(Path(self.directory.name), onboarding=False)
        self.window.portal.setText("https://portal.example.org")
        self.window.username.setText("test")
        self.window.password.setText("test-password")
        self.window.show()

    def tearDown(self):
        if self.window.worker:
            self.window.stop_guard()
            self.wait_for(lambda: self.window.worker is None)
        self.window._quit_now()
        self.window.hide()
        self.window.deleteLater()
        self.app.processEvents()
        self.directory.cleanup()

    def wait_for(self, predicate):
        deadline = time.monotonic() + 4
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertTrue(predicate(), "GUI worker did not finish in time")

    @patch("srun_guard.worker.SrunClient", OnlineClient)
    def test_one_shot_runs_and_releases_worker(self):
        self.window.check_now()
        self.assertFalse(self.window.basic_settings.isEnabled())
        self.assertTrue(self.window.tabs.isEnabled())
        self.wait_for(lambda: self.window.worker is None)
        self.assertIn("网络在线", self.window.state_label.text())
        self.assertTrue(self.window.basic_settings.isEnabled())
        config = (Path(self.directory.name) / "settings.json").read_text(encoding="utf-8")
        self.assertNotIn("test-password", config)
        self.assertNotIn("test-password", self.window.logs.toPlainText())

    @patch("srun_guard.worker.SrunClient", OnlineClient)
    def test_guard_start_stop(self):
        self.window.start_guard()
        self.wait_for(lambda: "网络在线" in self.window.state_label.text())
        self.assertTrue(self.window.worker.isRunning())
        self.window.stop_guard()
        self.wait_for(lambda: self.window.worker is None)
        self.assertIn("已暂停", self.window.state_label.text())

    @patch("srun_guard.worker.SrunClient", OnlineClient)
    def test_quit_waits_for_worker(self):
        self.window.start_guard()
        with patch.object(self.window, "_quit_now") as quit_now:
            self.window.request_exit()
            self.wait_for(lambda: self.window.worker is None)
            quit_now.assert_called_once()

    def test_tray_hide_and_restore(self):
        with patch("srun_guard.gui.QSystemTrayIcon.isSystemTrayAvailable", return_value=True), \
                patch.object(self.window.tray, "show"), patch.object(self.window.tray, "showMessage"):
            self.window._hide_to_tray()
            self.assertFalse(self.window.isVisible())
            self.window.show_window()
            self.assertTrue(self.window.isVisible())

    def test_unchecking_remember_removes_old_credential(self):
        self.window.saved = Settings(username="test", portal="https://portal.example.org", remember=True)
        self.window.remember.setChecked(False)
        with patch.object(self.window.vault, "delete") as delete:
            self.assertIsNotNone(self.window.save())
            delete.assert_called_once()

    def test_portal_case_change_does_not_delete_new_credential(self):
        self.window.saved = Settings(username="test", portal="https://portal.example.org", remember=True)
        self.window.portal.setText("https://PORTAL.EXAMPLE.ORG/")
        self.window.remember.setChecked(True)
        with patch.object(self.window.vault, "set") as store, \
                patch.object(self.window.vault, "delete") as delete:
            self.assertIsNotNone(self.window.save())
            store.assert_called_once()
            delete.assert_not_called()

    def test_first_run_prompt_is_shown_automatically(self):
        self.window._quit_now()
        self.window.hide()
        self.window.deleteLater()
        self.app.processEvents()
        self.window = MainWindow(Path(self.directory.name))
        self.window.show()
        self.wait_for(lambda: self.window.setup_dialog is not None)
        self.assertEqual(self.window.setup_dialog.portal.text(), "")
        self.assertEqual(self.window.setup_dialog.username.text(), "")
        self.assertEqual(self.window.setup_dialog.password.text(), "")
        self.assertIsNone(self.window.worker)

    def test_setup_validates_saves_and_does_not_start_network(self):
        self.window.portal.clear()
        self.window.username.clear()
        self.window.password.clear()
        self.window._show_setup()
        dialog = self.window.setup_dialog
        dialog.submit()
        self.assertTrue(dialog.error.text())
        self.assertFalse(self.window.config_path.exists())
        dialog.portal.setText("https://portal.example.org")
        dialog.username.setText("test")
        dialog.submit()
        self.assertTrue(dialog.error.text())
        dialog.password.setText("private password")
        with patch("srun_guard.gui.NetworkWorker") as worker:
            dialog.submit()
            worker.assert_not_called()
        self.assertIsNone(self.window.setup_dialog)
        self.assertEqual(self.window.password.text(), "private password")
        config = self.window.config_path.read_text(encoding="utf-8")
        self.assertIn("https://portal.example.org", config)
        self.assertNotIn("private password", config)
        self.assertTrue(self.window.logs.toPlainText().isascii())

    def test_setup_cancel_keeps_guard_stopped(self):
        self.window.portal.clear()
        self.window._show_setup()
        self.window.setup_dialog.reject()
        self.assertIsNone(self.window.setup_dialog)
        self.assertIsNone(self.window.worker)
        with patch.object(self.window, "_warn") as warn, patch("srun_guard.gui.NetworkWorker") as worker:
            self.window.start_guard()
            warn.assert_called_once()
            worker.assert_not_called()

    def test_configured_profile_skips_first_run_prompt(self):
        root = Path(self.directory.name)
        save_settings(root / "settings.json", Settings(username="test", portal="https://portal.example.org"))
        self.window._quit_now()
        self.window.hide()
        self.window.deleteLater()
        self.app.processEvents()
        self.window = MainWindow(root)
        QTest.qWait(20)
        self.assertIsNone(self.window.setup_dialog)
        self.assertIsNone(self.window.worker)

    def test_duration_units_preserve_seconds_and_long_presets(self):
        editor = self.window.max_backoff
        for seconds in (31, 3599, 10800, 259200, 604800):
            editor.setValue(seconds)
            for index in (2, 1, 0, 2, 0):
                editor.unit.setCurrentIndex(index)
                self.assertEqual(editor.value(), seconds)
        editor._preset_selected(editor.presets.findData(604800))
        self.assertEqual(editor.value(), 604800)
        self.assertEqual(editor.unit.currentData(), 3600)
        self.assertEqual(editor.number.value(), 168)
        self.assertEqual(self.window.tabs.tabText(2), "日志")

    def test_new_log_directory_receives_only_new_records(self):
        previous = Path(self.window.log_handler.baseFilename)
        destination = Path(self.directory.name) / "自定义日志"
        self.window.log_directory.setText(str(destination))
        self.assertIsNotNone(self.window.save())
        self.window._log("unique record in new file")
        current = destination / "guard.log"
        # Windows TEMP may use an 8.3 alias; log paths resolve to long names.
        # Compare file identity, not the spelling of the two paths.
        self.assertTrue(Path(self.window.log_handler.baseFilename).samefile(current))
        self.assertIn("unique record", current.read_text(encoding="utf-8"))
        self.assertNotIn("unique record", previous.read_text(encoding="utf-8"))
        self.assertIn(str(current.resolve()), self.window.active_log_path.text())

    def test_invalid_log_directory_preserves_working_handler(self):
        previous = self.window.log_handler
        invalid = Path(self.directory.name) / "not-a-directory"
        invalid.write_text("file")
        self.window.log_directory.setText(str(invalid))
        with patch.object(self.window, "_warn") as warn:
            self.assertIsNone(self.window.save())
            warn.assert_called_once()
        self.assertIs(self.window.log_handler, previous)
        self.window._log("still working")
        self.assertIn("still working", Path(previous.baseFilename).read_text(encoding="utf-8"))

    def test_unavailable_custom_directory_falls_back_on_startup(self):
        self.window._quit_now()
        self.window.hide()
        self.window.deleteLater()
        self.app.processEvents()
        root = Path(self.directory.name)
        invalid = root / "unavailable"
        invalid.write_text("not a directory")
        save_settings(root / "settings.json", Settings(log_directory=str(invalid)))
        self.window = MainWindow(root, onboarding=False)
        self.assertTrue(Path(self.window.log_handler.baseFilename).samefile(root / "guard.log"))
        self.assertIn("Log directory unavailable", self.window.logs.toPlainText())

    def test_config_save_failure_closes_candidate_and_keeps_old_handler(self):
        previous = self.window.log_handler
        destination = Path(self.directory.name) / "other"
        self.window.log_directory.setText(str(destination))
        with patch("srun_guard.gui.save_settings", side_effect=OSError), \
                patch.object(self.window, "_warn"):
            self.assertIsNone(self.window.save())
        self.assertIs(self.window.log_handler, previous)
        # Windows cannot remove open log files; this also checks candidate cleanup.
        (destination / "guard.log").unlink()

    def test_log_settings_can_be_saved_before_entering_account(self):
        self.window.username.clear()
        self.assertIsNotNone(self.window.save())

    def test_countdown_and_repeated_failures_do_not_flood_either_sink(self):
        before = self.window.logs.document().blockCount()
        for number in range(100):
            self.window._update(Update("checking", "正在检测", 0))
            self.window._update(Update("reconnecting", "认证中", 0))
            self.window._update(Update("cooldown", f"认证失败 {number}", 30, "auth.failure:test"))
            self.window._update(Update("cooldown", f"还有 {100-number} 秒", 30))
        self.assertEqual(self.window.logs.document().blockCount(), before + 1)
        file_text = Path(self.window.log_handler.baseFilename).read_text(encoding="utf-8")
        self.assertEqual(file_text.count("Login failed;"), 1)
        self.assertTrue(file_text.isascii())
        self.assertNotIn("还有", file_text)
        self.window._update(Update("online", "恢复联网", 30, "network.restored"))
        self.assertIn("Suppressed 99 repeated messages.", self.window.logs.toPlainText())
        self.assertIn("Network restored.", self.window.logs.toPlainText())

    def test_quit_does_not_veto_application_close_event(self):
        self.window.quitting = True
        event = QCloseEvent()
        self.window.closeEvent(event)
        self.assertTrue(event.isAccepted())

    @patch("srun_guard.worker.SrunClient", OnlineClient)
    def test_logs_remain_accessible_while_guard_runs(self):
        self.window.start_guard()
        self.window.tabs.setCurrentIndex(2)
        self.assertTrue(self.window.logs.isEnabled())
        self.assertFalse(self.window.log_settings.isEnabled())
        self.assertFalse(self.window.network_settings.isEnabled())

    @patch("srun_guard.worker.SrunClient", OnlineClient)
    def test_primary_and_tray_share_start_pause_button(self):
        self.assertEqual(self.window.guard_button.text(), "启动守护")
        self.window.guard_button.click()
        self.wait_for(lambda: "网络在线" in self.window.state_label.text())
        self.assertEqual(self.window.guard_button.text(), "暂停守护")
        self.assertEqual(self.window.tray_guard.text(), "暂停守护")
        self.assertTrue(self.window.guard_button.isEnabled())
        self.window.tray_guard.trigger()
        self.assertFalse(self.window.guard_button.isEnabled())
        self.assertEqual(self.window.guard_button.text(), "正在停止…")
        self.wait_for(lambda: self.window.worker is None)
        self.assertEqual(self.window.guard_button.text(), "启动守护")
        self.assertTrue(self.window.guard_button.isEnabled())

    @patch("srun_guard.worker.SrunClient", OnlineClient)
    def test_primary_cancels_single_check(self):
        self.window.check_now()
        self.assertEqual(self.window.guard_button.text(), "取消检测")
        self.window.guard_button.click()
        self.assertFalse(self.window.guard_button.isEnabled())
        self.wait_for(lambda: self.window.worker is None)
        self.assertEqual(self.window.guard_button.text(), "启动守护")
        self.assertTrue(self.window.guard_button.isEnabled())

    def test_unexpected_error_resets_button_without_leaking_details(self):
        with patch("srun_guard.worker.SrunClient", side_effect=RuntimeError("private-password-in-url")):
            self.window.guard_button.click()
            self.wait_for(lambda: self.window.worker is None)
        self.assertEqual(self.window.guard_button.text(), "启动守护")
        self.assertTrue(self.window.guard_button.isEnabled())
        important = self.window.logs.toPlainText()
        detailed = Path(self.window.detail_handler.baseFilename).read_text(encoding="utf-8")
        self.assertIn("[ERROR] Worker failed", important)
        self.assertIn("RuntimeError", detailed)
        self.assertNotIn("private-password-in-url", important + detailed)

    def test_save_is_secondary_and_never_starts_network(self):
        self.assertIn(self.window.save_action, self.window.more_button.menu().actions())
        with patch("srun_guard.gui.NetworkWorker") as worker:
            self.window.save_action.trigger()
            worker.assert_not_called()
        self.assertTrue(self.window.config_path.exists())
        self.assertNotIn("Settings saved", self.window.logs.toPlainText())
        self.assertIn("Settings saved", Path(self.window.detail_handler.baseFilename).read_text(encoding="utf-8"))

    @patch("srun_guard.worker.SrunClient", OnlineClient)
    def test_migrated_values_match_display_and_worker(self):
        root = Path(self.directory.name)
        self.window._quit_now()
        self.window.hide()
        self.window.deleteLater()
        self.app.processEvents()
        (root / "settings.json").write_text('{"interval":30,"retry_interval":30,"max_backoff":300}')
        self.window = MainWindow(root, onboarding=False)
        self.assertEqual(self.window.interval.number.value(), 30)
        self.assertEqual(self.window.interval.unit.currentText(), "分钟")
        self.assertEqual(self.window.retry_interval.number.value(), 3)
        self.assertEqual(self.window.retry_interval.unit.currentText(), "小时")
        self.window.portal.setText("https://portal.example.org")
        self.window.username.setText("test")
        self.window.password.setText("password")
        self.window.start_guard()
        self.assertEqual(self.window.worker.settings.interval, 1800)
        self.assertEqual(self.window.worker.settings.retry_interval, 10800)
        self.assertEqual(self.window.worker.settings.max_backoff, 10800)

    def test_diagnostics_are_separate_and_throttled(self):
        self.assertTrue(self.window.log_options.isHidden())
        before = self.window.logs.toPlainText()
        for number in range(100):
            self.window._diagnostic("retry.wait", f"Cooldown active; remaining={100-number}s.")
        self.window._flush_log_summary()
        self.assertEqual(self.window.logs.toPlainText(), before)
        detailed = Path(self.window.detail_handler.baseFilename)
        self.assertTrue(detailed.samefile(Path(self.directory.name) / "logs" / "diagnostics.log"))
        text = detailed.read_text(encoding="utf-8")
        self.assertEqual(text.count("Cooldown active"), 1)
        self.assertIn("Suppressed 99 repeated messages", text)
        self.assertIn("[DEBUG]", text)
        self.assertNotIn("Cooldown active", Path(self.window.log_handler.baseFilename).read_text(encoding="utf-8"))

    def test_gui_outage_events_have_timestamps_and_levels(self):
        self.window.logs.clear()
        client = Mock()
        client.stop.wait.return_value = False
        client.online.side_effect = [False, False, True]
        monitor = ReconnectMonitor(client, Settings(), self.window._update)
        self.window._update(monitor.tick())
        self.assertEqual(self.window.logs.toPlainText(), "")
        self.window._update(monitor.tick())
        lines = self.window.logs.toPlainText().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertRegex(lines[0], r"^\d{4}-\d{2}-\d{2} .*\[WARNING\] Network down, try login once\.$")
        self.assertIn("[INFO] Login success, network restored.", lines[1])
        self.assertNotIn("Connectivity check started", self.window.logs.toPlainText())

    def test_no_tray_keeps_window_reachable(self):
        with patch("srun_guard.gui.QSystemTrayIcon.isSystemTrayAvailable", return_value=False):
            self.window._hide_to_tray()
            self.assertTrue(self.window.isVisible())


if __name__ == "__main__":
    unittest.main()

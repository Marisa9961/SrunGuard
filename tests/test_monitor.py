from unittest.mock import Mock
import unittest

from srun_guard.monitor import ReconnectMonitor
from srun_guard.network import Cancelled
from srun_guard.protocol import ProtocolError
from srun_guard.settings import Settings


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.client.stop.wait.return_value = False
        self.client.online.return_value = False
        self.clock = Mock(return_value=1000.0)
        self.updates = []
        self.monitor = ReconnectMonitor(self.client, Settings(interval=10, retry_interval=10, max_backoff=40),
                                        self.updates.append, self.clock)

    def test_online_no_login(self):
        self.client.online.return_value = True
        self.assertEqual(self.monitor.tick().state, "online")
        self.client.login.assert_not_called()

    def test_threshold_and_verified_reconnect(self):
        self.assertEqual(self.monitor.tick().state, "suspect")
        self.client.login.assert_not_called()
        self.client.online.side_effect = [False, True]
        self.assertEqual(self.monitor.tick().state, "online")
        self.client.login.assert_called_once()
        self.assertEqual(self.monitor.failures, 0)
        self.assertEqual(self.monitor.missed, 0)

    def test_api_success_without_internet_is_failure(self):
        result = self.monitor.tick(manual=True)
        self.assertEqual(result.state, "cooldown")
        self.assertEqual(self.monitor.failures, 1)
        self.assertIn("Internet still unavailable", result.log_message)

    def test_backoff_caps_and_avoids_repeated_login(self):
        self.client.login.side_effect = ProtocolError("Authentication rejected.")
        self.monitor.tick(manual=True)
        self.assertEqual(self.monitor.retry_at, 1010)
        self.monitor.tick()
        self.client.login.assert_called_once()
        for now, expected in ((1010, 1030), (1030, 1070), (1070, 1110)):
            self.clock.return_value = now
            self.monitor.tick()
            self.assertEqual(self.monitor.retry_at, expected)

    def test_checks_continue_during_cooldown_and_reset_on_recovery(self):
        self.monitor.tick(manual=True)
        self.client.online.return_value = True
        self.assertEqual(self.monitor.tick().state, "online")
        self.assertEqual(self.monitor.failures, 0)
        self.assertEqual(self.monitor.retry_at, 0)

    def test_manual_bypasses_backoff(self):
        self.client.login.side_effect = ProtocolError("Failed.")
        self.monitor.tick(manual=True)
        self.monitor.tick(manual=True)
        self.assertEqual(self.client.login.call_count, 2)

    def test_independent_retry_interval_with_hour_backoff(self):
        self.monitor.settings = Settings(interval=30, retry_interval=10800, max_backoff=259200)
        self.client.login.side_effect = ProtocolError("Failed.")
        result = self.monitor.tick(manual=True)
        self.assertEqual(self.monitor.retry_at, 11800)
        self.assertEqual(result.delay, 30)
        self.assertIn("3 小时", result.message)
        waiting = self.monitor.tick()
        self.assertEqual(waiting.log_key, "retry.wait")
        self.assertFalse(waiting.important)
        self.clock.return_value = 11800
        self.monitor.tick()
        self.assertEqual(self.monitor.retry_at, 33400)

    def test_short_retry_base_eventually_reaches_week_cap(self):
        self.monitor.settings = Settings(retry_interval=5, max_backoff=604800)
        self.client.login.side_effect = ProtocolError("Failed.")
        for _ in range(30):
            self.monitor.tick(manual=True)
        self.assertEqual(self.monitor.retry_at, 1000 + 604800)

    def test_repeated_online_checks_are_silent_but_recovery_is_logged(self):
        self.client.online.return_value = True
        self.assertEqual(self.monitor.tick().log_key, "network.online")
        for _ in range(100):
            self.assertIsNone(self.monitor.tick().log_key)
        self.client.online.return_value = False
        self.assertEqual(self.monitor.tick().log_key, "network.suspect")
        self.client.online.return_value = True
        self.assertEqual(self.monitor.tick().log_key, "network.online")

    def test_default_cooldown_is_three_hours_and_logs_are_english(self):
        self.monitor.settings = Settings()
        self.client.login.side_effect = ProtocolError("Invalid password.")
        result = self.monitor.tick(manual=True)
        self.assertEqual(self.monitor.retry_at, 1000 + 10800)
        self.assertEqual(result.delay, 1800)
        self.assertTrue(result.log_message.isascii())
        self.assertIn("cooldown for 3h", result.log_message)
        self.assertFalse(self.monitor.tick().important)
        self.client.login.assert_called_once()

    def test_important_events_match_outage_and_recovery(self):
        self.client.online.side_effect = [False, False, True]
        suspect = self.monitor.tick()
        self.assertFalse(suspect.important)
        result = self.monitor.tick()
        important = [event for event in self.updates + [result] if event.log_key and event.important]
        self.assertEqual([event.log_key for event in important], ["network.down", "network.restored"])
        self.assertEqual(important[0].log_message, "Network down, try login once.")
        self.assertEqual(important[1].log_message, "Login success, network restored.")

    def test_no_repeated_outage_events_during_continuous_failure(self):
        self.client.login.side_effect = ProtocolError("Invalid password.")
        for _ in range(10):
            self.monitor.tick(manual=True)
        down = [event for event in self.updates if event.log_key == "network.down"]
        self.assertEqual(len(down), 1)
        self.client.online.return_value = True
        recovered = self.monitor.tick()
        self.assertEqual(recovered.log_message, "Network restored without login.")
        self.assertTrue(recovered.important)

    def test_stop_during_post_login_wait(self):
        self.client.stop.wait.return_value = True
        with self.assertRaises(Cancelled):
            self.monitor.tick(manual=True)
        self.assertEqual(self.client.online.call_count, 1)


if __name__ == "__main__":
    unittest.main()

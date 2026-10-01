import logging
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from srun_guard.logging_utils import LogLimiter, log_path, open_log_handler


class LogTests(unittest.TestCase):
    def test_varying_counter_text_uses_stable_key(self):
        clock = Mock(return_value=0)
        limiter = LogLimiter(300, clock)
        self.assertEqual(limiter.accept("failure", "Failure 1."), "Failure 1.")
        for number in range(2, 102):
            self.assertIsNone(limiter.accept("failure", f"Failure {number}."))
        clock.return_value = 300
        self.assertIn("100 repeats suppressed", limiter.accept("failure", "Still offline."))
        self.assertIsNone(limiter.flush())

    def test_independent_keys_and_immediate_recovery(self):
        limiter = LogLimiter(clock=lambda: 0)
        limiter.accept("password", "Invalid password.")
        self.assertIsNone(limiter.accept("password", "Invalid password."))
        self.assertEqual(limiter.accept("network", "Request failed."), "Request failed.")
        self.assertEqual(limiter.accept("online", "Online.", force=True), "Online.")
        self.assertEqual(limiter.flush(), "Suppressed 1 repeated messages.")
        self.assertIsNone(limiter.flush())

    def test_bounded_keys(self):
        limiter = LogLimiter()
        for number in range(1000):
            limiter.accept(str(number), "message")
        self.assertEqual(len(limiter.entries), 128)

    def test_log_rotation_and_default_path(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = log_path("", root)
            self.assertEqual(path, root.resolve() / "guard.log")
            handler = open_log_handler(path)
            handler.maxBytes = 100
            logger = logging.Logger("rotation-test")
            logger.addHandler(handler)
            try:
                for number in range(50):
                    logger.info("test record %d", number)
                self.assertTrue(path.exists())
                self.assertTrue(root.joinpath("guard.log.3").exists())
                self.assertEqual(len(list(root.glob("guard.log*"))), 4)
            finally:
                handler.close()


if __name__ == "__main__":
    unittest.main()

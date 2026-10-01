import unittest

from srun_guard.durations import format_duration


class DurationTests(unittest.TestCase):
    def test_display_rounds_up_without_losing_hours(self):
        for seconds, expected in ((0, "0 秒"), (0.1, "1 秒"), (60, "1 分钟"),
                                  (3661, "1 小时 1 分钟 1 秒"), (604800, "168 小时")):
            with self.subTest(seconds=seconds):
                self.assertEqual(format_duration(seconds), expected)


if __name__ == "__main__":
    unittest.main()

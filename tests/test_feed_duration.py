import unittest

from tools.ensure_ranch_feed import mill_remaining_seconds


class FeedDurationTests(unittest.TestCase):
    def test_zero_is_only_empty_duration(self):
        self.assertEqual(mill_remaining_seconds("预计可生产0秒"), 0)
        self.assertEqual(mill_remaining_seconds("预计可生产1分30秒"), 90)
        self.assertEqual(mill_remaining_seconds("预计可生产10分"), 600)

    def test_all_units_and_whitespace(self):
        self.assertEqual(mill_remaining_seconds("预计可生产：1小时 2分 10秒"), 3730)

    def test_other_zero_seconds_does_not_authorize_refill(self):
        self.assertIsNone(mill_remaining_seconds("生产速度0秒"))
        self.assertIsNone(mill_remaining_seconds("预计可生产?秒"))


if __name__ == "__main__":
    unittest.main()

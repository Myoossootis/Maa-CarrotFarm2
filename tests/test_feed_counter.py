import unittest

from tools.ensure_ranch_feed import parse_sack_readings


class FeedCounterTests(unittest.TestCase):
    def test_numeric_zero_shape_is_read_consistently(self):
        self.assertEqual(parse_sack_readings([("x5u", 0.85), ("5u", 0.82)]), 50)
        self.assertEqual(parse_sack_readings([("×60", 0.96), ("60", 0.93)]), 60)

    def test_missing_leading_digit_is_not_collection_evidence(self):
        self.assertIsNone(parse_sack_readings([("0", 0.95), ("50", 0.92)]))
        self.assertIsNone(parse_sack_readings([("×60", 0.96), ("50", 0.93)]))

    def test_low_confidence_or_single_reading_is_rejected(self):
        self.assertIsNone(parse_sack_readings([("x50", 0.9), ("50", 0.5)]))
        self.assertIsNone(parse_sack_readings([("x50", 0.9)]))


if __name__ == "__main__":
    unittest.main()

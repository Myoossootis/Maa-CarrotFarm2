from __future__ import annotations

import unittest

import cv2

from tools.ensure_ranch_feed import IMAGES, locate_scaled, template


class FeedTemplateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.silo_reference = cv2.imread(
            str(IMAGES / "feed_silo_shortage_reference.png"), cv2.IMREAD_COLOR
        )
        self.mill_reference = cv2.imread(
            str(IMAGES / "feed_mill_empty_reference.png"), cv2.IMREAD_COLOR
        )
        self.assertIsNotNone(self.silo_reference)
        self.assertIsNotNone(self.mill_reference)

    def test_shortage_template_matches_supplied_capture(self) -> None:
        hit = locate_scaled(
            self.silo_reference,
            template("feed_silo_shortage.png"),
            (1.0,),
            0.99,
        )
        self.assertIsNotNone(hit)
        self.assertEqual(hit[1:3], (0, 0))

    def test_structure_template_excludes_shortage_badge(self) -> None:
        hit = locate_scaled(
            self.silo_reference,
            template("feed_silo_structure.png"),
            (1.0,),
            0.99,
        )
        self.assertIsNotNone(hit)
        self.assertEqual(hit[1:3], (20, 43))

    def test_refill_button_and_sack_templates_match_supplied_capture(self) -> None:
        button = locate_scaled(
            self.mill_reference,
            template("feed_refill_button.png"),
            (1.0,),
            0.99,
        )
        sack = locate_scaled(
            self.mill_reference,
            template("feed_sack_count.png"),
            (1.0,),
            0.99,
        )
        icon = locate_scaled(
            self.mill_reference,
            template("feed_sack_icon.png"),
            (1.0,),
            0.99,
        )
        self.assertIsNotNone(button)
        self.assertIsNotNone(sack)
        self.assertIsNotNone(icon)
        self.assertEqual(button[1:3], (203, 247))
        self.assertEqual(sack[1:3], (51, 99))
        self.assertEqual(icon[1:3], (53, 100))


if __name__ == "__main__":
    unittest.main()

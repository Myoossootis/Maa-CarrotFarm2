import random
import unittest

from tools.production_targets import (
    build_targets,
    choose_target_order,
    order_contains_unsupported_product,
)


def item(name, actual, required):
    return {
        "name": name,
        "actual": actual,
        "required": required,
        "missing": max(0, required - actual),
        "satisfied": actual >= required,
    }


class ProductionTargetTests(unittest.TestCase):
    def test_field_shortage(self):
        targets = build_targets([item("小麦", 2, 5)])
        self.assertEqual(targets[0]["route"], "field")
        self.assertEqual(targets[0]["needed"], 3)

    def test_fish_is_not_a_production_target(self):
        with self.assertRaisesRegex(RuntimeError, "unsupported order product"):
            build_targets([item("鱼肉", 2, 3)])

    def test_fish_order_is_ignored_even_with_smaller_shortage(self):
        skipped = [
            {"order": 1, "reason": "unsupported_product", "items": [item("鱼肉", 2, 3)]},
            {"order": 2, "reason": "insufficient", "items": [item("小麦", 1, 4)]},
        ]
        target = choose_target_order(skipped, random.Random(0))
        self.assertEqual(target["order"], 2)
        self.assertEqual(target["total_missing"], 3)

    def test_mixed_order_with_fish_is_unsupported(self):
        self.assertTrue(
            order_contains_unsupported_product([item("小麦", 1, 4), item("鱼肉", 2, 3)])
        )

    def test_all_unsupported_has_no_target(self):
        skipped = [
            {"order": 1, "reason": "unsupported_product", "items": [item("鱼肉", 2, 3)]}
        ]
        self.assertIsNone(choose_target_order(skipped))

    def test_rejects_misclassified_fish_order(self):
        skipped = [
            {"order": 1, "reason": "insufficient", "items": [item("鱼肉", 2, 3)]}
        ]
        with self.assertRaisesRegex(RuntimeError, "unsupported product entered"):
            choose_target_order(skipped)

    def test_prefers_actionable_order_over_smaller_unavailable_shortage(self):
        skipped = [
            {"order": 1, "reason": "insufficient", "items": [item("白玉草莓", 0, 1)]},
            {"order": 2, "reason": "insufficient", "items": [item("小麦", 0, 3)]},
        ]
        target = choose_target_order(
            skipped,
            random.Random(0),
            is_feasible=lambda entry: entry["order"] == 2,
        )
        self.assertEqual(target["order"], 2)


if __name__ == "__main__":
    unittest.main()

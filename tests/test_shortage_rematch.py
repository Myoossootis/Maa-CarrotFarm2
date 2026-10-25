import unittest

from tools.production_targets import (
    order_matches_shortage_focus,
    shortage_focus_was_submitted,
    target_shortage_names,
)


def item(name, actual, required):
    return {
        "name": name,
        "actual": actual,
        "required": required,
        "missing": max(0, required - actual),
        "satisfied": actual >= required,
    }


class ShortageRematchTests(unittest.TestCase):
    def setUp(self):
        self.target = {
            "order": 3,
            "items": [item("玫瑰", 4, 27), item("小麦", 10, 10)],
            "missing_items": [item("玫瑰", 4, 27)],
            "production_targets": [{"name": "玫瑰", "needed": 23}],
        }

    def test_shortage_names_prefer_missing_items(self):
        self.assertEqual(target_shortage_names(self.target), frozenset({"玫瑰"}))

    def test_rematch_ignores_order_id_and_quantities(self):
        # Different grid slot and updated stock, same shortage crop present.
        scanned = [item("玫瑰", 27, 27), item("玉米", 1, 3)]
        self.assertTrue(order_matches_shortage_focus(self.target, scanned))
        self.assertFalse(
            order_matches_shortage_focus(self.target, [item("小麦", 0, 5), item("玉米", 0, 2)])
        )

    def test_submitted_covers_shortage_focus(self):
        state = {
            "submitted": [
                {
                    "order": 7,
                    "acceptance_confirmed": True,
                    "items": [item("玫瑰", 27, 27), item("土豆", 2, 2)],
                }
            ]
        }
        self.assertTrue(shortage_focus_was_submitted(state, self.target))
        self.assertFalse(
            shortage_focus_was_submitted(
                {"submitted": [{"order": 7, "items": [item("玫瑰", 27, 27)]}]},
                self.target,
            )
        )


if __name__ == "__main__":
    unittest.main()

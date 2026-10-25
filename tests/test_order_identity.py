import unittest

from tools.production_targets import order_signature
from tools.run_order_cycle import target_was_submitted


class OrderIdentityTests(unittest.TestCase):
    def setUp(self):
        self.target = {"order": 3, "items": [
            {"name": "小麦", "actual": 2, "required": 18},
            {"name": "鸡蛋", "actual": 44, "required": 3},
        ]}

    def test_inventory_and_display_order_do_not_change_identity(self):
        items = [{"name": "鸡蛋", "actual": 100, "required": 3},
                 {"name": "小麦", "actual": 28, "required": 18}]
        self.assertEqual(order_signature(items), order_signature(self.target["items"]))
        self.assertTrue(target_was_submitted({"submitted": [{"order": 3, "items": items, "acceptance_confirmed": True}]}, self.target))

    def test_refreshed_card_at_same_position_is_not_target_completion(self):
        entry = {"order": 3, "acceptance_confirmed": True, "items": [{"name": "小麦", "required": 6}, {"name": "鸡蛋", "required": 1}]}
        self.assertFalse(target_was_submitted({"submitted": [entry]}, self.target))

    def test_sleep_confirmation_alone_is_not_acceptance(self):
        self.assertFalse(target_was_submitted({"submitted": [{"order": 3, "items": self.target["items"]}], "sleep_prompt_confirmed": True}, self.target))

    def test_already_satisfied_product_is_part_of_the_target(self):
        self.assertNotEqual(order_signature(self.target["items"]), order_signature(self.target["items"][:1]))

    def test_duplicate_products_are_rejected(self):
        with self.assertRaises(ValueError):
            order_signature(self.target["items"] * 2)


if __name__ == "__main__":
    unittest.main()

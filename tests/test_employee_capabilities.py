import copy
import json
import unittest

from tools.allocate_employee_work import allocate
from tools.employee_capabilities import (
    CAPABILITIES_PATH,
    capable_slots,
    load_capabilities,
    normalize_product_name,
    slot_can_produce,
)


class EmployeeCapabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.caps = load_capabilities()
        cls.slots = cls.caps["slots"]
        cls.aliases = cls.caps.get("product_aliases", {})

    def test_schema_v2_has_level_gating_fields(self):
        self.assertGreaterEqual(int(self.caps["schema_version"]), 2)
        self.assertIn("level_gating", self.caps)
        self.assertIn("product_aliases", self.caps)
        self.assertTrue(self.caps.get("pending_capabilities"))
        field1 = next(slot for slot in self.slots if slot["id"] == "field_1")
        self.assertIn("product_gates", field1)
        self.assertEqual(field1.get("worker_level"), None)
        self.assertEqual(field1.get("worker_level_status"), "needs_live_recording")

    def test_wheat_alias_variants_normalize(self):
        self.assertEqual(normalize_product_name("小麥", self.aliases), "小麦")
        self.assertEqual(normalize_product_name("麦子", self.aliases), "小麦")
        self.assertEqual(normalize_product_name(" 小 麦 ", self.aliases), "小麦")

    def test_wheat_alias_allocates_instead_of_no_capability(self):
        result = allocate([{"name": "小麥", "needed": 3}], self.slots, aliases=self.aliases)
        self.assertTrue(result["ready"], result)
        self.assertEqual(sum(job["items"].get("小麦", 0) for job in result["jobs"]), 3)
        self.assertFalse(result["unmet"])

    def test_pending_crop_stays_unmet(self):
        result = allocate([{"name": "白玉草莓", "needed": 1}], self.slots, aliases=self.aliases)
        self.assertFalse(result["ready"])
        self.assertEqual(result["unmet"][0]["reason"], "no_employee_capability")

    def test_numeric_level_gate_denies_low_worker(self):
        slots = copy.deepcopy(self.slots)
        field1 = next(slot for slot in slots if slot["id"] == "field_1")
        field1["worker_level"] = 3
        field1["product_gates"]["玫瑰"] = {
            "min_level": 10,
            "eligibility": "verified",
        }
        self.assertFalse(slot_can_produce(field1, "玫瑰", aliases=self.aliases))
        # Other fields never had rose.
        result = allocate([{"name": "玫瑰", "needed": 1}], slots, aliases=self.aliases)
        self.assertFalse(result["ready"])
        self.assertEqual(result["unmet"][0]["reason"], "no_employee_capability")

    def test_numeric_level_gate_allows_high_enough_worker(self):
        slots = copy.deepcopy(self.slots)
        field1 = next(slot for slot in slots if slot["id"] == "field_1")
        field1["worker_level"] = 12
        field1["product_gates"]["玫瑰"] = {
            "min_level": 10,
            "eligibility": "verified",
        }
        self.assertTrue(slot_can_produce(field1, "玫瑰", aliases=self.aliases))
        result = allocate([{"name": "玫瑰", "needed": 2}], slots, aliases=self.aliases)
        self.assertTrue(result["ready"], result)
        self.assertEqual(result["jobs"][0]["slot_id"], "field_1")

    def test_observed_unlocked_without_worker_level_still_allows(self):
        field2 = next(slot for slot in self.slots if slot["id"] == "field_2")
        self.assertIsNone(field2.get("worker_level"))
        self.assertTrue(slot_can_produce(field2, "小麦", aliases=self.aliases))
        self.assertTrue(capable_slots(self.slots, "小麦", aliases=self.aliases))

    def test_unverified_visible_is_not_schedulable(self):
        field1 = next(slot for slot in self.slots if slot["id"] == "field_1")
        self.assertFalse(slot_can_produce(field1, "普通草莓", aliases=self.aliases))
        self.assertFalse(slot_can_produce(field1, "卷心菜", aliases=self.aliases))


class FeedThresholdExtensionTests(unittest.TestCase):
    def test_zero_duration_is_refill_threshold(self):
        from tools.ensure_ranch_feed import mill_remaining_seconds

        self.assertEqual(mill_remaining_seconds("预计可生产0秒"), 0)
        self.assertIsNone(mill_remaining_seconds("生产速度0秒"))

    def test_sack_parse_rejects_ambiguous_pairs(self):
        from tools.ensure_ranch_feed import parse_sack_readings

        self.assertIsNone(parse_sack_readings([("x50", 0.99)]))
        self.assertEqual(parse_sack_readings([("x50", 0.99), ("50", 0.95)]), 50)



if __name__ == "__main__":
    unittest.main()

import json
import unittest

from tools.allocate_employee_work import CAPABILITIES, allocate


class EmployeeAllocationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.slots = json.loads(CAPABILITIES.read_text(encoding="utf-8"))["slots"]

    def test_rose_uses_only_farm_one(self):
        result = allocate([{"name": "玫瑰", "needed": 3}], self.slots)
        self.assertTrue(result["ready"])
        self.assertEqual(result["jobs"][0]["slot_id"], "field_1")
        self.assertEqual(result["jobs"][0]["items"]["玫瑰"], 3)

    def test_wheat_is_split_across_three_fields(self):
        result = allocate([{"name": "小麦", "needed": 9}], self.slots)
        jobs = [job for job in result["jobs"] if "小麦" in job["items"]]
        self.assertEqual(len(jobs), 3)
        self.assertEqual(sum(job["items"]["小麦"] for job in jobs), 9)

    def test_apple_uses_two_orchards(self):
        result = allocate([{"name": "苹果", "needed": 4}], self.slots)
        self.assertEqual({job["slot_id"] for job in result["jobs"]}, {"orchard_1", "orchard_2"})

    def test_milk_uses_only_second_ranch(self):
        result = allocate([{"name": "牛奶", "needed": 2}], self.slots)
        self.assertEqual(result["jobs"][0]["slot_id"], "ranch_2")

    def test_uncovered_product_blocks_execution(self):
        result = allocate([{"name": "白玉草莓", "needed": 1}], self.slots)
        self.assertFalse(result["ready"])
        self.assertEqual(result["unmet"][0]["reason"], "no_employee_capability")

    def test_fish_is_never_allocated(self):
        with self.assertRaisesRegex(RuntimeError, "fish orders"):
            allocate([{"name": "鱼肉", "needed": 1}], self.slots)

    def test_capacity_limit_is_reported(self):
        result = allocate([{"name": "牛奶", "needed": 51}], self.slots)
        self.assertFalse(result["ready"])
        self.assertEqual(result["unmet"][0]["missing"], 1)


if __name__ == "__main__":
    unittest.main()

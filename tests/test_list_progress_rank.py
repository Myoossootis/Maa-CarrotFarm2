import unittest

from tools.configure_field_employee import _rank_progress_candidates


class ListProgressRankTests(unittest.TestCase):
    def test_prefers_expected_complete_over_capacity_badge(self):
        # Stuck partial + capacity badge + true complete for a 27 plan.
        candidates = [
            (4, 27, 0.99),
            (50, 50, 0.99),
            (27, 27, 0.80),
        ]
        self.assertEqual(
            _rank_progress_candidates(candidates, expected_planned=27, slot_capacity=50),
            (27, 27),
        )

    def test_left_ge_right_beats_partial_same_plan(self):
        candidates = [(4, 27, 0.99), (26, 27, 0.70), (27, 27, 0.60)]
        self.assertEqual(
            _rank_progress_candidates(candidates, expected_planned=27, slot_capacity=50),
            (27, 27),
        )

    def test_capacity_badge_only_is_rejected_for_smaller_plan(self):
        candidates = [(50, 50, 0.95)]
        self.assertIsNone(
            _rank_progress_candidates(candidates, expected_planned=27, slot_capacity=50),
        )

    def test_wrong_denominator_is_rejected(self):
        self.assertIsNone(_rank_progress_candidates([(30, 30, .99)], 27, 50))

    def test_real_fifty_item_plan_is_not_rejected_as_smaller_plan(self):
        self.assertEqual(_rank_progress_candidates([(50, 50, .99)], 50, 50), (50, 50))

    def test_empty(self):
        self.assertIsNone(_rank_progress_candidates([], expected_planned=27, slot_capacity=50))


if __name__ == "__main__":
    unittest.main()

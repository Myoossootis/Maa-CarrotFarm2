from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import complete_normal_orders as orders


def items(actual=5):
    return [{'name': '番茄', 'required': 3, 'actual': actual}]


def previous(actual=5, generated=None):
    moment = generated or datetime.now(timezone.utc).isoformat()
    return {
        'generated_at_utc': moment,
        'submitted': [{'items': items(actual)}],
    }


class DuplicateAcceptGuardTests(unittest.TestCase):
    def test_same_recipe_and_stock_blocks_another_accept(self):
        self.assertTrue(orders.duplicate_accept_blocked(previous(), items()))

    def test_changed_stock_or_old_record_does_not_block(self):
        self.assertFalse(orders.duplicate_accept_blocked(previous(actual=4), items()))
        old = (datetime.now(timezone.utc) - timedelta(seconds=301)).isoformat()
        self.assertFalse(orders.duplicate_accept_blocked(previous(generated=old), items()))
        self.assertFalse(orders.duplicate_accept_blocked({}, items()))

    def test_broken_previous_record_raises_instead_of_allowing_accept(self):
        with self.assertRaises(ValueError):
            orders.duplicate_accept_blocked(
                {'generated_at_utc': 'not-a-time', 'submitted': []}, items())
        with self.assertRaises(ValueError):
            orders.duplicate_accept_blocked(
                previous(), [{'name': '番茄', 'required': 0, 'actual': 5}])

    def test_structurally_broken_record_cannot_authorize_another_accept(self):
        for record in ([], None, {'submitted': [{'items': items()}]},
                       {'generated_at_utc': datetime.now(timezone.utc).isoformat(), 'submitted': {}}):
            with self.subTest(record=record), self.assertRaises(ValueError):
                orders.duplicate_accept_blocked(record, items())


if __name__ == '__main__':
    unittest.main()

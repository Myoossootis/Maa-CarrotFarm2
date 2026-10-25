from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import normal_order_delivery as journal


def data(status=1, stock=30):
    return dict(packageVersion='90', orders=[dict(uid=88, slot=1, category='normal', statusCode=status,
        costs=[dict(itemId=10, count=2), dict(itemId=11, count=3)])],
        inventory=dict(records=[dict(itemId=10, count=stock), dict(itemId=11, count=stock)]))


ITEMS = [dict(game_order_uid=88, game_item_id=item, actual=30, required=required,
              batch_count=3, satisfied=True) for item, required in ((10, 6), (11, 9))]


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / 'active.json'
        guard = patch.object(journal.decoded, 'validate')
        guard.start(); self.addCleanup(guard.stop)

    def test_intent_is_durable_and_blocks_new_or_uncertain_acceptance(self):
        intent = journal.reserve(ITEMS, 1, data(), self.path)
        self.assertEqual(journal.load(self.path), intent)
        with self.assertRaisesRegex(journal.decoded.DecodeError, 'Unresolved'):
            journal.reserve(ITEMS, 1, data(), self.path)
        with self.assertRaisesRegex(journal.decoded.DecodeError, 'Unresolved'):
            journal.recover(intent, data(), self.path)
        self.assertEqual(journal.load(self.path)['status'], 'accept_intent')

    def test_exact_debit_recovers_acceptance_and_claim_intent_never_replays(self):
        intent = journal.reserve(ITEMS, 1, data(), self.path)
        after = data(3); after['inventory']['records'][0]['count'] = 24
        after['inventory']['records'][1]['count'] = 21
        accepted = journal.recover(intent, after, self.path)
        self.assertEqual(accepted['status'], 'accepted')
        claim = journal.reserve_reward(accepted, after, self.path)
        with self.assertRaisesRegex(journal.decoded.DecodeError, 'reward input'):
            journal.recover(claim, after, self.path)
        with self.assertRaises(journal.decoded.DecodeError):
            journal.complete(claim, after, self.path)
        # The same UID is reused for the next recipe: waiting after a durable
        # success-to-reward intent can be completed by readback, with no input.
        complete = journal.recover(claim, data(), self.path)
        self.assertEqual(complete['status'], 'completed')
        self.assertTrue((self.path.parent / 'receipts' / (complete['operation_id'] + '.json')).exists())

    def test_old_unbound_reward_intent_still_blocks_readback_recovery(self):
        receipt=dict(status='reward_intent',package_version='90',uid=88,order=1)
        with self.assertRaisesRegex(journal.decoded.DecodeError,'reward input'):
            journal.recover(receipt,data(),self.path)

    def test_changed_costs_identity_stock_or_multiplier_cannot_reserve(self):
        for change in ('uid', 'slot', 'count', 'stock', 'batch', 'version'):
            current = data(); items = [dict(row) for row in ITEMS]
            if change == 'uid': current['orders'][0]['uid'] = 99
            if change == 'slot': current['orders'][0]['slot'] = 2
            if change == 'count': current['orders'][0]['costs'][0]['count'] = 3
            if change == 'stock': current['inventory']['records'][0]['count'] = 31
            if change == 'batch': items[0]['batch_count'] = 1
            if change == 'version': current['packageVersion'] = '91'
            with self.assertRaises(journal.decoded.DecodeError):
                journal.reserve(items, 1, current, self.path)
            self.assertFalse(self.path.exists())


if __name__ == '__main__': unittest.main()

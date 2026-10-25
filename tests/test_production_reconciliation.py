import copy
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools import decoded_runtime as decoded
from tools.production_reconciliation import reconcile, collection_candidate, confirm_collection
from tools.reconcile_production import collect_once
from test_formal_decoded_runtime import state


def ready():
    data = state()
    data['packageVersion'] = '87'
    for plan in data['employees']['plans']:
        for row in plan['targets'] + plan['heldProducts']:
            row.setdefault('itemId', 172)
    entry = decoded.plan('field_1', data)
    entry.update(targets=[dict(itemId=157, name='甘蔗', count=3)],
                 heldProducts=[dict(itemId=157, name='甘蔗', count=3)])
    return data


def collected(before):
    after = copy.deepcopy(before)
    entry = decoded.plan('field_1', after)
    entry.update(targets=[], heldProducts=[], accumulated=[])
    after['inventory']['records'].append(dict(itemId=157, count=3))
    return after


class ReconciliationTests(unittest.TestCase):
    def test_accept_intent_requires_exact_debit_and_never_reaccepts(self):
        import complete_batch_orders as batch
        data = ready()
        data['orders'] = [dict(uid=99, statusCode=2)]
        items = [dict(game_order_uid=99, game_item_id=157, actual=3, required=3)]
        intent = dict(status='accept_intent', submitted=[dict(order=5, items=items)])
        with tempfile.TemporaryDirectory() as folder, patch.object(batch.decoded, 'enabled', return_value=True), \
             patch.object(batch.decoded, 'snapshot', return_value=data), patch.object(batch, '_click_accept') as click:
            result = batch.recover_accept_intent(intent, Path(folder) / 'delivery.json')
            self.assertEqual(result['status'], 'accepted')
            self.assertTrue(result['submitted'][0]['acceptance_confirmed'])
            click.assert_not_called()
            data['orders'][0]['statusCode'] = 1
            intent['status'] = 'accept_intent'
            with self.assertRaises(decoded.DecodeError):
                batch.recover_accept_intent(intent, Path(folder) / 'delivery.json')
            click.assert_not_called()

    def test_current_work_and_unbound_history_are_distinct_and_not_modified(self):
        data = ready()
        original = copy.deepcopy(data)
        result = reconcile(data, feed=dict(pending='arrange', items={'甘蔗': 4}),
                           assignment=dict(saved=[dict(slot_id='field_1', items={'土豆': 3})]),
                           manual=dict(status='unconfirmed', seedInputsReserved=1))
        self.assertIn('field_1', result['ready_to_collect'])
        self.assertTrue(all(not row['automatic_retry_allowed'] for row in result['historical']))
        self.assertEqual(data, original)
        self.assertEqual(result['game_operations'], 0)

    def test_equal_legacy_recipe_does_not_prove_operation_identity(self):
        data = ready()
        result = reconcile(data, feed=dict(pending='arrange', items={}))
        self.assertEqual(result['historical'][0]['reason'], 'missing_operation_identity')

    def test_stale_snapshot_rejected(self):
        data = ready()
        data['capturedAt'] = (datetime.now(timezone.utc) - timedelta(seconds=20)).isoformat()
        with self.assertRaises(decoded.DecodeError):
            reconcile(data)

    def test_partial_multicrop_or_repeat_cannot_collect(self):
        for repeat, count in [(True, 3), (False, 2)]:
            data = ready()
            data['employees']['repeatEnabled'] = repeat
            decoded.plan('field_1', data)['heldProducts'][0]['count'] = count
            with self.assertRaises(decoded.DecodeError):
                collection_candidate('field_1', data)

    def test_receipt_requires_same_owner_and_full_inventory_delta(self):
        data = ready()
        intent = dict(before=data, candidate=collection_candidate('field_1', data))
        after = collected(data)
        self.assertTrue(confirm_collection(intent, after))
        decoded.plan('field_1', after)['buildingUid'] = 999
        self.assertFalse(confirm_collection(intent, after))
        after = collected(data)
        after['inventory']['records'][-1]['count'] = 2
        self.assertFalse(confirm_collection(intent, after))

    def test_intent_is_on_disk_before_business_input_and_success_is_receipted(self):
        before = ready()
        with tempfile.TemporaryDirectory() as folder:
            journal = Path(folder) / 'collections/field_1.json'
            def collect(candidate, reserve, evidence):
                reserve(before)
                self.assertEqual(json.loads(journal.read_text(encoding='utf-8'))['status'], 'intent')
                return dict(collected=True)
            result = collect_once('field_1', Mock(side_effect=[before, collected(before)]), collect, folder)
            self.assertEqual(result['status'], 'confirmed')
            self.assertEqual(json.loads(journal.read_text(encoding='utf-8'))['status'], 'confirmed')

    def test_interruption_is_not_retried_and_can_only_recover_by_receipt(self):
        before = ready()
        with tempfile.TemporaryDirectory() as folder:
            def interrupted(candidate, reserve, evidence):
                reserve(before)
                raise KeyboardInterrupt()
            with self.assertRaises(KeyboardInterrupt):
                collect_once('field_1', Mock(return_value=before), interrupted, folder)
            no_input = Mock()
            with self.assertRaisesRegex(decoded.DecodeError, 'Unresolved collection'):
                collect_once('field_1', Mock(return_value=before), no_input, folder)
            no_input.assert_not_called()
            result = collect_once('field_1', Mock(return_value=collected(before)), no_input, folder)
            self.assertEqual(result['game_operations'], 0)
            no_input.assert_not_called()

    def test_changed_recipe_before_click_stops_without_reserving_input(self):
        before = ready()
        with tempfile.TemporaryDirectory() as folder:
            def changed(candidate, reserve, evidence):
                other = copy.deepcopy(before)
                decoded.plan('field_1', other)['targets'][0]['count'] = 4
                reserve(other)
                self.fail('must not reach business input')
            with self.assertRaises(decoded.DecodeError):
                collect_once('field_1', Mock(return_value=before), changed, folder)
            record = json.loads((Path(folder) / 'collections/field_1.json').read_text(encoding='utf-8'))
            self.assertEqual(record['status'], 'blocked_before_input')


if __name__ == '__main__':
    unittest.main()

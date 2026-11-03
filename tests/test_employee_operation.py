from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools import employee_operation as ops
from tools import decoded_runtime as decoded
from test_production_reconciliation import ready as legacy_ready, collected


def ready():
    data = legacy_ready()
    data['packageVersion'] = '90'
    data['production']['semanticsPackageVersion'] = '90'
    for index, entry in enumerate(data['employees']['plans']):
        for employee in entry['employees']:
            employee.setdefault('uid', 1000 + index)
            employee.setdefault('itemId', 2011)
            employee.setdefault('level', 1)
    return data


def idle():
    data = ready()
    entry = decoded.plan('field_1', data)
    entry.update(targets=[], heldProducts=[], accumulated=[], productionOperations=None)
    return data


class EmployeeOperationTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.items = {row['name']: row['count'] for row in decoded.plan('field_1', ready())['targets']}
        self.record = ops.begin(dict(jobs=[dict(slot_id='field_1', items=self.items)]), self.root)

    def test_save_intent_precedes_input_and_recovers_without_reopening(self):
        ops.begin_edit(self.record, 'field_1', self.items, idle())
        intent = ops.reserve_save(self.record, 'field_1', idle())
        self.assertEqual(intent['status'], 'save_intent')
        self.assertEqual(ops.read(ops.job_path(self.record, 'field_1'))['status'], 'save_intent')
        result = ops.reconcile_save(self.record, 'field_1', ready())
        self.assertTrue(result['verified'])
        self.assertEqual(ops.begin_edit(self.record, 'field_1', self.items, ready())['status'], 'saved')

    def test_uncertain_editor_or_save_is_not_replayed(self):
        ops.begin_edit(self.record, 'field_1', self.items, idle())
        with self.assertRaisesRegex(decoded.DecodeError, 'Unresolved'):
            ops.begin_edit(self.record, 'field_1', self.items, idle())
        ops.reserve_save(self.record, 'field_1', idle())
        with self.assertRaisesRegex(decoded.DecodeError, 'Unresolved'):
            ops.reconcile_save(self.record, 'field_1', idle())

    def test_worker_change_or_new_production_prevents_save(self):
        ops.begin_edit(self.record, 'field_1', self.items, idle())
        for kind in ('worker','plan'):
            data = idle()
            if kind == 'worker': decoded.plan('field_1', data)['employees'][0]['uid'] += 1
            else: decoded.plan('field_1', data)['targets'] = ready()['employees']['plans'][0]['targets']
            with self.assertRaises(decoded.DecodeError): ops.reserve_save(self.record, 'field_1', data)
        self.assertEqual(ops.read(ops.job_path(self.record, 'field_1'))['status'], 'editing')

    def saved(self):
        ops.begin_edit(self.record, 'field_1', self.items, idle())
        ops.reserve_save(self.record, 'field_1', idle())
        ops.reconcile_save(self.record, 'field_1', ready())

    def test_collection_interruption_recovers_from_clearance_without_new_input(self):
        self.saved()
        before = ready()
        def interrupt(candidate, reserve, folder):
            reserve(before)
            self.assertEqual(ops.collection_record(self.record, 'field_1')['status'], 'intent')
            raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            ops.collect(self.record, 'field_1', Mock(return_value=before), interrupt)
        action = Mock()
        with self.assertRaisesRegex(decoded.DecodeError, 'Unresolved collection'):
            ops.collect(self.record, 'field_1', Mock(return_value=before), action)
        after = collected(before)
        result = ops.collect(self.record, 'field_1', Mock(return_value=after), action)
        self.assertEqual(result['game_operations'], 0)
        self.assertTrue(ops.collect(self.record, 'field_1', Mock(return_value=after), action)['collected'])
        action.assert_not_called()
        self.assertEqual(ops.mark(self.record, 'collected')['status'], 'collected')

    def test_previous_assignment_and_missing_claims_block_new_cycle(self):
        with self.assertRaises(decoded.DecodeError):ops.begin({}, self.root)
        with self.assertRaises(decoded.DecodeError):ops.mark(self.record, 'collected')

    def test_cleared_employee_plan_can_supersede_saved_operation_without_click(self):
        self.saved()
        self.record = ops.mark(self.record, 'saved')
        with self.assertRaisesRegex(decoded.DecodeError, 'still present'):
            ops.supersede_cleared_plans(self.record, ready())
        result = ops.supersede_cleared_plans(self.record, idle())
        self.assertEqual(result['status'], 'superseded_plan_cleared')
        self.assertEqual(ops.active(self.root)['status'], 'superseded_plan_cleared')
        self.assertIsNone(ops.unfinished_active(self.root))
        next_record = ops.begin(dict(jobs=[dict(slot_id='field_1', items=self.items)]), self.root)
        self.assertEqual(next_record['status'], 'applying')

    def test_real_save_helper_reserves_intent_before_only_save_click(self):
        from tools import configure_field_employee as workers
        ops.begin_edit(self.record, 'field_1', self.items, idle())
        saves = []
        def click(controller, x, y):
            if (x, y) == (500, 1060):
                self.assertEqual(ops.read(ops.job_path(self.record, 'field_1'))['status'], 'save_intent')
                saves.append((x, y))
        with patch.object(decoded, 'enabled', return_value=True), \
                patch.object(decoded, 'assignment_blocker', return_value=None), \
                patch.object(decoded, 'snapshot', side_effect=idle), \
                patch.object(decoded, 'verify_plan'), patch.object(workers, 'open_plan', return_value=(object(), 600)), \
                patch.object(workers, 'match', return_value=1), patch.object(workers, 'screenshot'), \
                patch.object(workers, 'ensure_repeat_checkbox'), patch.object(workers, 'click', side_effect=click):
            workers.configure_items(None, None, 'field_1', self.items,
                before_save=lambda data: ops.reserve_save(self.record, 'field_1', data))
        self.assertEqual(len(saves), 1)

    def test_supersede_cannot_hide_unconfirmed_claim_or_accumulated_products(self):
        self.saved()
        record = ops.mark(self.record, 'saved')
        for status in ('intent', 'unconfirmed', 'confirmed'):
            with self.subTest(claim=status), patch.object(ops, 'collection_record', return_value={'status': status}):
                with self.assertRaises(decoded.DecodeError):
                    ops.supersede_cleared_plans(record, idle())
        data = idle()
        decoded.plan('field_1', data)['accumulated'] = [{'itemId': 157, 'name': '甘蔗', 'count': 1}]
        with self.assertRaises(decoded.DecodeError):
            ops.supersede_cleared_plans(record, data)
        self.assertEqual(ops.active(self.root)['status'], 'saved')


if __name__ == '__main__': unittest.main()

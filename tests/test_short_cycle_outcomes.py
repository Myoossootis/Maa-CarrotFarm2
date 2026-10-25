import io
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tools import run_short_cycle as m


TARGET = {'order': 1, 'missing_items': [{'name': '小麦', 'missing': 5}], 'production_targets': [{'name': '小麦', 'needed': 5}]}
ASSIGNMENT = {'status': 'saved', 'saved': [{'slot_id': 'field_1', 'verified': True, 'repeat': False, 'one_shot': True}]}
ACCEPTED = {'submitted_count': 1, 'submitted': [{'acceptance_confirmed': True, 'items': [{'name': '小麦'}]}]}


class ShortCycleOutcomeTests(unittest.TestCase):
    def run_case(self, scan, reads=(), rounds=1):
        gate = SimpleNamespace(
            can_start=True,
            in_cooldown=False,
            completed_today=0,
            daily_cap=3,
            remaining_in_batch=3,
            next_available=None,
            as_dict=lambda: {'can_start': True, 'completed_today': 0, 'daily_cap': 3},
        )
        with patch.object(sys, 'argv', ['short_cycle', '--apply', '--skip-checklist', '--rounds', str(rounds)]), patch.object(sys, 'stdout', io.StringIO()), patch.object(m, 'confirm_cycle_start', return_value=True) as confirm, patch.object(m, 'can_start', return_value=gate), patch.object(m, 'TMP', MagicMock()), patch.object(m, 'LEGACY_TMP', MagicMock()), patch.object(m, 'checkpoint'), patch.object(m, 'write_json') as write, patch.object(m, 'scan_for_target', return_value=scan), patch.object(m, 'run_script') as run, patch.object(m, 'read_json', side_effect=reads):
            code = m.main()
            self.last_scripts = [call.args[0] for call in run.call_args_list]
            confirm.assert_called_once_with()
            return code, write.call_args.args[1]

    def test_existing_order_wait_is_not_completion(self):
        code, summary = self.run_case({'status': 'existing_order_in_progress'})
        self.assertEqual(summary['status'], 'waiting')
        self.assertEqual(code, 2)

    def test_no_actionable_order_is_distinct_from_completion(self):
        code, summary = self.run_case({'status': 'completed', 'no_acceptable_orders': True})
        self.assertEqual(summary['status'], 'no_actionable_orders')
        self.assertEqual(code, 0)

    def test_unresolved_shortage_is_not_completion(self):
        code, summary = self.run_case({'target_order': TARGET}, [ASSIGNMENT, {'status': 'complete'}, {'submitted': [], 'target_order': TARGET}])
        self.assertEqual(summary['status'], 'shortage_unresolved')
        self.assertEqual(code, 2)

    def test_only_confirmed_requested_rounds_are_complete(self):
        code, summary = self.run_case({'target_order': TARGET}, [ASSIGNMENT, {'status': 'complete'}, ACCEPTED] * 2, rounds=2)
        self.assertEqual(code, 0)
        self.assertEqual(summary['status'], 'complete')
        self.assertEqual(len(summary['rounds']), 2)

    def test_empty_assignment_is_not_verified(self):
        with self.assertRaisesRegex(RuntimeError, 'assignments'):
            self.run_case({'target_order': TARGET}, [{'status': 'saved', 'saved': []}])

    def test_empty_monitor_result_is_not_completion(self):
        with self.assertRaisesRegex(RuntimeError, 'monitor incomplete'):
            self.run_case({'target_order': TARGET}, [ASSIGNMENT, {}])

    def test_repeat_plan_is_rejected_before_monitoring(self):
        unsafe = {'status': 'saved', 'saved': [{'verified': True, 'repeat': True, 'one_shot': False}]}
        with self.assertRaisesRegex(RuntimeError, 'one-shot'):
            self.run_case({'target_order': TARGET}, [unsafe])

    def test_ready_single_order_skips_production_and_monitor(self):
        items = [{'name': '小麦', 'actual': 20, 'required': 5, 'missing': 0},
                 {'name': '鸡蛋', 'actual': 10, 'required': 3, 'missing': 0}]
        target = dict(order=1, items=items, missing_items=[], production_targets=[], selection='first_non_fish_of_first_three')
        accepted = dict(submitted_count=1, submitted=[dict(items=items, acceptance_confirmed=True)])
        code, summary = self.run_case({'target_order': target}, [accepted])
        self.assertEqual(code, 0)
        self.assertEqual(summary['status'], 'complete')
        self.assertEqual(self.last_scripts, ['complete_normal_orders.py'])

    def test_scan_requests_preparation_instead_of_accepting_unselected_orders(self):
        result = {'returned_to_farm': True, 'target_order': TARGET}
        with patch.object(m, 'run_script') as run, patch.object(m, 'read_json', return_value=result), patch.object(sys, 'stdout', io.StringIO()):
            self.assertEqual(m.scan_for_target(6), result)
            run.assert_called_once_with('complete_normal_orders.py', '--prepare-only')


if __name__ == '__main__':
    unittest.main()

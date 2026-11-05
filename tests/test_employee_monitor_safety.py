import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from contextlib import ExitStack

from tools import monitor_employee_products as m
from tools import configure_field_employee as config


class MonitorSafetyTests(unittest.TestCase):
    def test_watch_line_includes_order_gap_and_unjudged_time(self):
        self.assertEqual(
            m.format_order_need({'target_order': 1, 'target_items': [
                {'name': '玫瑰', 'required': 24, 'actual': 5, 'missing': 19},
            ]}),
            '[订单] 订单1：玫瑰 需24 有5 缺19',
        )
        line = m.format_watch_line('field_1', {'玫瑰': 19}, (0, 19), {'state': 'not_started'})
        self.assertEqual(line, '[进度] 农田1 玫瑰×19 0/19 还差19；剩余时间未判定')

    def test_timed_cycle_reports_current_round_only(self):
        self.assertEqual(
            m.format_cycle_time({'state': 'timed', 'seconds': 95, 'growing': 3}),
            '这轮剩余约2分钟',
        )
        data = {'production': {'buildings': [{
            'buildingUid': 7,
            'slots': [
                {'status': 'empty', 'timingReliable': True, 'remainingSeconds': 0},
                {'status': 'growing', 'timingReliable': False, 'remainingSeconds': None},
            ],
        }]}}
        with patch.object(m.decoded, 'plan', return_value={'buildingUid': 7}):
            self.assertEqual(m.current_cycle_remaining('field_1', data)['state'], 'unknown')

    def run_monitor(self, result=None, error=None):
        with ExitStack() as stack:
            # OCR collection is opt-in. These cases cover that click path.
            stack.enter_context(patch.dict(os.environ, {'CARROT_STATE_READER': 'ocr'}))
            for name in [
                "Win32Controller",
                "Resource",
                "Tasker",
                "find_window",
                "_close_employee_menu_with_retry",
                "write_json",
                "RESULT",
            ]:
                stack.enter_context(patch.object(m, name))
            collect = stack.enter_context(
                patch.object(m, "collect_finished_slot", return_value=result, side_effect=error)
            )
            try:
                return m.monitor({"field_1": 27}, 5, 30, {"field_1": {"灏忛害": 27}})
            finally:
                self.collect_calls = collect.call_count

    def test_click_failure_stops_without_retry(self):
        with self.assertRaises(m.CollectionUnconfirmedError):
            self.run_monitor(error=m.CollectionUnconfirmedError("click failed"))
        self.assertEqual(self.collect_calls, 1)

    def test_collected_completes_without_clearance_gate(self):
        with patch.object(config, "open_plan") as open_plan, patch.object(config, "reset_plan") as reset:
            result = self.run_monitor({"collected": True, "progress": [28, 27], "plan_cleared": True})
            self.assertTrue(result["complete"])
            self.assertEqual(result["cleared_slots"], ["field_1"])
            self.assertEqual(result["reset_slots"], [])
            open_plan.assert_not_called()
            reset.assert_not_called()

    def test_collected_without_plan_cleared_flag_still_completes(self):
        """Monitor treats collected as done; no plan_cleared gate."""
        with patch.object(config, "reset_plan") as reset:
            result = self.run_monitor({"collected": True, "progress": [28, 27]})
            self.assertTrue(result["complete"])
            self.assertEqual(result["cleared_slots"], ["field_1"])
            reset.assert_not_called()

    def test_wait_always_returns_to_actual_farm(self):
        with patch.object(m, 'close_employee_menu') as close, patch.object(m, 'enter_farm') as farm:
            m._close_employee_menu_with_retry(object())
        close.assert_called_once()
        farm.assert_called_once()


class RepeatCheckboxTests(unittest.TestCase):
    def test_checked_is_toggled_once_and_trusted(self):
        with patch.object(config, "repeat_checkbox_state", return_value=True), patch.object(
            config, "click"
        ) as click, patch.object(config, "screenshot", return_value="new"), patch.object(config.time, "sleep"):
            self.assertEqual(
                config.ensure_repeat_checkbox(object(), "old", repeat=False, tasker=object()),
                "new",
            )
            click.assert_called_once()

    def test_unchecked_is_not_toggled(self):
        with patch.object(config, "repeat_checkbox_state", return_value=False), patch.object(
            config, "click"
        ) as click:
            config.ensure_repeat_checkbox(object(), "old", repeat=False, tasker=object())
            click.assert_not_called()

    def test_unreadable_control_refuses_before_click(self):
        with patch.object(config, "repeat_checkbox_state", return_value=None), patch.object(
            config, "click"
        ) as click:
            with self.assertRaisesRegex(RuntimeError, "not verified"):
                config.ensure_repeat_checkbox(object(), "old", repeat=False, tasker=object())
            click.assert_not_called()

    def test_saved_plan_rearrange_skips_checkbox(self):
        with patch.object(config, "open_plan", return_value=(object(), 600)), patch.object(
            config, "capacity", return_value=(5, 50)
        ), patch.object(config, "icon_quantity", return_value=5), patch.object(
            config, "saved_plan_rearrange_view", return_value=True
        ), patch.object(config, "repeat_checkbox_state") as state, patch.object(
            config, "close_plan_dialog"
        ), patch.object(config, "click") as click:
            config.verify_saved_plan(object(), object(), "field_1", {"灏忛害": 5})
            state.assert_not_called()
            click.assert_not_called()


class CollectReaderDefaultTests(unittest.TestCase):
    def saved_assignment(self, folder):
        payload = {
            'status': 'saved',
            'assignment_id': '5eb7a435',
            'operation_dir': 'bound/op',
            'saved': [{
                'slot_id': 'field_1',
                'items': {'小麦': 10},
                'verified': True,
                'repeat': False,
                'one_shot': True,
            }],
        }
        path = Path(folder) / 'assignment.json'
        path.write_text(json.dumps(payload), encoding='utf-8')
        operation = {
            'assignment_id': '5eb7a435',
            'operation_dir': 'bound/op',
            'saved': [{'slot_id': 'field_1', 'items': {'小麦': 10}}],
        }
        return path, operation

    def test_unset_reader_defaults_to_decoded(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('CARROT_STATE_READER', None)
            self.assertEqual(m.select_collect_reader(), 'decoded')
            self.assertEqual(os.environ['CARROT_STATE_READER'], 'decoded')

    def test_explicit_ocr_is_preserved(self):
        with patch.dict(os.environ, {'CARROT_STATE_READER': 'ocr'}):
            self.assertEqual(m.select_collect_reader(), 'ocr')
            self.assertEqual(os.environ['CARROT_STATE_READER'], 'ocr')

    def test_unknown_reader_is_rejected(self):
        with patch.dict(os.environ, {'CARROT_STATE_READER': 'guess'}):
            with self.assertRaises(m.decoded.DecodeError):
                m.select_collect_reader()

    def test_apply_without_env_selects_decoded_and_binds_operation(self):
        output = io.StringIO()
        captured = {}

        def fake_monitor(plans, poll, timeout, expected, operation):
            captured['reader'] = os.environ.get('CARROT_STATE_READER')
            captured['operation'] = operation
            captured['plans'] = plans
            return {'complete': False, 'recipe_conflict_slots': ['field_1']}

        with tempfile.TemporaryDirectory() as folder:
            path, operation = self.saved_assignment(folder)
            argv = ['monitor_employee_products.py', '--assignment-file', str(path),
                    '--poll-seconds', '5', '--timeout-seconds', '30', '--apply']
            with patch.dict(os.environ, {}, clear=False), \
                    patch.object(sys, 'argv', argv), \
                    patch.object(m.operations, 'active', return_value=operation), \
                    patch.object(m, 'monitor', side_effect=fake_monitor), \
                    patch('sys.stdout', output):
                os.environ.pop('CARROT_STATE_READER', None)
                self.assertEqual(m.main(), 2)
                self.assertEqual(os.environ.get('CARROT_STATE_READER'), 'decoded')
        self.assertEqual(captured['reader'], 'decoded')
        self.assertEqual(captured['operation']['assignment_id'], '5eb7a435')
        self.assertEqual(captured['plans'], {'field_1': 10})
        self.assertIn('监控收取使用解码（CARROT_STATE_READER=decoded）', output.getvalue())

    def test_apply_without_env_rejects_unbound_assignment_before_monitor(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _operation = self.saved_assignment(folder)
            argv = ['monitor_employee_products.py', '--assignment-file', str(path),
                    '--poll-seconds', '5', '--timeout-seconds', '30', '--apply']
            with patch.dict(os.environ, {}, clear=False), \
                    patch.object(sys, 'argv', argv), \
                    patch.object(m.operations, 'active', return_value=None), \
                    patch.object(m, 'monitor') as monitor, \
                    patch('sys.stdout', io.StringIO()), \
                    patch('sys.stderr', io.StringIO()):
                os.environ.pop('CARROT_STATE_READER', None)
                with self.assertRaises(SystemExit):
                    m.main()
                self.assertEqual(os.environ.get('CARROT_STATE_READER'), 'decoded')
            monitor.assert_not_called()

    def test_explicit_ocr_apply_does_not_require_bound_operation(self):
        output = io.StringIO()
        captured = {}

        def fake_monitor(plans, poll, timeout, expected, operation):
            captured['reader'] = os.environ.get('CARROT_STATE_READER')
            captured['operation'] = operation
            return {'complete': True}

        with tempfile.TemporaryDirectory() as folder:
            path, _operation = self.saved_assignment(folder)
            argv = ['monitor_employee_products.py', '--assignment-file', str(path),
                    '--poll-seconds', '5', '--timeout-seconds', '30', '--apply']
            with patch.dict(os.environ, {'CARROT_STATE_READER': 'ocr'}), \
                    patch.object(sys, 'argv', argv), \
                    patch.object(m.operations, 'active', side_effect=AssertionError('ocr must not bind')), \
                    patch.object(m, 'monitor', side_effect=fake_monitor), \
                    patch('sys.stdout', output):
                self.assertEqual(m.main(), 0)
        self.assertEqual(captured, {'reader': 'ocr', 'operation': None})
        self.assertIn('显式识图不会启用 recipe_conflict_skip', output.getvalue())

    def test_plans_apply_without_env_does_not_fall_through_to_ocr(self):
        argv = ['monitor_employee_products.py', '--plans', 'field_1=10',
                '--poll-seconds', '5', '--timeout-seconds', '30', '--apply']
        with patch.dict(os.environ, {}, clear=False), \
                patch.object(sys, 'argv', argv), \
                patch.object(m, 'monitor') as monitor, \
                patch('sys.stdout', io.StringIO()), \
                patch('sys.stderr', io.StringIO()):
            os.environ.pop('CARROT_STATE_READER', None)
            with self.assertRaises(SystemExit):
                m.main()
            monitor.assert_not_called()

    def test_dry_run_does_not_change_reader(self):
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as folder:
            path, _operation = self.saved_assignment(folder)
            argv = ['monitor_employee_products.py', '--assignment-file', str(path),
                    '--poll-seconds', '5', '--timeout-seconds', '30']
            with patch.dict(os.environ, {}, clear=False), \
                    patch.object(sys, 'argv', argv), \
                    patch.object(m.operations, 'active', side_effect=AssertionError('preview must not bind')), \
                    patch.object(m, 'monitor') as monitor, \
                    patch('sys.stdout', output):
                os.environ.pop('CARROT_STATE_READER', None)
                self.assertEqual(m.main(), 0)
                self.assertIsNone(os.environ.get('CARROT_STATE_READER'))
            monitor.assert_not_called()
        self.assertIn('dry run', output.getvalue())
        self.assertNotIn('监控收取使用', output.getvalue())


if __name__ == "__main__":
    unittest.main()


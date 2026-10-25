import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from contextlib import ExitStack
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import refresh_order as refresh
from agent import production_actions as actions


class RefreshActionTests(unittest.TestCase):
    def run_action(self, parameters, *, kind=None, stopped=False):
        context = SimpleNamespace(tasker=SimpleNamespace(stopping=stopped),
            get_node_data=lambda _: {'attach': {'refresh_order_type': kind}} if kind else {})
        args = SimpleNamespace(node_name='RefreshOrder', custom_action_param=json.dumps(parameters))
        with patch.object(actions, 'run_cycle_worker', return_value=True) as worker, patch('sys.stdout', io.StringIO()):
            result = actions.RefreshOrder().run(context, args)
        return result, worker

    def test_dropdown_type_survives_position_override(self):
        for kind in ('party', 'takeout', 'normal'):
            result, worker = self.run_action({'order_number': 9}, kind=kind)
            self.assertTrue(result)
            self.assertEqual(worker.call_args.args[0], ['--order-type', kind, '--order-number', '9'])
            self.assertEqual(worker.call_args.kwargs['script'], 'refresh_order.py')

    def test_invalid_parameters_and_stop_never_launch_worker(self):
        for parameters in ({'order_number': 0}, {'order_number': 10}, {'order_number': True},
                           {'order_number': '2'}, {'order_type': 'all'}, [], None):
            result, worker = self.run_action(parameters)
            self.assertFalse(result)
            worker.assert_not_called()
        result, worker = self.run_action({}, stopped=True)
        self.assertFalse(result)
        worker.assert_not_called()

    def test_real_worker_rejects_invalid_position_before_game_connection(self):
        result = subprocess.run([sys.executable, str(ROOT / 'tools/ui_worker.py'), 'refresh_order.py',
                                 '--order-number', '10'], cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('--order-number: invalid choice', result.stderr)

    def test_resource_options_load_in_either_override_order(self):
        from maa.resource import Resource
        from maa.tasker import Tasker
        Tasker.set_log_dir('')
        Tasker.set_stdout_level(2)
        interface = json.loads((ROOT / 'assets/interface.json').read_text(encoding='utf-8'))
        task = next(item for item in interface['task'] if item['entry'] == 'RefreshOrder')
        self.assertFalse(task['default_check'])
        numbers = {'RefreshOrder': {'custom_action_param': {'order_number': 7}}}
        for case in interface['option']['刷新订单类型']['cases']:
            mode = case['pipeline_override']
            for overrides in ((mode, numbers), (numbers, mode)):
                resource = Resource()
                try:
                    self.assertTrue(resource.post_bundle(ROOT / 'assets/resource').wait().succeeded)
                    for override in overrides:
                        self.assertTrue(resource.override_pipeline(override))
                    node = resource.get_node_data('RefreshOrder')
                    self.assertEqual(node['action']['param']['custom_action'], 'RefreshOrder')
                    self.assertEqual(node['action']['param']['custom_action_param'], {'order_number': 7})
                    self.assertEqual(node['attach']['refresh_order_type'], mode['RefreshOrder']['attach']['refresh_order_type'])
                finally:
                    resource.__del__()
                    resource._handle = None


class RefreshBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)

    def frames(self):
        before = np.full((1358, 720, 3), 180, dtype=np.uint8)
        x, y = refresh.card_center(1)
        before[y-50:y+50, x-70:x+70] = (230, 210, 130)
        after = before.copy()
        after[y-30:y+30, x-30:x+30] = (100, 160, 200)
        return before, after

    def test_only_selected_card_is_recognized(self):
        before, _ = self.frames()
        self.assertTrue(refresh.card_selected(before, 1))
        for number in range(2, 10):
            self.assertFalse(refresh.card_selected(before, number))

    def run_refresh(self, before, after, *, replace=True, ready=True, pending=False, free=True):
        folder = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.addCleanup(folder.cleanup)
        self.progress = {}
        self.click = self.stack.enter_context(patch.object(refresh, 'post_click_normalized'))
        self.click.return_value.wait.return_value.succeeded = True
        self.stack.enter_context(patch.object(refresh, 'open_orders', return_value=True))
        self.stack.enter_context(patch.object(refresh, 'time', SimpleNamespace(sleep=lambda _: None)))
        self.stack.enter_context(patch.object(refresh, 'screenshot', side_effect=[before, before] + [after] * 5))
        self.replace = self.stack.enter_context(patch.object(refresh, 'click_template_node',
            side_effect=lambda controller, name, frame: replace if name == 'ClickNormalOrderReplace' else free))
        self.stack.enter_context(patch.object(refresh, 'card_pending', side_effect=lambda frame, number: pending and frame is before))
        self.confirm = self.stack.enter_context(patch.object(refresh, 'resolve_rare_abandon_dialog', return_value=True))
        self.stack.enter_context(patch.object(refresh, 'template_hit', return_value=(1, (0, 0), 0) if ready else None))
        return refresh.refresh_order(object(), 'party', 1, Path(folder.name), self.progress)

    def test_one_replace_and_rare_confirmation_with_changed_target(self):
        result = self.run_refresh(*self.frames())
        self.assertEqual(result['status'], 'refreshed')
        self.assertEqual(self.click.call_count, 2)
        self.assertEqual([call.args[1] for call in self.replace.call_args_list],
                         ['ClickNormalOrderReplace', 'ClickNormalOrderFreeAccelerate'])
        self.assertTrue(self.progress['free_accelerate_clicked'])
        self.assertTrue(self.confirm.call_args.kwargs['confirm'])

    def test_empty_or_unselectable_slot_does_not_replace_previous_card(self):
        before, after = self.frames()
        before[:] = 180
        with self.assertRaisesRegex(RuntimeError, 'order_not_selected'):
            self.run_refresh(before, after)
        self.replace.assert_not_called()

    def test_in_progress_or_reward_card_has_no_replace(self):
        with self.assertRaisesRegex(RuntimeError, 'replace_unavailable'):
            self.run_refresh(*self.frames(), replace=False)
        self.confirm.assert_not_called()

    def test_unchanged_card_fails_without_repeating_replace(self):
        before, _ = self.frames()
        with self.assertRaisesRegex(RuntimeError, 'refresh_unconfirmed'):
            self.run_refresh(before, before)
        self.assertEqual(self.replace.call_count, 2)


    def test_overlay_does_not_count_as_changed_order(self):
        with self.assertRaisesRegex(RuntimeError, 'refresh_unconfirmed'):
            self.run_refresh(*self.frames(), ready=False)
        self.assertEqual(self.replace.call_count, 2)

    def test_pending_target_only_free_accelerates_without_replacing(self):
        result = self.run_refresh(*self.frames(), pending=True)
        self.assertEqual(result['status'], 'refreshed')
        self.assertEqual(self.replace.call_count, 1)
        self.assertEqual(self.replace.call_args.args[1], 'ClickNormalOrderFreeAccelerate')
        self.confirm.assert_not_called()
        self.assertTrue(self.progress['resumed_pending'])

    def test_missing_free_acceleration_fails_without_paid_click(self):
        with self.assertRaisesRegex(RuntimeError, 'free_accelerate_unavailable'):
            self.run_refresh(*self.frames(), free=False)
        self.assertEqual(self.replace.call_count, 5)
        self.assertEqual([call.args[1] for call in self.replace.call_args_list],
                         ['ClickNormalOrderReplace'] + ['ClickNormalOrderFreeAccelerate'] * 4)


class RefreshEvidenceTests(unittest.TestCase):
    def test_failure_records_stage_trace_and_screenshot_and_releases_controller(self):
        def fail(controller, kind, number, evidence, progress):
            progress['stage'] = 'replace'
            raise RuntimeError('replace_unavailable')
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp') as folder, patch.object(refresh, 'ROOT', Path(folder)), patch.object(sys, 'argv', ['refresh_order.py']), patch.object(refresh, 'connect_controller', return_value=object()), patch.object(refresh, 'refresh_order', side_effect=fail), patch.object(refresh, 'screenshot', return_value=np.zeros((1358, 720, 3), dtype=np.uint8)), patch.object(refresh, 'release_controller') as release, patch('sys.stdout', io.StringIO()):
            self.assertEqual(refresh.main(), 1)
            evidence = next((Path(folder) / '.tmp/order_refresh').iterdir())
            result = json.loads((evidence / 'result.json').read_text(encoding='utf-8'))
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['stage'], 'replace')
            self.assertIn('Traceback', (evidence / 'error.log').read_text(encoding='utf-8'))
            self.assertTrue((evidence / 'failure.png').is_file())
            release.assert_called_once()

    def test_stop_after_replace_records_uncertainty_without_replacing_again(self):
        def stop(controller, kind, number, evidence, progress):
            progress.update(stage='verify_refresh', replace_clicked=True)
            raise refresh.TaskCancelled()
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp') as folder, patch.object(refresh, 'ROOT', Path(folder)), patch.object(sys, 'argv', ['refresh_order.py']), patch.object(refresh, 'connect_controller', return_value=object()), patch.object(refresh, 'refresh_order', side_effect=stop), patch.object(refresh, 'release_controller') as release, patch('sys.stdout', io.StringIO()):
            with self.assertRaises(refresh.TaskCancelled):
                refresh.main()
            evidence = next((Path(folder) / '.tmp/order_refresh').iterdir())
            result = json.loads((evidence / 'result.json').read_text(encoding='utf-8'))
            self.assertEqual(result['status'], 'cancelled')
            self.assertTrue(result['replace_clicked'])
            release.assert_called_once()


if __name__ == '__main__':
    unittest.main()

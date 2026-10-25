import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from agent import production_actions as actions
from tools import task_control
from tools import maa_frame_normalize as frames


class UiOverrideRegressionTests(unittest.TestCase):
    def verify_case(self, index, expected_apply):
        from maa.resource import Resource
        from maa.tasker import Tasker
        Tasker.set_log_dir('')
        Tasker.set_stdout_level(2)
        root = Path(__file__).resolve().parents[1]
        interface = json.loads((root / 'assets/interface.json').read_text(encoding='utf-8'))
        mode = interface['option']['周期运行模式']['cases'][index]['pipeline_override']
        numbers = interface['option']['周期参数']['pipeline_override']
        # Same typed substitutions performed by the UI's input options.
        def render(value):
            if isinstance(value, dict):
                return {key: render(item) for key, item in value.items()}
            return {'{rounds}': 2, '{poll}': 7, '{timeout}': 111}.get(value, value)
        numbers = render(numbers)
        for order in [(mode, numbers), (numbers, mode)]:
            with self.subTest(order=order):
                resource = Resource()
                try:
                    self.assertTrue(resource.post_bundle(root / 'assets/resource').wait().succeeded)
                    for override in order:
                        self.assertTrue(resource.override_pipeline(override))
                    node = resource.get_node_data('RunShortCycle')
                    self.assertEqual(node['action']['param']['custom_action_param'],
                                     {'rounds': 2, 'poll_seconds': 7, 'timeout_seconds': 111})
                    self.assertIs(node['attach'].get('ui_apply'), expected_apply)
                finally:
                    resource.__del__()
                    resource._handle = None

    def test_live_mode_and_numbers_survive_both_override_orders(self):
        self.verify_case(1, True)

    def test_preview_mode_and_numbers_survive_both_override_orders(self):
        self.verify_case(0, False)


class UiActionTests(unittest.TestCase):
    def run_action(self, parameters, *, stopped=False, approved=True):
        context = SimpleNamespace(tasker=SimpleNamespace(stopping=stopped),
                                  get_node_data=lambda _: {'attach': {'ui_apply': parameters.get('apply', False)}})
        args = SimpleNamespace(node_name='RunShortCycle', custom_action_param=json.dumps({key: value for key, value in parameters.items() if key != 'apply'}))
        with patch.object(actions, 'confirm_cycle_start', return_value=approved) as confirm, patch.object(actions, 'wait_for_background_ready', return_value=True), patch.object(actions, 'run_cycle_worker', return_value=True) as worker:
            result = actions.RunShortCycle().run(context, args)
            return result, confirm, worker

    def test_default_preview_never_prompts_or_applies(self):
        result, confirm, worker = self.run_action({})
        self.assertTrue(result)
        confirm.assert_not_called()
        self.assertNotIn('--apply', worker.call_args.args[0])

    def test_live_prompts_once_and_passes_validated_parameters(self):
        result, confirm, worker = self.run_action({'apply': True, 'rounds': 2, 'poll_seconds': 10, 'timeout_seconds': 300})
        self.assertTrue(result)
        confirm.assert_called_once()
        self.assertEqual(worker.call_args.args[0], ['--rounds', '2', '--poll-seconds', '10', '--timeout-seconds', '300', '--skip-checklist', '--apply'])

    def test_cancelled_confirmation_never_launches_worker(self):
        result, confirm, worker = self.run_action({'apply': True}, approved=False)
        self.assertFalse(result)
        worker.assert_not_called()

    def test_unsafe_foreground_timeout_never_launches_worker(self):
        context = SimpleNamespace(tasker=SimpleNamespace(stopping=False),
                                  get_node_data=lambda _: {'attach': {'ui_apply': True}})
        args = SimpleNamespace(node_name='RunShortCycle', custom_action_param='{}')
        with patch.object(actions, 'confirm_cycle_start', return_value=True), patch.object(actions, 'wait_for_background_ready', return_value=False), patch.object(actions, 'run_cycle_worker') as worker:
            self.assertFalse(actions.RunShortCycle().run(context, args))
            worker.assert_not_called()

    def test_stopped_before_start_never_prompts_or_launches(self):
        result, confirm, worker = self.run_action({'apply': True}, stopped=True)
        self.assertFalse(result)
        confirm.assert_not_called()
        worker.assert_not_called()

    def test_invalid_types_and_ranges_do_not_reach_game(self):
        for parameters in [{'apply': 'false'}, {'rounds': True}, {'rounds': 21}, {'poll_seconds': 4}, {'timeout_seconds': 1}]:
            with self.subTest(parameters=parameters), patch('sys.stdout', io.StringIO()):
                result, confirm, worker = self.run_action(parameters)
                self.assertFalse(result)
                confirm.assert_not_called()
                worker.assert_not_called()

    def test_interface_defaults_to_preview_and_has_agent_and_options(self):
        root = Path(__file__).resolve().parents[1]
        interface = json.loads((root / 'assets/interface.json').read_text(encoding='utf-8'))
        cycle = next(task for task in interface['task'] if task['entry'] == 'RunShortCycle')
        self.assertFalse(cycle['default_check'])
        self.assertEqual(interface['option']['周期运行模式']['default_case'], '仅预览')
        self.assertEqual(interface['option']['周期运行模式']['cases'][1]['name'], '正式执行')
        self.assertEqual(interface['agent']['child_args'][-1], '../agent/main.py')
        preflight = next(task for task in interface['task'] if task['entry'] == 'RuntimePreflight')
        self.assertTrue(preflight['default_check'])
        entries = {task['entry'] for task in interface['task']}
        self.assertIn('RunShortCyclePreview', entries)
        self.assertIn('RunShortCycleApply', entries)
        self.assertRegex(interface['version'], r'^\d+\.\d+\.\d+$')

    def test_preview_and_apply_nodes_bake_ui_apply(self):
        from maa.resource import Resource
        from maa.tasker import Tasker
        Tasker.set_log_dir('')
        Tasker.set_stdout_level(2)
        root = Path(__file__).resolve().parents[1]
        resource = Resource()
        try:
            self.assertTrue(resource.post_bundle(root / 'assets/resource').wait().succeeded)
            preview = resource.get_node_data('RunShortCyclePreview')
            apply = resource.get_node_data('RunShortCycleApply')
            shared = resource.get_node_data('RunShortCycle')
            self.assertIs(preview['attach'].get('ui_apply'), False)
            self.assertIs(apply['attach'].get('ui_apply'), True)
            self.assertIs(shared['attach'].get('ui_apply'), False)
            self.assertEqual(preview['action']['param']['custom_action'], 'RunShortCycle')
            self.assertEqual(apply['action']['param']['custom_action'], 'RunShortCycle')
        finally:
            resource.__del__()
            resource._handle = None

    def test_cycle_param_override_reaches_preview_and_apply_nodes(self):
        from maa.resource import Resource
        from maa.tasker import Tasker
        Tasker.set_log_dir('')
        Tasker.set_stdout_level(2)
        root = Path(__file__).resolve().parents[1]
        interface = json.loads((root / 'assets/interface.json').read_text(encoding='utf-8'))
        numbers = interface['option']['周期参数']['pipeline_override']

        def render(value):
            if isinstance(value, dict):
                return {key: render(item) for key, item in value.items()}
            return {'{rounds}': 3, '{poll}': 15, '{timeout}': 600}.get(value, value)

        numbers = render(numbers)
        resource = Resource()
        try:
            self.assertTrue(resource.post_bundle(root / 'assets/resource').wait().succeeded)
            self.assertTrue(resource.override_pipeline(numbers))
            for name in ('RunShortCycle', 'RunShortCyclePreview', 'RunShortCycleApply'):
                node = resource.get_node_data(name)
                self.assertEqual(
                    node['action']['param']['custom_action_param'],
                    {'rounds': 3, 'poll_seconds': 15, 'timeout_seconds': 600},
                    msg=name,
                )
        finally:
            resource.__del__()
            resource._handle = None


class UiCancellationTests(unittest.TestCase):
    def test_cancel_checked_before_native_input(self):
        with patch.object(frames, 'check_cancelled', side_effect=task_control.TaskCancelled), patch.object(frames, 'post_click_postmessage_only') as post:
            with self.assertRaises(task_control.TaskCancelled):
                frames.post_click_normalized(object(), 1, 2)
            post.assert_not_called()

    def test_cancel_during_mouse_down_always_posts_button_up(self):
        native = MagicMock()
        native.PostMessageW.return_value = True
        with patch.object(frames.ctypes, 'WinDLL', return_value=native), patch.object(frames.time, 'sleep', side_effect=[None, task_control.TaskCancelled]):
            with self.assertRaises(task_control.TaskCancelled):
                frames.post_click_postmessage_only(22, 1, 2)
        self.assertEqual([call.args[1] for call in native.PostMessageW.call_args_list], [0x200, 0x201, 0x202])

    def test_cancel_signal_interrupts_long_wait(self):
        with patch.object(task_control, 'check_cancelled', side_effect=task_control.TaskCancelled), patch.object(task_control, '_sleep') as sleep:
            with self.assertRaises(task_control.TaskCancelled):
                task_control.interruptible_sleep(60)
            sleep.assert_not_called()

    def test_absent_cancel_env_does_not_touch_files(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(task_control, 'Path') as path:
            task_control.check_cancelled()
            path.assert_not_called()

    def test_agent_exit_prevents_further_work(self):
        with patch.dict(os.environ, {'CARROT_UI_PARENT_PID': '123'}, clear=True), patch.object(task_control, '_parent_has_exited', return_value=True):
            with self.assertRaises(task_control.TaskCancelled):
                task_control.check_cancelled()


if __name__ == '__main__':
    unittest.main()

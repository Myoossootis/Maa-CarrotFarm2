import io
import json
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from agent import order_preferences as preferences
from agent import production_actions as actions
import check_first_orders as policy
from tools import run_daily_orders as daily


class CheckboxPreferenceTests(unittest.TestCase):
    def setUp(self):
        self.interface = json.loads((ROOT / 'assets/interface.json').read_text(encoding='utf-8'))
        self.option = self.interface['option'][preferences.OPTION]
        self.pipeline = json.loads((ROOT / 'assets/resource/pipeline/carrot_farm.json').read_text(encoding='utf-8'))
        self.entries = ['RunDailyBatchOrders', 'RunDailyPartyOrders', 'RunDailyTakeoutOrders', 'RunDailyNormalOrders']

    def test_all_catalog_products_are_selectable_with_the_requested_defaults(self):
        self.assertEqual(self.option['type'], 'checkbox')
        names = [item['name'] for item in self.option['cases']]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(set(names), {item['name'] for item in preferences.registry_products()})
        self.assertEqual(set(self.option['default_case']),
                         {'苹果', '番茄', '水稻', '玉米', '甘蔗', '土豆', '玫瑰', '牛肉', '鸡蛋'})
        for entry in self.entries:
            task = next(item for item in self.interface['task'] if item['entry'] == entry)
            self.assertIn(preferences.OPTION, task['option'])

    def test_native_overrides_accumulate_selections_in_any_order_and_keep_task_type(self):
        from maa.resource import Resource
        from maa.tasker import Tasker
        Tasker.set_log_dir('')
        Tasker.set_stdout_level(2)
        choices = {item['name']: item for item in self.option['cases']}
        for selected in (self.option['default_case'], ['苹果', '猪肉', '辣椒'], ['牛肉']):
            for order in (selected, list(reversed(selected))):
                resource = Resource()
                try:
                    self.assertTrue(resource.post_bundle(ROOT / 'assets/resource').wait().succeeded)
                    for name in order:
                        self.assertTrue(resource.override_pipeline(choices[name]['pipeline_override']))
                    for entry in self.entries:
                        node = resource.get_node_data(entry)
                        self.assertEqual(preferences.allowed_products_from_node(node), set(selected))
                        self.assertTrue(node['attach']['ui_apply'])
                        self.assertEqual(node['action']['param']['custom_action_param']['order_type'],
                                         self.pipeline[entry]['custom_action_param']['order_type'])
                finally:
                    resource.__del__()
                    resource._handle = None

    def test_unchecked_default_product_is_not_silently_reintroduced(self):
        node = self.pipeline['RunDailyPartyOrders']
        node['attach'][preferences.PREFIX + 'crop_018'] = True  # User allows pork only.
        self.assertEqual(preferences.allowed_products_from_node(node), {'猪肉'})
        self.assertNotIn('苹果', preferences.allowed_products_from_node(node))

    def test_empty_selection_and_invalid_flags_are_rejected(self):
        for node in (self.pipeline['RunDailyPartyOrders'],
                     {'attach': {preferences.PREFIX + 'crop_005': 'true'}},
                     {'attach': {preferences.PREFIX + 'crop_999': True}}):
            with self.assertRaises(ValueError):
                preferences.allowed_products_from_node(node)

    def test_agent_passes_actual_selection_to_worker(self):
        node = self.pipeline['RunDailyPartyOrders']
        node['attach'][preferences.PREFIX + 'crop_018'] = True
        node['attach'][preferences.PREFIX + 'crop_024'] = True
        context = SimpleNamespace(tasker=SimpleNamespace(stopping=False), get_node_data=lambda _: node)
        args = SimpleNamespace(node_name='RunDailyPartyOrders', custom_action_param='{"order_type":"party"}')
        with patch.object(actions, 'run_cycle_worker', return_value=True) as worker, patch('sys.stdout', io.StringIO()):
            self.assertTrue(actions.RunDailyOrders().run(context, args))
        arguments = worker.call_args.args[0]
        position = arguments.index('--allowed-products')
        self.assertEqual(set(arguments[position+1:-1]), {'猪肉', '辣椒'})
        self.assertEqual(arguments[-1], '--apply')

    def test_empty_selection_stops_before_game_worker(self):
        context = SimpleNamespace(tasker=SimpleNamespace(stopping=False),
                                  get_node_data=lambda _: self.pipeline['RunDailyPartyOrders'])
        args = SimpleNamespace(node_name='RunDailyPartyOrders', custom_action_param='{"order_type":"party"}')
        with patch.object(actions, 'run_cycle_worker') as worker, patch('sys.stdout', io.StringIO()) as output:
            self.assertFalse(actions.RunDailyOrders().run(context, args))
        worker.assert_not_called()
        self.assertIn('至少勾选一种', output.getvalue())


class PreferencePropagationTests(unittest.TestCase):
    def test_first_card_loading_waits_without_another_selection_click(self):
        import numpy as np
        from tempfile import TemporaryDirectory
        frame=np.zeros((1358,720,3),dtype=np.uint8)
        click=SimpleNamespace(wait=lambda:SimpleNamespace(succeeded=True))
        with TemporaryDirectory() as folder,patch.object(policy,'open_orders',return_value=True), \
                patch.object(policy,'post_click_normalized',return_value=click) as inputs, \
                patch.object(policy,'screenshot',return_value=frame) as screen, \
                patch.object(policy,'card_pending',return_value=False), \
                patch.object(policy,'card_selected',side_effect=[False,False,True]), \
                patch.object(policy,'template_hit',return_value=(1,(0,0),0)), \
                patch.object(policy,'read_names',return_value=['苹果','鸡蛋']), \
                patch.object(policy.decoded,'enabled',return_value=False), \
                patch.object(policy.time,'sleep'),patch('sys.stdout',io.StringIO()):
            result=policy.enforce_first_order(None,None,[],'normal',Path(folder),{},allowed_products={'苹果','鸡蛋'})
        self.assertEqual(result['status'],'allowed')
        inputs.assert_called_once();self.assertEqual(screen.call_count,3)

    def test_cli_selection_reaches_both_startup_checks_and_scheduler(self):
        calls = []
        argv = ['daily', '--order-types', 'party', 'takeout', '--allowed-products', '猪肉', '玉米', '--apply']
        with patch.object(sys, 'argv', argv), patch.object(daily.short_cycle, 'run_script', side_effect=lambda *args: calls.append(args)), patch.object(daily, 'run_scheduled', return_value=0) as schedule:
            self.assertEqual(daily.main(startup_confirmed=True), 0)
        self.assertEqual([call[2] for call in calls], ['party', 'takeout'])
        for call in calls:
            self.assertEqual(call[3], '--allowed-products')
            self.assertEqual(set(call[4:]), {'猪肉', '玉米'})
        self.assertEqual(schedule.call_args.kwargs['allowed_products'], {'猪肉', '玉米'})

    def test_adding_pork_changes_policy_decision(self):
        import numpy as np
        frame = np.zeros((1358, 720, 3), dtype=np.uint8)
        products = [{'name': '番茄', 'center_x': 100}, {'name': '猪肉', 'center_x': 250}]
        tasker = SimpleNamespace(post_recognition=lambda *a: SimpleNamespace(wait=lambda: SimpleNamespace(get=lambda:
            SimpleNamespace(nodes=[SimpleNamespace(recognition=SimpleNamespace(filtered_results=[SimpleNamespace(text='1/2 3/4')]))]))))
        with patch.object(policy, 'match_products', return_value=products):
            # Explicitly selecting pork approves it through the complete recipe check.
            self.assertEqual(policy.read_names(tasker, frame, [], allowed_products={'番茄', '猪肉'}), ['番茄', '猪肉'])

    def test_removing_apple_refreshes_even_though_apple_is_a_default(self):
        from tempfile import TemporaryDirectory
        import numpy as np
        frame = np.zeros((1358, 720, 3), dtype=np.uint8)
        click = SimpleNamespace(wait=lambda: SimpleNamespace(succeeded=True))
        with TemporaryDirectory(dir=ROOT / '.tmp') as folder, patch.object(policy, 'open_orders', return_value=True), patch.object(policy, 'post_click_normalized', return_value=click), patch.object(policy, 'time', SimpleNamespace(sleep=lambda _: None)), patch.object(policy, 'screenshot', return_value=frame), patch.object(policy, 'card_selected', return_value=True), patch.object(policy, 'card_pending', return_value=False), patch.object(policy, 'template_hit', return_value=(1, (0, 0), 0)), patch.object(policy, 'read_names', side_effect=[['苹果', '鸡蛋'], ['土豆', '鸡蛋']]), patch.object(policy, 'refresh_order') as refresh, patch('sys.stdout', io.StringIO()):
            progress = {}
            result = policy.enforce_first_order(object(), object(), [], 'normal', Path(folder), progress,
                                                allowed_products={'土豆', '鸡蛋'})
        refresh.assert_called_once()
        self.assertEqual(progress['history'][0]['forbidden'], ['苹果'])
        self.assertEqual(result['products'], ['土豆', '鸡蛋'])


if __name__ == '__main__':
    unittest.main()

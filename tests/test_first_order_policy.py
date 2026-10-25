from contextlib import ExitStack
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import check_first_orders as policy
from tools import run_daily_orders as daily


class ProductPolicyTests(unittest.TestCase):
    def test_whitelist_is_exact_and_variants_are_not_implicitly_allowed(self):
        self.assertEqual(policy.default_allowed_products(),
            {'苹果', '番茄', '水稻', '玉米', '甘蔗', '土豆', '玫瑰', '牛肉', '鸡蛋'})
        self.assertFalse({'青苹果', '红皮土豆', '猪肉', '羊肉', '牛奶', '鱼肉'} & policy.default_allowed_products())

    def test_known_forbidden_product_rejects_even_if_stock_is_sufficient(self):
        with patch.object(policy, 'match_products', return_value=[{'name': '猪肉', 'center_x': 100}]) as match:
            tasker = MagicMock()
            self.assertEqual(policy.read_names(tasker, object(), []), ['猪肉'])
            tasker.post_recognition.assert_not_called()

    def read_allowed(self, counters):
        tasker = MagicMock()
        tasker.post_recognition.return_value.wait.return_value.get.return_value = SimpleNamespace(
            nodes=[SimpleNamespace(recognition=SimpleNamespace(filtered_results=counters))])
        with patch.object(policy, 'match_products', return_value=[
            {'name': '番茄', 'center_x': 100}, {'name': '鸡蛋', 'center_x': 250}]):
            return policy.read_names(tasker, np.zeros((1358, 720, 3), dtype=np.uint8), [])

    def test_complete_allowed_recipe_passes(self):
        names = self.read_allowed([SimpleNamespace(text='9/4', box=(80, 1020, 40, 20)),
                                   SimpleNamespace(text='0/5', box=(380, 20, 40, 20))])
        self.assertEqual(names, ['番茄', '鸡蛋'])

    def test_merged_ocr_box_is_counted_as_two_ingredients(self):
        self.assertEqual(self.read_allowed([SimpleNamespace(text='9/4 0/5', box=(80, 20, 340, 20))]), ['番茄', '鸡蛋'])

    def test_unknown_extra_ingredient_cannot_pass_partial_allowed_recipe(self):
        with self.assertRaisesRegex(RuntimeError, 'first_recipe_incomplete'):
            self.read_allowed([SimpleNamespace(text='9/4', box=(80, 1020, 40, 20)),
                               SimpleNamespace(text='0/5', box=(380, 20, 40, 20)),
                               SimpleNamespace(text='0/7', box=(680, 20, 40, 20))])

    def test_unlocked_catalog_preserves_sweet_potato_id_and_adds_rice_and_pepper(self):
        from tools.production_targets import ROUTE_BY_ID, build_targets
        from tools.employee_capabilities import load_capabilities, slot_can_produce
        registry = json.loads((ROOT / 'assets/catalog/backpack_crops/registry.json').read_text(encoding='utf-8'))['products']
        by_name = {item['name']: item for item in registry}
        self.assertEqual(len(by_name), len(registry))
        self.assertEqual(by_name['红薯']['id'], 'crop_011')
        self.assertEqual(by_name['水稻']['id'], 'crop_023')
        self.assertEqual(by_name['辣椒']['id'], 'crop_024')
        self.assertEqual(set(ROUTE_BY_ID), {item['id'] for item in registry})
        for name in ('水稻', '红薯', '辣椒'):
            self.assertTrue((ROOT / by_name[name]['template']).is_file())
        targets = build_targets([dict(name='水稻', actual=0, required=5, missing=5)])
        self.assertEqual(targets[0]['route'], 'field')
        self.assertIn('水稻', policy.default_allowed_products())
        self.assertNotIn('红薯', policy.default_allowed_products())
        self.assertNotIn('辣椒', policy.default_allowed_products())
        self.assertNotIn('小麦', policy.default_allowed_products())
        # A player's newly unlocked crop is not proof about any employee level.
        self.assertFalse(any(slot_can_produce(slot, '水稻') for slot in load_capabilities()['slots']))


class FirstSlotFlowTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        folder = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.addCleanup(folder.cleanup)
        self.evidence = Path(folder.name)
        self.progress = {}
        self.frame = np.zeros((1358, 720, 3), dtype=np.uint8)
        self.stack.enter_context(patch.object(policy, 'open_orders', return_value=True))
        self.stack.enter_context(patch.object(policy, 'probe_availability', return_value={'status': 'available'}))
        self.stack.enter_context(patch.object(policy, 'screenshot', return_value=self.frame))
        self.click = self.stack.enter_context(patch.object(policy, 'post_click_normalized'))
        self.click.return_value.wait.return_value.succeeded = True
        self.stack.enter_context(patch.object(policy, 'time', SimpleNamespace(sleep=lambda _: None)))
        self.stack.enter_context(patch.object(policy, 'card_pending', return_value=False))
        self.stack.enter_context(patch.object(policy, 'card_selected', return_value=True))
        self.stack.enter_context(patch.object(policy, 'template_hit', return_value=(1, (0, 0), 0)))
        self.stack.enter_context(patch.object(policy, 'recognized_text', return_value=''))
        self.refresh = self.stack.enter_context(patch.object(policy, 'refresh_order', return_value={'status': 'refreshed'}))

    def run_policy(self, kind='party'):
        with patch('sys.stdout', io.StringIO()):
            return policy.enforce_first_order(object(), object(), [], kind, self.evidence, self.progress)

    def test_allowed_first_slot_does_not_refresh(self):
        for kind in ('party', 'takeout', 'normal'):
            with patch.object(policy, 'read_names', return_value=['苹果', '鸡蛋']):
                self.assertEqual(self.run_policy(kind)['status'], 'allowed')
        self.refresh.assert_not_called()
        self.assertTrue(all(call.args[1:] == policy.card_center(1) for call in self.click.call_args_list))

    def test_repeated_forbidden_recipes_refresh_until_allowed_only_at_slot_one(self):
        with patch.object(policy, 'read_names', side_effect=[['苹果', '猪肉'], ['牛奶', '番茄'], ['土豆', '牛肉']]):
            result = self.run_policy()
        self.assertEqual(result['products'], ['土豆', '牛肉'])
        self.assertEqual(self.refresh.call_count, 2)
        self.assertTrue(all(call.args[2] == 1 for call in self.refresh.call_args_list))
        self.assertEqual(self.progress['refresh_count'], 2)

    def test_unreadable_recipe_retries_without_discarding_order(self):
        with patch.object(policy, 'read_names', side_effect=RuntimeError('first_recipe_incomplete')) as read:
            with self.assertRaisesRegex(RuntimeError, 'first_recipe_incomplete'):
                self.run_policy()
        self.assertEqual(read.call_count, 3)
        self.refresh.assert_not_called()

    def test_pending_slot_resumes_free_acceleration_then_checks_new_recipe(self):
        with patch.object(policy, 'card_pending', side_effect=[True, False]), patch.object(policy, 'read_names', return_value=['玉米', '鸡蛋']):
            self.assertEqual(self.run_policy()['status'], 'allowed')
        self.refresh.assert_called_once()

    def test_dish_level_locked_refreshes_instead_of_upgrading_or_stopping(self):
        with patch.object(policy, 'read_names', side_effect=[RuntimeError('first_recipe_incomplete'), ['水稻', '鸡蛋']]), patch.object(policy, 'recognized_text', return_value='你的菜品等级太低,无法接受客人的订单'):
            self.assertEqual(self.run_policy('takeout')['status'], 'allowed')
        self.refresh.assert_called_once()
        self.assertEqual(self.progress['history'][0]['reason'], 'dish_level_locked')

    def test_hidden_ingredient_is_refreshed_instead_of_approving_partial_recipe(self):
        with patch.object(policy, 'read_names', side_effect=[policy.RecipeNotFullyVisible('配方超出可见区域'), ['番茄', '牛肉']]):
            self.assertEqual(self.run_policy('takeout')['status'], 'allowed')
        self.refresh.assert_called_once()
        self.assertEqual(self.progress['history'][0]['reason'], 'recipe_not_fully_visible')

    def test_refresh_failure_stops_before_accepting_or_producing(self):
        self.refresh.side_effect = RuntimeError('free_accelerate_unavailable')
        with patch.object(policy, 'read_names', return_value=['羊肉', '土豆']):
            with self.assertRaisesRegex(RuntimeError, 'free_accelerate_unavailable'):
                self.run_policy()
        self.assertEqual(self.progress['stage'], 'refresh_first_order')

    def test_unselectable_slot_never_refreshes_another_position(self):
        with patch.object(policy, 'card_selected', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'first_order_not_selected'):
                self.run_policy()
        self.refresh.assert_not_called()

    def test_existing_delivery_is_left_to_original_reward_flow(self):
        with patch.object(policy, 'template_hit', return_value=None), patch.object(policy, 'recognized_text', return_value='领取奖励'), patch.object(policy, 'read_names') as read:
            self.assertEqual(self.run_policy()['status'], 'existing_delivery')
        self.refresh.assert_not_called()
        read.assert_not_called()

    def test_refresh_limit_is_reported_instead_of_unbounded_rerolls(self):
        with patch.object(policy, 'MAX_REFRESHES', 2), patch.object(policy, 'read_names', return_value=['猪肉', '土豆']):
            with self.assertRaisesRegex(RuntimeError, 'first_order_refresh_limit'):
                self.run_policy()
        self.assertEqual(self.refresh.call_count, 2)

    def test_stop_prevents_new_recognition_and_refresh(self):
        with patch.object(policy, 'check_cancelled', side_effect=policy.TaskCancelled):
            with self.assertRaises(policy.TaskCancelled):
                self.run_policy()
        self.refresh.assert_not_called()


class StartupWiringTests(unittest.TestCase):
    def test_new_recipe_after_delivery_is_checked_before_next_quota_decision(self):
        events = []
        gate = SimpleNamespace(can_start=False, in_cooldown=True, as_dict=lambda: {}, completed_today=6,
                               daily_cap=12, next_available='later')
        first_gate = SimpleNamespace(can_start=True, as_dict=lambda: {})
        accepted = {'_exit_code': 0, 'rounds': [{'accepted': True, 'batch_count': 1}]}
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp') as folder, patch.object(daily, 'TMP', Path(folder)), patch.object(daily, 'MONITOR_DIR', Path(folder)), patch.object(daily, 'can_start', side_effect=[first_gate, gate, gate]), patch.object(daily, '_run_one_short', side_effect=lambda **kw: events.append('submit') or accepted), patch.object(daily.short_cycle, 'run_script', side_effect=lambda *a: events.append('policy')), patch('sys.stdout', io.StringIO()):
            self.assertEqual(daily.run_daily('party', apply=True, poll=20, timeout=900, startup_confirmed=True), 0)
        self.assertEqual(events, ['submit', 'policy'])

    def test_policy_runs_before_daily_processing_for_each_task(self):
        for kind in ('party', 'takeout', 'normal'):
            events = []
            with patch.object(sys, 'argv', ['run_daily_orders.py', '--order-type', kind, '--apply']), patch.object(daily.short_cycle, 'run_script', side_effect=lambda *a: events.append(a)), patch.object(daily, 'run_daily', side_effect=lambda *a, **kw: events.append(('daily', a[0])) or 0):
                self.assertEqual(daily.main(startup_confirmed=True), 0)
            self.assertEqual(events, [('check_first_orders.py', '--order-type', kind), ('daily', kind)])

    def test_joint_task_checks_both_types_before_any_production_or_cooldown(self):
        events = []
        with patch.object(sys, 'argv', ['daily', '--order-types', 'party', 'takeout', '--apply']), patch.object(daily.short_cycle, 'run_script', side_effect=lambda *a: events.append(a[-1])), patch.object(daily, 'run_scheduled', side_effect=lambda *a, **kw: events.append('schedule') or 0):
            self.assertEqual(daily.main(startup_confirmed=True), 0)
        self.assertEqual(events, ['party', 'takeout', 'schedule'])

    def test_failed_type_does_not_start_production_but_other_type_can_continue(self):
        def guard(*args):
            if args[-1] == 'party':
                raise RuntimeError('first_order_refresh_limit')
        with patch.object(sys, 'argv', ['daily', '--order-types', 'party', 'takeout', '--apply']), patch.object(daily.short_cycle, 'run_script', side_effect=guard), patch.object(daily, 'write_json'), patch.object(daily, 'run_scheduled', return_value=0) as schedule, patch('sys.stdout', io.StringIO()):
            self.assertEqual(daily.main(startup_confirmed=True), 2)
        self.assertEqual(schedule.call_args.args[0], ['takeout'])

    def test_dry_run_never_recognizes_or_refreshes(self):
        with patch.object(sys, 'argv', ['daily', '--order-type', 'normal']), patch.object(daily.short_cycle, 'run_script') as guard, patch.object(daily, 'run_daily', return_value=0):
            self.assertEqual(daily.main(), 0)
        guard.assert_not_called()

    def test_cancelled_startup_confirmation_never_refreshes(self):
        with patch.object(sys, 'argv', ['daily', '--order-type', 'normal', '--apply']), patch.object(daily, 'confirm_cycle_start', return_value=False), patch.object(daily.short_cycle, 'run_script') as guard:
            self.assertEqual(daily.main(), 2)
        guard.assert_not_called()

    def test_worker_dispatches_policy_cli(self):
        result = subprocess.run([sys.executable, str(ROOT / 'tools/ui_worker.py'), 'check_first_orders.py', '--order-type', 'invalid'], cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('--order-type: invalid choice', result.stderr)


if __name__ == '__main__':
    unittest.main()

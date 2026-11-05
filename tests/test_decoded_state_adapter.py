import copy
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools.allocate_employee_work import plan_decoded
from tools.compare_decoded_state import compare_inventory, compare_orders, compare_employees
from tools import compare_decoded_state as comparison
from tools.decoded_state_adapter import adapt_snapshot


ROOT = Path(__file__).resolve().parents[1]
COUNTS = dict(normal=3, party=6, takeout=6)


def snapshot(*, production=False):
    data = {'schemaVersion': 1, 'readOnly': True, 'source': 'local_client_cache', 'packageVersion': '87',
            'capturedAt': '2026-10-17T18:00:00Z',
            'inventory': {'products': [{'itemId': 29, 'name': '苹果', 'count': 2},
                                       {'itemId': 157, 'name': '甘蔗', 'count': 1}]},
            'orders': [{'category': 'normal', 'slot': 1, 'uid': 1001, 'statusCode': 1,
                        'costs': [{'itemId': 29, 'name': '苹果', 'count': 2},
                                  {'itemId': 157, 'name': '甘蔗', 'count': 3}]}],
            'production': {'complete': True, 'semanticsPackageVersion': '87', 'buildings': [
                {'buildingItemId': 12, 'buildingUid': 12, 'slotCount': 1,
                 'slots': [{'slot': 1, 'outputId': 0, 'status': 'empty'}]},
                {'buildingItemId': 723, 'buildingUid': 723, 'slotCount': 1,
                 'slots': [{'slot': 1, 'outputId': 0, 'status': 'empty'}]}]},
            'employees': {'plans': [
                {'buildingItemId': 12, 'buildingUid': 12, 'employees': [{'name': '员工甲'}], 'targets': [],
                 'heldProducts': [], 'productionOperations': None},
                {'buildingItemId': 723, 'buildingUid': 723, 'employees': [{'name': '员工乙'}],
                 'targets': [{'name': '鸡蛋', 'count': 18}],
                 'heldProducts': [{'name': '鸡蛋', 'count': 20}], 'productionOperations': None}]}}
    if not production:
        data.pop('production')
    return data


class DecodedAdapterTests(unittest.TestCase):
    def test_counter_only_mode_never_impersonates_a_complete_family_board(self):
        source=snapshot();source.update(mode='lion_counter',family=dict(period=dict(completed=2)))
        adapted=adapt_snapshot(source,COUNTS)
        self.assertIsNone(adapted['family'])
        self.assertEqual(adapted['lion_counter'],dict(completed=2))
        source['family']['tasks']=[]
        with self.assertRaisesRegex(ValueError,'counter-only'):adapt_snapshot(source,COUNTS)

    def test_preview_preserves_planted_ground_even_without_employee_plan(self):
        source = snapshot(production=True)
        source['production']['buildings'][0]['slots'][0].update(outputId=103, status='growing')
        result = plan_decoded(source, 1)
        self.assertFalse(result['allocation']['ready'])
        self.assertEqual(result['allocation']['jobs'], [])
        self.assertEqual(result['assignment_blockers']['field_1'], 'physical_production_busy')
        self.assertEqual(result['allocation']['unmet'][0]['reason'], 'all_capable_slots_busy')

    def test_multiplier_applied_once_and_inventory_not_multiplied(self):
        source = snapshot()
        original = copy.deepcopy(source)
        adapted = adapt_snapshot(source, COUNTS)
        apple = adapted['orders']['normal'][0]['items'][0]
        self.assertEqual((apple['actual'], apple['required'], apple['missing']), (2, 6, 4))
        self.assertEqual(apple['single_required'], 2)
        self.assertFalse(adapted['ui_batch_controls_verified'])
        self.assertEqual(source, original)
        single = adapt_snapshot(source, dict(normal=1, party=1, takeout=1))
        self.assertEqual(single['orders']['normal'][0]['items'][0]['required'], 2)

    def test_name_collision_does_not_merge_game_ids(self):
        source = snapshot()
        source['inventory']['products'].append({'itemId': 999, 'name': '苹果', 'count': 100})
        adapted = adapt_snapshot(source, COUNTS)
        apples = [x for x in adapted['inventory']['items'] if x['name'] == '苹果']
        self.assertEqual(len(apples), 2)
        self.assertTrue(all(x['id'] is None for x in apples))
        self.assertEqual(adapted['orders']['normal'][0]['reason'], 'unmapped_product')

    def test_unknown_product_is_not_aliased_to_variant(self):
        source = snapshot()
        source['inventory']['products'].append({'itemId': 9999, 'name': '未知新品种', 'count': 7})
        adapted = adapt_snapshot(source, COUNTS)
        self.assertIsNone(adapted['inventory']['items'][-1]['id'])
        self.assertEqual(adapted['mapping_issues'][0]['game_item_id'], 9999)

    def test_cabbage_and_strawberry_have_distinct_decoded_mappings_and_routes(self):
        from tools.production_targets import build_targets
        source = snapshot()
        source['inventory']['products'] += [{'itemId': 161, 'name': '白菜', 'count': 5},
                                           {'itemId': 160, 'name': '草莓', 'count': 7}]
        adapted = adapt_snapshot(source, COUNTS)
        by_game = {x['game_item_id']: x for x in adapted['inventory']['items']}
        self.assertEqual(by_game[161]['id'], 'crop_025')
        self.assertEqual(by_game[160]['id'], 'crop_026')
        self.assertNotEqual(by_game[160]['id'], 'crop_002')
        self.assertEqual(adapted['mapping_issues'], [])
        targets = build_targets([dict(name='白菜', actual=5, required=8, missing=3),
                                 dict(name='草莓', actual=7, required=9, missing=2)])
        self.assertEqual([x['route'] for x in targets], ['field', 'field'])

    def test_duplicate_ids_and_invalid_counts_are_rejected(self):
        source = snapshot()
        source['inventory']['products'].append(source['inventory']['products'][0])
        with self.assertRaisesRegex(ValueError, 'duplicate inventory'):
            adapt_snapshot(source, COUNTS)
        for value in (0, -1, True, 1.5, 4):
            with self.assertRaises(ValueError):
                adapt_snapshot(snapshot(), dict(COUNTS, normal=value))

    def test_non_waiting_orders_are_not_selected_for_planning(self):
        source = snapshot()
        source['orders'][0]['statusCode'] = 3
        self.assertEqual(adapt_snapshot(source, COUNTS)['orders']['normal'][0]['reason'], 'non_waiting')
        self.assertIsNone(plan_decoded(source, 3)['target_order'])

    def test_held_products_do_not_increase_inventory_or_prove_idle(self):
        adapted = adapt_snapshot(snapshot(), COUNTS)
        self.assertEqual(len(adapted['inventory']['items']), 2)
        ranch = next(x for x in adapted['employees'] if x['slot_id'] == 'ranch_2')
        self.assertEqual(ranch['items'], {'鸡蛋': 18})
        self.assertEqual(ranch['cached_state'], 'busy')
        self.assertFalse(ranch['idle_verified_for_execution'])

    def test_existing_planner_consumes_adapter_and_preserves_busy_slots(self):
        preview = plan_decoded(snapshot(production=True), 3)
        self.assertEqual(preview['target_order']['total_missing'], 12)
        self.assertIn('ranch_2', preview['cached_busy_slots_preserved'])
        self.assertFalse(preview['execution_eligible'])
        self.assertEqual(preview['allocation']['jobs'][0]['slot_id'], 'field_1')
        self.assertTrue(any(x['name'] == '苹果' for x in preview['allocation']['unmet']))

    def test_empty_plan_without_employee_is_not_an_allocation_candidate(self):
        source = snapshot()
        source['employees']['plans'][0]['employees'] = []
        adapted = adapt_snapshot(source, COUNTS)
        self.assertEqual(adapted['employees'][0]['cached_state'], 'worker_unavailable')
        self.assertEqual(plan_decoded(source, 3)['allocation']['jobs'], [])

    def test_cost_identity_conflict_fails(self):
        source = snapshot()
        source['orders'][0]['costs'][0]['name'] = '番茄'
        with self.assertRaisesRegex(ValueError, 'conflicts'):
            adapt_snapshot(source, COUNTS)

    def test_family_occupied_and_unavailable_progress_survive_adapter(self):
        source = snapshot()
        tasks = [dict(slot=i, acceptor={'uid': 5} if i == 1 else None,
                      status='occupied' if i == 1 else 'unclaimed',
                      progress=dict(current=None, available=False)) for i in range(1, 91)]
        source['family'] = {'tasks': tasks}
        adapted = adapt_snapshot(source, COUNTS)
        self.assertEqual(adapted['family']['tasks'][0]['status'], 'occupied')
        self.assertIsNone(adapted['family']['tasks'][0]['progress']['current'])
        source['family']['tasks'].pop()
        with self.assertRaisesRegex(ValueError, '90'):
            adapt_snapshot(source, COUNTS)

    def test_only_read_only_decoder_source_and_aware_timestamp_accepted(self):
        for key, value in [('readOnly', False), ('schemaVersion', 2), ('source', 'ocr'),
                           ('capturedAt', '2026-10-17T18:00:00')]:
            source = dict(snapshot(), **{key: value})
            with self.assertRaises(ValueError):
                adapt_snapshot(source, COUNTS)


class ShadowComparisonTests(unittest.TestCase):
    def setUp(self):
        self.adapted = adapt_snapshot(snapshot(), COUNTS)

    def test_compare_full_recipe_across_changed_slots(self):
        old = {'completion': {'required': 3}, 'orders': [
            {'order': 7, 'items': [{'name': '甘蔗', 'required': 9, 'actual': 1},
                                   {'name': '苹果', 'required': 6, 'actual': 2}]}]}
        result = compare_orders(self.adapted, old)
        self.assertEqual(result['orders'][0]['decoded_candidate_slots'], [1])
        self.assertEqual(result['orders'][0]['inventory_differences'], [])
        old['orders'][0]['items'][0]['required'] = 12
        self.assertEqual(compare_orders(self.adapted, old)['orders'][0]['status'], 'recipe_absent')

    def test_batch_mismatch_and_inconsistent_ocr_quantities_not_certified(self):
        self.assertEqual(compare_orders(self.adapted, {'completion': {'required': 1}})['status'], 'batch_count_unknown_or_different')
        old = {'completion': {'required': 3}, 'orders': [{'order': 1, 'items': [{'name': '苹果', 'required': 5}]}]}
        self.assertEqual(compare_orders(self.adapted, old)['orders'][0]['status'], 'batch_metadata_inconsistent')

    def test_duplicate_recipes_remain_ambiguous(self):
        self.adapted['orders']['normal'].append(dict(self.adapted['orders']['normal'][0], order=2))
        old = {'completion': {'required': 3}, 'orders': [{'order': 1, 'items': self.adapted['orders']['normal'][0]['items']}]}
        self.assertEqual(compare_orders(self.adapted, old)['orders'][0]['status'], 'ambiguous_recipe')

    def test_historical_inventory_diff_and_employee_plan_diff(self):
        old = {'items': [{'id': 'crop_012', 'name': '苹果', 'quantity': 4}]}
        result = compare_inventory(self.adapted, old)
        self.assertEqual(result['items'][0]['delta'], -2)
        self.assertEqual(result['status'], 'saved_evidence_only')
        plan = compare_employees(self.adapted, {'saved': [{'slot_id': 'field_1', 'items': {'甘蔗': 1}}]})
        self.assertEqual(plan['plans'][0]['status'], 'different')

    def test_shadow_cli_imports_have_no_ui_dependency(self):
        code = """import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.')[0] in {'maa', 'cv2', 'scan_normal_orders', 'configure_field_employee', 'complete_normal_orders'}:
        raise AssertionError('UI dependency: ' + name)
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from tools import compare_decoded_state
from tools.allocate_employee_work import plan_decoded
"""
        result = subprocess.run([sys.executable, '-c', code], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_live_wrapper_only_invokes_read_only_decoder_and_writes_shadow_output(self):
        # Simulated launch routing only; actual target reads are verified separately.
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)

            def decoder(command, **kwargs):
                self.assertEqual(command[1], str(ROOT / 'tools/read_only_game_state.cjs'))
                self.assertEqual(command[2], 'all')
                self.assertEqual(command[3:5], ['--python', sys.executable])
                self.assertNotIn('--apply', command)
                Path(command[-1]).write_text(json.dumps(snapshot()), encoding='utf-8')
                return subprocess.CompletedProcess(command, 0, '', '')

            with patch.object(comparison, 'OUTPUT', output), \
                 patch.object(comparison.subprocess, 'run', side_effect=decoder) as launch, \
                 patch.object(comparison, 'read_baseline', return_value=({'available': False}, None)), \
                 patch.object(sys, 'argv', ['compare_decoded_state.py', '--live']), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(comparison.main(), 0)
                launch.assert_called_once()
            report = json.loads((output / 'comparison.json').read_text(encoding='utf-8'))
            self.assertFalse(report['execution_eligible'])
            self.assertFalse(report['live_reader_replaced'])
            self.assertTrue((output / 'compatibility.json').exists())


if __name__ == '__main__':
    unittest.main()

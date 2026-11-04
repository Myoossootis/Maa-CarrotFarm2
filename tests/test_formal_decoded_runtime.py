from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from tools import decoded_runtime as reader
from tools import configure_field_employee as workers
from tools import run_employee_order_plan as planner
from tools import scan_normal_orders as scans
from tools import scan_backpack_inventory as backpack
from tools import allocate_employee_work as allocation
from tools import monitor_employee_products as monitoring
import complete_batch_orders as batch
import check_first_orders as first
from tools.feed_production_ui import FeedProductionUI
from test_decoded_state_adapter import snapshot


def state():
    data = snapshot(production=True)
    data.update(mode='automation', capturedAt=datetime.now(timezone.utc).isoformat(), storageSequence=22)
    data['inventory']['records'] = [{'itemId': 19, 'count': 9}]
    data['orders'][0]['menuEligibility'] = dict(unlocked=True, levelSufficient=True)
    data['employees']['repeatEnabled'] = False
    buildings = {12, 723}
    for building, slot in reader.SLOT_BY_BUILDING.items():
        if building not in buildings:
            data['employees']['plans'].append(dict(buildingItemId=building, employees=[{'name': slot}],
                                                   targets=[], heldProducts=[], productionOperations=None))
    for plan in data['employees']['plans']:
        plan.setdefault('accumulated', [])
        plan.setdefault('buildingUid', plan['buildingItemId'])
        if plan['buildingItemId'] not in buildings and plan['buildingItemId'] not in (32, 878):
            data['production']['buildings'].append(dict(buildingItemId=plan['buildingItemId'],
                buildingUid=plan['buildingUid'], slotCount=1, slots=[dict(slot=1, outputId=0, status='empty')]))
    return data


class FormalBackendTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, CARROT_STATE_READER='decoded')
        self.env.start()
        self.addCleanup(self.env.stop)
        reader.select_order('normal', 1, 3)

    def test_worker_snapshot_uses_automation_not_family_or_ui(self):
        with patch.object(reader, '_request_report', return_value=state()) as request:
            data = reader.snapshot()
        self.assertEqual(data['mode'], 'automation')
        self.assertEqual(request.call_args.args[0], 'automation')

    def test_each_snapshot_reads_again_instead_of_reusing_the_bag(self):
        first_state, second_state = state(), state()
        second_state['inventory']['records'][0]['count'] = 4
        second_state['storageSequence'] = 23
        with patch.object(reader, '_request_report', side_effect=[first_state, second_state]) as request:
            first = reader.snapshot()
            second = reader.snapshot()
        self.assertEqual(request.call_count, 2)
        self.assertEqual(first['inventory']['records'][0]['count'], 9)
        self.assertEqual(second['inventory']['records'][0]['count'], 4)
        self.assertNotEqual(first['storageSequence'], second['storageSequence'])

    def test_error_and_stale_snapshot_never_fall_back(self):
        with patch.object(reader, '_request_report', side_effect=reader.DecodeError('bad key')):
            with self.assertRaises(reader.DecodeError):
                reader.snapshot()
        old = state()
        old['capturedAt'] = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
        with self.assertRaises(reader.DecodeError):
            reader.validate(old)

    def test_uid_change_and_menu_locked_are_rejected(self):
        data = state()
        reader.read_selected(data)
        data['orders'][0]['uid'] += 1
        with self.assertRaisesRegex(reader.DecodeError, 'UID changed'):
            reader.read_selected(data)
        reader.select_order('normal', 1, 3)
        data['orders'][0]['menuEligibility']['levelSufficient'] = False
        with self.assertRaises(reader.OrderUnavailable):
            reader.read_selected(data)
        data['orders'][0]['statusCode'] = 2
        with self.assertRaises(reader.DecodeError) as raised:
            reader.read_selected(data)
        self.assertNotIsInstance(raised.exception, reader.OrderUnavailable)

    def test_actual_counts_and_multipliers_do_not_mix_categories(self):
        data = state()
        data['orders'].append(dict(data['orders'][0], category='party', uid=2002))
        reader.select_order('party', 1, 6)
        rows = reader.read_selected(data)
        self.assertEqual((rows[0]['single_required'], rows[0]['required'], rows[0]['actual']), (2, 12, 2))
        self.assertEqual(rows[0]['game_order_uid'], 2002)
        reader.set_batch_count(1)
        self.assertEqual(reader.read_selected(data)[0]['required'], 2)

    def test_oversupply_cannot_hide_another_product_shortage(self):
        data = state()
        plan = reader.plan('field_1', data)
        plan['targets'] = [dict(itemId=29, name='苹果', count=3), dict(itemId=157, name='甘蔗', count=3)]
        plan['accumulated'] = [dict(itemId=29, name='苹果', count=1)]
        plan['heldProducts'] = [dict(itemId=29, name='苹果', count=20), dict(itemId=157, name='甘蔗', count=1)]
        self.assertEqual(reader.progress('field_1', 6, data), (4, 6))
        with self.assertRaises(reader.DecodeError):
            reader.progress('field_1', 5, data)

    def test_verify_saved_plan_requires_exact_items_and_repeat(self):
        data = state()
        plan = reader.plan('field_1', data)
        plan['targets'] = [dict(name='番茄', count=2, itemId=14)]
        with patch.object(reader, 'snapshot', return_value=data):
            self.assertEqual(reader.verify_plan('field_1', {'番茄': 2}, timeout=0)['storage_sequence'], 22)
            with self.assertRaises(reader.DecodeError):
                reader.verify_plan('field_1', {'番茄': 3}, timeout=0)
            with self.assertRaises(reader.DecodeError):
                reader.verify_plan('field_1', {'番茄': 2}, repeat=True, timeout=0)

    def test_order_read_routes_bypass_recipe_ocr(self):
        with patch.object(reader, 'snapshot', return_value=state()), \
             patch.object(scans, 'require_decoded_card_selection'), \
             patch.object(scans, 'match_products', side_effect=AssertionError('recipe image matching')), \
             patch.object(scans, 'recognize_quantity', side_effect=AssertionError('quantity OCR')):
            tasker = MagicMock()
            self.assertEqual(scans.read_order(tasker, object(), [])[0]['required'], 6)
            tasker.post_recognition.assert_not_called()
        with patch.object(reader, 'snapshot', return_value=state()), \
             patch.object(batch, 'require_decoded_card_selection'), \
             patch.object(batch, 'screenshot', return_value=object()), \
             patch.object(batch, 'read_order', side_effect=AssertionError('legacy recipe reader')):
            self.assertEqual(batch._read_current(object(), object(), [])[0]['required'], 6)

    def test_first_recipe_whitelist_reads_full_decoded_names(self):
        with patch.object(reader, 'snapshot', return_value=state()), \
             patch.object(first, 'match_products', side_effect=AssertionError('icon OCR')):
            self.assertEqual(first.read_names(object(), object(), []), ['苹果', '甘蔗'])

    def test_idle_allocation_and_saved_verification_do_not_open_dialogs(self):
        with patch.object(reader, 'snapshot', return_value=state()), \
             patch.object(planner, 'find_slot_action', side_effect=AssertionError('UI state read')), \
             patch.object(planner, 'open_plan', side_effect=AssertionError('open dialog')), \
             patch.object(planner, 'capacity', side_effect=AssertionError('capacity OCR')), patch('sys.stdout', io.StringIO()):
            result = planner.idle_allocation(object(), object(), [{'name': '甘蔗', 'needed': 3}])
            self.assertTrue(result['ready'])
        with patch.object(reader, 'verify_plan') as verify, patch.object(workers, 'open_plan', side_effect=AssertionError('open dialog')):
            workers.verify_saved_plan(object(), object(), 'field_1', {'番茄': 2})
            verify.assert_called_once_with('field_1', {'番茄': 2}, repeat=False)

    def test_empty_employee_plan_does_not_make_a_growing_field_schedulable(self):
        data = state()
        area = data['production']['buildings'][0]
        area['slots'][0].update(outputId=103, status='growing')
        with patch.object(reader, 'snapshot', return_value=data), patch('sys.stdout', io.StringIO()):
            result = planner.idle_allocation(object(), object(), [{'name': '甘蔗', 'needed': 3}])
        self.assertNotIn('field_1', [job['slot_id'] for job in result['jobs']])
        self.assertIn({'slot_id': 'field_1', 'state': 'physical_production_busy'}, result['busy_slots_preserved'])

    def test_missing_production_and_existing_crops_stop_before_opening_editor(self):
        for status in ('growing', 'mature', 'needs_feed', 'unknown', None):
            data = state()
            if status is None:
                del data['production']
            else:
                data['production']['buildings'][0]['slots'][0].update(outputId=103, status=status)
            with self.subTest(status=status), patch.object(reader, 'snapshot', return_value=data), patch.object(workers, 'open_plan') as editor:
                with self.assertRaises(reader.DecodeError):
                    workers.configure_items(object(), object(), 'field_1', {'番茄': 2})
                editor.assert_not_called()

    def test_assignment_requires_complete_owned_slots_and_no_pending_products(self):
        cases = [
            (lambda data: data['production'].update(complete=False), 'production_state_incomplete'),
            (lambda data: data['production'].update(semanticsPackageVersion=None), 'production_state_incomplete'),
            (lambda data: data['production']['buildings'][0].update(buildingUid=999), 'production_owner_unconfirmed'),
            (lambda data: data['production']['buildings'][0].update(slots=[]), 'production_state_incomplete'),
            (lambda data: data['production']['buildings'][0]['slots'][0].update(slot=None), 'production_state_incomplete'),
            (lambda data: reader.plan('field_1', data).update(accumulated=[dict(count=1)]), 'employee_products_pending'),
        ]
        for change, reason in cases:
            data = state()
            change(data)
            with self.subTest(reason=reason):
                self.assertEqual(reader.assignment_blocker('field_1', data), reason)
        data = state()
        data['employees']['plans'][0]['employees'] = []
        self.assertEqual(reader.assignment_blocker('field_1', data), 'missing_employee')
        self.assertIsNone(reader.assignment_blocker('field_1', state()))

    def test_configure_uses_same_clicks_but_only_postsave_decoded_quantity_check(self):
        # Every game/UI method is mocked; this does not send any input.
        with patch.object(reader, 'assignment_blocker', return_value=None), \
             patch.object(reader, 'verify_plan') as verify, \
             patch.object(workers, 'open_plan', return_value=(object(), 600)), \
             patch.object(workers, 'screenshot', return_value=object()), \
             patch.object(workers, 'match', return_value=1), \
             patch.object(workers, 'click') as click, \
             patch.object(workers, 'ensure_repeat_checkbox', return_value=object()), \
             patch.object(workers, 'icon_quantity', side_effect=AssertionError('draft OCR')), \
             patch.object(workers, 'capacity', side_effect=AssertionError('capacity OCR')):
            result = workers.configure_items(object(), object(), 'field_1', {'番茄': 2})
            self.assertTrue(result['saved'])
            self.assertEqual([call.args[1:] for call in click.call_args_list], [(128, 580), (547, 953), (547, 953), (500, 1060)])
            verify.assert_called_once_with('field_1', {'番茄': 2}, repeat=False)

    def test_busy_worker_and_missing_saved_readback_block_completion(self):
        with patch.object(reader, 'assignment_blocker', return_value='employee_plan_busy'), patch.object(workers, 'open_plan') as open_plan:
            with self.assertRaises(reader.DecodeError):
                workers.configure_items(object(), object(), 'field_1', {'番茄': 2})
            open_plan.assert_not_called()

    def test_save_readback_failure_does_not_report_success_or_repeat_save(self):
        with patch.object(reader, 'assignment_blocker', return_value=None), \
             patch.object(reader, 'verify_plan', side_effect=reader.DecodeError('not persisted')), \
             patch.object(workers, 'open_plan', return_value=(object(), 600)), \
             patch.object(workers, 'screenshot', return_value=object()), \
             patch.object(workers, 'match', return_value=1), \
             patch.object(workers, 'click') as click, \
             patch.object(workers, 'ensure_repeat_checkbox', return_value=object()):
            with self.assertRaisesRegex(reader.DecodeError, 'not persisted'):
                workers.configure_items(object(), object(), 'field_1', {'番茄': 2})
            self.assertEqual(sum(call.args[1:] == (500, 1060) for call in click.call_args_list), 1)

    def test_maa_worker_forces_decoded_backend_before_dispatch_without_running_action(self):
        from tools import ui_worker
        with patch.object(sys, 'argv', ['ui_worker.py', 'verify_slot_work_plan.py', '--slot', 'field_2']), \
             patch.dict(os.environ, CARROT_STATE_READER='ocr'), \
             patch.object(ui_worker.importlib.metadata, 'version', return_value='5.13.1'), \
             patch.object(ui_worker, 'install_interruptible_waits'), \
             patch.object(ui_worker, 'install_safe_inputs'), \
             patch.object(ui_worker, 'check_cancelled'), \
             patch.object(ui_worker.os, 'chdir'), \
             patch.object(ui_worker.runpy, 'run_path') as dispatch, patch('sys.stdout', io.StringIO()):
            ui_worker.main()
            self.assertEqual(os.environ['CARROT_STATE_READER'], 'decoded')
            dispatch.assert_called_once()

    def test_progress_and_inventory_readers_do_not_use_ocr(self):
        data = state()
        with patch.object(reader, 'snapshot', return_value=data), \
             patch.object(workers, 'slot_match', side_effect=AssertionError('card OCR')):
            self.assertIsNone(workers.list_progress(object(), object(), 'field_1'))
            with self.assertRaises(reader.DecodeError):
                workers.capacity(object(), object())
        ui = FeedProductionUI.__new__(FeedProductionUI)
        with patch.object(ui, 'wait_on_farm'), patch.object(reader, 'snapshot', return_value=data):
            self.assertEqual(ui.inventory(), 9)

    def test_formal_backpack_entry_reads_without_creating_controller(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(backpack, 'RESULT_PATH', Path(folder) / 'inventory.json'), \
             patch.object(reader, 'snapshot', return_value=state()), \
             patch.object(backpack, 'Win32Controller', side_effect=AssertionError('game connection')), \
             patch('sys.stdout', io.StringIO()):
            self.assertEqual(backpack.main(), 0)
            report = json.loads(backpack.RESULT_PATH.read_text(encoding='utf-8'))
            self.assertEqual(report['state_reader'], 'decoded')
            self.assertFalse(report['background_input'])

    def test_formal_planner_rejects_ocr_checkpoint(self):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / 'completion.json'
            file.write_text(json.dumps({'generated_at_utc': datetime.now(timezone.utc).isoformat()}), encoding='utf-8')
            with patch.object(allocation, 'LATEST_ORDER', file):
                with self.assertRaisesRegex(reader.DecodeError, 'old OCR'):
                    allocation.plan_latest()

    def test_changed_plan_identity_stops_monitor_before_any_collection(self):
        with ExitStack() as stack:
            for name in ('Win32Controller', 'Resource', 'Tasker', 'find_window', 'write_json', 'RESULT'):
                stack.enter_context(patch.object(monitoring, name))
            collect = stack.enter_context(patch.object(monitoring, 'collect_finished_slot'))
            stack.enter_context(patch.object(reader, 'snapshot', return_value=state()))
            observed = stack.enter_context(patch.object(reader, 'plan_items', return_value={'番茄': 27}))
            with self.assertRaises(reader.DecodeError):
                monitoring.monitor({'field_1': 27}, 5, 30, {'field_1': {'小麦': 27}})
            observed.assert_called_once()
            collect.assert_not_called()

    def test_held_recipe_conflict_skips_collect_and_keeps_matching_slot(self):
        data = state()
        plans = {row['buildingItemId']: row for row in data['employees']['plans']}
        field = plans[284]
        field['targets'] = [{'name': '番茄', 'itemId': 155, 'count': 10}]
        field['heldProducts'] = [{'name': '番茄', 'itemId': 155, 'count': 10}]
        field['accumulated'] = []
        orchard = plans[28]
        orchard['targets'] = [{'name': '苹果', 'itemId': 29, 'count': 4}]
        orchard['heldProducts'] = [{'name': '苹果', 'itemId': 29, 'count': 4}]
        orchard['accumulated'] = []
        orchard['buildingUid'] = 28
        with ExitStack() as stack:
            for name in ('Win32Controller', 'Resource', 'Tasker', 'find_window', 'write_json',
                         'RESULT', '_close_employee_menu_with_retry'):
                stack.enter_context(patch.object(monitoring, name))
            stack.enter_context(patch.object(monitoring.time, 'sleep'))
            stack.enter_context(patch('sys.stdout', io.StringIO()))
            collect = stack.enter_context(patch.object(
                monitoring, 'collect_finished_slot',
                return_value={'collected': True, 'progress': (4, 4)}))
            stack.enter_context(patch.object(reader, 'snapshot', return_value=data))
            result = monitoring.monitor(
                {'field_2': 10, 'orchard_1': 4}, 5, 30,
                {'field_2': {'小麦': 10}, 'orchard_1': {'苹果': 4}})
        self.assertEqual(collect.call_count, 1)
        self.assertEqual(collect.call_args.args[1], 'orchard_1')
        self.assertEqual(result['recipe_conflict_slots'], ['field_2'])
        self.assertNotIn('field_2', result['collected_at_utc'])
        self.assertIn('orchard_1', result['collected_at_utc'])
        self.assertFalse(result['complete'])
        self.assertEqual(result['pending'], [])


if __name__ == '__main__':
    unittest.main()

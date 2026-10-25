import copy
import ast
from datetime import datetime, timezone
import io
import importlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools import manual_planting as planting
from agent.manual_planting_action import ManualPlantingPreflight, request_from_node

NOW = 1792304000


def stamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def slot(number, output=0, cycle=40):
    values = [output, NOW if output else 0, 0, cycle]
    return dict(slot=number, outputId=output, status='growing' if output else 'empty',
                cycleIndex=cycle, startedAtUnix=values[1] or None, raw=dict(tuple=values))


def snapshot():
    return dict(schemaVersion=1, readOnly=True, source='local_client_cache', packageVersion='87',
                capturedAt=stamp(NOW), storageSequence=20,
                inventory=dict(records=[dict(uid=1, itemId=1, count=100, type=1)]),
                employees=dict(plans=[dict(buildingItemId=12, buildingUid=10, targets=[], accumulated=[],
                                          heldProducts=[], productionOperations=None)]),
                production=dict(complete=True, semanticsPackageVersion='87',
                                manualControl=dict(totalControlEnabled=False),
                                plantingCatalog=[dict(itemId=155, name='玉米', outputId=103,
                                                      acceptedOutputIds=[103, 153], unlocked=True, costKnown=True,
                                                      costs=[dict(itemId=1, name='金币', perSlot=10, available=100)])],
                                buildings=[dict(buildingItemId=12, buildingUid=10, carrierUid=11,
                                                kind='field', buildingName='农田1', slotCount=3,
                                                raw=dict(datas_list='0_0_0_40#0_0_0_40#0_0_0_40'),
                                                slots=[slot(1), slot(2), slot(3)])]))


def planted(before, count=1, output=103):
    result = copy.deepcopy(before)
    result.update(capturedAt=stamp(NOW + 1), storageSequence=21)
    building = result['production']['buildings'][0]
    for index in range(count):
        building['slots'][index] = slot(index + 1, output, 41)
    building['raw']['datas_list'] = '#'.join('_'.join(map(str, s['raw']['tuple'])) for s in building['slots'])
    result['inventory']['records'][0]['count'] -= 10 * count
    return result


class ManualPlantingTests(unittest.TestCase):
    def plan(self, data=None, count=1):
        return planting.build_preflight(data or snapshot(), 'field_1', 155, count, now=NOW)

    def test_quantity_is_physical_slots_and_cost_is_not_multiplied_by_six(self):
        result = self.plan(count=2)
        self.assertTrue(result['ready'])
        self.assertEqual(result['emptySlots'], [1, 2, 3])
        self.assertEqual(result['selectedSlots'], [1, 2])
        self.assertEqual(result['costs'][0]['total'], 20)
        self.assertFalse(result['executionEnabled'])

    def test_occupied_slots_are_not_reused_and_no_partial_plan_reports_ready(self):
        data = snapshot()
        data['production']['buildings'][0]['slots'][0] = slot(1, 103)
        result = self.plan(data, 3)
        self.assertFalse(result['ready'])
        self.assertEqual(result['selectedSlots'], [2, 3])

    def test_unknown_balance_is_not_zero_or_sufficient_but_zero_cost_is_allowed(self):
        data = snapshot()
        data['production']['plantingCatalog'][0]['costs'][0]['available'] = None
        result = self.plan(data)
        self.assertFalse(result['ready'])
        self.assertIsNone(result['costs'][0]['shortfall'])
        data['production']['plantingCatalog'][0]['costs'][0]['perSlot'] = 0
        self.assertTrue(self.plan(data)['ready'])

    def test_insufficient_balance_and_unknown_or_locked_crop_block(self):
        data = snapshot()
        data['production']['plantingCatalog'][0]['costs'][0]['available'] = 5
        self.assertEqual(self.plan(data)['costs'][0]['shortfall'], 5)
        self.assertFalse(self.plan(data)['ready'])
        for value in (False, None):
            data = snapshot()
            data['production']['plantingCatalog'][0]['unlocked'] = value
            self.assertFalse(self.plan(data)['ready'])
        self.assertFalse(planting.build_preflight(snapshot(), 'field_1', 999, 1, now=NOW)['ready'])

    def test_active_employee_target_held_products_and_operations_block(self):
        for key, value in [('targets', [dict(count=1)]), ('heldProducts', [dict(count=1)]),
                           ('accumulated', [dict(count=1)]), ('productionOperations', 'busy')]:
            data = snapshot()
            data['employees']['plans'][0][key] = value
            self.assertFalse(self.plan(data)['ready'], key)

    def test_missing_duplicate_or_mismatched_employee_records_block(self):
        for mode in ('missing', 'duplicate', 'wrong_uid'):
            data = snapshot()
            if mode == 'missing':
                data['employees']['plans'] = []
            elif mode == 'duplicate':
                data['employees']['plans'] *= 2
            else:
                data['employees']['plans'][0]['buildingUid'] = 99
            self.assertFalse(self.plan(data)['ready'])

    def test_stale_incomplete_unknown_global_mode_and_new_version_block(self):
        for mode in ('stale', 'incomplete', 'unknown_slot', 'total_mode', 'missing_mode', 'new_version'):
            data = snapshot()
            if mode == 'stale': data['capturedAt'] = stamp(NOW - 11)
            if mode == 'incomplete': data['production']['complete'] = False
            if mode == 'unknown_slot': data['production']['buildings'][0]['slots'][0]['status'] = 'unknown'
            if mode == 'total_mode': data['production']['manualControl']['totalControlEnabled'] = True
            if mode == 'missing_mode': data['production'].pop('manualControl')
            if mode == 'new_version': data['packageVersion'] = '88'
            self.assertFalse(self.plan(data)['ready'], mode)

    def test_invalid_parameters_and_duplicate_slot_indices_fail_closed(self):
        for count in (0, 37, True, '2'):
            with self.assertRaises(planting.PlantingError): self.plan(count=count)
        with self.assertRaises(planting.PlantingError):
            planting.build_preflight(snapshot(), 'ranch_1', 155, 1, now=NOW)
        data = snapshot()
        data['production']['buildings'][0]['slots'][1]['slot'] = 1
        self.assertFalse(self.plan(data)['ready'])

    def test_before_guard_accepts_same_state_and_rejects_stale_or_changed_state(self):
        data = snapshot()
        result = self.plan(data)
        self.assertEqual(planting.validate_before(result, data, now=NOW)['gameOperations'], 0)
        with self.assertRaises(planting.PlantingError): planting.validate_before(result, data, now=NOW + 11)
        data['inventory']['records'][0]['count'] -= 1
        with self.assertRaises(planting.PlantingError): planting.validate_before(result, data, now=NOW)

    def test_after_guard_accepts_exact_normal_and_allowed_mutation_only_in_offline_snapshots(self):
        data = snapshot()
        for output in (103, 153):
            report = planting.verify_after(self.plan(data), data, planted(data, output=output), now=NOW + 1)
            self.assertEqual(report['count'], 1)
            self.assertFalse(report['liveAcceptancePerformed'])
            self.assertEqual(report['gameOperations'], 0)

    def test_after_rejects_no_update_partial_extra_wrong_crop_and_wrong_debit(self):
        data = snapshot()
        for mode in ('no_update', 'old_time', 'partial', 'extra', 'wrong_crop', 'debit', 'cycle', 'employee'):
            result = self.plan(data, 2 if mode == 'partial' else 1)
            after = planted(data)
            if mode == 'no_update':
                after = copy.deepcopy(data)
                after['capturedAt'] = stamp(NOW + 1)
            if mode == 'old_time': after['capturedAt'] = data['capturedAt']
            if mode == 'extra': after = planted(data, 2)
            if mode == 'wrong_crop': after = planted(data, output=105)
            if mode == 'debit': after['inventory']['records'][0]['count'] += 1
            if mode == 'cycle': after['production']['buildings'][0]['slots'][0]['cycleIndex'] = 40
            if mode == 'employee': after['employees']['plans'][0]['targets'] = [dict(count=1)]
            with self.assertRaises(planting.PlantingError, msg=mode):
                planting.verify_after(result, data, after, now=NOW + 1)

    def test_new_production_tuple_is_confirmed_even_without_sequence_advance(self):
        before = snapshot()
        after = planted(before)
        after['storageSequence'] = before['storageSequence']
        result = planting.verify_after(self.plan(before), before, after, now=NOW + 1)
        self.assertEqual(result['status'], 'confirmed')
        self.assertFalse(result['storageSequenceAdvanced'])
        self.assertEqual(result['confirmationBasis'], 'exact_target_slot_tuple_and_new_cycle')

    def test_sequence_advance_alone_never_proves_planting(self):
        before = snapshot()
        after = copy.deepcopy(before)
        after.update(capturedAt=stamp(NOW + 1), storageSequence=999)
        with self.assertRaises(planting.PlantingPending):
            planting.verify_after(self.plan(before), before, after, now=NOW + 1)

    def test_save_preflight_only_uses_decoder_and_writes_json(self):
        data = snapshot()
        data['capturedAt'] = datetime.now(timezone.utc).isoformat()
        reader = importlib.import_module('tools.decoded_runtime')
        with tempfile.TemporaryDirectory() as folder, patch.object(reader, 'snapshot', return_value=data):
            result = planting.save_preflight('field_1', 155, 1, folder)
            saved = json.loads((Path(folder) / 'preflight.json').read_text(encoding='utf-8'))
            self.assertEqual(saved, result)
            self.assertFalse(saved['executionEnabled'])


class MaaManualPlantingTests(unittest.TestCase):
    def test_preview_code_has_no_game_input_or_controller_dependency(self):
        banned = {'maa_frame_normalize', 'configure_field_employee', 'enter_farm', 'ui_worker',
                  'production_actions', 'Win32Controller', 'post_click_normalized', 'post_swipe_normalized'}
        for name in ('tools/manual_planting.py', 'agent/manual_planting_action.py'):
            tree = ast.parse((planting.ROOT / name).read_text(encoding='utf-8'))
            identifiers = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
            imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
            self.assertFalse(banned & (identifiers | imported), name)

    def test_node_parameters_cannot_turn_on_execution(self):
        for value in ({'apply': True}, {'execute': True}, [], {'count': True}, {'count': 37}):
            with self.assertRaises(ValueError): request_from_node({}, value)
        request = request_from_node({'attach': dict(manual_field_id='field_3', manual_crop_item_id=161)}, {'count': '5'})
        self.assertEqual(request, dict(field_id='field_3', crop_item_id=161, count=5))

    def test_action_only_reads_and_opens_preselected_window_and_stops_on_cancellation(self):
        context = SimpleNamespace(tasker=SimpleNamespace(stopping=False), get_node_data=lambda _: {})
        args = SimpleNamespace(custom_action_param='{"count":2}', node_name='ManualPlantingPreflight')
        with patch.object(planting, 'save_preflight', return_value=dict(ready=True, blockers=[])) as save, \
             patch('tools.decoded_dashboard.launch_dashboard', return_value=123) as launch, \
             patch('builtins.print'):
            self.assertTrue(ManualPlantingPreflight().run(context, args))
            save.assert_called_once_with(field_id='field_1', crop_item_id=14, count=2)
            launch.assert_called_once_with('manual_planting', planting_request=dict(field_id='field_1', crop_item_id=14, count=2))
            context.tasker.stopping = True
            self.assertFalse(ManualPlantingPreflight().run(context, args))
            self.assertEqual(save.call_count, 1)

    def test_actual_maa_resource_merges_field_crop_and_count_without_running_tasker(self):
        from maa.resource import Resource
        root = planting.ROOT
        interface = json.loads((root / 'assets/interface.json').read_text(encoding='utf-8'))
        pipeline = json.loads((root / 'assets/resource/pipeline/carrot_farm.json').read_text(encoding='utf-8'))
        task = next(t for t in interface['task'] if t['entry'] == 'ManualPlantingPreflight')
        self.assertFalse(task['default_check'])
        node = pipeline['ManualPlantingPreflight']
        self.assertNotIn('next', node)
        resource = Resource()
        self.assertTrue(resource.override_pipeline({'ManualPlantingPreflight': node}))
        for key, choice in [('手动种植农田', '农田3'), ('手动种植作物', '白菜')]:
            case = next(c for c in interface['option'][key]['cases'] if c['name'] == choice)
            self.assertTrue(resource.override_pipeline(case['pipeline_override']))
        number = interface['option']['手动种植数量']['pipeline_override']
        number = json.loads(json.dumps(number).replace('{count}', '5'))
        self.assertTrue(resource.override_pipeline(number))
        actual = resource.get_node_data('ManualPlantingPreflight')
        self.assertEqual(actual['attach']['manual_field_id'], 'field_3')
        self.assertEqual(actual['attach']['manual_crop_item_id'], 161)
        self.assertEqual(actual['action']['type'], 'Custom')
        self.assertEqual(actual['action']['param']['custom_action_param']['count'], '5')
        self.assertEqual(len(interface['option']['手动种植作物']['cases']), 12)


if __name__ == '__main__':
    unittest.main()

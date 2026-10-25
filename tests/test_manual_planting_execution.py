"""Offline-only execution guards; no game/controller connection is created."""
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_manual_planting import NOW, snapshot, slot, stamp
from tools import run_manual_planting as execution
from tools.manual_planting import PlantingError, build_preflight
from agent.manual_planting_action import ManualPlantingOnce


REQUEST = dict(field_id='field_1', crop_item_id=155, count=2)


class FakeUI:
    def __init__(self, mode='normal'):
        self.data = snapshot()
        self.sent = []
        self.mode = mode
        self.navigation = 0

    def check_supported(self, plan):
        if self.mode == 'unmapped':
            raise RuntimeError('未校准')

    def prepare(self, plan):
        self.navigation += 1
        if self.mode == 'prechange':
            self.data['storageSequence'] += 1

    def locate(self, plan):
        pass

    def assert_ready(self, plan):
        if self.mode == 'occluded':
            raise RuntimeError('遮挡')

    def plant(self, plan):
        self.sent.append(plan['selectedSlots'][0])
        if self.mode == 'input_failed':
            raise OSError('点击可能已发送')
        if self.mode == 'no_update':
            return
        self.data['storageSequence'] += 1
        self.data['capturedAt'] = stamp(NOW + len(self.sent))
        building = self.data['production']['buildings'][0]
        number = self.sent[-1]
        building['slots'][number - 1] = slot(number, 103 if self.mode != 'wrong_crop' else 999, 41)
        if self.mode == 'extra':
            building['slots'][2] = slot(3, 103, 41)
        building['raw']['datas_list'] = '#'.join('_'.join(map(str, s['raw']['tuple'])) for s in building['slots'])
        self.data['inventory']['records'][0]['count'] -= 10

    def read(self):
        return copy.deepcopy(self.data)


class ExecutionTests(unittest.TestCase):
    def run_request(self, ui, folder, **kwargs):
        return execution.execute_once(REQUEST, ui, ui.read, folder, now=NOW,
                                      sleep=lambda _: None, **kwargs)

    def test_two_slots_confirm_individually_then_validate_whole_request(self):
        with tempfile.TemporaryDirectory() as folder:
            ui = FakeUI()
            result = self.run_request(ui, folder)
            self.assertEqual(ui.sent, [1, 2])
            self.assertEqual(result['status'], 'confirmed')
            self.assertEqual(result['validation']['count'], 2)
            self.assertEqual(len(list((Path(folder) / 'sent').glob('*.json'))), 2)
            self.assertFalse(result['continuousLoopStarted'])

    def test_same_sequence_tuple_update_confirms_once_without_resending(self):
        ui = FakeUI()
        original = ui.plant
        def same_sequence(plan):
            original(plan)
            ui.data['storageSequence'] = 20
        ui.plant = same_sequence
        with tempfile.TemporaryDirectory() as folder:
            result = self.run_request(ui, folder)
        self.assertEqual(ui.sent, [1, 2])
        self.assertEqual(result['status'], 'confirmed')
        self.assertFalse(result['validation']['storageSequenceAdvanced'])

    def test_unmapped_blocks_before_any_navigation_or_journal(self):
        with tempfile.TemporaryDirectory() as folder:
            ui = FakeUI('unmapped')
            with self.assertRaises(RuntimeError):
                self.run_request(ui, folder)
            self.assertEqual(ui.navigation, 0)
            self.assertFalse((Path(folder) / 'active.json').exists())

    def test_changed_state_and_occlusion_send_no_planting_input(self):
        for mode in ('prechange', 'occluded'):
            with tempfile.TemporaryDirectory() as folder:
                ui = FakeUI(mode)
                with self.assertRaises((PlantingError, RuntimeError)):
                    self.run_request(ui, folder)
                self.assertEqual(ui.sent, [])

    def test_timeout_and_contradictory_update_stop_after_one_input(self):
        for mode in ('no_update', 'wrong_crop', 'extra', 'input_failed'):
            with tempfile.TemporaryDirectory() as folder:
                ui = FakeUI(mode)
                with self.assertRaises((PlantingError, OSError)):
                    self.run_request(ui, folder)
                self.assertEqual(ui.sent, [1])
                saved = json.loads((Path(folder) / 'active.json').read_text(encoding='utf8'))
                self.assertEqual(saved['status'], 'unconfirmed')
                self.assertEqual(saved['seedInputsReserved'], 1)
                # Process restart / request changes cannot turn uncertainty into another click.
                with self.assertRaises(PlantingError):
                    self.run_request(ui, folder)
                self.assertEqual(ui.sent, [1])

    def test_durable_marker_is_present_before_click_and_does_not_depend_on_sequence(self):
        with tempfile.TemporaryDirectory() as folder:
            ui = FakeUI()
            one = build_preflight(ui.read(), 'field_1', 155, 1, now=NOW)
            key = execution.slot_key(one, ui.read())
            ui.data['storageSequence'] += 1
            self.assertEqual(key, execution.slot_key(one, ui.read()))
            original = ui.plant
            def checked(plan):
                self.assertTrue((Path(folder) / 'sent' / (execution.slot_key(plan, ui.read()) + '.json')).exists())
                original(plan)
            ui.plant = checked
            self.run_request(ui, folder)

    def test_exclusive_slot_reservation_rejects_replay_even_with_a_new_sequence(self):
        with tempfile.TemporaryDirectory() as folder:
            ui = FakeUI()
            one = build_preflight(ui.read(), 'field_1', 155, 1, now=NOW)
            ledger = Path(folder) / 'sent'
            ledger.mkdir()
            (ledger / (execution.slot_key(one, ui.read()) + '.json')).write_text('{}')
            ui.data['storageSequence'] += 2
            with self.assertRaises(FileExistsError):
                self.run_request(ui, folder)
            self.assertEqual(ui.sent, [])

    def test_cancel_after_reserved_input_blocks_next_slot_and_restart(self):
        class Cancelled(BaseException):
            pass
        with tempfile.TemporaryDirectory() as folder:
            ui = FakeUI()
            def cancel():
                if ui.sent:
                    raise Cancelled()
            with self.assertRaises(Cancelled):
                self.run_request(ui, folder, check_cancelled=cancel)
            self.assertEqual(ui.sent, [1])
            with self.assertRaises(PlantingError):
                self.run_request(ui, folder)

    def test_read_only_poll_may_wait_for_cache_but_never_resend(self):
        with tempfile.TemporaryDirectory() as folder:
            ui = FakeUI()
            previous = ui.read()
            reads = 0
            def delayed():
                nonlocal reads, previous
                if ui.sent:
                    reads += 1
                    if reads < 3:
                        pending = copy.deepcopy(previous)
                        pending['capturedAt'] = stamp(NOW + 1)
                        return pending
                previous = ui.read()
                return previous
            result = execution.execute_once(dict(REQUEST, count=1), ui, delayed, folder,
                                            now=NOW, sleep=lambda _: None)
            self.assertEqual(result['status'], 'confirmed')
            self.assertEqual(ui.sent, [1])


class IntegrationTests(unittest.TestCase):
    def test_maa_action_routes_finite_request_and_cancels_without_worker(self):
        context = SimpleNamespace(tasker=SimpleNamespace(stopping=False),
                                  get_node_data=lambda _: {'attach': {'manual_field_id': 'field_3', 'manual_crop_item_id': 161}})
        argv = SimpleNamespace(custom_action_param='{"count":"3"}', node_name='ManualPlantingOnce')
        with patch('agent.cycle_bridge.run_cycle_worker', return_value=True) as worker:
            self.assertTrue(ManualPlantingOnce().run(context, argv))
            worker.assert_called_once()
            self.assertEqual(worker.call_args.args[0], ['--field', 'field_3', '--crop-item-id', '161', '--count', '3', '--apply'])
            self.assertEqual(worker.call_args.kwargs['script'], 'run_manual_planting.py')
            context.tasker.stopping = True
            self.assertFalse(ManualPlantingOnce().run(context, argv))
            self.assertEqual(worker.call_count, 1)

    def test_real_resource_merges_execution_choices_without_controller(self):
        from maa.resource import Resource
        root = execution.ROOT
        interface = json.loads((root / 'assets/interface.json').read_text(encoding='utf8'))
        pipeline = json.loads((root / 'assets/resource/pipeline/carrot_farm.json').read_text(encoding='utf8'))
        task = next(t for t in interface['task'] if t['entry'] == 'ManualPlantingOnce')
        self.assertFalse(task['default_check'])
        self.assertNotIn('next', pipeline['ManualPlantingOnce'])
        self.assertNotIn('on_error', pipeline['ManualPlantingOnce'])
        resource = Resource()
        self.assertTrue(resource.override_pipeline(pipeline))
        for key, name in [('手动种植农田', '农田2'), ('手动种植作物', '草莓')]:
            case = next(c for c in interface['option'][key]['cases'] if c['name'] == name)
            self.assertTrue(resource.override_pipeline(case['pipeline_override']))
        override = json.loads(json.dumps(interface['option']['手动种植数量']['pipeline_override']).replace('{count}', '2'))
        self.assertTrue(resource.override_pipeline(override))
        node = resource.get_node_data('ManualPlantingOnce')
        self.assertEqual(node['attach']['manual_field_id'], 'field_2')
        self.assertEqual(node['attach']['manual_crop_item_id'], 160)
        self.assertEqual(node['action']['param']['custom_action_param']['count'], '2')

    def test_profile_accepts_all_three_calibrated_fields_and_blocks_missing_slots(self):
        import copy
        from tools.manual_planting_ui import check_profile
        profile = json.loads((execution.ROOT / 'assets/catalog/manual_planting_ui.json').read_text(encoding='utf8'))
        for field in ('field_1', 'field_2', 'field_3'):
            check_profile(profile, dict(fieldId=field, selectedSlots=[1, 15]))
            missing = copy.deepcopy(profile)
            del missing['fields'][field]['slots']['1']
            with self.assertRaises(RuntimeError):
                check_profile(missing, dict(fieldId=field, selectedSlots=[1]))

    def test_saved_actual_frames_localize_farm_and_reject_unrelated_scene_offline(self):
        import cv2
        import numpy as np
        from tools.manual_planting_ui import scene_translation, IMAGES
        reference = cv2.imread(str(IMAGES / 'farm_reference.png'))
        frames = execution.ROOT / 'tests/fixtures/manual_planting'
        neutral = np.zeros((1358, 720, 3), np.uint8)
        neutral[280:1030] = cv2.imread(str(frames / 'neutral_farm.png'))
        self.assertLess(np.linalg.norm(scene_translation(reference, neutral)), 3)
        wrong = neutral.copy()
        wrong[280:1030] = cv2.imread(str(frames / 'wrong_scene.png'))
        with self.assertRaises(RuntimeError):
            scene_translation(reference, wrong)
        # An unvalidated zoom must never be treated as a camera pan.
        zoomed = neutral.copy()
        zoomed[280:1030] = cv2.warpAffine(neutral[280:1030], np.float32([[1.1, 0, 0], [0, 1.1, 0]]), (720, 750))
        with self.assertRaises(RuntimeError):
            scene_translation(reference, zoomed)


if __name__ == '__main__':
    unittest.main()

"""Static geometry, historical pixels and fake planting only; never open a controller."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from test_manual_planting import NOW, snapshot, slot, stamp
from test_manual_planting_execution import FakeUI
from tools.build_manual_planting_calibration import ROOT, GEOMETRY, PROFILE, build_profile
from tools.manual_planting import build_preflight, PlantingError
from tools.manual_planting_ui import IMAGES, ManualPlantingUI, check_profile, canonical_translation, scene_translation, seed_bar_close_point
from tools.run_manual_planting import execute_once


def read_geometry():
    return json.loads(GEOMETRY.read_text(encoding='utf8'))


def frame_from_map(name):
    frame = np.zeros((1358, 720, 3), np.uint8)
    frame[280:1030] = cv2.imread(str(IMAGES / name))
    return frame


class CalibrationTests(unittest.TestCase):
    def test_close_point_uses_blank_ground_and_excludes_soil_and_plot_centers(self):
        road = cv2.cvtColor(np.uint8([[[22, 88, 228]]]), cv2.COLOR_HSV2BGR)[0, 0]
        frame = np.full((1358, 720, 3), road, np.uint8)
        self.assertEqual(seed_bar_close_point(frame, []), (500, 900))
        self.assertNotEqual(seed_bar_close_point(frame, [np.array([500, 900])]), (500, 900))
        soil = cv2.cvtColor(np.uint8([[[21, 190, 180]]]), cv2.COLOR_HSV2BGR)[0, 0]
        with self.assertRaises(RuntimeError):
            seed_bar_close_point(np.full_like(frame, soil), [])

    def test_close_bar_clicks_map_once_and_never_retries_if_not_dismissed(self):
        road = cv2.cvtColor(np.uint8([[[22, 88, 228]]]), cv2.COLOR_HSV2BGR)[0, 0]
        frame = np.full((1358, 720, 3), road, np.uint8)
        ui = ManualPlantingUI()
        ui.marker = cv2.imread(str(IMAGES / 'seed_bar_marker.png'))
        frame[1084:1111, 511:608] = ui.marker
        ui.profile = {'fields': {}}
        ui.references = []
        from types import SimpleNamespace
        inputs = []
        ui.backend = SimpleNamespace(frame=lambda: frame, click=lambda *xy: inputs.append(xy))
        with patch('tools.manual_planting_ui.canonical_transform', return_value=np.array([[1, 0, 0], [0, 1, 0]])):
            with self.assertRaises(RuntimeError):
                ui.close_bar()
        self.assertEqual(inputs, [(500, 900)])

    def test_checked_in_profile_rebuilds_exactly_and_independent_pixels_agree(self):
        profile, report = build_profile(read_geometry())
        self.assertEqual(profile, json.loads(PROFILE.read_text(encoding='utf8')))
        self.assertEqual(report['slotCoverage'], dict(field_1=15, field_2=15, field_3=15))
        self.assertEqual(len(report['observations']), 14)
        self.assertLess(report['maxErrorPixels'], 8)
        self.assertEqual(report['gameInputs'], 0)
        self.assertFalse(report['liveAcceptancePerformed'])

    def test_field2_order_is_not_field1_order_and_map_delta_separates_field3(self):
        geometry = read_geometry()
        profile, _ = build_profile(geometry)
        self.assertNotEqual(geometry['layouts']['farmland'][0], geometry['layouts']['farmland2'][0])
        two = np.array(profile['fields']['field_2']['slots']['1'])
        three = np.array(profile['fields']['field_3']['slots']['1'])
        self.assertGreater(two[0] - three[0], 300)
        self.assertGreater(three[1] - two[1], 150)

    def test_identity_wrong_slot_order_and_mixed_map_anchors_are_rejected(self):
        for mode in ('identity', 'order', 'anchor', 'version'):
            geometry = read_geometry()
            if mode == 'identity':
                geometry['fields']['field_3']['buildingItemId'] = 284
            if mode == 'order':
                geometry['layouts']['farmland2'][0], geometry['layouts']['farmland2'][1] = geometry['layouts']['farmland2'][1], geometry['layouts']['farmland2'][0]
            if mode == 'anchor':
                geometry['fields']['field_3']['mapAnchor'] = geometry['fields']['field_2']['mapAnchor']
            if mode == 'version':
                geometry['packageVersion'] = '88'
            with self.assertRaises(ValueError, msg=mode):
                build_profile(geometry)

    def test_western_view_registration_has_same_canonical_map_origin(self):
        profile, _ = build_profile(read_geometry())
        references = [(cv2.imread(str(IMAGES / v['image'])), v.get('canonicalMatrix', v.get('canonicalShift')))
                      for v in profile['referenceViews']]
        frame = frame_from_map('farm_west_reference.png')
        # Cross-view registration, not just comparison with the exact same picture.
        observed = scene_translation(references[0][0], frame)
        np.testing.assert_allclose(observed, [226.4, 0], atol=2)
        np.testing.assert_allclose(canonical_translation(references, frame), [226.4, 0], atol=2)
        ui = ManualPlantingUI()  # __enter__ is deliberately NOT called.
        ui.profile, ui.references = profile, references
        for field in ('field_2', 'field_3'):
            for observation in [o for o in read_geometry()['observations'] if o['fieldId'] == field]:
                result = ui.target(dict(fieldId=field, selectedSlots=[observation['slot']]), frame)
                self.assertLess(np.linalg.norm(result - observation['pixel']), 8)

    def test_static_hud_does_not_cancel_detected_map_pan(self):
        reference = cv2.imread(str(IMAGES / 'farm_west_reference.png'))
        original = frame_from_map('farm_west_reference.png')
        frame = original.copy()
        frame[280:1030] = cv2.warpAffine(reference, np.float32([[1, 0, -120], [0, 1, 0]]), (720, 750))
        # Keep the fixed left HUD stationary while the map content moves.
        frame[280:900, :190] = original[280:900, :190]
        frame[400:570, 420:] = original[400:570, 420:]
        frame[570:1030, 590:] = original[570:1030, 590:]
        np.testing.assert_allclose(scene_translation(reference, frame), [-120, 0], atol=2)

    def test_reference_disagreement_fails_instead_of_choosing_one_field(self):
        ref = cv2.imread(str(IMAGES / 'farm_west_reference.png'))
        frame = frame_from_map('farm_west_reference.png')
        with self.assertRaises(RuntimeError):
            canonical_translation([(ref, [0, 0]), (ref, [200, 0])], frame)


def three_fields():
    data = snapshot()
    for field, item_id, number in [('field_2', 284, 15), ('field_3', 285, 6)]:
        building = copy.deepcopy(data['production']['buildings'][0])
        building.update(buildingItemId=item_id, buildingUid=item_id * 10, carrierUid=item_id * 10 + 1,
                        slotCount=number, buildingName=field)
        building['slots'] = [slot(i) for i in range(1, number + 1)]
        building['raw']['datas_list'] = '#'.join('_'.join(map(str, s['raw']['tuple'])) for s in building['slots'])
        data['production']['buildings'].append(building)
        employee = copy.deepcopy(data['employees']['plans'][0])
        employee.update(buildingItemId=item_id, buildingUid=building['buildingUid'])
        data['employees']['plans'].append(employee)
    return data


class FieldExecutionTests(unittest.TestCase):
    def test_field3_only_six_purchased_slots_never_plans_seventh_or_locked_land(self):
        data = three_fields()
        ready = build_preflight(data, 'field_3', 155, 6, now=NOW)
        self.assertTrue(ready['ready'])
        self.assertEqual(ready['selectedSlots'], list(range(1, 7)))
        self.assertFalse(build_preflight(data, 'field_3', 155, 7, now=NOW)['ready'])

    def test_each_field_routes_to_its_own_carrier_and_confirms_without_other_changes(self):
        profile, _ = build_profile(read_geometry())
        class FieldUI(FakeUI):
            def __init__(self):
                super().__init__()
                self.data = three_fields()
            def check_supported(self, plan):
                check_profile(profile, plan)
            def plant(self, plan):
                building = next(b for b in self.data['production']['buildings'] if b['carrierUid'] == plan['carrierUid'])
                number = plan['selectedSlots'][0]
                self.sent.append((plan['fieldId'], number))
                building['slots'][number - 1] = slot(number, 103, 41)
                building['raw']['datas_list'] = '#'.join('_'.join(map(str, s['raw']['tuple'])) for s in building['slots'])
                self.data['inventory']['records'][0]['count'] -= 10
                self.data['storageSequence'] += 1
                self.data['capturedAt'] = stamp(NOW + len(self.sent))
        for field in ('field_2', 'field_3'):
            ui = FieldUI()
            before = ui.read()
            with tempfile.TemporaryDirectory() as folder:
                result = execute_once(dict(field_id=field, crop_item_id=155, count=2), ui, ui.read,
                                      folder, now=NOW, sleep=lambda _: None)
                self.assertEqual(result['status'], 'confirmed')
                self.assertEqual(ui.sent, [(field, 1), (field, 2)])
                for old, new in zip(before['production']['buildings'], ui.data['production']['buildings']):
                    if old['buildingName'] != field:
                        self.assertEqual(old, new)
        wrong = FieldUI()
        original = wrong.plant
        wrong.plant = lambda plan: original(dict(plan, carrierUid=2841))
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(PlantingError):
                execute_once(dict(field_id='field_3', crop_item_id=155, count=2), wrong,
                             wrong.read, folder, now=NOW, sleep=lambda _: None)
            self.assertEqual(len(wrong.sent), 1)  # Wrong-field change stops before a second input.


if __name__ == '__main__':
    unittest.main()

"""Replay saved acceptance images only. No controller or new game reads."""
import json
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import cv2
import numpy as np

from tools.build_manual_planting_calibration import PROFILE, ROOT
from tools.manual_planting_ui import IMAGES, ManualPlantingUI, canonical_transform, reference_matrix, transform_point

FIXTURES = ROOT / 'tests/fixtures/manual_planting'


def saved_frame(field):
    frame = np.zeros((1358, 720, 3), np.uint8)
    frame[280:1030] = cv2.imread(str(FIXTURES / (field + '_scaled_map.png')))
    return frame


def configured_ui():
    ui = ManualPlantingUI()
    ui.profile = json.loads(PROFILE.read_text(encoding='utf8'))
    ui.references = [(cv2.imread(str(IMAGES / view['image'])),
                      view.get('canonicalMatrix', view.get('canonicalShift')))
                     for view in ui.profile['referenceViews']]
    return ui


class DefaultScaledTargetTests(unittest.TestCase):
    def test_default_entry_recovers_each_accepted_target_without_session_overrides(self):
        ui = configured_ui()  # Deliberately never connect a controller.
        expected = json.loads((FIXTURES / 'scaled_targets.json').read_text(encoding='utf8'))
        for field, target in expected.items():
            with self.subTest(field=field):
                point = ui.target(dict(fieldId=field, selectedSlots=[target['slot']]), saved_frame(field))
                self.assertLess(np.linalg.norm(point - target['pixel']), 3)

    def test_actual_default_ui_bootstrap_loads_current_zoom_without_game_backend(self):
        backend = MagicMock()
        backend.frame.return_value = saved_frame('field_2')
        module = SimpleNamespace(FeedProductionUI=lambda: backend)
        # Prevent importing or creating any real controller/resource backend.
        with patch.dict('sys.modules', {'tools.feed_production_ui': module}):
            with ManualPlantingUI() as ui:
                plan = dict(fieldId='field_2', selectedSlots=[1])
                ui.prepare(plan)
                point = ui.target(plan, backend.frame())
                self.assertLess(np.linalg.norm(point - [284.794, 553.740]), 3)
                self.assertEqual(len(ui.references), 4)
        backend.click.assert_not_called()

    def test_successive_fields_do_not_rescale_or_mutate_stored_coordinates(self):
        ui = configured_ui()
        original = json.dumps(ui.profile, sort_keys=True)
        plan = dict(fieldId='field_2', selectedSlots=[1])
        a = ui.target(plan, saved_frame('field_2'))
        ui.target(dict(fieldId='field_3', selectedSlots=[1]), saved_frame('field_3'))
        b = ui.target(plan, saved_frame('field_2'))
        np.testing.assert_allclose(a, b, atol=.01)
        self.assertEqual(original, json.dumps(ui.profile, sort_keys=True))

    def test_native_zoom_pan_is_remeasured_with_hud_stationary(self):
        ui = configured_ui()
        view = ui.profile['referenceViews'][3]
        reference = cv2.imread(str(IMAGES / view['image']))
        frame = np.zeros((1358, 720, 3), np.uint8)
        original = frame.copy()
        original[280:1030] = reference
        frame[280:1030] = cv2.warpAffine(reference, np.float32([[1, 0, -90], [0, 1, 35]]), (720, 750))
        frame[280:900, :190] = original[280:900, :190]
        frame[400:570, 420:] = original[400:570, 420:]
        frame[570:1030, 590:] = original[570:1030, 590:]
        point = ui.profile['fields']['field_3']['slots']['1']
        expected = transform_point(reference_matrix(view['canonicalMatrix']), point) + [-90, 35]
        actual = ui.target(dict(fieldId='field_3', selectedSlots=[1]), frame)
        self.assertLess(np.linalg.norm(actual - expected), 3)

    def test_close_bar_checks_all_plot_centers_at_current_scale(self):
        ui = configured_ui()
        frame = saved_frame('field_2')
        ui.marker = cv2.imread(str(IMAGES / 'seed_bar_marker.png'))
        frame[1084:1111, 511:608] = ui.marker
        closed = frame.copy()
        closed[1084:1111, 511:608] = 0
        ui.backend = MagicMock()
        ui.backend.frame.side_effect = [frame, closed]
        with patch('tools.manual_planting_ui.seed_bar_close_point', return_value=(500, 900)) as ground:
            ui.close_bar()
        centers = ground.call_args.args[1]
        self.assertEqual(len(centers), 45)
        self.assertLess(min(np.linalg.norm(point - [284.794, 553.740]) for point in centers), 3)
        ui.backend.click.assert_called_once_with(500, 900)

    def test_unknown_zoom_and_unrelated_scene_fail_before_navigation(self):
        ui = configured_ui()
        ui.close_bar = MagicMock()
        reference = cv2.imread(str(IMAGES / 'farm_reference.png'))
        for scale in (1.1, .8):
            frame = np.zeros((1358, 720, 3), np.uint8)
            frame[280:1030] = cv2.warpAffine(reference, np.float32([[scale, 0, 0], [0, scale, 0]]), (720, 750))
            ui.backend = SimpleNamespace(frame=lambda: frame)
            with self.assertRaises(RuntimeError):
                ui.prepare(dict(fieldId='field_2', selectedSlots=[1]))
        ui.close_bar.assert_not_called()
        wrong = np.zeros((1358, 720, 3), np.uint8)
        wrong[280:1030] = cv2.imread(str(FIXTURES / 'wrong_scene.png'))
        with self.assertRaises(RuntimeError):
            canonical_transform(ui.references, wrong)

    def test_conflicting_registered_zoom_models_fail_instead_of_picking_one(self):
        reference = cv2.imread(str(IMAGES / 'farm_scaled_reference.png'))
        frame = np.zeros((1358, 720, 3), np.uint8)
        frame[280:1030] = reference
        with self.assertRaises(RuntimeError):
            canonical_transform([(reference, [0, 0]),
                                 (reference, [[5/7, 0, 0], [0, 5/7, 0]])], frame)

    def test_uncalibrated_reference_matrices_are_rejected(self):
        for value in ([[.8, 0, 0], [0, .8, 0]], [[1, .03, 0], [0, 1, 0]],
                      [[-1, 0, 0], [0, 1, 0]], [[1, 0, float('nan')], [0, 1, 0]]):
            with self.assertRaises(RuntimeError):
                reference_matrix(value)


if __name__ == '__main__':
    unittest.main()

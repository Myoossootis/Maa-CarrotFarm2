"""Replay a real blocking work-plan dialog; never connect a controller."""
import unittest
from unittest.mock import MagicMock
import cv2
import numpy as np
from tools import manual_planting_ui as m
from tests.test_manual_planting_scale import configured_ui, saved_frame, FIXTURES


class WorkPlanPopupTests(unittest.TestCase):
    def setUp(self):
        self.dialog = cv2.imread(str(FIXTURES / 'work_plan_overlay.png'))

    def test_work_plan_cannot_be_used_as_map_even_with_matching_background(self):
        ui = configured_ui()
        with self.assertRaisesRegex(RuntimeError, '工作计划'):
            m.canonical_transform(ui.references, self.dialog)

    def test_prepare_dismisses_only_footer_then_confirms_real_map(self):
        ui = configured_ui()
        ui.backend = MagicMock()
        ui.backend.frame.side_effect = [self.dialog, saved_frame('field_2')]
        ui.close_bar = MagicMock()
        ui.prepare(dict(fieldId='field_2', selectedSlots=[2]))
        ui.backend.click.assert_called_once_with(360, 1220)
        ui.close_bar.assert_called_once()

    def test_persistent_popup_stops_after_one_close_click(self):
        ui = configured_ui()
        ui.backend = MagicMock()
        ui.backend.frame.return_value = self.dialog
        ui.close_bar = MagicMock()
        with self.assertRaisesRegex(RuntimeError, '关闭未确认'):
            ui.prepare(dict(fieldId='field_2', selectedSlots=[2]))
        ui.backend.click.assert_called_once_with(360, 1220)
        ui.close_bar.assert_not_called()

    def test_missing_footer_stops_without_any_input(self):
        ui = configured_ui()
        frame = self.dialog.copy()
        frame[1190:1250] = 0
        ui.backend = MagicMock()
        ui.backend.frame.return_value = frame
        with self.assertRaisesRegex(RuntimeError, '关闭提示'):
            ui.prepare(dict(fieldId='field_2', selectedSlots=[2]))
        ui.backend.click.assert_not_called()

    def test_open_seed_bar_uses_verified_soil_side_point(self):
        ui = configured_ui()
        frame = np.zeros((1358, 720, 3), dtype=np.uint8)
        frame[596:615, 528:547] = cv2.cvtColor(np.uint8([[[22, 200, 180]]]), cv2.COLOR_HSV2BGR)[0, 0]
        ui.backend = MagicMock()
        ui.backend.frame.return_value = frame
        ui.close_bar = MagicMock()
        ui.target = MagicMock(return_value=np.array([512., 605.]))
        ui.bar_visible = MagicMock(return_value=True)
        ui.select_crop = MagicMock()
        ui.assert_ready = MagicMock()
        ui.locate(dict(fieldId='field_2', selectedSlots=[2]))
        ui.backend.click.assert_called_once_with(537, 605)
        np.testing.assert_allclose(ui.point, [537, 605])
        frame[:] = 0
        ui.backend.click.reset_mock()
        with self.assertRaisesRegex(RuntimeError, '不是空地'):
            ui.locate(dict(fieldId='field_2', selectedSlots=[2]))
        ui.backend.click.assert_not_called()


if __name__ == '__main__':
    unittest.main()

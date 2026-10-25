"""Offline tests for one-shot repeat checkbox detection and saved-plan verify."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import cv2
import numpy as np

from tools import configure_field_employee as config


class RepeatCheckboxStateTests(unittest.TestCase):
    def _tasker_with_ocr(self, matched: bool = True):
        tasker = MagicMock()
        detail = MagicMock()
        if matched:
            detail.nodes[0].recognition.filtered_results = [MagicMock(text="完成后重复：继续使用当前计划")]
        else:
            detail.nodes = []
        tasker.post_recognition.return_value.wait.return_value.get.return_value = detail
        return tasker

    def test_ocr_gate_blocks_template_only_guess(self):
        frame = np.zeros((1358, 720, 3), dtype=np.uint8)
        tpl = cv2.imread(str(config.IMAGES / "repeat_unchecked.png"))
        h, w = tpl.shape[:2]
        frame[1120 : 1120 + h, 500 : 500 + w] = tpl
        self.assertIsNone(config.repeat_checkbox_state(self._tasker_with_ocr(False), frame))

    def test_comparative_scores_prefer_unchecked_over_cross_talk(self):
        frame = np.zeros((1358, 720, 3), dtype=np.uint8)
        tpl = cv2.imread(str(config.IMAGES / "repeat_unchecked.png"))
        h, w = tpl.shape[:2]
        frame[1118 : 1118 + h, 498 : 498 + w] = tpl
        self.assertIs(config.repeat_checkbox_state(self._tasker_with_ocr(True), frame), False)

    def test_comparative_scores_prefer_checked(self):
        frame = np.zeros((1358, 720, 3), dtype=np.uint8)
        tpl = cv2.imread(str(config.IMAGES / "repeat_checked.png"))
        h, w = tpl.shape[:2]
        frame[1132 : 1132 + h, 502 : 502 + w] = tpl
        self.assertIs(config.repeat_checkbox_state(self._tasker_with_ocr(True), frame), True)

    def test_real_unchecked_capture_is_false(self):
        path = config.ROOT / ".tmp/single_order_check/after_toggle.png"
        if not path.is_file():
            self.skipTest("missing after_toggle capture")
        frame = cv2.imread(str(path))
        self.assertIs(config.repeat_checkbox_state(self._tasker_with_ocr(True), frame), False)

    def test_real_checked_capture_is_true(self):
        path = config.ROOT / ".tmp/employee_capture/plan.png"
        if not path.is_file():
            self.skipTest("missing plan.png capture")
        frame = cv2.imread(str(path))
        self.assertIs(config.repeat_checkbox_state(self._tasker_with_ocr(True), frame), True)


class VerifySavedPlanTests(unittest.TestCase):
    def test_rearrange_summary_skips_checkbox_after_capacity_items(self):
        frame = object()
        with patch.object(config, "open_plan", return_value=(frame, 600)), patch.object(
            config, "capacity", return_value=(3, 50)
        ), patch.object(config, "icon_quantity", return_value=3), patch.object(
            config, "saved_plan_rearrange_view", return_value=True
        ) as rearrange, patch.object(
            config, "repeat_checkbox_state"
        ) as checkbox, patch.object(
            config, "close_plan_dialog"
        ) as close:
            config.verify_saved_plan(object(), object(), "field_1", {"玉米": 3})
            rearrange.assert_called_once_with(frame)
            checkbox.assert_not_called()
            close.assert_called_once()

    def test_edit_view_still_requires_unchecked(self):
        with patch.object(config, "open_plan", return_value=(object(), 600)), patch.object(
            config, "capacity", return_value=(3, 50)
        ), patch.object(config, "icon_quantity", return_value=3), patch.object(
            config, "saved_plan_rearrange_view", return_value=False
        ), patch.object(
            config, "repeat_checkbox_state", return_value=True
        ), patch.object(
            config, "repeat_checkbox_scores", return_value=(0.95, 0.70)
        ), patch.object(config, "close_plan_dialog"):
            with self.assertRaisesRegex(RuntimeError, "one-shot checkbox"):
                config.verify_saved_plan(object(), object(), "field_1", {"玉米": 3})

    def test_edit_view_accepts_confirmed_unchecked(self):
        with patch.object(config, "open_plan", return_value=(object(), 600)), patch.object(
            config, "capacity", return_value=(3, 50)
        ), patch.object(config, "icon_quantity", return_value=3), patch.object(
            config, "saved_plan_rearrange_view", return_value=False
        ), patch.object(
            config, "repeat_checkbox_state", return_value=False
        ), patch.object(config, "close_plan_dialog") as close:
            config.verify_saved_plan(object(), object(), "field_1", {"玉米": 3})
            close.assert_called_once()

    def test_real_plan_opened_capture_is_rearrange_view(self):
        path = config.ROOT / ".tmp/plan_opened.png"
        if not path.is_file():
            self.skipTest("missing plan_opened.png capture")
        frame = cv2.imread(str(path))
        self.assertTrue(config.saved_plan_rearrange_view(frame))
        # Checkbox templates do not resolve on the rearrange summary.
        checked, unchecked = config.repeat_checkbox_scores(frame)
        self.assertLess(checked, config.REPEAT_CHECKBOX_MIN_SCORE)
        self.assertLess(unchecked, config.REPEAT_CHECKBOX_MIN_SCORE)


if __name__ == "__main__":
    unittest.main()

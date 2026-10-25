from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import ui_worker as worker
import maa_frame_normalize as frames
import complete_normal_orders as orders
from maa.tasker import Tasker


class NormalizedEntryTests(unittest.TestCase):
    def test_worker_accepts_only_a_measured_supported_frame(self):
        original = frames.build_runtime_resource
        native_task = Tasker.post_task
        with patch.object(frames, 'build_runtime_resource', original), patch.object(Tasker, 'post_task', native_task):
            worker.install_safe_inputs()
            for height in (1358, 1399, 1410):
                frames.normalize_frame(np.zeros((height, 720, 3), np.uint8))
                self.assertEqual(frames.build_runtime_resource(frames.get_top_offset()), frames.SOURCE_RESOURCE)
                with self.assertRaisesRegex(RuntimeError, 'unvalidated screenshot'):
                    frames.build_runtime_resource(frames.get_top_offset() + 1)
            with patch.object(frames, '_LAST_FRAME_WH', (720, 1600)):
                with self.assertRaisesRegex(RuntimeError, 'unvalidated screenshot'):
                    frames.build_runtime_resource(52)

    def test_worker_blocks_all_native_tasks_including_former_progress_exception(self):
        original = frames.build_runtime_resource
        native_task = Tasker.post_task
        with patch.object(frames, 'build_runtime_resource', original), patch.object(Tasker, 'post_task', native_task):
            worker.install_safe_inputs()
            for name in ('VerifyNormalOrderInProgress', 'AcceptNormalOrderStart', 'unknown'):
                with self.assertRaisesRegex(RuntimeError, 'native task'):
                    Tasker.post_task(object(), name)

    def test_progress_recognition_uses_normalized_capture_without_native_task(self):
        template = cv2.imread(str(ROOT / 'assets/resource/image/navigation/normal_order_in_progress.png'))
        height, width = template.shape[:2]
        for offset in (0, 41):
            raw = np.zeros((1358 + offset, 720, 3), np.uint8)
            raw[850 + offset:850 + offset + height, 200:200 + width] = template
            frame = frames.normalize_frame(raw)
            tasker = MagicMock()
            with patch.object(orders, 'screenshot', return_value=frame), patch.object(orders, '_order_delivery_ocr') as ocr:
                self.assertTrue(orders._acceptance_confirmed(tasker, object()))
            tasker.post_task.assert_not_called()
            ocr.assert_not_called()


if __name__ == '__main__':
    unittest.main()

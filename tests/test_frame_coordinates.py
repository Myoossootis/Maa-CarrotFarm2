"""Exercise the real crop-to-input transform without any game/window access."""
from contextlib import nullcontext
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from tools import maa_frame_normalize as frames


class FrameCoordinateTests(unittest.TestCase):
    def setUp(self):
        old_size, old_offset = frames._LAST_FRAME_WH, frames._LAST_TOP_OFFSET
        self.addCleanup(setattr, frames, '_LAST_FRAME_WH', old_size)
        self.addCleanup(setattr, frames, '_LAST_TOP_OFFSET', old_offset)

    def test_letterbox_restored_before_scaling_uses_uncropped_frame_height(self):
        raw = np.zeros((1399, 720, 3), np.uint8)
        cropped = frames.normalize_frame(raw)
        self.assertEqual(cropped.shape, (1358, 720, 3))
        self.assertEqual(frames._screenshot_to_client(*frames.to_controller_xy(535, 1238), 720, 1399),
                         (535, 1279))
        self.assertEqual(frames._screenshot_to_client(*frames.to_controller_xy(535, 1238), 360, 700),
                         (267, 639))

    def test_uncropped_and_near_height_frames_keep_direct_scaling(self):
        for height in (1348, 1358, 1370):
            with self.subTest(height=height):
                frames.normalize_frame(np.zeros((height, 720, 3), np.uint8))
                self.assertEqual(frames.to_controller_xy(100, 1200), (100, 1200))
                self.assertEqual(frames._screenshot_to_client(100, 1200, 360, height // 2), (50, 600))

    def test_successive_captures_replace_the_transform_instead_of_accumulating_offset(self):
        for height, expected_y in ((1399, 1279), (1358, 1238), (1410, 1290), (1399, 1279)):
            frames.normalize_frame(np.zeros((height, 720, 3), np.uint8))
            self.assertEqual(frames._screenshot_to_client(*frames.to_controller_xy(535, 1238), 720, height),
                             (535, expected_y))

    def test_click_and_swipe_share_the_real_letterboxed_transform(self):
        frames.normalize_frame(np.zeros((1399, 720, 3), np.uint8))
        with patch.object(frames, '_lock_foreground', return_value=nullcontext(44)), \
             patch.object(frames, '_foreground_window', return_value=44), \
             patch.object(frames, '_client_size', return_value=(720, 1399)), \
             patch.object(frames, '_render_input_target', side_effect=lambda hwnd,x,y:(22,x,y)), \
             patch.object(frames, 'post_click_postmessage_only', return_value=True) as click, \
             patch.object(frames, 'post_swipe_postmessage_only', return_value=True) as swipe, \
             patch.object(frames, 'install_foreground_safe_inputs'), patch.object(frames.time, 'sleep'):
            controller = SimpleNamespace(info={'hwnd': 11})
            frames.post_click_normalized(controller, 535, 1238)
            frames.post_swipe_normalized(controller, 690, 1050, 690, 300, 550)
        click.assert_called_once_with(22, 535, 1279)
        swipe.assert_called_once_with(22, (690, 1091), (690, 341), 550)


if __name__ == '__main__':
    unittest.main()

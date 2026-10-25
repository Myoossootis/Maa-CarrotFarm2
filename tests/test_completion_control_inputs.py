from pathlib import Path
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from tools import scan_normal_orders as scan

ROOT = Path(__file__).resolve().parents[1]


class CompletionInputTests(unittest.TestCase):
    def test_missing_control_sends_no_input(self):
        frame = np.zeros((1358, 720, 3), np.uint8)
        with patch.object(scan, 'screenshot', return_value=frame), \
                patch.object(scan, 'post_click_normalized') as click, patch.object(scan.time, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, 'completion_control_unavailable'):
                scan.ensure_triple_checked(None, None)
        click.assert_not_called()

    def test_unconfirmed_toggle_is_not_repeated_at_neighboring_points(self):
        frame = np.zeros((1358, 720, 3), np.uint8)
        template = cv2.imread(str(ROOT / 'assets/resource/image/navigation/normal_order_completion_unchecked_live.png'))
        height, width = template.shape[:2]
        frame[1190:1190 + height, 480:480 + width] = template
        self.assertTrue(scan.completion_control_visible(frame))
        self.assertFalse(scan.completion_checked(frame)[0])
        with patch.object(scan, 'screenshot', return_value=frame), \
                patch.object(scan, 'post_click_normalized') as click, patch.object(scan.time, 'sleep'):
            click.return_value.wait.return_value.succeeded = True
            with self.assertRaisesRegex(RuntimeError, 'was not verified'):
                scan.ensure_triple_checked(None, None)
        self.assertEqual(click.call_count, 1)


if __name__ == '__main__':
    unittest.main()

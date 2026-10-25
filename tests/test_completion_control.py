import unittest

import cv2
import numpy as np

from tools.scan_normal_orders import RESOURCE, completion_checked, completion_control_visible


class CompletionControlTests(unittest.TestCase):
    def frame_with_control(self, name):
        frame = np.zeros((1356, 720, 3), dtype=np.uint8)
        image = cv2.imread(str(RESOURCE / "image/navigation" / name))
        self.assertIsNotNone(image)
        height, width = image.shape[:2]
        frame[1190:1190 + height, 480:480 + width] = image
        return frame

    def test_full_checked_control_is_verified(self):
        frame = self.frame_with_control("normal_order_completion_checked_full_live.png")
        self.assertTrue(completion_control_visible(frame))
        self.assertTrue(completion_checked(frame)[0])

    def test_unchecked_control_is_not_complete(self):
        frame = self.frame_with_control("normal_order_completion_unchecked_live.png")
        self.assertTrue(completion_control_visible(frame))
        self.assertFalse(completion_checked(frame)[0])

    def test_card_green_checks_cannot_authorize_triple_mode(self):
        frame = np.zeros((1356, 720, 3), dtype=np.uint8)
        cv2.circle(frame, (650, 550), 40, (20, 220, 80), -1)
        self.assertFalse(completion_control_visible(frame))
        self.assertFalse(completion_checked(frame)[0])


if __name__ == "__main__":
    unittest.main()

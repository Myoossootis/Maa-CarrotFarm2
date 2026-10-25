import sys
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import complete_normal_orders as normal
import order_board_helpers as board


class RewardDialogTests(unittest.TestCase):
    def test_record_reward_needs_specific_text_and_claim_button(self):
        for body, button, expected in (('订单解锁历史新高16星', '领取', True),
                                       ('其他奖励', '领取', False),
                                       ('订单解锁历史新高16星', '购买', False)):
            with patch.object(board, '_prompt_text', side_effect=[body, button]):
                self.assertEqual(board.order_record_reward_prompt(object(), object()), expected)

    def test_dark_claim_button_behind_modal_is_not_clickable(self):
        template = cv2.imread(str(normal.RESOURCE / 'image/navigation/normal_order_claim_reward.png'))
        self.assertIsNotNone(template)
        frame = np.zeros((1358, 720, 3), dtype=np.uint8)
        height, width = template.shape[:2]
        frame[1090:1090 + height, 220:220 + width] = template
        self.assertIsNotNone(normal._claim_reward_button(frame))
        dark = (frame.astype(np.float32) * .25).astype(np.uint8)
        self.assertIsNone(normal._claim_reward_button(dark))

    def test_record_claim_once_requires_visible_uncovered_reward_button(self):
        with patch.object(normal, 'order_record_reward_prompt', return_value=True), \
             patch.object(normal, 'post_click_normalized', return_value=SimpleNamespace(wait=lambda: SimpleNamespace(succeeded=True))) as click, \
             patch.object(normal, 'screenshot'), patch.object(normal, 'reward_overlay_visible', return_value=False), \
             patch.object(normal, '_claim_reward_button', return_value=(360, 1130)), \
             patch.object(normal.time, 'sleep'):
            self.assertTrue(normal.claim_order_record_reward(object(), object(), object()))
        click.assert_called_once_with(unittest.mock.ANY, 360, 875)

    def test_unconfirmed_record_claim_does_not_reclick(self):
        with patch.object(normal, 'order_record_reward_prompt', return_value=True), \
             patch.object(normal, 'post_click_normalized', return_value=SimpleNamespace(wait=lambda: SimpleNamespace(succeeded=True))) as click, \
             patch.object(normal, 'screenshot'), patch.object(normal, 'reward_overlay_visible', return_value=False), \
             patch.object(normal, '_claim_reward_button', return_value=None), patch.object(normal.time, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, 'unconfirmed'):
                normal.claim_order_record_reward(object(), object(), object(), timeout=0)
        click.assert_called_once()


if __name__ == '__main__':
    unittest.main()

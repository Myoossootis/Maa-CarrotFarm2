from pathlib import Path
import unittest
from unittest.mock import patch

import cv2

from tools import order_board_helpers as board
from tools import enter_farm as farm

ROOT = Path(__file__).resolve().parents[1]


class FarmReturnTests(unittest.TestCase):
    def test_shared_phone_and_backpack_do_not_prove_farm(self):
        for name, expected in (('restaurant', False), ('farm', True)):
            frame = cv2.imread(str(ROOT / f'tests/fixtures/map_return/{name}_navigation.png'))
            self.assertEqual(board.is_on_farm(frame), expected, name)

    def template(self, frame, name):
        if frame == 'orders' and name == 'VerifyNormalOrdersReady':
            return (.99, (0, 0), 0)
        return None

    def test_known_order_board_is_closed_before_map_entry(self):
        with patch.object(board, 'screenshot', side_effect=['orders', 'restaurant']), \
                patch.object(board, 'template_hit', side_effect=self.template), \
                patch.object(board, 'close_order_board', return_value=True) as close, \
                patch.object(board, 'enter_farm', return_value={'status': 'entered'}) as enter, \
                patch.object(board.time, 'sleep'):
            self.assertTrue(board.ensure_returned_to_farm(None, None))
        close.assert_called_once()
        enter.assert_called_once()

    def test_order_board_not_closed_does_not_enter_or_close_again(self):
        for closed in (False, True):
            with patch.object(board, 'screenshot', return_value='orders'), \
                    patch.object(board, 'template_hit', side_effect=self.template), \
                    patch.object(board, 'close_order_board', return_value=closed) as close, \
                    patch.object(board, 'enter_farm') as enter:
                with self.assertRaisesRegex(RuntimeError, 'order board'):
                    board.ensure_returned_to_farm(None, None)
            close.assert_called_once()
            enter.assert_not_called()

    def test_map_confirmation_failure_is_not_success_or_retry(self):
        for page in ('restaurant', 'unknown', 'farm'):
            with patch.object(board, 'screenshot', return_value=page), \
                    patch.object(board, 'template_hit', return_value=None), \
                    patch.object(board, 'close_order_board') as close, \
                    patch.object(board, 'enter_farm', side_effect=RuntimeError('map not confirmed')) as enter, \
                    patch.object(board, 'post_click_normalized') as click:
                with self.assertRaisesRegex(RuntimeError, 'map not confirmed'):
                    board.ensure_returned_to_farm(None, None)
            enter.assert_called_once()
            close.assert_not_called()
            click.assert_not_called()

    def test_unknown_frame_with_generic_close_sends_no_input(self):
        import numpy as np
        frame = np.zeros((1358, 720, 3), dtype=np.uint8)
        template = cv2.imread(str(ROOT / 'assets/resource/image/navigation/popup_close.png'))
        height, width = template.shape[:2]
        frame[1237:1237 + height, 25:25 + width] = template
        with patch.object(board, 'screenshot', return_value=frame), \
                patch.object(farm, 'screenshot', return_value=frame), \
                patch.object(board, 'post_click_normalized') as click, \
                patch.object(farm, 'click_template_node') as map_click:
            with self.assertRaisesRegex(RuntimeError, 'farm entry not recognized'):
                board.ensure_returned_to_farm(None, None)
        click.assert_not_called()
        map_click.assert_not_called()


if __name__ == '__main__':
    unittest.main()

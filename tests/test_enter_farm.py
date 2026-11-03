from pathlib import Path
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from tools import enter_farm as m
from tools.order_template_click import template_hit

ROOT = Path(__file__).resolve().parents[1]


class EnterFarmTests(unittest.TestCase):
    def template(self, frame, name):
        if frame == 'farm' and name == 'VerifyRestaurantEntryVisible':
            return (.99, (58, 1270), 0)
        if frame == 'restaurant' and name in {'VerifyFarmEntryVisible', 'ClickFarmEntry'}:
            return (.99, (58, 1270), 0)
        if frame == 'alert' and name == 'ClickFarmEntryAlert':
            return (.99, (58, 1270), 0)
        if frame == 'ambiguous':
            return (.99, (58, 1270), 0)
        return None

    def test_one_entry_click_then_two_verified_farm_frames(self):
        with patch.object(m, 'screenshot', side_effect=['restaurant', 'loading', 'farm', 'farm']), patch.object(m, 'template_hit', side_effect=self.template), patch.object(m, 'click_template_node', return_value=True) as click, patch.object(m.time, 'sleep'):
            self.assertEqual(m.enter_farm(object()), {'status': 'entered', 'clicks': 1})
            click.assert_called_once()
            self.assertEqual(click.call_args.args[1], 'ClickFarmEntry')

    def test_alert_entry_is_supported(self):
        with patch.object(m, 'screenshot', side_effect=['alert', 'farm', 'farm']), patch.object(m, 'template_hit', side_effect=self.template), patch.object(m, 'click_template_node', return_value=True) as click, patch.object(m.time, 'sleep'):
            self.assertEqual(m.enter_farm(object()), {'status': 'entered', 'clicks': 1})
            self.assertEqual(click.call_args.args[1], 'ClickFarmEntryAlert')

    def test_already_on_farm_does_not_click(self):
        with patch.object(m, 'screenshot', return_value='farm'), patch.object(m, 'template_hit', side_effect=self.template), patch.object(m, 'click_template_node') as click, patch.object(m.time, 'sleep'):
            self.assertEqual(m.enter_farm(object()), {'status': 'already_on_farm', 'clicks': 0})
            click.assert_not_called()

    def test_already_on_farm_tolerates_brief_hud_flicker(self):
        with patch.object(m, 'screenshot', side_effect=['farm', 'loading', 'farm', 'farm']), patch.object(m, 'template_hit', side_effect=self.template), patch.object(m, 'click_template_node') as click, patch.object(m.time, 'sleep'):
            self.assertEqual(m.enter_farm(object()), {'status': 'already_on_farm', 'clicks': 0})
            click.assert_not_called()

    def test_unknown_page_does_not_guess_a_click(self):
        with patch.object(m, 'screenshot', return_value='unknown'), patch.object(m, 'template_hit', side_effect=self.template), patch.object(m, 'click_template_node') as click:
            with self.assertRaisesRegex(RuntimeError, 'entry not recognized'):
                m.enter_farm(object())
            click.assert_not_called()

    def test_no_arrival_is_failure_without_reclicking(self):
        with patch.object(m, 'screenshot', return_value='restaurant'), patch.object(m, 'template_hit', side_effect=self.template), patch.object(m, 'click_template_node', return_value=True) as click, patch.object(m.time, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, 'not confirmed'):
                m.enter_farm(object())
            click.assert_called_once()

    def test_conflicting_entry_icons_do_not_count_as_success(self):
        with patch.object(m, 'template_hit', side_effect=self.template):
            self.assertFalse(m.farm_visible('ambiguous'))

    def test_client_98_map_icon_proves_farm_and_not_an_order_board(self):
        icon = cv2.imread(str(ROOT / 'assets/resource/image/navigation/restaurant_entry_map_98.png'))
        frame = np.zeros((1353, 720, 3), np.uint8)
        frame[1125:1125 + icon.shape[0], 25:25 + icon.shape[1]] = icon
        self.assertTrue(m.farm_visible(frame))
        board = cv2.imread(str(ROOT / 'tests/fixtures/order_board/empty_party.png'))
        self.assertIsNone(template_hit(board, 'VerifyRestaurantEntryVisible'))


if __name__ == '__main__':
    unittest.main()

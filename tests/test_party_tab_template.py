from pathlib import Path
import unittest
from unittest.mock import patch

import cv2

from tools import order_template_click as tabs

ROOT = Path(__file__).resolve().parents[1]


class PartyTabTests(unittest.TestCase):
    def test_gray_party_tab_matches_at_unchanged_threshold(self):
        frame = cv2.imread(str(ROOT / 'tests/fixtures/order_board/takeout_header.png'))
        hit = tabs.template_hit(frame, 'ClickPartyOrderTab')
        self.assertIsNotNone(hit)
        self.assertGreaterEqual(hit[0], .78)
        self.assertTrue(95 <= hit[1][0] <= 240 and 239 <= hit[1][1] <= 288)

    def test_active_party_does_not_match_unselected_tab(self):
        frame = cv2.imread(str(ROOT / 'tests/fixtures/order_board/empty_party.png'))
        self.assertIsNone(tabs.template_hit(frame, 'ClickPartyOrderTab'))

    def test_takeout_to_party_navigation_clicks_only_the_identified_tab(self):
        takeout = cv2.imread(str(ROOT / 'tests/fixtures/order_board/takeout_header.png'))
        party = cv2.imread(str(ROOT / 'tests/fixtures/order_board/empty_party.png'))
        with patch.object(tabs, 'screenshot', side_effect=[takeout, party]), \
                patch.object(tabs, 'post_click_normalized') as click, patch.object(tabs.time, 'sleep'):
            self.assertTrue(tabs.open_orders(object(), 'party'))
        self.assertEqual(click.call_count, 1)
        x, y = click.call_args.args[1:]
        self.assertTrue(95 <= x <= 240 and 239 <= y <= 288)


if __name__ == '__main__':
    unittest.main()

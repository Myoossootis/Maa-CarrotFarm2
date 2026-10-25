import unittest
from unittest.mock import patch

from tools import enter_restaurant as m


class EnterRestaurantTests(unittest.TestCase):
    def template(self, frame, name):
        if frame == 'restaurant' and name == 'VerifyFarmEntryVisible':
            return (.99, (58, 1270), 0)
        if frame == 'farm' and name in {'VerifyRestaurantEntryVisible', 'ClickRestaurantEntry'}:
            return (.99, (58, 1270), 0)
        if frame == 'ambiguous':
            return (.99, (58, 1270), 0)
        return None

    def test_one_entry_click_then_two_verified_restaurant_frames(self):
        with patch.object(m, 'screenshot', side_effect=['farm', 'loading', 'restaurant', 'restaurant']), patch.object(m, 'template_hit', side_effect=self.template), patch.object(m, 'click_template_node', return_value=True) as click, patch.object(m.time, 'sleep'):
            self.assertEqual(m.enter_restaurant(object()), {'status': 'entered', 'clicks': 1})
            click.assert_called_once()
            self.assertEqual(click.call_args.args[1], 'ClickRestaurantEntry')

    def test_already_in_restaurant_does_not_click_return_to_farm(self):
        with patch.object(m, 'screenshot', return_value='restaurant'), patch.object(m, 'template_hit', side_effect=self.template), patch.object(m, 'click_template_node') as click, patch.object(m.time, 'sleep'):
            self.assertEqual(m.enter_restaurant(object()), {'status': 'already_in_restaurant', 'clicks': 0})
            click.assert_not_called()

    def test_unknown_page_does_not_guess_a_click(self):
        with patch.object(m, 'screenshot', return_value='unknown'), patch.object(m, 'template_hit', side_effect=self.template), patch.object(m, 'click_template_node') as click:
            with self.assertRaisesRegex(RuntimeError, 'entry not recognized'):
                m.enter_restaurant(object())
            click.assert_not_called()

    def test_no_arrival_is_failure_without_reclicking(self):
        with patch.object(m, 'screenshot', return_value='farm'), patch.object(m, 'template_hit', side_effect=self.template), patch.object(m, 'click_template_node', return_value=True) as click, patch.object(m.time, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, 'not confirmed'):
                m.enter_restaurant(object())
            click.assert_called_once()

    def test_conflicting_entry_icons_do_not_count_as_success(self):
        with patch.object(m, 'template_hit', side_effect=self.template):
            self.assertFalse(m.restaurant_visible('ambiguous'))


if __name__ == '__main__':
    unittest.main()

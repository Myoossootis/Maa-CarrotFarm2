import unittest
from unittest.mock import patch

from tools import enter_farm as m


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


if __name__ == '__main__':
    unittest.main()

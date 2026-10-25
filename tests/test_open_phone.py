import unittest
from unittest.mock import patch

from tools import open_phone as m


class OpenPhoneTests(unittest.TestCase):
    def template(self, frame, name):
        if frame == 'menu' and name == 'VerifyPhoneMenuOpen':
            return (.99, (360, 520), 0)
        if frame == 'farm' and name == 'ClickPhoneIcon':
            return (.99, (663, 1285), 0)
        return None

    def test_one_click_then_two_verified_menu_frames(self):
        with patch.object(m, 'screenshot', side_effect=['farm', 'loading', 'menu', 'menu']), patch.object(m, 'template_hit', side_effect=self.template), patch.object(m, 'click_template_node', return_value=True) as click, patch.object(m.time, 'sleep'):
            self.assertEqual(m.open_phone(object()), {'status': 'opened', 'clicks': 1})
            click.assert_called_once()
            self.assertEqual(click.call_args.args[1], 'ClickPhoneIcon')

    def test_already_open_does_not_click(self):
        with patch.object(m, 'screenshot', return_value='menu'), patch.object(m, 'template_hit', side_effect=self.template), patch.object(m, 'click_template_node') as click, patch.object(m.time, 'sleep'):
            self.assertEqual(m.open_phone(object()), {'status': 'already_open', 'clicks': 0})
            click.assert_not_called()

    def test_unknown_page_does_not_guess_a_click(self):
        with patch.object(m, 'screenshot', return_value='unknown'), patch.object(m, 'template_hit', side_effect=self.template), patch.object(m, 'click_template_node') as click:
            with self.assertRaisesRegex(RuntimeError, 'icon not recognized'):
                m.open_phone(object())
            click.assert_not_called()

    def test_no_menu_is_failure_without_reclicking(self):
        with patch.object(m, 'screenshot', return_value='farm'), patch.object(m, 'template_hit', side_effect=self.template), patch.object(m, 'click_template_node', return_value=True) as click, patch.object(m.time, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, 'not confirmed'):
                m.open_phone(object())
            click.assert_called_once()


if __name__ == '__main__':
    unittest.main()

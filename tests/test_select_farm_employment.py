import unittest
from unittest.mock import patch

from tools import select_farm_employment as m


class SelectFarmEmploymentTests(unittest.TestCase):
    def template(self, frame, name):
        if frame == 'orange' and name == 'VerifyFarmEmploymentSelected':
            return (.99, (550, 1260), 0)
        if frame == 'gray' and name == 'ClickFarmEmploymentTab':
            return (.99, (550, 1260), 0)
        if frame == 'conflict':
            return (.99, (550, 1260), 0)
        return None

    def test_gray_hat_clicks_once_then_confirms_orange_twice(self):
        with patch.object(m, 'screenshot', side_effect=['gray', 'loading', 'orange', 'orange']), patch.object(m, 'template_hit', side_effect=self.template), patch.object(m, 'click_template_node', return_value=True) as click, patch.object(m.time, 'sleep'):
            self.assertEqual(m.select_farm_employment(object()), {'status': 'selected', 'clicks': 1})
            click.assert_called_once()
            self.assertEqual(click.call_args.args[1], 'ClickFarmEmploymentTab')

    def test_orange_hat_ends_without_click(self):
        with patch.object(m, 'screenshot', return_value='orange'), patch.object(m, 'template_hit', side_effect=self.template), patch.object(m, 'click_template_node') as click, patch.object(m.time, 'sleep'):
            self.assertEqual(m.select_farm_employment(object()), {'status': 'already_selected', 'clicks': 0})
            click.assert_not_called()

    def test_unknown_page_does_not_click(self):
        with patch.object(m, 'screenshot', return_value='unknown'), patch.object(m, 'template_hit', side_effect=self.template), patch.object(m, 'click_template_node') as click:
            with self.assertRaisesRegex(RuntimeError, 'state is unknown'):
                m.select_farm_employment(object())
            click.assert_not_called()

    def test_conflicting_hats_do_not_click(self):
        with patch.object(m, 'screenshot', return_value='conflict'), patch.object(m, 'template_hit', side_effect=self.template), patch.object(m, 'click_template_node') as click:
            with self.assertRaisesRegex(RuntimeError, 'state is unknown'):
                m.select_farm_employment(object())
            click.assert_not_called()

    def test_orange_never_appears_does_not_reclick(self):
        with patch.object(m, 'screenshot', return_value='gray'), patch.object(m, 'template_hit', side_effect=self.template), patch.object(m, 'click_template_node', return_value=True) as click, patch.object(m.time, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, 'orange.*not confirmed'):
                m.select_farm_employment(object())
            click.assert_called_once()


if __name__ == '__main__':
    unittest.main()

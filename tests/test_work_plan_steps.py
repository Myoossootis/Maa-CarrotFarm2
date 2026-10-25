import unittest
from unittest.mock import patch

from tools import close_work_plan as close
from tools import configure_field_employee as config
from tools import open_work_plan as open_plan


class WorkPlanStepTests(unittest.TestCase):
    def test_dialog_close_uses_the_common_blank_footer(self):
        with patch.object(config, 'screenshot', side_effect=['plan', 'list', 'list']), \
                patch.object(config, 'match', return_value=.9), \
                patch.object(config, 'employee_page_state', return_value='employee_list'), \
                patch.object(config, 'click') as click, patch.object(config.time, 'sleep'):
            config.close_plan_dialog(object())
        click.assert_called_once_with(unittest.mock.ANY, 360, 1220)

    def test_open_from_list_clicks_blue_button_once(self):
        with patch.object(open_plan, 'screenshot', side_effect=['list', 'plan', 'plan']), patch.object(open_plan, 'employee_page_state', side_effect=lambda frame: {'list': 'employee_list', 'plan': 'work_plan'}[frame]), patch.object(open_plan, 'visible_slot_action', return_value=(405, 'work_plan')), patch.object(open_plan, 'click') as click, patch.object(open_plan.time, 'sleep'):
            self.assertEqual(open_plan.open_work_plan(object()), {'status': 'opened', 'clicks': 1, 'slot': 'field_1'})
            click.assert_called_once_with(unittest.mock.ANY, 230, 405)

    def test_open_rejects_unknown_page(self):
        with patch.object(open_plan, 'screenshot', return_value='unknown'), patch.object(open_plan, 'employee_page_state', return_value='unknown'), patch.object(open_plan, 'click') as click:
            with self.assertRaisesRegex(RuntimeError, 'list not recognized'):
                open_plan.open_work_plan(object())
            click.assert_not_called()

    def test_close_to_detail_clicks_once(self):
        with patch.object(close, 'screenshot', return_value='plan'), patch.object(close, 'employee_page_state', return_value='work_plan'), patch.object(close, 'close_plan_dialog') as close_dialog:
            self.assertEqual(close.close_work_plan(object()), {'status': 'closed', 'clicks': 1})
            close_dialog.assert_called_once()

    def test_close_rejects_unknown_page(self):
        with patch.object(close, 'screenshot', return_value='unknown'), patch.object(close, 'employee_page_state', return_value='unknown'), patch.object(close, 'close_plan_dialog') as close_dialog:
            with self.assertRaisesRegex(RuntimeError, 'dialog not recognized'):
                close.close_work_plan(object())
            close_dialog.assert_not_called()


if __name__ == '__main__':
    unittest.main()

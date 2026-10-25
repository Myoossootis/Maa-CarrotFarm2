import unittest
from unittest.mock import patch

from tools import open_field_detail as detail
from tools import return_employee_list as back


class EmployeeListStepTests(unittest.TestCase):
    def test_open_field_detail_clicks_once(self):
        with patch.object(detail, 'screenshot', side_effect=['list', 'detail', 'detail']), patch.object(detail, 'employee_page_state', side_effect=lambda frame: {'list': 'employee_list', 'detail': 'slot_detail'}[frame]), patch.object(detail, 'slot_match', return_value=(.99, (0, 0))), patch.object(detail, 'visible_slot_action', return_value=(405, 'work_plan')), patch.object(detail, 'click') as click, patch.object(detail.time, 'sleep'):
            self.assertEqual(detail.open_field_1_detail(object()), {'status': 'opened', 'clicks': 1})
            click.assert_called_once_with(unittest.mock.ANY, 560, 405)

    def test_open_field_detail_rejects_wrong_page(self):
        with patch.object(detail, 'screenshot', return_value='unknown'), patch.object(detail, 'employee_page_state', return_value='unknown'), patch.object(detail, 'slot_match', return_value=(0, (0, 0))), patch.object(detail, 'click') as click:
            with self.assertRaisesRegex(RuntimeError, 'list not recognized'):
                detail.open_field_1_detail(object())
            click.assert_not_called()

    def test_return_from_detail_delegates_once(self):
        with patch.object(back, 'screenshot', return_value='detail'), patch.object(back, 'employee_page_state', return_value='slot_detail'), patch.object(back, 'return_to_employee_list', return_value=('list', 2)) as returned:
            self.assertEqual(back.return_employee_list(object()), {'status': 'returned', 'clicks': 2})
            returned.assert_called_once()

    def test_already_on_list_is_zero_click(self):
        with patch.object(back, 'screenshot', return_value='list'), patch.object(back, 'employee_page_state', return_value='employee_list'), patch.object(back, 'return_to_employee_list') as returned, patch.object(back.time, 'sleep'):
            self.assertEqual(back.return_employee_list(object()), {'status': 'already_on_list', 'clicks': 0})
            returned.assert_not_called()


if __name__ == '__main__':
    unittest.main()

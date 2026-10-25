import unittest
from unittest.mock import patch

from tools import verify_slot_work_plan as m


class VerifySlotWorkPlanTests(unittest.TestCase):
    def test_opens_target_and_closes_back_to_list(self):
        with patch.object(m, 'screenshot', side_effect=['list', 'list']), \
                patch.object(m, 'employee_page_state', return_value='employee_list'), \
                patch.object(m, 'open_plan', return_value=('plan', 700)) as open_plan, \
                patch.object(m, 'close_plan_dialog') as close:
            result = m.verify_slot_work_plan(object(), 'orchard_2')
        self.assertEqual(result['status'], 'verified')
        self.assertEqual(result['slot'], 'orchard_2')
        self.assertTrue(result['opened'])
        self.assertTrue(result['closed'])
        open_plan.assert_called_once_with(unittest.mock.ANY, 'orchard_2')
        close.assert_called_once()

    def test_existing_plan_dialog_is_not_closed_for_unknown_target(self):
        with patch.object(m, 'screenshot', return_value='plan'), \
                patch.object(m, 'employee_page_state', return_value='work_plan'), \
                patch.object(m, 'open_plan') as open_plan, \
                patch.object(m, 'close_plan_dialog') as close:
            with self.assertRaisesRegex(RuntimeError, 'target slot is unknown'):
                m.verify_slot_work_plan(object(), 'field_2')
        open_plan.assert_not_called()
        close.assert_not_called()


if __name__ == '__main__':
    unittest.main()

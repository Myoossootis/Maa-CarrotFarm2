import unittest
from pathlib import Path
from unittest.mock import patch

import cv2

from tools import configure_field_employee as m


FIXTURES = Path(__file__).parent / 'fixtures/employee_pages'


class EmployeePageStateTests(unittest.TestCase):
    def test_unrecognized_page_never_uses_color_or_blank_recovery_click(self):
        with patch.object(m, 'screenshot', return_value=object()), patch.object(m, 'match', return_value=0), \
             patch.object(m, 'match_nav', return_value=0), patch.object(m, 'locate_nav', return_value=(0, (0, 0))), \
             patch.object(m, 'slot_match', return_value=(0, (0, 0))), \
             patch.object(m, '_schedule_plan_confirm_visible', return_value=True), \
             patch.object(m, '_field_work_plan_button_visible', return_value=True), \
             patch.object(m.time, 'sleep'), patch.object(m.cv2, 'imwrite'), \
             patch.object(m, '_click_close_nav') as click, patch('sys.stdout', new=__import__('io').StringIO()):
            with self.assertRaisesRegex(RuntimeError, 'farm map was not verified'):
                m.close_employee_menu(object())
            click.assert_not_called()

    def frame(self, name):
        frame = cv2.imread(str(FIXTURES / name))
        self.assertIsNotNone(frame)
        return frame

    def test_real_employee_list(self):
        frame = self.frame('employee_list.png')
        self.assertEqual(m.employee_page_state(frame), 'employee_list')
        self.assertTrue(m._farm_worker_list_visible(frame))

    def test_real_slot_detail_is_not_list(self):
        frame = self.frame('field_detail.png')
        self.assertEqual(m.employee_page_state(frame), 'slot_detail')
        self.assertFalse(m._farm_worker_list_visible(frame))

    def test_real_work_plan_is_not_list(self):
        frame = self.frame('work_plan.png')
        self.assertEqual(m.employee_page_state(frame), 'work_plan')
        self.assertFalse(m._farm_worker_list_visible(frame))

    def test_real_empty_plan_prompt_is_not_list(self):
        frame = self.frame('empty_plan_prompt.png')
        self.assertEqual(m.employee_page_state(frame), 'empty_plan_prompt')
        self.assertFalse(m._farm_worker_list_visible(frame))

    def test_unrelated_overlay_is_unknown(self):
        frame = self.frame('offline_reward.png')
        self.assertEqual(m.employee_page_state(frame), 'unknown')
        self.assertFalse(m._farm_worker_list_visible(frame))

    def test_detail_returns_with_exactly_one_back_click(self):
        detail = self.frame('field_detail.png')
        listing = self.frame('employee_list.png')
        with patch.object(m, 'screenshot', side_effect=[detail, listing, listing]), patch.object(m, 'click') as click, patch.object(m.time, 'sleep'):
            returned, clicks = m.return_to_employee_list(object())
            self.assertIs(returned, listing)
            self.assertEqual(clicks, 1)
            click.assert_called_once_with(unittest.mock.ANY, 57, 1280)

    def test_detail_prompt_cancel_then_list_uses_two_clicks(self):
        detail = self.frame('field_detail.png')
        prompt = self.frame('empty_plan_prompt.png')
        listing = self.frame('employee_list.png')
        with patch.object(m, 'screenshot', side_effect=[detail, prompt, listing, listing]), patch.object(m, 'click') as click, patch.object(m.time, 'sleep'):
            returned, clicks = m.return_to_employee_list(object())
            self.assertIs(returned, listing)
            self.assertEqual(clicks, 2)
            self.assertEqual(click.call_args_list, [
                unittest.mock.call(unittest.mock.ANY, 57, 1280),
                unittest.mock.call(unittest.mock.ANY, 230, 686),
            ])

    def test_unknown_state_never_clicks_back(self):
        frame = self.frame('offline_reward.png')
        with patch.object(m, 'screenshot', return_value=frame), patch.object(m, 'click') as click:
            with self.assertRaisesRegex(RuntimeError, 'from unknown; no click'):
                m.return_to_employee_list(object())
            click.assert_not_called()

    def test_open_employee_list_returns_from_detail_without_opening_phone(self):
        detail = self.frame('field_detail.png')
        listing = self.frame('employee_list.png')
        with patch.object(m, 'screenshot', side_effect=[detail, detail, listing, listing]), patch.object(m, 'click'), patch.object(m, 'open_phone') as phone, patch.object(m.time, 'sleep'):
            self.assertIs(m.open_employee_list(object()), listing)
            phone.assert_not_called()

    def test_open_plan_uses_blue_list_button_not_green_assignment(self):
        plan = self.frame('work_plan.png')
        with patch.object(m, 'find_slot_action', return_value=(405, 'work_plan')), patch.object(m, 'screenshot', return_value=plan), patch.object(m, 'click') as click, patch.object(m.time, 'sleep'):
            returned, button_y = m.open_plan(object(), 'field_1')
            self.assertIs(returned, plan)
            self.assertEqual(button_y, 405)
            click.assert_called_once_with(unittest.mock.ANY, 230, 405)

    def test_all_scrolled_slot_captures_locate_the_expected_blue_action_row(self):
        cases = {
            'employee_list.png': {'field_1': 405, 'field_2': 794},
            'ranch_list.png': {'field_3': 521, 'ranch_1': 909},
            'ranch_lower.png': {'ranch_2': 609, 'orchard_1': 1002},
            'orchard_list.png': {'orchard_2': 700},
        }
        for fixture_name, expected in cases.items():
            frame = self.frame(fixture_name)
            self.assertIsNotNone(frame)
            for slot_id, button_y in expected.items():
                with self.subTest(capture=fixture_name, slot=slot_id):
                    self.assertEqual(m.visible_slot_action(frame, slot_id),
                                     (button_y, 'work_plan'))


if __name__ == '__main__':
    unittest.main()

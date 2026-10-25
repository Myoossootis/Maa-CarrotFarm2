import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tools import dismiss_offline_income as m
from agent import production_actions as actions


def text(value, box=(260, 1210, 200, 40)):
    return SimpleNamespace(text=value, box=box)


class OfflineIncomeTests(unittest.TestCase):
    def test_ui_action_runs_only_the_close_worker_without_production_confirmation(self):
        context = SimpleNamespace(tasker=SimpleNamespace(stopping=False))
        with patch.object(actions, 'wait_for_background_ready', return_value=True), patch.object(actions, 'run_cycle_worker', return_value=True) as worker, patch.object(actions, 'confirm_cycle_start') as confirm:
            self.assertTrue(actions.CloseOfflineIncome().run(context, None))
            self.assertEqual(worker.call_args.args, ([],))
            self.assertEqual(worker.call_args.kwargs['script'], 'dismiss_offline_income.py')
            confirm.assert_not_called()

    def test_stopping_prevents_ui_close_worker(self):
        context = SimpleNamespace(tasker=SimpleNamespace(stopping=True))
        with patch.object(actions, 'run_cycle_worker') as worker:
            self.assertFalse(actions.CloseOfflineIncome().run(context, None))
            worker.assert_not_called()

    def test_only_footer_without_income_title_never_clicks(self):
        with patch.object(m, 'recognize_text', side_effect=[[], [text('点击空白区域继续')]]), patch.object(m, 'post_click_normalized') as click:
            self.assertIsNone(m.income_footer(object(), object()))
            click.assert_not_called()

    def test_title_without_footer_is_unknown_not_permission_to_click(self):
        with patch.object(m, 'recognize_text', side_effect=[[text('餐厅离线收益')], []]):
            with self.assertRaisesRegex(RuntimeError, 'footer'):
                m.income_footer(object(), object())

    def test_recognized_footer_box_is_used_instead_of_fixed_blank_point(self):
        with patch.object(m, 'recognize_text', side_effect=[[text('餐厅离线收益')], [text('点击空白区域继续')]]):
            self.assertEqual(m.income_footer(object(), object()), (360, 1230))

    def test_receive_button_cannot_be_used_as_blank_footer(self):
        with patch.object(m, 'recognize_text', side_effect=[[text('餐厅离线收益')], [text('领取', (200, 980, 300, 100))]]):
            with self.assertRaisesRegex(RuntimeError, 'footer'):
                m.income_footer(object(), object())

    def test_close_is_one_click_followed_by_two_absent_frames(self):
        with patch.object(m, 'screenshot', return_value=object()), patch.object(m, 'income_footer', return_value=(360, 1230)), patch.object(m, 'income_present', side_effect=[False, False]), patch.object(m, 'post_click_normalized') as click, patch.object(m.time, 'sleep'):
            click.return_value.wait.return_value.succeeded = True
            self.assertTrue(m.dismiss_offline_income(object(), object()))
            click.assert_called_once()
            self.assertEqual(click.call_args.args[1:], (360, 1230))

    def test_no_panel_sends_no_input(self):
        with patch.object(m, 'screenshot', return_value=object()), patch.object(m, 'income_footer', return_value=None), patch.object(m, 'post_click_normalized') as click:
            self.assertFalse(m.dismiss_offline_income(object(), object()))
            click.assert_not_called()

    def test_still_visible_fails_without_retrying_click(self):
        with patch.object(m, 'screenshot', return_value=object()), patch.object(m, 'income_footer', return_value=(360, 1230)), patch.object(m, 'income_present', return_value=True), patch.object(m, 'post_click_normalized') as click, patch.object(m.time, 'sleep'):
            click.return_value.wait.return_value.succeeded = True
            with self.assertRaisesRegex(RuntimeError, 'not confirmed'):
                m.dismiss_offline_income(object(), object())
            click.assert_called_once()


if __name__ == '__main__':
    unittest.main()

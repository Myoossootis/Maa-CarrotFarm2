import unittest
from unittest.mock import patch
from contextlib import ExitStack

from tools import monitor_employee_products as m
from tools import configure_field_employee as config


class MonitorSafetyTests(unittest.TestCase):
    def test_watch_line_includes_order_gap_and_unjudged_time(self):
        self.assertEqual(
            m.format_order_need({'target_order': 1, 'target_items': [
                {'name': '玫瑰', 'required': 24, 'actual': 5, 'missing': 19},
            ]}),
            '[订单] 订单1：玫瑰 需24 有5 缺19',
        )
        line = m.format_watch_line('field_1', {'玫瑰': 19}, (0, 19), {'state': 'not_started'})
        self.assertEqual(line, '[进度] 农田1 玫瑰×19 0/19 还差19；剩余时间未判定')

    def test_timed_cycle_reports_current_round_only(self):
        self.assertEqual(
            m.format_cycle_time({'state': 'timed', 'seconds': 95, 'growing': 3}),
            '这轮剩余约2分钟',
        )
        data = {'production': {'buildings': [{
            'buildingUid': 7,
            'slots': [
                {'status': 'empty', 'timingReliable': True, 'remainingSeconds': 0},
                {'status': 'growing', 'timingReliable': False, 'remainingSeconds': None},
            ],
        }]}}
        with patch.object(m.decoded, 'plan', return_value={'buildingUid': 7}):
            self.assertEqual(m.current_cycle_remaining('field_1', data)['state'], 'unknown')

    def run_monitor(self, result=None, error=None):
        with ExitStack() as stack:
            for name in [
                "Win32Controller",
                "Resource",
                "Tasker",
                "find_window",
                "_close_employee_menu_with_retry",
                "write_json",
                "RESULT",
            ]:
                stack.enter_context(patch.object(m, name))
            collect = stack.enter_context(
                patch.object(m, "collect_finished_slot", return_value=result, side_effect=error)
            )
            try:
                return m.monitor({"field_1": 27}, 5, 30, {"field_1": {"灏忛害": 27}})
            finally:
                self.collect_calls = collect.call_count

    def test_click_failure_stops_without_retry(self):
        with self.assertRaises(m.CollectionUnconfirmedError):
            self.run_monitor(error=m.CollectionUnconfirmedError("click failed"))
        self.assertEqual(self.collect_calls, 1)

    def test_collected_completes_without_clearance_gate(self):
        with patch.object(config, "open_plan") as open_plan, patch.object(config, "reset_plan") as reset:
            result = self.run_monitor({"collected": True, "progress": [28, 27], "plan_cleared": True})
            self.assertTrue(result["complete"])
            self.assertEqual(result["cleared_slots"], ["field_1"])
            self.assertEqual(result["reset_slots"], [])
            open_plan.assert_not_called()
            reset.assert_not_called()

    def test_collected_without_plan_cleared_flag_still_completes(self):
        """Monitor treats collected as done; no plan_cleared gate."""
        with patch.object(config, "reset_plan") as reset:
            result = self.run_monitor({"collected": True, "progress": [28, 27]})
            self.assertTrue(result["complete"])
            self.assertEqual(result["cleared_slots"], ["field_1"])
            reset.assert_not_called()

    def test_wait_always_returns_to_actual_farm(self):
        with patch.object(m, 'close_employee_menu') as close, patch.object(m, 'enter_farm') as farm:
            m._close_employee_menu_with_retry(object())
        close.assert_called_once()
        farm.assert_called_once()


class RepeatCheckboxTests(unittest.TestCase):
    def test_checked_is_toggled_once_and_trusted(self):
        with patch.object(config, "repeat_checkbox_state", return_value=True), patch.object(
            config, "click"
        ) as click, patch.object(config, "screenshot", return_value="new"), patch.object(config.time, "sleep"):
            self.assertEqual(
                config.ensure_repeat_checkbox(object(), "old", repeat=False, tasker=object()),
                "new",
            )
            click.assert_called_once()

    def test_unchecked_is_not_toggled(self):
        with patch.object(config, "repeat_checkbox_state", return_value=False), patch.object(
            config, "click"
        ) as click:
            config.ensure_repeat_checkbox(object(), "old", repeat=False, tasker=object())
            click.assert_not_called()

    def test_unreadable_control_refuses_before_click(self):
        with patch.object(config, "repeat_checkbox_state", return_value=None), patch.object(
            config, "click"
        ) as click:
            with self.assertRaisesRegex(RuntimeError, "not verified"):
                config.ensure_repeat_checkbox(object(), "old", repeat=False, tasker=object())
            click.assert_not_called()

    def test_saved_plan_rearrange_skips_checkbox(self):
        with patch.object(config, "open_plan", return_value=(object(), 600)), patch.object(
            config, "capacity", return_value=(5, 50)
        ), patch.object(config, "icon_quantity", return_value=5), patch.object(
            config, "saved_plan_rearrange_view", return_value=True
        ), patch.object(config, "repeat_checkbox_state") as state, patch.object(
            config, "close_plan_dialog"
        ), patch.object(config, "click") as click:
            config.verify_saved_plan(object(), object(), "field_1", {"灏忛害": 5})
            state.assert_not_called()
            click.assert_not_called()


if __name__ == "__main__":
    unittest.main()


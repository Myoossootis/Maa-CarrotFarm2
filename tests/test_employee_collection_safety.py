import unittest
import io
import sys
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

from tools import configure_field_employee as m


class CollectionSafetyTests(unittest.TestCase):
    def collect(self, progress, after="work_plan"):
        with ExitStack() as stack:
            stack.enter_context(patch.object(m, "find_slot_action", return_value=(600, "collect_supplies")))
            stack.enter_context(patch.object(m, "screenshot", return_value=object()))
            stack.enter_context(patch.object(m, "list_progress", side_effect=list(progress) if not callable(progress) else progress))
            stack.enter_context(
                patch.object(
                    m,
                    "visible_slot_action",
                    side_effect=lambda *_: (600, "work_plan" if click.called else "collect_supplies"),
                )
            )
            stack.enter_context(patch.object(m.time, "sleep"))
            click = stack.enter_context(patch.object(m, "click"))
            result = m.collect_finished_slot(object(), "field_1", MagicMock(), 27)
            self.last_click_count = click.call_count
            return result, click.call_count, None

    def test_incomplete_does_not_collect(self):
        result, clicks, _ = self.collect([(4, 27)] * 5)
        self.assertFalse(result["collected"])
        self.assertEqual(clicks, 0)
        self.assertEqual(result["progress"], (4, 27))

    def test_unreadable_does_not_collect(self):
        result, clicks, _ = self.collect([None] * 5)
        self.assertFalse(result["collected"])
        self.assertIsNone(result["progress"])
        self.assertEqual(clicks, 0)

    def test_wrong_denominator_does_not_collect(self):
        for value in [(50, 50), (30, 30), (27, 9)]:
            with self.subTest(value=value):
                result, clicks, _ = self.collect([value] * 5)
                self.assertFalse(result["collected"])
                self.assertEqual(clicks, 0)

    def test_ready_collect_once_and_trust_click(self):
        for value in [(27, 27), (28, 27)]:
            with self.subTest(value=value):
                result, clicks, _ = self.collect([value])
                self.assertTrue(result["collected"])
                self.assertTrue(result["plan_cleared"])
                self.assertEqual(result["clearance"]["reason"], "trust_collect_click")
                self.assertEqual(result["progress"], list(value))
                self.assertEqual(clicks, 1)

    def test_one_ready_reading_is_enough(self):
        """No consecutive multi-frame gate before collect."""
        result, clicks, _ = self.collect([(27, 27)])
        self.assertTrue(result["collected"])
        self.assertEqual(clicks, 1)

    def test_click_failure_does_not_reclick(self):
        with ExitStack() as stack:
            stack.enter_context(patch.object(m, "find_slot_action", return_value=(600, "collect_supplies")))
            stack.enter_context(patch.object(m, "screenshot", return_value=object()))
            stack.enter_context(patch.object(m, "visible_slot_action", return_value=(600, "collect_supplies")))
            stack.enter_context(patch.object(m, "list_progress", return_value=(27, 27)))
            stack.enter_context(patch.object(m.time, "sleep"))
            click = stack.enter_context(patch.object(m, "click", side_effect=RuntimeError("fg blocked")))
            with self.assertRaises(m.CollectionUnconfirmedError):
                m.collect_finished_slot(object(), "field_1", MagicMock(), 27)
            self.assertEqual(click.call_count, 1)

    def test_missing_target_or_tasker_fails_before_navigation(self):
        for tasker, target in [(None, 27), (object(), None), (object(), 0)]:
            with patch.object(m, "find_slot_action") as find, patch.object(m, "click") as click:
                with self.assertRaises(ValueError):
                    m.collect_finished_slot(object(), "field_1", tasker, target)
                find.assert_not_called()
                click.assert_not_called()

    def test_opening_a_busy_plan_never_collects(self):
        with patch.object(m, "find_slot_action", return_value=(600, "collect_supplies")), patch.object(
            m, "collect_finished_slot"
        ) as collect, patch.object(m, "click") as click:
            with self.assertRaisesRegex(RuntimeError, "uncollected supplies"):
                m.open_plan(object(), "field_1")
            collect.assert_not_called()
            click.assert_not_called()

    def test_cli_requires_target_before_connecting(self):
        with patch.object(sys, "argv", ["employee", "--collect", "--apply"]), patch.object(
            sys, "stderr", io.StringIO()
        ), patch.object(m, "Win32Controller") as controller:
            with self.assertRaises(SystemExit) as error:
                m.main()
            self.assertEqual(error.exception.code, 2)
            controller.assert_not_called()

    def test_cli_success_when_collected(self):
        with ExitStack() as stack:
            stack.enter_context(patch.object(sys, "argv", ["employee", "--collect", "--target", "27", "--apply"]))
            stack.enter_context(patch.object(sys, "stdout", io.StringIO()))
            for name in ["Win32Controller", "Resource", "Tasker", "find_window"]:
                stack.enter_context(patch.object(m, name))
            stack.enter_context(
                patch.object(
                    m,
                    "collect_finished_slot",
                    return_value={"collected": True, "plan_cleared": True},
                )
            )
            self.assertEqual(m.main(), 0)

    def test_cli_failure_when_not_collected(self):
        with ExitStack() as stack:
            stack.enter_context(patch.object(sys, "argv", ["employee", "--collect", "--target", "27", "--apply"]))
            stack.enter_context(patch.object(sys, "stdout", io.StringIO()))
            for name in ["Win32Controller", "Resource", "Tasker", "find_window"]:
                stack.enter_context(patch.object(m, name))
            stack.enter_context(patch.object(m, "collect_finished_slot", return_value={"collected": False}))
            self.assertEqual(m.main(), 2)

class ClosePlanTrustTests(unittest.TestCase):
    def test_close_plan_dialog_clicks_once_without_list_confirm(self):
        with patch.object(m, "screenshot", return_value=object()), patch.object(
            m, "match", return_value=0.9
        ), patch.object(m, "click") as click, patch.object(m.time, "sleep") as sleep, patch.object(
            m, "employee_page_state"
        ) as state:
            m.close_plan_dialog(object())
            click.assert_called_once()
            sleep.assert_called()
            state.assert_not_called()


if __name__ == "__main__":
    unittest.main()

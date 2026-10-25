import io
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agent import production_start as m
from agent.production_actions import ConfirmProductionCycleStart
from tools import run_short_cycle


class CycleStartTests(unittest.TestCase):
    def test_cancel_blocks_cycle(self):
        with patch.object(m, 'show_land_clearance_dialog', return_value=False):
            self.assertFalse(m.confirm_cycle_start())

    def test_stopping_before_dialog_does_not_prompt(self):
        with patch.object(m, 'show_land_clearance_dialog') as dialog:
            self.assertFalse(m.confirm_cycle_start(is_stopping=lambda: True))
            dialog.assert_not_called()

    def test_stop_during_dialog_overrides_approval(self):
        with patch.object(m, 'show_land_clearance_dialog', return_value=True):
            self.assertFalse(m.confirm_cycle_start(is_stopping=MagicMock(side_effect=[False, True])))

    def test_approval_is_never_cached_between_runs(self):
        with patch.object(m, 'show_land_clearance_dialog', return_value=True) as dialog:
            self.assertTrue(m.confirm_cycle_start())
            self.assertTrue(m.confirm_cycle_start())
            self.assertEqual(dialog.call_count, 2)

    def test_dialog_defaults_to_cancel_without_activating_game(self):
        user32 = MagicMock()
        user32.MessageBoxW.return_value = 2
        with patch.object(m.ctypes, 'WinDLL', return_value=user32):
            self.assertFalse(m.show_land_clearance_dialog())
        args = user32.MessageBoxW.call_args.args
        self.assertIsNone(args[0])
        self.assertTrue(args[3] & 0x100)  # second button = Cancel
        user32.SetForegroundWindow.assert_not_called()
        user32.SetCursorPos.assert_not_called()

    def test_background_wait_no_longer_blocks_on_wechat_foreground(self):
        # Safe render-child clicks do not require leaving WeChat first.
        self.assertTrue(m.wait_for_background_ready())

    def test_background_wait_obeys_stop(self):
        self.assertFalse(m.wait_for_background_ready(is_stopping=lambda: True))


    def test_background_wait_times_out_without_launching(self):
        # Retained name: wait is now immediate; stopping still wins.
        self.assertTrue(m.wait_for_background_ready(is_stopping=lambda: False))
        self.assertFalse(m.wait_for_background_ready(is_stopping=lambda: True))


    def test_maa_action_obeys_tasker_stop(self):
        context = SimpleNamespace(tasker=SimpleNamespace(stopping=True))
        with patch.object(m, 'show_land_clearance_dialog') as dialog:
            self.assertFalse(ConfirmProductionCycleStart().run(context, None))
            dialog.assert_not_called()

    def test_cli_cancel_blocks_every_game_script_even_when_checklist_hidden(self):
        with patch.object(sys, 'argv', ['cycle', '--apply', '--skip-checklist']), patch.object(sys, 'stdout', io.StringIO()), patch.object(run_short_cycle, 'confirm_cycle_start', return_value=False), patch.object(run_short_cycle, 'scan_for_target') as scan, patch.object(run_short_cycle, 'run_script') as run:
            self.assertEqual(run_short_cycle.main(), 2)
            scan.assert_not_called()
            run.assert_not_called()

    def test_dry_run_does_not_prompt(self):
        with patch.object(sys, 'argv', ['cycle', '--skip-checklist']), patch.object(sys, 'stdout', io.StringIO()), patch.object(run_short_cycle, 'confirm_cycle_start') as confirm:
            self.assertEqual(run_short_cycle.main(), 0)
            confirm.assert_not_called()


if __name__ == '__main__':
    unittest.main()

import unittest
from unittest.mock import patch

from tools import game_launcher as launch
from tools.task_control import TaskCancelled


class GameLauncherTests(unittest.TestCase):
    def test_existing_window_is_reused_without_launch_or_focus(self):
        with patch.object(launch, 'game_windows', return_value=[dict(hwnd=99, visible=True, minimized=False)]), \
                patch.object(launch.os, 'startfile', create=True) as start:
            self.assertEqual(launch.ensure_open(notify=lambda _: None), 99)
            start.assert_not_called()

    def test_missing_window_launches_exact_desktop_protocol_once(self):
        with patch.object(launch, 'game_windows', side_effect=[[], [], [dict(hwnd=99, visible=True, minimized=False)]]), \
                patch.object(launch.os, 'startfile', create=True) as start, patch.object(launch.time, 'sleep'):
            self.assertEqual(launch.ensure_open(notify=lambda _: None), 99)
            start.assert_called_once_with('weixin://launchapplet/?app_id=wx2d7c1a53c1bd45e1')

    def test_timeout_does_not_retry_protocol(self):
        with patch.object(launch, 'game_windows', return_value=[]), \
                patch.object(launch.os, 'startfile', create=True) as start:
            with self.assertRaisesRegex(RuntimeError, '超时'):
                launch.ensure_open(timeout=0, notify=lambda _: None)
            start.assert_called_once()

    def test_ambiguous_hidden_and_minimized_windows_block(self):
        for windows in ([{}, {}], [dict(hwnd=2, visible=True, minimized=True)], [dict(hwnd=2, visible=False, minimized=False)]):
            with self.subTest(windows=windows), patch.object(launch, 'game_windows', return_value=windows), \
                    patch.object(launch.os, 'startfile', create=True) as start, self.assertRaises(RuntimeError):
                launch.ensure_open(notify=lambda _: None)
            start.assert_not_called()

    def test_cancellation_prevents_launch(self):
        with patch.object(launch.os, 'startfile', create=True) as start, self.assertRaises(TaskCancelled):
            launch.ensure_open(check_cancelled=lambda: (_ for _ in ()).throw(TaskCancelled()))
        start.assert_not_called()

    def test_previews_do_not_open_game_but_live_tasks_do(self):
        from tools import hud_task_runner as runner, decoded_runtime as decoded
        with patch.object(launch, 'ensure_open') as ensure, patch.object(decoded, 'snapshot', return_value={'packageVersion':'100'}):
            runner.check_business_version({'entry':'RunShortCyclePreview'})
            runner.check_business_version({'entry':'RunShortCycle', 'options':{'周期运行模式':'仅预览'}})
            ensure.assert_not_called()
            runner.check_business_version({'entry':'RunNormalOrderLoop'})
            ensure.assert_called_once()

    def test_missing_protocol_reports_actionable_error(self):
        with patch.object(launch, 'game_windows', return_value=[]), \
                patch.object(launch.os, 'startfile', create=True, side_effect=OSError('no handler')):
            with self.assertRaisesRegex(RuntimeError, '微信安装'):
                launch.request_open()

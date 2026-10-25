import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tools import maa_frame_normalize as m
from tools.task_control import TaskCancelled


class BackgroundClickTests(unittest.TestCase):
    def test_background_drag_posts_only_mouse_messages_to_child(self):
        native = MagicMock()
        native.PostMessageW.return_value = True
        with patch.object(m.ctypes, 'WinDLL', return_value=native), patch.object(m, '_reject_wechat_foreground'), patch.object(m, 'check_cancelled'), patch.object(m.time, 'sleep'):
            self.assertTrue(m.post_swipe_postmessage_only(22, (10, 20), (10, 40), 50))
        messages = [call.args[1] for call in native.PostMessageW.call_args_list]
        self.assertEqual(messages, [0x200, 0x201, 0x200, 0x200, 0x202])
        self.assertTrue(all(call.args[0].value == 22 for call in native.PostMessageW.call_args_list))
        native.SetForegroundWindow.assert_not_called()
        native.SetCursorPos.assert_not_called()

    def test_background_drag_stop_always_releases_button(self):
        native = MagicMock()
        native.PostMessageW.return_value = True
        with patch.object(m.ctypes, 'WinDLL', return_value=native), patch.object(m, '_reject_wechat_foreground'), patch.object(m, 'check_cancelled', side_effect=[None, TaskCancelled]), patch.object(m.time, 'sleep'):
            with self.assertRaises(TaskCancelled):
                m.post_swipe_postmessage_only(22, (10, 20), (10, 40), 50)
        self.assertEqual([call.args[1] for call in native.PostMessageW.call_args_list], [0x200, 0x201, 0x202])

    def native(self, children):
        native = MagicMock()
        native.EnumChildWindows.side_effect = lambda root, callback, value: [callback(child, 0) for child in children]
        native.GetClassNameW.side_effect = lambda child, buffer, size: setattr(buffer, "value", "Chrome_RenderWidgetHostHWND")
        native.ClientToScreen.return_value = True
        native.ScreenToClient.return_value = True
        return native

    def test_unique_render_child_receives_input(self):
        with patch.object(m.ctypes, "WinDLL", return_value=self.native([22])):
            self.assertEqual(m._render_input_target(11, 123, 456), (22, 123, 456))

    def test_missing_or_ambiguous_child_does_not_fall_back_to_root(self):
        for children in ([], [22, 33]):
            with self.subTest(children=children), patch.object(m.ctypes, "WinDLL", return_value=self.native(children)):
                with self.assertRaises(RuntimeError):
                    m._render_input_target(11, 123, 456)

    def test_normalized_click_posts_only_to_render_target(self):
        with patch.object(m, "_lock_foreground", return_value=nullcontext(44)), patch.object(m, "_foreground_window", return_value=44), patch.object(m, "to_controller_xy", return_value=(100, 200)), patch.object(m, "_client_size", return_value=(720, 1358)), patch.object(m, "_screenshot_to_client", return_value=(100, 200)), patch.object(m, "_render_input_target", return_value=(22, 90, 180)) as target, patch.object(m, "post_click_postmessage_only", return_value=True) as post, patch.object(m, "install_foreground_safe_inputs"), patch.object(m.time, 'sleep'):
            self.assertTrue(m.post_click_normalized(SimpleNamespace(info={"hwnd": 11}), 100, 200).succeeded)
            target.assert_called_once_with(11, 100, 200)
            post.assert_called_once_with(22, 90, 180)

    def test_wechat_or_mfa_foreground_is_allowed_for_safe_click(self):
        # Thorough FG policy: do not refuse unrelated apps; only assert FG unchanged after click.
        with patch.object(m, '_lock_foreground', return_value=nullcontext(44)), \
                patch.object(m, '_foreground_window', return_value=44), \
                patch.object(m, 'to_controller_xy', return_value=(100, 200)), \
                patch.object(m, '_client_size', return_value=(720, 1358)), \
                patch.object(m, '_screenshot_to_client', return_value=(100, 200)), \
                patch.object(m, '_render_input_target', return_value=(22, 90, 180)), \
                patch.object(m, 'post_click_postmessage_only', return_value=True) as post, \
                patch.object(m, 'install_foreground_safe_inputs'), \
                patch.object(m.time, 'sleep'):
            self.assertTrue(m.post_click_normalized(SimpleNamespace(info={'hwnd': 11}), 100, 200).succeeded)
            post.assert_called_once_with(22, 90, 180)

    def test_install_blocks_native_controller_post_click(self):
        from maa.controller import Win32Controller
        m._SAFE_INPUTS_INSTALLED = False
        m.install_foreground_safe_inputs()
        with self.assertRaisesRegex(RuntimeError, 'unsafe Maa native input blocked'):
            Win32Controller.post_click(SimpleNamespace(), 1, 2)


    def test_foreground_change_after_delivered_click_is_logged_without_replay_or_abort(self):
        with patch.object(m, '_lock_foreground', return_value=nullcontext(44)), \
                patch.object(m, '_foreground_window', return_value=55), \
                patch.object(m, 'to_controller_xy', return_value=(100, 200)), \
                patch.object(m, '_client_size', return_value=(720, 1358)), \
                patch.object(m, '_screenshot_to_client', return_value=(100, 200)), \
                patch.object(m, '_render_input_target', return_value=(22, 90, 180)), \
                patch.object(m, 'post_click_postmessage_only', return_value=True) as post, \
                patch.object(m, '_log_fg_event') as log, \
                patch.object(m, 'install_foreground_safe_inputs'), \
                patch.object(m.time, 'sleep'):
            self.assertTrue(m.post_click_normalized(SimpleNamespace(info={'hwnd': 11}), 100, 200).succeeded)
            post.assert_called_once()
            event = log.call_args.args[0]
            self.assertTrue(event['input_delivered'])
            self.assertFalse(event['input_replayed'])
            self.assertEqual(event['policy'], 'verify_game_result')

    def test_foreground_change_after_swipe_keeps_actual_input_result(self):
        for delivered in (True, False):
            with self.subTest(delivered=delivered), \
                    patch.object(m, '_lock_foreground', return_value=nullcontext(44)), \
                    patch.object(m, '_foreground_window', return_value=55), \
                    patch.object(m, '_client_size', return_value=(720,1358)), \
                    patch.object(m, '_render_input_target', side_effect=[(22,10,20),(22,10,40)]), \
                    patch.object(m, 'post_swipe_postmessage_only', return_value=delivered) as post, \
                    patch.object(m, '_log_fg_event') as log, \
                    patch.object(m, '_window_title', return_value='switched'), \
                    patch.object(m, 'install_foreground_safe_inputs'):
                result=m.post_swipe_normalized(SimpleNamespace(info={'hwnd':11}),10,20,10,40,100)
                self.assertEqual(result.succeeded,delivered)
                post.assert_called_once()
                self.assertIs(log.call_args.args[0]['input_delivered'],delivered)
                self.assertFalse(log.call_args.args[0]['input_replayed'])

    def test_background_target_disabled_only_during_input(self):
        native = MagicMock()
        native.IsWindowEnabled.side_effect = [True, False]
        with patch.object(m, '_foreground_window', return_value=44), patch.object(m.ctypes, 'WinDLL', return_value=native):
            with m._lock_foreground(11) as foreground:
                self.assertEqual(foreground, 44)
                self.assertEqual(native.EnableWindow.call_args.args[1], False)
        self.assertEqual([call.args[1] for call in native.EnableWindow.call_args_list], [False, True])
        native.AttachThreadInput.assert_not_called()
        native.SetForegroundWindow.assert_not_called()

    def test_foreground_game_needs_no_lock_or_disable(self):
        native = MagicMock()
        native.IsWindowEnabled.return_value = True
        with patch.object(m, '_foreground_window', return_value=11), patch.object(m.ctypes, 'WinDLL', return_value=native):
            with m._lock_foreground(11):
                pass
        native.EnableWindow.assert_not_called()
        native.LockSetForegroundWindow.assert_not_called()

    def test_disabled_state_restored_when_cancelled(self):
        native = MagicMock()
        native.IsWindowEnabled.side_effect = [True, False]
        with patch.object(m, '_foreground_window', return_value=44), patch.object(m.ctypes, 'WinDLL', return_value=native):
            with self.assertRaises(TaskCancelled):
                with m._lock_foreground(11):
                    raise TaskCancelled()
        self.assertEqual(native.EnableWindow.call_args.args[1], True)

    def test_preexisting_disabled_window_is_not_touched(self):
        native = MagicMock()
        native.IsWindowEnabled.return_value = False
        with patch.object(m, '_foreground_window', return_value=44), patch.object(m.ctypes, 'WinDLL', return_value=native):
            with self.assertRaisesRegex(RuntimeError, 'already disabled'):
                with m._lock_foreground(11):
                    self.fail('must not send input')
        native.EnableWindow.assert_not_called()


if __name__ == "__main__":
    unittest.main()

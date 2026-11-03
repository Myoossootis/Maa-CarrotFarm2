import io
import json
from contextlib import ExitStack
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import order_board_availability as board
from maa_frame_normalize import EXPECTED_HEIGHT
import check_first_orders as first
import complete_batch_orders as batch
from tools import run_short_cycle as cycle
from tools import run_daily_orders as daily


class BoardAvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.frame = cv2.imread(str(ROOT / 'tests/fixtures/order_board/empty_party.png'))

    def read(self, caption, frame=None):
        with patch.object(board, 'recognized_text', return_value=caption):
            return board.read_availability(None, self.frame if frame is None else frame, 'party')

    def test_limit_notice_and_all_nine_empty_slots_are_required(self):
        result = self.read('完成订单次数:0/6已达每日接受6次上限')
        self.assertEqual(result['status'], 'board_loading')
        self.assertEqual(result['ui_counter'], [0, 6])
        self.assertIsNone(result['next_available'])
        for number in range(1, 10):
            frame = self.frame.copy()
            board.card_image(frame, number)[20:80, 20:100] = (255, 255, 0)
            self.assertEqual(self.read('已达每日接受6次上限加载中', frame)['status'], 'board_unknown')

    def test_counter_alone_is_unknown_loading_requires_text(self):
        for caption in ('', '完成订单次数:0/6', '0/6 1/6'):
            self.assertEqual(self.read(caption)['status'], 'board_unknown')
        self.assertEqual(self.read('正在加载')['status'], 'board_loading')

    def test_empty_loading_frame_can_become_available_with_the_same_notice(self):
        frame = self.frame.copy()
        board.card_image(frame, 1)[:] = (235, 220, 100)
        with patch.object(board, 'screenshot', side_effect=[self.frame, frame]) as capture, \
                patch.object(board, 'recognized_text', return_value='0/6已达每日恢复6次上限'), \
                patch.object(board.time, 'sleep'):
            result = board.probe_availability(None, None, 'party')
        self.assertEqual(result['status'], 'available')
        self.assertEqual(capture.call_count, 2)

    def test_only_three_consistent_empty_frames_confirm_unavailable(self):
        with patch.object(board, 'screenshot', return_value=self.frame) as capture, \
                patch.object(board, 'recognized_text', return_value='0/6已达每日恢复6次上限'), \
                patch.object(board.time, 'sleep'):
            result = board.probe_availability(None, None, 'party')
        self.assertEqual(result['status'], 'board_unavailable')
        self.assertEqual(result['reason'], 'confirmed_empty_board')
        self.assertEqual(capture.call_count, 3)
        self.assertIsNone(result['next_available'])

    def test_loading_or_changing_caption_does_not_claim_stable_empty(self):
        for captions in (['正在加载'] * 3, ['0/6已达每日恢复6次上限', '1/6已达每日恢复6次上限', '0/6已达每日恢复6次上限']):
            with patch.object(board, 'screenshot', return_value=self.frame), \
                    patch.object(board, 'recognized_text', side_effect=captions), patch.object(board.time, 'sleep'):
                result = board.probe_availability(None, None, 'party')
            self.assertEqual(result['status'], 'board_loading')
            self.assertIsNone(result['next_available'])

    def test_wrong_tab_and_geometry_do_not_use_caption(self):
        with patch.object(board, 'recognized_text') as read:
            self.assertEqual(board.read_availability(None, self.frame, 'takeout')['status'], 'board_unknown')
            self.assertEqual(board.read_availability(None, self.frame[:1300], 'party')['status'], 'board_unknown')
            read.assert_not_called()

    def test_live_short_frame_still_reads_the_board(self):
        short = self.frame[:EXPECTED_HEIGHT - 5]
        self.assertEqual(short.shape[:2], (1353, 720))
        with patch.object(board, 'recognized_text', return_value='完成订单次数:0/6已达每日恢复6次上限') as read:
            result = board.read_availability(None, short, 'party')
        read.assert_called_once()
        self.assertEqual(result['status'], 'board_loading')
        self.assertNotEqual(result['reason'], 'unrecognized_board')

    def test_visible_card_is_available_but_other_unknown_frames_are_blocked(self):
        frame = self.frame.copy()
        # Synthetic selected card: exercises the real cyan detector, not OCR.
        board.card_image(frame, 5)[:] = (235, 220, 100)
        self.assertEqual(self.read('完成订单次数:6/6', frame)['status'], 'available')
        frame[420:832, 85:645] = 0
        self.assertEqual(self.read('', frame)['status'], 'board_unknown')

    def test_real_maa_ocr_replays_sanitized_empty_board_without_input(self):
        from maa.controller import CustomController
        from maa.resource import Resource
        from maa.tasker import Tasker
        reject = lambda self, *args: False
        methods = {name: reject for name in dir(CustomController)
                   if getattr(getattr(CustomController, name), '__isabstractmethod__', False)}
        frame = self.frame
        methods.update(connect=lambda self: True, request_uuid=lambda self: 'order-board-fixture',
                       screencap=lambda self: np.copy(frame))
        controller = type('BoardFixtureController', (CustomController,), methods)()
        resource, tasker = Resource(), Tasker()
        try:
            Tasker.set_log_dir(''); Tasker.set_stdout_level(2)
            self.assertTrue(controller.post_connection().wait().succeeded)
            self.assertTrue(resource.post_bundle(ROOT / 'assets/resource').wait().succeeded)
            self.assertTrue(tasker.bind(resource, controller))
            with patch.object(board, 'screenshot', return_value=self.frame), patch.object(board.time, 'sleep'):
                result = board.probe_availability(tasker, controller, 'party')
            self.assertEqual(result['status'], 'board_unavailable', result)
        finally:
            for value in (tasker, resource, controller):
                value.__del__(); value._handle = None


class BoardGateTests(unittest.TestCase):
    def test_first_order_policy_stops_before_select_or_refresh(self):
        for status in (*sorted(board.WAITING_STATES), 'board_unknown'):
            result = dict(status=status, reason='fixture', next_available=None)
            with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
                stack.enter_context(patch.object(first, 'open_orders', return_value=True))
                stack.enter_context(patch.object(first, 'probe_availability', return_value=result))
                stack.enter_context(patch.object(first, 'screenshot'))
                stack.enter_context(patch.object(first.cv2, 'imwrite'))
                click = stack.enter_context(patch.object(first, 'post_click_normalized'))
                refresh = stack.enter_context(patch.object(first, 'refresh_order'))
                if status == 'board_unknown':
                    with self.assertRaisesRegex(RuntimeError, 'board_unknown'):
                        first.enforce_first_order(None, None, [], 'party', Path(folder), {})
                else:
                    self.assertEqual(first.enforce_first_order(None, None, [], 'party', Path(folder), {})['status'], status)
                click.assert_not_called()
                refresh.assert_not_called()

    def test_batch_wait_invalidates_cached_target_without_accepting_or_rewriting_delivery(self):
        for status in (*sorted(board.WAITING_STATES), 'board_unknown'):
            with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
                folder = Path(folder)
                delivery = folder / 'latest_party_delivery.json'
                delivery.write_text('{"status":"completed","old_receipt":true}', encoding='utf-8')
                receipt = delivery.read_bytes()
                for name in ('Win32Controller', 'Resource', 'Tasker'):
                    stack.enter_context(patch.object(batch, name, return_value=MagicMock()))
                for name in ('load_products', 'find_window', 'install_foreground_safe_inputs',
                             'measure_controller_offset', 'build_runtime_resource', 'dismiss_offline_income',
                             'leave_employee_ui_if_visible', 'screenshot'):
                    stack.enter_context(patch.object(batch, name))
                stack.enter_context(patch.object(batch, 'open_orders', return_value=True))
                stack.enter_context(patch.object(batch, 'probe_availability', return_value=dict(status=status)))
                returned = stack.enter_context(patch.object(batch, 'ensure_returned_to_farm'))
                select = stack.enter_context(patch.object(batch, '_select_recipe'))
                accept = stack.enter_context(patch.object(batch, '_click_accept'))
                stack.enter_context(patch.object(batch, 'RESULT', folder / 'result.json'))
                stack.enter_context(patch.object(batch, 'ROOT', folder))
                stack.enter_context(patch('sys.stdout', io.StringIO()))
                if status == 'board_unknown':
                    with self.assertRaisesRegex(RuntimeError, 'board_unknown'):
                        batch.run_batch(order_type='party', prepare_only=False, requested={'order': 5})
                    returned.assert_not_called()
                else:
                    self.assertEqual(batch.run_batch(order_type='party', prepare_only=False, requested={'order': 5}), 0)
                    returned.assert_called_once()
                result = json.loads((folder / 'result.json').read_text(encoding='utf-8'))
                self.assertIsNone(result['target_order'])
                self.assertEqual(result['status'], 'failed' if status == 'board_unknown' else status)
                self.assertEqual(delivery.read_bytes(), receipt)
                select.assert_not_called()
                accept.assert_not_called()

    def test_scan_stops_after_one_waiting_pass(self):
        for status in sorted(board.WAITING_STATES):
            result = dict(status=status, target_order=None)
            with patch.object(cycle, 'run_script') as run, patch.object(cycle, 'read_json', return_value=result), patch('sys.stdout', io.StringIO()):
                self.assertEqual(cycle.scan_for_target(6, 'party'), result)
                self.assertEqual(run.call_count, 1)

    def test_daily_wait_has_no_quota_write_or_invented_resume_time(self):
        for status in sorted(board.WAITING_STATES):
            summary = dict(_exit_code=0, status='waiting', rounds=[dict(status='waiting', scan=dict(
                status=status, board_availability=dict(caption='fixture', next_available=None)))])
            gate = MagicMock(can_start=True, in_cooldown=False)
            gate.as_dict.return_value = dict(completed_today=0, next_available='stale-local-eta')
            with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
                stack.enter_context(patch.object(daily, 'TMP', Path(folder)))
                stack.enter_context(patch.object(daily, 'MONITOR_DIR', Path(folder)))
                stack.enter_context(patch.object(daily, 'can_start', return_value=gate))
                run = stack.enter_context(patch.object(daily, '_run_one_short', return_value=summary))
                stack.enter_context(patch('sys.stdout', io.StringIO()))
                self.assertEqual(daily.run_daily('party', apply=True, poll=20, timeout=900, startup_confirmed=True), 0)
                final = json.loads((Path(folder) / 'latest_party.json').read_text(encoding='utf-8'))
                self.assertEqual(final['status'], status)
                self.assertEqual(final['done_this_run'], 0)
                self.assertIsNone(final['next_available'])
                self.assertEqual(run.call_count, 1)


if __name__ == '__main__':
    unittest.main()

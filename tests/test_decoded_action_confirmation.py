import unittest
import io
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tools import decoded_runtime as d
from tools import configure_field_employee as workers
from tools import order_board_helpers as board


def state(status=1, count=10, idle=False):
    return dict(storageSequence=42, orders=[dict(uid=9, statusCode=status)],
                inventory=dict(records=[dict(itemId=155, count=count)]),
                employees=dict(plans=[dict(buildingItemId=12, employees=[dict(name='worker')],
                    targets=[] if idle else [dict(itemId=155, name='玉米', count=1)],
                    accumulated=[],
                    heldProducts=[] if idle else [dict(itemId=155, name='玉米', count=2)],
                    productionOperations=None)]))


ITEMS = [dict(game_order_uid=9, game_item_id=155, actual=10, required=6)]


class ConfirmationTests(unittest.TestCase):
    def test_collection_requires_both_empty_plan_and_received_inventory(self):
        for after in (state(count=10, idle=True), state(count=12), state(count=10)):
            with self.subTest(after=after), patch.object(d, 'snapshot', return_value=after):
                with self.assertRaises(d.DecodeError):
                    d.verify_collection('field_1', state(), timeout=0)
        with patch.object(d, 'snapshot', return_value=state(count=12, idle=True)):
            self.assertTrue(d.verify_collection('field_1', state(), timeout=0)['cleared'])

    def test_submission_requires_exact_debit_and_active_order_status(self):
        self.assertFalse(d.submission_confirmed(ITEMS, state(status=1, count=4)))
        self.assertFalse(d.submission_confirmed(ITEMS, state(status=2, count=10)))
        self.assertFalse(d.submission_confirmed(ITEMS, state(status=2, count=3)))
        self.assertTrue(d.submission_confirmed(ITEMS, state(status=2, count=4)))

    def test_missing_staff_dialog_confirmed_once_then_decode_proves_submission(self):
        click_result = SimpleNamespace(wait=lambda: SimpleNamespace(succeeded=True))
        with patch.object(d, 'snapshot', side_effect=[state(), state(), state(status=2, count=4)]), \
             patch.object(board, 'screenshot'), patch.object(board, 'restaurant_closed_prompt', return_value=False), \
             patch.object(board, 'missing_staff_prompt', return_value=True), \
             patch.object(board, 'post_click_normalized', return_value=click_result) as click, \
             patch.object(board.time, 'sleep'):
            result = board.finish_order_acceptance(object(), object(), ITEMS)
        self.assertTrue(result['missing_staff_confirmed'])
        click.assert_called_once_with(unittest.mock.ANY, 200, 675)

    def test_night_closure_is_never_reported_as_accepted(self):
        with patch.object(d, 'snapshot', return_value=state()), patch.object(board, 'screenshot'), \
             patch.object(board, 'restaurant_closed_prompt', return_value=True), \
             patch.object(board, 'confirm_sleep_or_close_shop', return_value=True) as close, \
             patch.object(board, 'post_click_normalized') as click:
            with self.assertRaises(board.RestaurantClosedError):
                board.finish_order_acceptance(object(), object(), ITEMS)
        close.assert_called_once()
        click.assert_not_called()

    def test_unknown_or_unconfirmed_prompt_does_not_repeat_accept(self):
        with patch.object(d, 'snapshot', return_value=state()), patch.object(board, 'screenshot'), \
             patch.object(board, 'restaurant_closed_prompt', return_value=False), \
             patch.object(board, 'missing_staff_prompt', return_value=False), \
             patch.object(board, 'post_click_normalized') as click:
            with self.assertRaises(d.DecodeError):
                board.finish_order_acceptance(object(), object(), ITEMS, timeout=0)
        click.assert_not_called()

    def test_already_confirmed_submission_needs_no_dialog_reads_or_clicks(self):
        with patch.object(d, 'snapshot', return_value=state(status=2, count=4)), \
             patch.object(board, 'screenshot') as capture, \
             patch.object(board, 'post_click_normalized') as click:
            result = board.finish_order_acceptance(object(), object(), ITEMS)
        self.assertEqual(result['data_source'], 'decoded')
        capture.assert_not_called()
        click.assert_not_called()

    def test_staff_continue_that_does_not_submit_is_not_counted_or_reclicked(self):
        with patch.object(d, 'snapshot', return_value=state()), patch.object(board, 'screenshot'), \
             patch.object(board, 'restaurant_closed_prompt', return_value=False), \
             patch.object(board, 'missing_staff_prompt', return_value=True), \
             patch.object(board, 'post_click_normalized', return_value=SimpleNamespace(wait=lambda: SimpleNamespace(succeeded=True))) as click, \
             patch.object(board.time, 'sleep'):
            with self.assertRaises(d.DecodeError):
                board.finish_order_acceptance(object(), object(), ITEMS, timeout=0)
        click.assert_called_once_with(unittest.mock.ANY, 200, 675)

    def test_prompt_text_requires_both_specific_body_and_continue_button(self):
        import tools.ensure_ranch_feed as feed
        body = '您还未拥有噗噗(厨师)，无法获得双倍奖品，是否查看当前员工获得方式？'
        for text, button, expected in ((body, '继续订单', True), (body, '前往', False), ('余额不足', '继续订单', False)):
            with patch.object(feed, 'recognized_text', side_effect=[text, button]):
                self.assertEqual(board.missing_staff_prompt(object(), object()), expected)

    def test_shared_prompt_title_cannot_trigger_night_confirmation(self):
        with patch.object(board, 'template_hit', return_value=object()), \
             patch.object(board, '_prompt_text', return_value='您还未拥有厨师，无法获得双倍奖品，是否查看当前员工获得方式？'), \
             patch.object(board, 'screenshot'), patch.object(board, 'click_template_node') as click:
            self.assertFalse(board.confirm_sleep_or_close_shop(object(), object()))
            click.assert_not_called()

    def test_collection_reacquires_moving_button_and_never_retries_failed_claim(self):
        for fail in (False, True):
            with self.subTest(fail=fail), ExitStack() as stack:
                stack.enter_context(patch.object(d, 'enabled', return_value=True))
                stack.enter_context(patch.object(d, 'snapshot', return_value=state()))
                stack.enter_context(patch.object(d, 'progress', return_value=(1, 1)))
                stack.enter_context(patch.object(workers, 'find_slot_action', return_value=(600, 'collect_supplies')))
                stack.enter_context(patch.object(workers, 'screenshot'))
                stack.enter_context(patch.object(workers, 'visible_slot_action', return_value=(600, 'collect_supplies')))
                stack.enter_context(patch.object(workers, 'list_progress', return_value=(1, 1)))
                stack.enter_context(patch.object(workers, 'locate', side_effect=[(.99, (150, 540)), (.99, (150, 555)), (.99, (150, 556))]))
                stack.enter_context(patch.object(workers.cv2, 'imread', return_value=SimpleNamespace(shape=(60, 168, 3))))
                stack.enter_context(patch.object(workers.time, 'sleep'))
                click = stack.enter_context(patch.object(workers, 'click'))
                verify = stack.enter_context(patch.object(d, 'verify_collection',
                    side_effect=d.DecodeError('unchanged') if fail else None,
                    return_value=dict(cleared=True, reason='decoded_plan_clear_and_inventory_received')))
                if fail:
                    with self.assertRaises(workers.CollectionUnconfirmedError):
                        workers.collect_finished_slot(object(), 'field_1', object(), 1)
                else:
                    result = workers.collect_finished_slot(object(), 'field_1', object(), 1)
                    self.assertEqual(result['clearance']['reason'], 'decoded_plan_clear_and_inventory_received')
                click.assert_called_once_with(unittest.mock.ANY, 234, 586)
                verify.assert_called_once()

    def test_unfinished_monitor_reads_cache_without_opening_employee_list(self):
        from tools import monitor_employee_products as monitoring
        unfinished = state()
        unfinished['employees']['plans'][0]['heldProducts'] = []
        with ExitStack() as stack:
            stack.enter_context(patch.object(d, 'enabled', return_value=True))
            snapshots = stack.enter_context(patch.object(d, 'snapshot', side_effect=[unfinished, state()]))
            for name in ('Win32Controller', 'Resource', 'Tasker', 'find_window', 'write_json', 'RESULT'):
                stack.enter_context(patch.object(monitoring, name))
            stack.enter_context(patch.object(monitoring, '_close_employee_menu_with_retry'))
            stack.enter_context(patch.object(monitoring.time, 'sleep'))
            stack.enter_context(patch('sys.stdout', io.StringIO()))
            collect = stack.enter_context(patch.object(monitoring, 'collect_finished_slot',
                return_value=dict(collected=True, progress=(1, 1))))
            result = monitoring.monitor({'field_1': 1}, 5, 100, {'field_1': {'玉米': 1}})
        self.assertTrue(result['complete'])
        self.assertEqual(snapshots.call_count, 2)
        collect.assert_called_once()


if __name__ == '__main__':
    unittest.main()

import importlib
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tools import order_template_click as ui

with patch.object(sys, 'path', [str(Path(__file__).resolve().parents[1] / 'tools'), *sys.path]):
    orders = importlib.import_module('complete_normal_orders')


class OrderBackgroundTests(unittest.TestCase):
    def test_navigation_waits_for_loading_frame_without_guessing_click(self):
        frames = ['farm', 'loading', 'restaurant', 'orders']
        def click(controller, name, frame):
            return (frame, name) in {('farm', 'NormalOrdersClickRestaurantEntry'),
                                    ('restaurant', 'NormalOrdersClickNoticeBoard')}
        with patch.object(ui, 'screenshot', side_effect=frames), patch.object(ui, 'template_hit', side_effect=lambda frame, _: object() if frame == 'orders' else None), patch.object(ui, 'click_template_node', side_effect=click), patch.object(ui.time, 'sleep'):
            self.assertTrue(ui.open_normal_orders(object()))

    def test_navigation_gives_up_after_bounded_unrecognized_frames(self):
        with patch.object(ui, 'screenshot', return_value=object()) as screenshot, patch.object(ui, 'template_hit', return_value=None), patch.object(ui, 'click_template_node', return_value=False), patch.object(ui.time, 'sleep'):
            self.assertFalse(ui.open_normal_orders(object()))
            self.assertEqual(screenshot.call_count, 12)

    def test_missing_template_never_sends_input(self):
        with patch.object(ui, 'template_hit', return_value=None), patch.object(ui, 'post_click_normalized') as click:
            self.assertFalse(ui.click_template_node(object(), 'AcceptNormalOrderStart', object()))
            click.assert_not_called()

    def test_matched_template_uses_safe_click(self):
        with patch.object(ui, 'template_hit', return_value=(.99, (50, 60), 0)), patch.object(ui, 'post_click_normalized') as click, patch.object(ui.time, 'sleep'):
            click.return_value.wait.return_value.succeeded = True
            self.assertTrue(ui.click_template_node('controller', 'AcceptNormalOrderStart', object()))
            click.assert_called_once_with('controller', 50, 60)

    def test_map_without_progress_is_not_accepted(self):
        tasker = MagicMock()
        tasker.post_task.return_value.wait.return_value.succeeded = False
        with patch.object(orders, 'screenshot', return_value=object()), patch.object(orders, 'template_hit', return_value=None), patch.object(orders, '_order_delivery_ocr', return_value=False):
            self.assertFalse(orders._acceptance_confirmed(tasker, object()))

    def test_accelerate_copy_is_not_in_progress(self):
        for text, expected in [('免费加速', False), ('订单还没有准备好', False), ('订单正在进行中', True)]:
            tasker = MagicMock()
            tasker.post_recognition.return_value.wait.return_value.get.return_value = SimpleNamespace(nodes=[SimpleNamespace(recognition=SimpleNamespace(filtered_results=[SimpleNamespace(text=text)]))])
            self.assertEqual(orders._order_delivery_ocr(tasker, object()), expected)


if __name__ == '__main__':
    unittest.main()

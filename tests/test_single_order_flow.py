import importlib
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

from tools import scan_normal_orders as scanner
from tools import configure_field_employee as employees

with patch.object(sys, 'path', [str(Path(__file__).resolve().parents[1] / 'tools'), *sys.path]):
    orders = importlib.import_module('complete_normal_orders')


def recipe(fish=False, ready=False):
    return [dict(name='鱼肉' if fish else '玉米', actual=100 if ready else 2,
                 required=3, missing=0 if ready else 1, satisfied=ready),
            dict(name='鸡蛋', actual=10, required=3, missing=0, satisfied=True)]


class SingleOrderFlowTests(unittest.TestCase):
    def scan(self, recipes, icons=None):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            result_file = Path(directory) / 'completion.json'
            stack.enter_context(patch.object(orders, 'RESULT', result_file))
            stack.enter_context(patch.object(sys, 'argv', ['orders', '--prepare-only']))
            stack.enter_context(patch.object(sys, 'stdout', io.StringIO()))
            for name in ['Win32Controller', 'Resource', 'find_window', 'leave_employee_ui_if_visible',
                         'dismiss_reward_overlay', 'dismiss_offline_income', 'ensure_returned_to_farm']:
                stack.enter_context(patch.object(orders, name))
            tasker = MagicMock()
            tasker.post_task.return_value.wait.return_value.succeeded = False
            stack.enter_context(patch.object(orders, 'Tasker', return_value=tasker))
            stack.enter_context(patch.object(orders, 'measure_controller_offset', return_value=0))
            stack.enter_context(patch.object(orders, 'build_runtime_resource', return_value=Path(directory)))
            stack.enter_context(patch.object(orders, 'load_products', return_value=[]))
            stack.enter_context(patch.object(orders, 'open_normal_orders', return_value=True))
            stack.enter_context(patch.object(orders, 'claim_completed_reward', return_value=False))
            stack.enter_context(patch.object(orders, 'prepare_completion_control', return_value=object()))
            stack.enter_context(patch.object(orders, 'recognize_pins', return_value=[(100 * i, 418) for i in range(1, 10)]))
            stack.enter_context(patch.object(orders, 'screenshot', return_value=object()))
            stack.enter_context(patch.object(orders, 'completion_control_visible', return_value=True))
            stack.enter_context(patch.object(orders, 'completion_checked', return_value=(True, .99)))
            read = stack.enter_context(patch.object(orders, 'read_order', side_effect=recipes))
            stack.enter_context(patch.object(orders, 'match_products', return_value=[], side_effect=icons))
            click = stack.enter_context(patch.object(orders, 'post_click_normalized'))
            accept = stack.enter_context(patch.object(orders, '_bright_accept_box'))
            stack.enter_context(patch.object(orders.time, 'sleep'))
            self.assertEqual(orders.main(), 0)
            accept.assert_not_called()
            return json.loads(result_file.read_text(encoding='utf-8')), read.call_count, click.call_args_list

    def test_first_non_fish_stops_after_one_read_without_accepting(self):
        output, reads, clicks = self.scan([recipe()])
        self.assertEqual(reads, 1)
        self.assertEqual(output['target_order']['order'], 1)
        self.assertEqual(len(clicks), 1)

    def test_fish_skipped_then_second_order_selected(self):
        output, reads, _ = self.scan([recipe(fish=True, ready=True), recipe()])
        self.assertEqual(reads, 2)
        self.assertEqual(output['target_order']['order'], 2)
        self.assertEqual(output['skipped'][0]['reason'], 'unsupported_product')

    def test_three_fish_stop_without_fourth_order(self):
        output, reads, clicks = self.scan([recipe(fish=True)] * 3)
        self.assertEqual(reads, 3)
        self.assertIsNone(output['target_order'])
        self.assertEqual(len(clicks), 3)

    def test_ready_first_order_does_not_create_employee_jobs(self):
        output, reads, _ = self.scan([recipe(ready=True)])
        self.assertEqual(reads, 1)
        self.assertEqual(output['target_order']['production_targets'], [])

    def test_fish_icon_skips_quantity_ocr_entirely(self):
        output, reads, _ = self.scan([recipe()], icons=[[{'name': '鱼肉'}], []])
        self.assertEqual(reads, 1)
        self.assertEqual(output['target_order']['order'], 2)

    def test_incomplete_quantity_ocr_never_returns_partial_recipe(self):
        products = [{'name': '玉米', 'center_x': 100, 'score': .99}, {'name': '鸡蛋', 'center_x': 200, 'score': .99},
                    {'name': '鱼肉', 'center_x': 300, 'score': .99}]
        with patch.object(scanner, 'match_products', return_value=products), patch.object(scanner, 'recognize_quantity', side_effect=[recipe()[0], recipe()[1], None]):
            self.assertEqual(scanner.read_order(object(), object(), []), [])

    def test_pin_recognition_needs_only_top_row(self):
        template = cv2.imread(str(scanner.RESOURCE / 'image/navigation/normal_order_pin.png'))
        frame = np.zeros((1358, 720, 3), dtype=np.uint8)
        height, width = template.shape[:2]
        for x in (130, 340, 550):
            frame[395:395 + height, x:x + width] = template
        pins = scanner.recognize_pins(frame)
        self.assertEqual(len(pins), 3)
        self.assertTrue(all(y < 465 for _, y in pins))

    def test_missing_repeat_control_cannot_be_false_positive_unchecked(self):
        frame = np.zeros((1358, 720, 3), dtype=np.uint8)
        tasker = MagicMock()
        tasker.post_recognition.return_value.wait.return_value.get.return_value.nodes = []
        with patch.object(employees, 'match') as match:
            self.assertIsNone(employees.repeat_checkbox_state(tasker, frame))
            match.assert_not_called()

    def test_repeat_templates_distinguish_checked_unchecked_and_unknown(self):
        for filename, expected in [('repeat_checked.png', True), ('repeat_unchecked.png', False), (None, None)]:
            with self.subTest(template=filename):
                frame = np.zeros((1358, 720, 3), dtype=np.uint8)
                if filename:
                    template = cv2.imread(str(employees.IMAGES / filename))
                    height, width = template.shape[:2]
                    frame[1120:1120 + height, 500:500 + width] = template
                tasker = MagicMock()
                self.assertIs(employees.repeat_checkbox_state(tasker, frame), expected)


if __name__ == '__main__':
    unittest.main()

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from unittest.mock import MagicMock

from tools import configure_field_employee as m


class ConfigureCapabilityTests(unittest.TestCase):
    def test_quantity_crop_includes_top_of_current_digit(self):
        frame = np.zeros((1358, 720, 3), dtype=np.uint8)
        frame[615:620, 243:247] = 255
        tasker = MagicMock()
        tasker.post_recognition.return_value.wait.return_value.get.return_value = SimpleNamespace(
            nodes=[SimpleNamespace(recognition=SimpleNamespace(filtered_results=[SimpleNamespace(text="1", score=.99)]))])
        self.assertEqual(m.icon_quantity(tasker, frame, "小麦"), 1)
        first_image = tasker.post_recognition.call_args_list[0].args[2]
        self.assertGreater(int(first_image.max()), 0)

    def test_unlocked_product_reaches_ui_validation(self):
        with patch.object(m, "open_plan", side_effect=RuntimeError("UI validation reached")) as open_plan:
            with self.assertRaisesRegex(RuntimeError, "UI validation reached"):
                m.configure_items(object(), object(), "field_1", {"小麦": 1})
            open_plan.assert_called_once()

    def test_unsupported_product_is_rejected_before_ui(self):
        with patch.object(m, "open_plan") as open_plan:
            with self.assertRaisesRegex(RuntimeError, "cannot produce"):
                m.configure_items(object(), object(), "field_1", {"鱼肉": 1})
            open_plan.assert_not_called()


if __name__ == "__main__":
    unittest.main()

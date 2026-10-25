import unittest
from unittest.mock import patch

from tools import locate_field_2 as m


class LocateField2Tests(unittest.TestCase):
    def test_reports_the_located_action_without_opening(self):
        with patch.object(m, 'find_slot_action', return_value=(794, 'work_plan')) as locate:
            self.assertEqual(m.locate_field_2(object()), {
                'status': 'located', 'slot': 'field_2', 'action': 'work_plan', 'button_y': 794,
            })
            locate.assert_called_once_with(unittest.mock.ANY, 'field_2')


if __name__ == '__main__':
    unittest.main()

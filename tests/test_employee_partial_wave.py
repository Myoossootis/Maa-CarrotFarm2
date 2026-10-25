import io
from contextlib import ExitStack
import unittest
from unittest.mock import patch

from tools import run_employee_order_plan as planner


class EmployeePartialWaveTests(unittest.TestCase):
    def allocate(self,targets,partial):
        with ExitStack() as stack:
            stack.enter_context(patch.object(planner.decoded,'enabled',return_value=True))
            stack.enter_context(patch.object(planner.decoded,'snapshot',return_value={}))
            stack.enter_context(patch.object(planner.decoded,'assignment_blocker',
                side_effect=lambda slot,data:'employee_plan_busy' if slot=='field_3' else None))
            stack.enter_context(patch('sys.stdout',io.StringIO()))
            return planner.idle_allocation(None,None,targets,allow_partial=partial)

    def test_112_gap_uses_only_100_idle_capacity_and_exposes_remainder(self):
        targets=[dict(name='番茄',needed=64,route='field'),dict(name='玉米',needed=48,route='field')]
        with self.assertRaisesRegex(RuntimeError,'not enough idle'):
            self.allocate(targets,False)
        wave=self.allocate(targets,True)
        self.assertTrue(wave['partial_wave'])
        self.assertFalse(wave['order_fulfilled_after_wave'])
        self.assertEqual(sum(sum(job['items'].values()) for job in wave['jobs']),100)
        self.assertEqual({job['slot_id'] for job in wave['jobs']},{'field_1','field_2'})
        self.assertEqual(sum(row['missing'] for row in wave['unmet']),12)
        self.assertEqual(wave['busy_slots_preserved'],[dict(slot_id='field_3',state='employee_plan_busy')])

    def test_unsupported_product_cannot_be_hidden_by_a_partial_wave(self):
        with self.assertRaises(RuntimeError):
            self.allocate([dict(name='番茄',needed=64),dict(name='不支持的作物',needed=12)],True)


if __name__=='__main__':unittest.main()

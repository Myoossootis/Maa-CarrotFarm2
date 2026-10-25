import importlib
import io
import json
from contextlib import ExitStack
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
normal=importlib.import_module('complete_normal_orders')


class NormalDeliveryUITests(unittest.TestCase):
    def test_triple_requires_one_actual_three_and_checked_control(self):
        for text,checked,expected in (('完成3次',True,True),('完成1次',True,False),
                                      ('完成3次完成2次',True,False),('完成3次',False,False),('',True,False)):
            with patch.object(normal,'recognized_text',return_value=text), \
                    patch.object(normal,'completion_checked',return_value=(checked,.99)), \
                    patch.object(normal,'completion_control_visible',return_value=True):
                if expected:normal.verify_normal_triple(None,None)
                else:
                    with self.assertRaises(normal.decoded.DecodeError):normal.verify_normal_triple(None,None)

    def test_pending_delivery_resumes_before_recipe_or_accept(self):
        pending=dict(status='accepted',operation_id='op',order=1,items=[])
        completed={**pending,'status':'completed'}
        with tempfile.TemporaryDirectory() as folder,ExitStack() as stack:
            stack.enter_context(patch.object(normal,'RESULT',Path(folder)/'result.json'))
            stack.enter_context(patch.object(sys,'argv',['normal','--prepare-only']))
            stack.enter_context(patch('sys.stdout',io.StringIO()))
            stack.enter_context(patch.object(normal.decoded,'enabled',return_value=True))
            stack.enter_context(patch.object(normal.decoded,'snapshot'))
            stack.enter_context(patch.object(normal.delivery,'load',return_value=pending))
            stack.enter_context(patch.object(normal.delivery,'recover',return_value=pending))
            finish=stack.enter_context(patch.object(normal,'finish_normal_delivery',return_value=completed))
            for name in ('Win32Controller','Resource','Tasker','find_window','load_products',
                         'install_foreground_safe_inputs','measure_controller_offset','build_runtime_resource',
                         'dismiss_offline_income','leave_employee_ui_if_visible','dismiss_reward_overlay'):
                stack.enter_context(patch.object(normal,name,return_value=MagicMock()))
            stack.enter_context(patch.object(normal,'open_normal_orders',return_value=True))
            prepare=stack.enter_context(patch.object(normal,'prepare_completion_control'))
            accept=stack.enter_context(patch.object(normal,'_bright_accept_box'))
            self.assertEqual(normal.main(),0)
            result=json.loads(normal.RESULT.read_text(encoding='utf-8'))
            self.assertEqual((result['operation_id'],result['batch_count'],result['reward_claimed']),('op',3,True))
            finish.assert_called_once()
            prepare.assert_not_called();accept.assert_not_called()

    def test_uncertain_accept_blocks_before_game_connection(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(normal,'RESULT',Path(folder)/'result.json'), \
                patch.object(sys,'argv',['normal','--prepare-only']), \
                patch.object(normal.decoded,'enabled',return_value=True), \
                patch.object(normal.decoded,'snapshot'), \
                patch.object(normal.delivery,'load',return_value={'status':'accept_intent'}), \
                patch.object(normal.delivery,'recover',side_effect=normal.decoded.DecodeError('Unresolved')), \
                patch.object(normal,'find_window') as connect:
            with self.assertRaisesRegex(normal.decoded.DecodeError,'Unresolved'):normal.main()
            connect.assert_not_called()


if __name__=='__main__':unittest.main()

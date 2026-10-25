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
    def test_reward_navigation_is_reserved_and_stops_at_overlay_without_second_claim(self):
        overlay={'visible':True}
        receipt=dict(status='accepted',uid=7,order=1)
        data=dict(orders=[dict(category='normal',uid=7,slot=1,statusCode=3)])
        def dismiss(controller):
            overlay['visible']=False
        def navigate(controller, *, stop_when):
            reserve.assert_called_once()
            return False
        with patch.object(normal.decoded,'snapshot',return_value=data), \
                patch.object(normal.delivery,'recover',return_value=receipt), \
                patch.object(normal,'dismiss_reward_overlay',side_effect=dismiss) as close, \
                patch.object(normal,'open_normal_orders',side_effect=navigate) as navigation, \
                patch.object(normal,'post_click_normalized',return_value=MagicMock()), \
                patch.object(normal.time,'sleep'),patch.object(normal,'screenshot'), \
                patch.object(normal,'reward_overlay_visible',side_effect=lambda _:overlay['visible']), \
                patch.object(normal,'card_selected',return_value=True), \
                patch.object(normal,'_claim_reward_button',return_value=(360,1100)), \
                patch.object(normal.delivery,'reserve_reward',return_value=dict(receipt,status='reward_intent')) as reserve, \
                patch.object(normal,'claim_completed_reward',return_value=True) as claim, \
                patch.object(normal.delivery,'complete',return_value=dict(receipt,status='completed')), \
                patch.object(normal,'ensure_returned_to_farm'):
            self.assertEqual(normal.finish_normal_delivery(None,None,receipt)['status'],'completed')
            close.assert_called_once();reserve.assert_called_once();claim.assert_not_called()
            navigation.assert_called_once_with(None,stop_when=normal.reward_overlay_visible)

    def test_reward_navigation_guard_stops_before_any_click_on_overlay(self):
        import order_template_click as nav
        with patch.object(nav,'screenshot',return_value='overlay'), \
                patch.object(nav,'template_hit') as hit,patch.object(nav,'click_template_node') as click:
            self.assertFalse(nav.open_normal_orders(None,stop_when=lambda frame:frame=='overlay'))
        click.assert_not_called();hit.assert_not_called()

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
            stack.enter_context(patch.object(normal.decoded,'snapshot',return_value={'packageVersion':'90'}))
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
                patch.object(normal.decoded,'snapshot',return_value={'packageVersion':'90'}), \
                patch.object(normal.delivery,'load',return_value={'status':'accept_intent'}), \
                patch.object(normal.delivery,'recover',side_effect=normal.decoded.DecodeError('Unresolved')), \
                patch.object(normal,'find_window') as connect:
            with self.assertRaisesRegex(normal.decoded.DecodeError,'Unresolved'):normal.main()
            connect.assert_not_called()

    def test_old_terminal_receipt_enters_fresh_scan_instead_of_returning_same_completion(self):
        with tempfile.TemporaryDirectory() as folder,ExitStack() as stack:
            stack.enter_context(patch.object(normal,'RESULT',Path(folder)/'result.json'))
            stack.enter_context(patch.object(sys,'argv',['normal','--prepare-only']))
            stack.enter_context(patch('sys.stdout',io.StringIO()))
            stack.enter_context(patch.object(normal.decoded,'enabled',return_value=True))
            stack.enter_context(patch.object(normal.decoded,'snapshot',return_value={'packageVersion':'90','orders':[]}))
            stack.enter_context(patch.object(normal.delivery,'load',return_value={'status':'completed','operation_id':'old'}))
            finish=stack.enter_context(patch.object(normal,'finish_normal_delivery'))
            for name in ('Win32Controller','Resource','Tasker','find_window','load_products',
                         'install_foreground_safe_inputs','measure_controller_offset','build_runtime_resource',
                         'dismiss_offline_income','leave_employee_ui_if_visible','dismiss_reward_overlay'):
                stack.enter_context(patch.object(normal,name,return_value=MagicMock()))
            stack.enter_context(patch.object(normal,'open_normal_orders',return_value=True))
            stack.enter_context(patch.object(normal,'claim_completed_reward',return_value=False))
            stack.enter_context(patch.object(normal,'screenshot'))
            stack.enter_context(patch.object(normal,'template_hit',return_value=None))
            scan=stack.enter_context(patch.object(normal,'prepare_completion_control',side_effect=RuntimeError('fresh scan reached')))
            with self.assertRaisesRegex(RuntimeError,'fresh scan reached'):
                normal.main()
            finish.assert_not_called();scan.assert_called_once()

    def test_unknown_version_blocks_before_game_connection_or_receipt_read(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(normal,'RESULT',Path(folder)/'result.json'), \
                patch.object(sys,'argv',['normal','--prepare-only']), \
                patch.object(normal.decoded,'enabled',return_value=True), \
                patch.object(normal.decoded,'snapshot',return_value={'packageVersion':'91'}), \
                patch.object(normal.delivery,'load') as receipt,patch.object(normal,'find_window') as connect:
            with self.assertRaisesRegex(normal.decoded.DecodeError,'version is unverified'):normal.main()
            connect.assert_not_called();receipt.assert_not_called()


if __name__=='__main__':unittest.main()

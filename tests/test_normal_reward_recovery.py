from pathlib import Path
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import complete_normal_orders as normal


class RewardRecoveryTests(unittest.TestCase):
    def test_claim_intent_precedes_only_claim_and_no_new_accept(self):
        receipt={'uid':88,'order':1,'status':'accepted'}
        data={'orders':[{'uid':88,'category':'normal','slot':1,'statusCode':3}]}
        events=[]
        with patch.object(normal.decoded,'snapshot',return_value=data), \
                patch.object(normal.delivery,'recover',return_value=receipt), \
                patch.object(normal.delivery,'reserve_reward',side_effect=lambda *a:events.append('intent') or dict(receipt,status='reward_intent')), \
                patch.object(normal.delivery,'complete',side_effect=lambda *a:events.append('complete') or {'status':'completed'}), \
                patch.object(normal,'open_normal_orders',side_effect=lambda *a,**k:events.append('navigate') or True), \
                patch.object(normal,'reward_overlay_visible',return_value=False), \
                patch.object(normal,'card_selected',return_value=True), \
                patch.object(normal,'post_click_normalized') as select, \
                patch.object(normal,'screenshot'),patch.object(normal,'_claim_reward_button',return_value=(500,1100)), \
                patch.object(normal,'claim_completed_reward',side_effect=lambda *a:events.append('claim') or True), \
                patch.object(normal,'ensure_returned_to_farm') as returned,patch.object(normal.time,'sleep'), \
                patch.object(normal,'_bright_accept_box') as accept:
            self.assertEqual(normal.finish_normal_delivery(None,None,receipt)['status'],'completed')
        self.assertEqual(events,['intent','navigate','claim','complete'])
        accept.assert_not_called();returned.assert_called_once();select.assert_called_once()

    def test_missing_claim_control_blocks_claim_and_changed_uid_blocks_navigation_intent(self):
        for uid, selected, button in ((88,True,None),(99,True,None),(88,False,(500,1100))):
            data={'orders':[{'uid':uid,'category':'normal','slot':1,'statusCode':3}]}
            with patch.object(normal.decoded,'snapshot',return_value=data), \
                    patch.object(normal.delivery,'recover',return_value={'uid':88,'status':'accepted'}), \
                    patch.object(normal.delivery,'reserve_reward') as reserve, \
                    patch.object(normal,'open_normal_orders',return_value=True), \
                    patch.object(normal,'reward_overlay_visible',return_value=False), \
                    patch.object(normal,'card_selected',return_value=selected), \
                    patch.object(normal,'post_click_normalized'),patch.object(normal,'screenshot'), \
                    patch.object(normal,'_claim_reward_button',return_value=button), \
                    patch.object(normal,'claim_completed_reward') as claim,patch.object(normal.time,'sleep'):
                with self.assertRaises(normal.decoded.DecodeError):
                    normal.finish_normal_delivery(None,None,{'uid':88,'status':'accepted'})
            self.assertEqual(reserve.call_count,1 if uid==88 else 0)
            claim.assert_not_called()


if __name__=='__main__':unittest.main()

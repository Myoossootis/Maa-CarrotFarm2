import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools import run_normal_order_loop as loop
from tools.task_control import TaskCancelled


PREPARED = dict(status='prepared',returned_to_farm=True,target_order=dict(order=1,items=[],missing_items=[],production_targets=[]))


def completed(operation):
    return dict(status='completed',operation_id=operation,batch_count=3,submitted_count=1,
                submitted=[{'acceptance_confirmed':True}],reward_claimed=True,returned_to_farm=True)


class NormalLoopTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        self.folder=Path(folder.name)
        output=patch('sys.stdout',io.StringIO());output.start();self.addCleanup(output.stop)

    def test_two_distinct_rewarded_triples_and_no_extra_iteration(self):
        step=Mock(side_effect=[PREPARED,completed('one'),PREPARED,completed('two')])
        read=Mock(side_effect=[{'status':'completed','operation_id':'one'},{'status':'completed','operation_id':'two'}])
        result=loop.loop(step,self.folder,max_orders=2,read_receipt=read)
        self.assertEqual((result['status'],result['completed_orders'],result['completed_count']),('complete',2,6))
        self.assertEqual(step.call_count,4)
        self.assertTrue((self.folder/'order_0002.json').exists())

    def test_shortage_yields_before_submit_or_production(self):
        gap=dict(PREPARED,target_order=dict(order=1,missing_items=[{'name':'甘蔗','missing':41}],production_targets=[{'needed':41}]))
        step=Mock(return_value=gap)
        result=loop.loop(step,self.folder)
        self.assertEqual(result['status'],'waiting_for_materials')
        self.assertEqual(result['completed_count'],0)
        step.assert_called_once_with('--prepare-only')

    def test_resumed_delivery_does_not_submit_and_duplicate_receipt_stops(self):
        step=Mock(return_value=completed('same'))
        read=Mock(return_value={'status':'completed','operation_id':'same'})
        with self.assertRaisesRegex(RuntimeError,'duplicate'):
            loop.loop(step,self.folder,read_receipt=read)
        summary=json.loads((self.folder/'summary.json').read_text(encoding='utf-8'))
        self.assertEqual(summary['completed_orders'],1)
        self.assertEqual(summary['status'],'failed')
        self.assertTrue(all(call.args==('--prepare-only',) for call in step.call_args_list))

    def test_unclaimed_wrong_multiplier_or_missing_receipt_never_advances(self):
        for bad in (dict(completed('one'),reward_claimed=False),dict(completed('one'),batch_count=1),
                    dict(completed('one'),submitted=[])):
            step=Mock(return_value=bad)
            with self.assertRaisesRegex(RuntimeError,'receipt'):
                loop.loop(step,self.folder,read_receipt=lambda:{'status':'completed','operation_id':'one'})
            step.assert_called_once()

    def test_cancellation_preserves_no_completed_iteration(self):
        with patch.object(loop,'check_cancelled',side_effect=TaskCancelled):
            with self.assertRaises(TaskCancelled):loop.loop(Mock(),self.folder)
        summary=json.loads((self.folder/'summary.json').read_text(encoding='utf-8'))
        self.assertEqual((summary['status'],summary['completed_orders']),('cancelled',0))


if __name__=='__main__':unittest.main()

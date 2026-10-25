import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import nullcontext
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

    def test_shortage_production_requires_fresh_scan_before_submission(self):
        gap=dict(PREPARED,target_order=dict(order=1,missing_items=[{'missing':41}],production_targets=[{'needed':41}]))
        step=Mock(side_effect=[gap, PREPARED, completed('one')])
        produce=Mock()
        result=loop.loop(step,self.folder,max_orders=1,produce=produce,
                         read_receipt=lambda:{'operation_id':'one','status':'completed'})
        produce.assert_called_once_with()
        self.assertEqual(step.call_args_list[1].args,('--prepare-only',))
        self.assertEqual(result['completed_count'],3)

    def test_failed_production_never_submits_or_counts(self):
        gap=dict(PREPARED,target_order=dict(missing_items=[{'missing':1}]))
        step=Mock(return_value=gap)
        with self.assertRaisesRegex(RuntimeError,'uncertain claim'):
            loop.loop(step,self.folder,produce=Mock(side_effect=RuntimeError('uncertain claim')))
        step.assert_called_once_with('--prepare-only')
        self.assertEqual(json.loads((self.folder/'summary.json').read_text())['completed_count'],0)

    def test_two_waves_require_two_rescans_and_only_final_order_counts(self):
        gap=dict(PREPARED,target_order=dict(order=1,missing_items=[{'missing':112}]))
        remainder=dict(PREPARED,target_order=dict(order=1,missing_items=[{'missing':12}]))
        step=Mock(side_effect=[gap,remainder,PREPARED,completed('one')]);produce=Mock()
        result=loop.loop(step,self.folder,max_orders=1,produce=produce,
                         read_receipt=lambda:{'status':'completed','operation_id':'one'})
        self.assertEqual(produce.call_count,2)
        self.assertEqual([c.args for c in step.call_args_list[:3]],[('--prepare-only',)]*3)
        self.assertEqual((result['completed_orders'],result['completed_count']),(1,3))


class ProductionResumeTests(unittest.TestCase):
    def test_saved_assignment_monitors_canonical_file_without_new_save(self):
        saved=dict(status='saved',assignment_id='one',operation_dir='bound/one')
        with patch.object(loop.employees,'active',side_effect=[saved,dict(saved,status='collected')]), \
                patch.object(loop.delivery,'load',return_value=None),patch.object(loop,'run_script') as run:
            loop.finish_production(20,7200)
        run.assert_called_once_with('monitor_employee_products.py','--assignment-file',str(Path('bound/one/assignment.json')),
                                    '--poll-seconds','20','--timeout-seconds','7200','--apply')

    def test_partial_assignment_resumes_before_monitor_and_requires_confirmed_collection(self):
        applying=dict(status='applying',assignment_id='one',operation_dir='bound/one')
        saved=dict(applying,status='saved')
        with patch.object(loop.employees,'active',side_effect=[applying,saved,saved]), \
                patch.object(loop.delivery,'load',return_value=None),patch.object(loop,'run_script') as run:
            with self.assertRaisesRegex(loop.decoded.DecodeError,'collection not confirmed'):
                loop.finish_production(20,7200)
        self.assertEqual(run.call_args_list[0].args,('run_employee_order_plan.py','--resume','--allow-partial','--apply'))
        self.assertEqual(run.call_count,2)

    def test_old_completed_assignment_does_not_authorize_monitoring(self):
        with patch.object(loop.employees,'active',return_value={'status':'collected'}), \
                patch.object(loop,'run_script') as run:
            loop.finish_production(20,7200)
        run.assert_not_called()


class LoopEntryGuardTests(unittest.TestCase):
    def test_unknown_version_stops_before_production_policy_or_game_inputs(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(loop,'ROOT',Path(folder)),patch('sys.argv',['loop','--apply']), \
                patch.dict('os.environ',{'CARROT_STATE_READER':'decoded'}), \
                patch.object(loop,'cycle_lock',return_value=nullcontext()), \
                patch.object(loop.decoded,'snapshot',return_value={'packageVersion':'999'}), \
                patch.object(loop,'finish_production') as production,patch.object(loop,'run_script') as run:
            with self.assertRaisesRegex(loop.decoded.DecodeError,'version is unverified'):
                loop.main()
            production.assert_not_called();run.assert_not_called()
            state=json.loads(next(Path(folder).rglob('summary.json')).read_text())
            self.assertEqual(state['status'],'failed')

    def test_lock_conflict_keeps_original_error_and_failure_report(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(loop,'ROOT',Path(folder)),patch('sys.argv',['loop','--apply']), \
                patch.dict('os.environ',{'CARROT_STATE_READER':'decoded'}), \
                patch.object(loop,'cycle_lock',side_effect=RuntimeError('another UI cycle')), \
                patch.object(loop.decoded,'snapshot') as read:
            with self.assertRaisesRegex(RuntimeError,'another UI cycle'):
                loop.main()
            read.assert_not_called()
            state=json.loads(next(Path(folder).rglob('summary.json')).read_text())
            self.assertIn('another UI cycle',state['error'])

    def test_pending_delivery_conflict_blocks_employee_inputs(self):
        with patch.object(loop.employees,'active',return_value={'status':'applying'}), \
                patch.object(loop.delivery,'load',return_value={'status':'accepted'}),patch.object(loop,'run_script') as run:
            with self.assertRaisesRegex(loop.decoded.DecodeError,'conflict'):
                loop.finish_production(20,7200)
        run.assert_not_called()


if __name__=='__main__':unittest.main()

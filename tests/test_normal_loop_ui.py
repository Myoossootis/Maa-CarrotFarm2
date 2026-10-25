import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent import production_actions as actions
from agent.order_preferences import allowed_products_from_node

ROOT=Path(__file__).resolve().parents[1]


class NormalLoopUITests(unittest.TestCase):
    def setUp(self):
        self.interface=json.loads((ROOT/'assets/interface.json').read_text(encoding='utf-8'))
        self.pipeline=json.loads((ROOT/'assets/resource/pipeline/carrot_farm.json').read_text(encoding='utf-8'))

    def test_explicit_opt_in_native_options_keep_continuous_type_and_selection(self):
        from maa.resource import Resource
        from maa.tasker import Tasker
        Tasker.set_log_dir('');Tasker.set_stdout_level(2)
        task=next(t for t in self.interface['task'] if t['entry']=='RunNormalOrderLoop')
        self.assertFalse(task['default_check'])
        self.assertIn('普通循环设置',task['option'])
        option=self.interface['option']['普通循环设置']
        self.assertEqual(option['inputs'][0]['default'],'0')
        resource=Resource()
        try:
            self.assertTrue(resource.post_bundle(ROOT/'assets/resource').wait().succeeded)
            for name in ['苹果','甘蔗']:
                case=next(c for c in self.interface['option']['一号位允许品类']['cases'] if c['name']==name)
                self.assertTrue(resource.override_pipeline(case['pipeline_override']))
            override=option['pipeline_override']
            override['RunNormalOrderLoop']['custom_action_param']['max_orders']=2
            self.assertTrue(resource.override_pipeline(override))
            node=resource.get_node_data('RunNormalOrderLoop')
            self.assertEqual(allowed_products_from_node(node),{'苹果','甘蔗'})
            params=node['action']['param']['custom_action_param']
            self.assertEqual(params['max_orders'],2)
            self.assertIs(params['continuous'],True)
            self.assertEqual(params['order_type'],'normal')
        finally:
            resource.__del__();resource._handle=None

    def run_action(self,params,apply=True):
        node=self.pipeline['RunNormalOrderLoop']
        node['attach'].update(ui_apply=apply,first_order_allow_crop_012=True)
        context=SimpleNamespace(tasker=SimpleNamespace(stopping=False),get_node_data=lambda _:node)
        argv=SimpleNamespace(node_name='RunNormalOrderLoop',custom_action_param=json.dumps(params))
        with patch.object(actions,'run_cycle_worker',return_value=True) as worker, \
                patch.object(actions,'wait_for_background_ready',return_value=True) as ready,patch('sys.stdout',io.StringIO()):
            result=actions.RunDailyOrders().run(context,argv)
        return result,worker,ready

    def test_finite_and_continuous_ui_launch_correct_worker(self):
        for maximum in (0,2):
            result,worker,_=self.run_action(dict(order_type='normal',continuous=True,max_orders=maximum,timeout_seconds=7200))
            self.assertTrue(result)
            self.assertEqual(worker.call_args.kwargs['script'],'run_normal_order_loop.py')
            arguments=worker.call_args.args[0]
            self.assertEqual(arguments[arguments.index('--max-orders')+1],str(maximum))
            self.assertNotIn('--order-type',arguments)
            self.assertIn('--apply',arguments)

    def test_preview_and_bad_parameters_never_start_game_inputs(self):
        result,worker,ready=self.run_action(dict(order_type='normal',continuous=True),apply=False)
        self.assertTrue(result);ready.assert_not_called()
        self.assertNotIn('--apply',worker.call_args.args[0])
        for params in (dict(order_type='party',continuous=True),dict(continuous=True,max_orders=-1),
                       dict(continuous=True,max_orders=True),dict(continuous='true')):
            result,worker,ready=self.run_action(params)
            self.assertFalse(result);worker.assert_not_called();ready.assert_not_called()


if __name__=='__main__':unittest.main()

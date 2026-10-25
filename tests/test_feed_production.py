import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import cv2

from agent import production_actions
from tools import feed_production_ui as vision
from tools import run_feed_production as feed
from tools.task_control import TaskCancelled

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / 'fixtures/feed_production'
COMPLETE = {name: [target, target] for name, target in feed.ITEMS.items()}


class CompletionTests(unittest.TestCase):
    def test_red_potato_counts_one_for_one_toward_ordinary_potato(self):
        ordinary={**COMPLETE,'土豆':[10,12]}
        effective=feed.combine_crop_progress(ordinary,{'红皮土豆':2})
        self.assertEqual(effective['土豆'],[12,12])
        self.assertTrue(feed.all_crops_complete(effective))
        self.assertEqual(ordinary['土豆'],[10,12])

    def test_mutation_does_not_complete_other_products_or_unfinished_potatoes(self):
        self.assertFalse(feed.all_crops_complete(feed.combine_crop_progress(
            {**COMPLETE,'玉米':[19,20],'土豆':[10,12]}, {'红皮土豆':2})))
        self.assertFalse(feed.all_crops_complete(feed.combine_crop_progress(
            {**COMPLETE,'土豆':[9,12]}, {'红皮土豆':2})))
        for counts in ({'番茄':2},{'红皮土豆':-1},{'红皮土豆':True}):
            with self.assertRaises(ValueError):
                feed.combine_crop_progress(COMPLETE,counts)
    def test_requires_all_three_complete_and_correct_denominators(self):
        for invalid in ({}, {'玉米': [48, 48]}, {**COMPLETE, '甘蔗': [15, 16]},
                        {**COMPLETE, '土豆': [12, 20]}, {**COMPLETE, '土豆': None},
                        {**COMPLETE, '番茄': [1, 1]}, {**COMPLETE, '玉米': ['20', 20]}):
            self.assertFalse(feed.all_crops_complete(invalid), invalid)
        self.assertTrue(feed.all_crops_complete(COMPLETE))
        self.assertTrue(feed.all_crops_complete({**COMPLETE, '玉米': [22, 20]}))

    def test_fraction_rejects_wrong_or_ambiguous_counters(self):
        self.assertEqual(vision.fraction('玉米22/20', 20), (22, 20))
        self.assertIsNone(vision.fraction('50/50', 20))
        self.assertIsNone(vision.fraction('0/20 20/20', 20))

    def test_mill_capacity_is_separate_from_feed_satiation_duration(self):
        self.assertEqual(vision.mill_capacity('最大生产次数:12提供饱腹度3小时生产速度:8分54秒'), (12,534))
        self.assertIsNone(vision.mill_capacity('提供饱腹度3小时'))


class FeedLoopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.ui = Mock()
        self.ui.inventory.return_value = 10
        self.ui.mill_ready.return_value = 0
        self.ui.refill.return_value = {'status': 'started'}
        self.ui.arrange.return_value = {'saved': True}
        self.ui.collect_crops.return_value = {'cleared': True}
        self.ui.collect_mill.return_value = {'after': 0}
        self.ui.crop_progress.return_value = COMPLETE
        self.stdout = patch('sys.stdout', io.StringIO())
        self.stdout.start()
        self.addCleanup(self.stdout.stop)

    def loop(self, phase='idle', **state):
        feed.write_json(self.folder/'state.json', dict(crop_phase=phase, refill_due=False,
                        pending=None, items=feed.ITEMS, **state))
        return feed.FeedLoop(self.ui, state_path=self.folder/'state.json', log_dir=self.folder/'log')

    def test_arrange_then_wait_then_collect_refill_and_arrange_next_batch(self):
        loop = self.loop()
        loop.tick()
        self.assertEqual(loop.state['crop_phase'], 'growing')
        self.ui.arrange.assert_called_once()
        self.ui.collect_crops.assert_not_called()
        self.ui.reset_mock()
        self.ui.inventory.return_value = 10
        self.ui.mill_ready.return_value = 0
        self.ui.crop_progress.return_value = COMPLETE
        self.ui.refill.return_value = {'status': 'started'}
        loop.tick()
        actions = [call[0] for call in self.ui.mock_calls]
        self.assertLess(actions.index('collect_crops'), actions.index('refill'))
        self.assertLess(actions.index('refill'), actions.index('arrange'))
        self.assertEqual(self.ui.crop_progress.call_count, 2)
        self.assertFalse(loop.state['refill_due'])

    def test_partial_or_unstable_product_progress_never_harvests(self):
        for values in ([{**COMPLETE, '土豆': [11, 12]}],
                       [COMPLETE, {**COMPLETE, '甘蔗': [15, 16]}]):
            loop = self.loop('growing')
            self.ui.crop_progress.side_effect = values
            loop.tick()
            self.ui.collect_crops.assert_not_called()
            self.ui.refill.assert_not_called()

    def test_at_cap_pauses_all_actions_and_resumes_after_consumption(self):
        loop = self.loop('growing')
        self.ui.inventory.return_value = 120
        loop.tick()
        self.ui.mill_ready.assert_not_called()
        self.ui.collect_crops.assert_not_called()
        self.ui.arrange.assert_not_called()
        self.assertTrue(loop.state['paused'])
        self.assertEqual(loop.state['crop_phase'], 'growing')
        self.ui.inventory.return_value = 119
        loop.tick()
        self.ui.collect_crops.assert_called_once()
        self.assertFalse(loop.state['paused'])

    def test_claiming_feed_that_crosses_cap_prevents_refill_and_crop_action(self):
        loop = self.loop('growing')
        self.ui.inventory.side_effect = [119, 125]
        self.ui.mill_ready.return_value = 6
        loop.tick()
        self.ui.collect_mill.assert_called_once_with(6)
        self.ui.collect_crops.assert_not_called()
        self.ui.arrange.assert_not_called()
        self.ui.refill.assert_not_called()

    def test_full_mill_keeps_refill_due_without_rearranging(self):
        loop = self.loop('growing')
        self.ui.refill.return_value = {'status': 'waiting'}
        loop.tick()
        self.assertTrue(loop.state['refill_due'])
        self.assertEqual(loop.state['crop_phase'], 'idle')
        self.ui.arrange.assert_not_called()

    def test_restart_after_success_does_not_duplicate_arrangement(self):
        loop = self.loop()
        loop.tick()
        restarted = feed.FeedLoop(self.ui, state_path=self.folder/'state.json', log_dir=self.folder/'log2')
        self.ui.crop_progress.return_value = {**COMPLETE, '甘蔗': [0, 16]}
        restarted.tick()
        self.ui.arrange.assert_called_once()

    def test_existing_plan_is_not_marked_as_an_uncertain_arrangement(self):
        loop = self.loop()
        self.ui.ensure_idle.side_effect = RuntimeError('已有计划')
        with self.assertRaisesRegex(RuntimeError, '已有计划'):
            loop.tick()
        self.assertIsNone(loop.state['pending'])
        self.ui.arrange.assert_not_called()

    def test_uncertain_action_is_saved_and_never_blindly_repeated_after_restart(self):
        loop = self.loop()
        self.ui.arrange.side_effect = RuntimeError('save result unknown')
        with self.assertRaisesRegex(RuntimeError, 'unknown'):
            loop.run(max_checks=1)
        saved = json.loads((self.folder/'state.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['pending'], 'arrange')
        self.assertTrue((self.folder/'log/error.log').is_file())
        self.ui.save_failure.assert_called_once()
        restarted = feed.FeedLoop(self.ui, state_path=self.folder/'state.json', log_dir=self.folder/'log2')
        with self.assertRaisesRegex(RuntimeError, '未确认'):
            restarted.tick()
        self.ui.arrange.assert_called_once()

    def test_cancellation_preserves_existing_plan_and_records_status(self):
        loop = self.loop('growing')
        self.ui.inventory.side_effect = TaskCancelled('stopped')
        with self.assertRaises(TaskCancelled):
            loop.run()
        self.assertEqual(loop.state['crop_phase'], 'growing')
        records = [json.loads(line) for line in (self.folder/'log/events.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual(records[-1]['event'], 'cancelled')
        self.assertTrue((self.folder/'log/cancelled.log').is_file())
        self.assertFalse((self.folder/'log/error.log').exists())
        self.ui.arrange.assert_not_called()

    def test_unknown_inventory_never_produces(self):
        loop = self.loop()
        self.ui.inventory.return_value = None
        with self.assertRaisesRegex(RuntimeError, '无法确认'):
            loop.tick()
        self.ui.arrange.assert_not_called()

    def test_three_observation_failures_stop_with_evidence(self):
        loop = self.loop('growing')
        self.ui.inventory.side_effect = RuntimeError('OCR failed')
        with patch.object(feed.time, 'sleep'), self.assertRaisesRegex(RuntimeError, 'OCR failed'):
            loop.run(max_checks=1)
        self.assertEqual(self.ui.inventory.call_count, 3)
        self.assertEqual(self.ui.save_failure.call_count, 4)
        self.assertIsNone(loop.state['pending'])

    def test_transient_observation_error_retries_without_resetting_plan(self):
        loop = self.loop('growing')
        self.ui.inventory.side_effect = [RuntimeError('OCR failed'), 120]
        with patch.object(feed.time, 'sleep'):
            loop.run(max_checks=1)
        self.assertEqual(self.ui.inventory.call_count, 2)
        self.ui.arrange.assert_not_called()
        self.ui.collect_crops.assert_not_called()

    def test_invalid_parameters_fail_before_input(self):
        for maximum, poll in ((True,60),(0,60),(120,9),(120,3601),(120,'60')):
            with self.assertRaises(ValueError):
                feed.FeedLoop(self.ui, maximum, poll, state_path=self.folder/'state.json')


class FeedVisionTests(unittest.TestCase):
    def mutation_progress_ui(self, filename, readings):
        ui=vision.FeedProductionUI()
        ui.tasker=object()
        ui.frame=Mock(return_value=cv2.imread(str(FIXTURES/filename)))
        ui.field_action=Mock()
        ui.audit=Mock()
        mock=patch.object(vision,'text',side_effect=readings)
        mock.start();self.addCleanup(mock.stop)
        return ui

    def test_actual_mutated_employee_card_is_identified_and_combined(self):
        ui=self.mutation_progress_ui('field3_mutation.png',['20/20','16/16','10/12','2'])
        self.assertEqual(ui.crop_progress(),COMPLETE)
        self.assertEqual(ui.audit.call_args.kwargs['mutations'],{'红皮土豆':2})
        self.assertEqual(ui.audit.call_args.kwargs['ordinary_progress']['土豆'],(10,12))

    def test_ordinary_card_does_not_invent_mutations(self):
        ui=self.mutation_progress_ui('field3_no_mutation.png',['0/20','0/16','0/12'])
        self.assertEqual(ui.crop_progress(),{name:[0,target] for name,target in feed.ITEMS.items()})
        self.assertEqual(ui.last_crop_evidence['mutations'],{})

    def test_unreadable_mutation_counter_cannot_authorize_collection(self):
        ui=self.mutation_progress_ui('field3_mutation.png',['20/20','16/16','10/12','2/12'])
        with self.assertRaisesRegex(RuntimeError,'变异数量不可读'):
            ui.crop_progress()
    def inventory_ui(self, matches):
        ui=vision.FeedProductionUI()
        ui.controller=object()
        ui.tasker=object()
        ui.wait_on_farm=Mock()
        ui.frame=Mock(return_value=cv2.imread(str(FIXTURES/'materials.png')))
        ui.click=Mock()
        ui.swipe=Mock()
        ui.audit=Mock()
        for mock in (patch.object(vision,'hit',side_effect=matches),
                     patch.object(vision,'template_hit',return_value=object()),
                     patch.object(vision.employees,'close_employee_menu'),
                     patch.object(vision.time,'sleep')):
            mock.start();self.addCleanup(mock.stop)
        return ui

    def test_zero_requires_selected_materials_tab_and_confirmed_absence(self):
        anchor=(.99,110,500,54,42)
        ui=self.inventory_ui([anchor,anchor,None,anchor,None])
        self.assertEqual(ui.inventory(),0)
        self.assertEqual(ui.click.call_count,2) # open and tab, no item/sell click

    def test_weak_feed_candidate_is_unknown_rather_than_zero(self):
        anchor=(.99,110,500,54,42)
        ui=self.inventory_ui([anchor,anchor,None,anchor,(.7,110,500,54,42)])
        with self.assertRaisesRegex(RuntimeError,'不能推断为零'):
            ui.inventory()

    def test_wrong_inventory_tab_cannot_report_zero(self):
        ui=self.inventory_ui([(.99,110,500,54,42),None])
        with self.assertRaisesRegex(RuntimeError,'物资页未确认'):
            ui.inventory()

    def test_live_full_checkbox_states_are_distinguished(self):
        self.assertIs(vision.checked_full(cv2.imread(str(FIXTURES/'mill_unchecked.png'))), False)
        self.assertIs(vision.checked_full(cv2.imread(str(FIXTURES/'mill_checked.png'))), True)
        self.assertIsNone(vision.checked_full(cv2.imread(str(FIXTURES/'materials.png'))))

    def test_scale_reduced_live_mill_is_detected(self):
        from tools.ensure_ranch_feed import feed_match, MAP_SCALES
        frame = cv2.imread(str(FIXTURES/'zoomed_out_mill.png'))
        self.assertIsNotNone(feed_match(frame, 'feed_silo_structure.png', MAP_SCALES, .72))

    def test_inventory_feed_and_employee_output_have_separate_anchors(self):
        materials = cv2.imread(str(FIXTURES/'materials.png'))
        employee = cv2.imread(str(FIXTURES/'mill_employee.png'))
        self.assertIsNotNone(vision.hit(materials,'feed_inventory_icon.png',(65,475,590,450)))
        self.assertIsNotNone(vision.hit(employee,'mill_employee_title.png',(140,95,220,1030),.9))
        self.assertIsNone(vision.hit(materials,'mill_employee_title.png',(140,95,220,1030),.9))

    def refill_ui(self, checked=False, before=0, after=2100, materials='20/5', batches=1):
        ui = vision.FeedProductionUI()
        ui.controller = object()
        ui.tasker = object()
        ui.wait_on_farm = Mock()
        ui.frame = Mock(return_value=cv2.imread(str(FIXTURES/'mill_checked.png')))
        ui.click = Mock()
        ui.audit = Mock()
        ui._mocks = (
            patch.object(vision,'open_mill',return_value={'status':'opened'}),
            patch.object(vision,'checked_full',side_effect=[checked,True]),
            patch.object(vision,'read_mill_state',side_effect=[f'磨坊预计可生产{before}秒',f'磨坊预计可生产{after}秒']),
            patch.object(vision,'text',side_effect=['12','8分54秒',
                         '补货' if batches==1 else f'补货{batches}次', materials,
                         f'{4*batches}/{4*batches}',f'{3*batches}/{3*batches}']),
            patch.object(vision,'close_mill_popup'),
        )
        for mock in ui._mocks:
            mock.start()
            self.addCleanup(mock.stop)
        return ui

    def test_refill_checks_full_once_then_confirms_longer_production(self):
        ui = self.refill_ui()
        result = ui.refill()
        self.assertEqual(result['status'],'started')
        self.assertTrue(result['full_checked'])
        self.assertEqual(ui.click.call_args_list[0].args,(596,949))
        self.assertEqual(ui.click.call_count,2)

    def test_already_checked_full_is_not_toggled_off(self):
        ui = self.refill_ui(checked=True)
        self.assertEqual(ui.refill()['status'],'started')
        self.assertEqual(ui.click.call_count,1)

    def test_one_key_full_uses_four_batch_label_and_scaled_materials(self):
        ui=self.refill_ui(checked=True,materials='21/20',batches=4)
        result=ui.refill()
        self.assertEqual(result['status'],'started')
        self.assertEqual(result['batches'],4)
        ui.click.assert_called_once_with(520,858)

    def test_button_and_recipe_batch_mismatch_never_clicks_refill(self):
        ui=self.refill_ui(checked=True,materials='21/5',batches=4)
        with self.assertRaisesRegex(RuntimeError,'材料数量不可读'):
            ui.refill()
        ui.click.assert_not_called()

    def test_refill_shortage_and_full_mill_wait_without_clicking_refill(self):
        ui = self.refill_ui(checked=True,materials='1/5')
        self.assertEqual(ui.refill()['reason'],'materials_short')
        ui.click.assert_not_called()

    def test_refill_at_capacity_waits_instead_of_repeatedly_clicking(self):
        ui = self.refill_ui(checked=True,before=6300)
        self.assertEqual(ui.refill()['reason'],'mill_full')
        ui.click.assert_not_called()

    def test_unchanged_timer_is_not_reported_as_success_or_clicked_twice(self):
        ui = self.refill_ui(checked=True,after=0)
        with self.assertRaisesRegex(RuntimeError,'未确认'):
            ui.refill()
        self.assertEqual(ui.click.call_count,1)

    def test_mill_collection_requires_claim_button_and_counter_clearing(self):
        ui=vision.FeedProductionUI()
        ui.mill_ready=Mock(side_effect=[6,0])
        ui.frame=Mock(return_value=object())
        ui.click=Mock()
        ui.audit=Mock()
        with patch.object(vision,'hit',return_value=(1,153,786,70,40)), patch.object(vision.employees,'match',return_value=.99):
            self.assertEqual(ui.collect_mill(6),dict(before=6,after=0))
        ui.click.assert_called_once_with(230,1078)

    def test_mill_product_icon_is_never_used_as_unverified_claim_control(self):
        ui=vision.FeedProductionUI()
        ui.mill_ready=Mock(return_value=6)
        ui.frame=Mock(return_value=object())
        ui.click=Mock()
        with patch.object(vision,'hit',return_value=(1,153,786,70,40)), patch.object(vision.employees,'match',return_value=.2):
            with self.assertRaisesRegex(RuntimeError,'领取按钮未确认'):
                ui.collect_mill(6)
        ui.click.assert_not_called()


class FeedIntegrationTests(unittest.TestCase):
    def test_real_maa_ocr_replays_four_batch_refill_screenshot_without_input(self):
        import numpy as np
        from maa.controller import CustomController
        from maa.resource import Resource
        from maa.tasker import Tasker
        frame=cv2.imread(str(FIXTURES/'mill_four_batches.png'))
        reject=lambda self,*args:False
        methods={name:reject for name in dir(CustomController)
                 if getattr(getattr(CustomController,name),'__isabstractmethod__',False)}
        methods.update(connect=lambda self:True,request_uuid=lambda self:'feed-recipe-ocr',
                       screencap=lambda self:np.copy(frame))
        controller=type('FixtureController',(CustomController,),methods)()
        resource,tasker=Resource(),Tasker()
        try:
            Tasker.set_log_dir('');Tasker.set_stdout_level(2)
            self.assertTrue(controller.post_connection().wait().succeeded)
            self.assertTrue(resource.post_bundle(ROOT/'assets/resource').wait().succeeded)
            self.assertTrue(tasker.bind(resource,controller))
            ui=vision.FeedProductionUI()
            ui.controller=controller;ui.tasker=tasker
            ui.frame=Mock(return_value=frame);ui.wait_on_farm=Mock();ui.audit=Mock();ui.click=Mock()
            with patch.object(vision,'open_mill',return_value={'status':'already_open'}), \
                    patch.object(vision,'read_mill_state',side_effect=['磨坊预计可生产0秒','磨坊预计可生产2156秒']), \
                    patch.object(vision,'close_mill_popup'):
                result=ui.refill()
            self.assertEqual(result['batches'],4)
            self.assertEqual(result['status'],'started')
            ui.click.assert_called_once_with(520,858)
            materials=[call.kwargs for call in ui.audit.call_args_list if call.args[0]=='mill_material']
            self.assertEqual([item['progress'] for item in materials],[(21,20),(18,16),(36,12)])
        finally:
            for obj in (tasker,resource,controller):
                obj.__del__();obj._handle=None

    def test_maa_action_forwards_validated_settings_and_respects_stop(self):
        context = SimpleNamespace(tasker=SimpleNamespace(stopping=False))
        argv = SimpleNamespace(custom_action_param=json.dumps(dict(max_feed=240, poll_seconds=90)))
        with patch.object(production_actions,'wait_for_background_ready',return_value=True), patch.object(production_actions,'run_cycle_worker',return_value=True) as worker:
            self.assertTrue(production_actions.RunFeedProduction().run(context,argv))
            self.assertEqual(worker.call_args.args[0],['--max-feed','240','--poll-seconds','90'])
            self.assertEqual(worker.call_args.kwargs['script'],'run_feed_production.py')
            context.tasker.stopping=True
            self.assertFalse(production_actions.RunFeedProduction().run(context,argv))
            self.assertEqual(worker.call_count,1)

    def test_default_unchecked_final_task_and_native_numeric_override(self):
        from maa.resource import Resource
        interface=json.loads((ROOT/'assets/interface.json').read_text(encoding='utf-8'))
        task=interface['task'][-1]
        self.assertEqual(task['entry'],'RunFeedProduction')
        self.assertFalse(task['default_check'])
        resource=Resource()
        try:
            self.assertTrue(resource.post_bundle(ROOT/'assets/resource').wait().succeeded)
            override=interface['option']['饲料循环设置']['pipeline_override']
            rendered=json.loads(json.dumps(override).replace('"{maximum}"','240').replace('"{interval}"','90'))
            self.assertTrue(resource.override_pipeline(rendered))
            node=resource.get_node_data('RunFeedProduction')
            self.assertEqual(node['action']['param']['custom_action_param'],dict(max_feed=240,poll_seconds=90))
        finally:
            resource.__del__();resource._handle=None


if __name__ == '__main__':
    unittest.main()

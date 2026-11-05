from pathlib import Path
from unittest.mock import Mock, patch
from types import SimpleNamespace
import unittest
import cv2
import numpy as np

from tools import lion_task_ui as vision

ROOT=Path(__file__).resolve().parents[1]
FIXTURES=ROOT/'tests/fixtures/lion_acquisition'


def image(name,x,y):
    frame=np.zeros((1353,720,3),dtype=np.uint8)
    patch_image=cv2.imread(str(FIXTURES/f'{name}.png'))
    if patch_image is None:raise AssertionError('missing sanitized fixture '+name)
    h,w=patch_image.shape[:2];frame[y:y+h,x:x+w]=patch_image
    return frame


class LionUIReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from maa.controller import CustomController
        from maa.resource import Resource
        from maa.tasker import Tasker
        reject=lambda self,*args:False
        methods={name:reject for name in dir(CustomController) if getattr(getattr(CustomController,name),'__isabstractmethod__',False)}
        methods.update(connect=lambda self:True,request_uuid=lambda self:'lion-acquisition-replay',
                       screencap=lambda self:np.zeros((1353,720,3),dtype=np.uint8))
        cls.controller=type('LionReplayController',(CustomController,),methods)()
        Tasker.set_log_dir('');Tasker.set_stdout_level(2)
        cls.resource,cls.tasker=Resource(),Tasker()
        assert cls.controller.post_connection().wait().succeeded
        assert cls.resource.post_bundle(ROOT/'assets/resource').wait().succeeded
        assert cls.tasker.bind(cls.resource,cls.controller)

    @classmethod
    def tearDownClass(cls):
        for obj in (cls.tasker,cls.resource,cls.controller):obj.__del__();obj._handle=None

    def ui(self,frame):
        ui=object.__new__(vision.LionUI);ui.tasker=self.tasker;ui.frame=Mock(return_value=frame)
        return ui

    def test_real_maa_badge_ocr_reads_all_nine_slots_without_reward_confusion(self):
        ui=self.ui(image('board_badges',75,345))
        self.assertEqual({slot for slot,*_ in ui.visible_slots(ui.frame())},set(range(61,70)))

    def test_real_maa_detail_and_owned_controls_match_user_screenshots(self):
        ui=self.ui(image('unclaimed_detail',50,875))
        self.assertEqual(ui.text(vision.DETAIL_SLOT),'66')
        self.assertEqual(ui.text(vision.ACCEPT),'接受')
        ui.frame.return_value=image('owned_buttons',125,1018)
        self.assertEqual(ui.text(vision.UPGRADE),'升级')
        self.assertEqual(ui.text(vision.REFRESH),'刷新任务')

    def test_accept_preflight_checks_exact_condition_and_bells_before_badge_input(self):
        ui=self.ui(image('unclaimed_detail',50,875))
        self.assertEqual(ui.text(vision.DETAIL_TEXT),'收获96个甘蔗')
        self.assertEqual(ui.text(vision.DETAIL_BELLS),'21')
        ui.check_detail=Mock()
        task=dict(slot=66,text='收获96个甘蔗',bellReward=dict(count=22),isUpgraded=False,upgrader=None)
        with patch.object(vision,'post_click_normalized') as click, self.assertRaisesRegex(RuntimeError,'条件／铃铛'):
            ui.check_accept(task)
        click.assert_not_called()

    def test_reservation_tooltip_cannot_be_overridden_by_an_old_base_cache(self):
        ui=object.__new__(vision.LionUI);ui.check_detail=Mock();ui.controller=Mock();ui.frame=Mock()
        task=dict(slot=66,text='收获96个甘蔗',bellReward=dict(count=21),isUpgraded=False,upgrader=None)
        ui.text=Mock(side_effect=['收获96个甘蔗','21','接受','家族成员成员乙升级了任务'])
        click=Mock();click.return_value.wait.return_value.succeeded=True
        with patch.object(vision,'post_click_normalized',click),patch.object(vision.time,'sleep'):
            with self.assertRaises(vision.ReservationConflict):ui.check_accept(task)
        self.assertEqual(click.call_count,2)  # open and close tooltip, no acceptance

    def test_contribution_numeric_identity_requires_all_three_values_in_one_row(self):
        ui=object.__new__(vision.LionUI);ui.frame=Mock(return_value=np.zeros((1353,720,3),dtype=np.uint8))
        hit=SimpleNamespace(box=(0,100,20,30))
        result=SimpleNamespace(nodes=[SimpleNamespace(recognition=SimpleNamespace(filtered_results=[hit]))])
        ui.tasker=Mock();ui.tasker.post_recognition.return_value.wait.return_value.get.return_value=result
        ui.text=Mock(side_effect=['48','164'])
        ui.verify_period_row(dict(completed=2,points=164,selfLevel=48,identityTripleUnique=True))
        self.assertEqual(ui.text.call_count,2)
        ui.text=Mock(side_effect=['48','165']);ui.board_visible=Mock(return_value=False)
        with self.assertRaisesRegex(RuntimeError,'页面改变'):
            ui.verify_period_row(dict(completed=2,points=164,selfLevel=48,identityTripleUnique=True))

    def test_real_maa_refresh_panel_accepts_voucher_icon_despite_static_pearl_copy(self):
        frame=image('refresh_panel',40,220);ui=self.ui(frame);ui.click_label=Mock()
        for cost in (2,20):
            with self.subTest(cost=cost):ui.refresh(cost)
        self.assertEqual(ui.click_label.call_count,2)
        for y in (514,660):frame[y+31:y+81,475:530]=0
        ui.click_label.reset_mock()
        with self.assertRaisesRegex(RuntimeError,'礼券图标'):ui.refresh(20)
        ui.click_label.assert_not_called()

    def test_real_high_bell_warning_matches_both_buttons_and_current_amount(self):
        ui=self.ui(image('high_bell_hint',30,394))
        self.assertTrue(vision.high_bell_warning(ui.text(vision.HINT),51))
        self.assertFalse(vision.high_bell_warning(ui.text(vision.HINT),52))
        self.assertEqual(ui.text(vision.CANCEL),'取消')
        self.assertEqual(ui.text(vision.CONFIRM),'确认')

    def test_warning_cancels_star_but_confirms_known_nonstar_only(self):
        for kind,button in [('star_orders','取消'),('regular_harvest','确认')]:
            ui=object.__new__(vision.LionUI);ui.check_detail=Mock();ui.click_label=Mock()
            ui.text=Mock(side_effect=['当前任务铃铛为51，是高铃铛任务，确定要更换或刷新吗?','刷新出新的任务'])
            task=dict(slot=66,bellReward=dict(count=51),requirement=dict(kind=kind))
            result=ui.open_refresh(task)
            self.assertEqual(result,kind!='star_orders')
            self.assertEqual(ui.click_label.call_args_list[-1].args[0],button)

    def test_upgrade_confirmation_requires_exact_reward_cost_and_vouchers(self):
        task=dict(upgrade=dict(bellReward=dict(count=82)))
        ui=object.__new__(vision.LionUI);ui.click_label=Mock()
        for text in ('是否消耗80珍珠升级任务，奖励增加到82铃铛。','是否消耗81狮王争霸赛礼券升级任务，奖励增加到82铃铛。',
                     '是否消耗80狮王争霸赛礼券升级任务，奖励增加到84铃铛。'):
            ui.text=Mock(return_value=text)
            with self.subTest(text=text),self.assertRaises(RuntimeError):ui.upgrade(task,80)
        ui.click_label.assert_not_called()
        ui.text=Mock(return_value='是否消耗80狮王争霸赛礼券升级任务，升级后难度增加，奖励增加到82铃铛。')
        ui.upgrade(task,80);ui.click_label.assert_called_once_with('确认',vision.CONFIRM)

    def test_restart_cancels_known_unpaid_dialog_instead_of_confirming_or_navigating_blindly(self):
        task=dict(slot=66,bellReward=dict(count=51))
        ui=object.__new__(vision.LionUI);ui.click_label=Mock()
        ui.text=Mock(side_effect=['','当前任务铃铛为51，是高铃铛任务，确定要更换或刷新吗?'])
        ui.recover_prepared(task)
        ui.click_label.assert_called_once_with('取消',vision.CANCEL)
        ui.click_label.reset_mock()
        ui.text=Mock(return_value='刷新出新的任务');ui.board_visible=Mock(return_value=True)
        ui.recover_prepared(task)
        ui.click_label.assert_called_once_with('点击空白区域继续',(229,1202,228,40))

    def test_period_read_waits_for_two_equal_counter_values_before_returning(self):
        ui=object.__new__(vision.LionUI);ui.open_board=Mock();ui.click_label=Mock();ui.leave_board=Mock()
        ui.verify_period_row=Mock();ui.show_tasks=Mock()
        values=[{'family':{'period':dict(completed=n,season=126,selfUid=7,points=82*n)}} for n in (2,3,3,3)]
        with patch.object(vision.time,'sleep'):
            result=ui.read_period(Mock(side_effect=values))
        self.assertEqual(result['family']['period']['completed'],3)
        ui.show_tasks.assert_not_called()
        ui.leave_board.assert_called_once_with()

    def test_task_tab_retry_is_bounded_and_requires_a_live_detail_badge(self):
        ui=object.__new__(vision.LionUI);ui.click_label=Mock();ui.task_board_visible=Mock(side_effect=[False,False,True])
        ui.text=Mock(side_effect=['','84']);ui.show_tasks()
        self.assertEqual(ui.click_label.call_count,2)
        ui.click_label.reset_mock();ui.text=Mock(return_value='');ui.task_board_visible=Mock(return_value=False)
        with self.assertRaisesRegex(RuntimeError,'页签切换'):ui.show_tasks()
        self.assertEqual(ui.click_label.call_count,2)

    def test_contribution_avatar_level_is_not_task_board_proof(self):
        ui=object.__new__(vision.LionUI);ui.board_visible=Mock(return_value=True)
        ui.text=Mock(return_value='完成任务')
        self.assertFalse(ui.task_board_visible())
        ui.text=Mock(return_value='可领取任务次数17/20')
        self.assertTrue(ui.task_board_visible())

    def test_failed_board_back_stops_before_family_or_farm_input(self):
        ui=object.__new__(vision.LionUI);ui.board_visible=Mock(return_value=True);ui.back=Mock()
        with self.assertRaisesRegex(RuntimeError,'任务板关闭'):ui.leave_board()
        ui.back.assert_called_once_with()

    def test_back_arrow_matches_dark_backdrop_and_animation_size_but_not_blank_area(self):
        reference=cv2.imread(str(ROOT/'assets/resource/image/lion/back.png'))
        for scale in (1,1.15):
            with self.subTest(scale=scale):
                sample=cv2.resize(reference,None,fx=scale,fy=scale)
                background=np.max(np.abs(sample.astype(np.int16)-sample[0,0]),axis=2)<=25
                sample[background]=[56,71,76]
                frame=np.zeros((1353,720,3),dtype=np.uint8)
                h,w=sample.shape[:2];frame[1230:1230+h,20:20+w]=sample
                hit=vision.back_hit(frame)
                self.assertIsNotNone(hit)
                self.assertLess(abs(hit[0]-(20+w//2)),3)
                self.assertLess(abs(hit[1]-(1230+h//2)),3)
        self.assertIsNone(vision.back_hit(np.zeros((1353,720,3),dtype=np.uint8)))


if __name__=='__main__':unittest.main()

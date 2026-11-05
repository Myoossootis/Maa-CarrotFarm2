from pathlib import Path
from unittest.mock import Mock, patch
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


if __name__=='__main__':unittest.main()

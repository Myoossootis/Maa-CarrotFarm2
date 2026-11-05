"""Bounded Lion King navigation through the existing Maa safe-input helpers."""
from pathlib import Path
import time
import cv2
from maa.controller import Win32Controller
from maa.define import MaaWin32InputMethodEnum, MaaWin32ScreencapMethodEnum
from maa.resource import Resource
from maa.tasker import Tasker

from tools.capture_current_frame_with_maa import find_window
from tools.scan_normal_orders import screenshot
from tools.maa_frame_normalize import measure_controller_offset, build_runtime_resource, post_click_normalized
from tools.ensure_ranch_feed import recognized_text
from tools.open_phone import open_phone, phone_menu_visible


class LionUI:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.controller = Win32Controller(hWnd=find_window(), screencap_method=MaaWin32ScreencapMethodEnum.FramePool,
            mouse_method=MaaWin32InputMethodEnum.PostMessage, keyboard_method=MaaWin32InputMethodEnum.PostMessage)
        self.resource, self.tasker = Resource(), Tasker()
        self.controller.set_screenshot_target_short_side(720)
        if not self.controller.post_connection().wait().succeeded:
            raise RuntimeError('Maa connection failed')
        bundle = build_runtime_resource(measure_controller_offset(self.controller))
        if not self.resource.post_bundle(bundle).wait().succeeded or not self.tasker.bind(self.resource, self.controller):
            raise RuntimeError('Maa resource binding failed')
        self.step = 0

    def frame(self, label='frame'):
        frame = screenshot(self.controller)
        self.step += 1
        cv2.imwrite(str(self.folder / f'{self.step:03d}_{label}.png'), frame)
        return frame

    def text(self, roi, frame=None):
        return recognized_text(self.tasker, self.frame() if frame is None else frame, roi, expected=(r'.+',))

    def click_label(self, expected, roi):
        frame = self.frame('before_click')
        actual = self.text(roi, frame)
        if actual != expected:
            raise RuntimeError(f'狮王页面未确认：需要{expected!r}，读到{actual!r}；未点击')
        x, y, width, height = roi
        if not post_click_normalized(self.controller, x + width // 2, y + height // 2).wait().succeeded:
            raise RuntimeError('狮王点击结果不明，禁止重发')
        time.sleep(.7)
        self.frame('after_click')

    def close(self):
        for value in (self.tasker, self.resource, self.controller):
            value.__del__()
            value._handle = None

    def board_visible(self):
        return self.text((235, 205, 260, 75)) == '狮王争霸'

    def open_board(self):
        if self.board_visible():
            return
        frame = self.frame()
        if self.text((310, 190, 100, 55), frame) != '功能':
            open_phone(self.controller)
            if not phone_menu_visible(self.frame()):
                raise RuntimeError('手机菜单未确认')
            self.click_label('家族', (146, 965, 110, 45))
        # The two tabs are stable, regardless of family name.
        self.click_label('功能', (309, 199, 101, 43))
        self.click_label('狮王争霸', (297, 441, 129, 41))
        if not self.board_visible():
            raise RuntimeError('狮王任务板打开未确认，不重发导航')

    def leave_board(self):
        if self.board_visible():
            self.click_label('点击空白区域继续', (229, 1202, 228, 40))
        frame = self.frame()
        if self.text((309, 199, 101, 43), frame) == '功能':
            reference = cv2.imread(str(Path(__file__).resolve().parents[1] / 'assets/resource/image/lion/back.png'))
            patch = frame[1245:1325, 12:88]
            if reference is None or patch.shape != reference.shape or cv2.matchTemplate(patch, reference, cv2.TM_CCOEFF_NORMED).max() < .93:
                raise RuntimeError('家族返回按钮未确认')
            if not post_click_normalized(self.controller, 45, 1285).wait().succeeded:
                raise RuntimeError('家族返回结果不明')
            time.sleep(.5)
        if phone_menu_visible(self.frame()):
            self.click_label('点击空白区域继续', (229, 1202, 228, 40))
        from tools.enter_farm import enter_farm
        enter_farm(self.controller)

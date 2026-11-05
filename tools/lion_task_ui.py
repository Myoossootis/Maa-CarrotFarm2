"""Bounded Lion King navigation through the existing Maa safe-input helpers."""
from pathlib import Path
import time
import cv2
from maa.pipeline import JOCR, JRecognitionType
from maa.controller import Win32Controller
from maa.define import MaaWin32InputMethodEnum, MaaWin32ScreencapMethodEnum
from maa.resource import Resource
from maa.tasker import Tasker

from tools.capture_current_frame_with_maa import find_window
from tools.scan_normal_orders import screenshot
from tools.maa_frame_normalize import measure_controller_offset, build_runtime_resource, post_click_normalized, post_swipe_normalized
from tools.ensure_ranch_feed import recognized_text
from tools.open_phone import open_phone, phone_menu_visible

ACCEPT = (245, 1025, 235, 65)
UPGRADE = (142, 1025, 162, 42)
REFRESH = (413, 1025, 162, 58)
DETAIL_SLOT = (56, 879, 75, 44)
HINT = (82, 470, 562, 151)
CANCEL = (100, 672, 180, 55)
CONFIRM = (435, 672, 180, 55)
REFRESH_TITLE = (132, 244, 473, 65)


def high_bell_warning(text, bells):
    return (f'当前任务铃铛为{bells}' in text and '高铃铛任务' in text
            and '更换或刷新' in text)


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

    def visible_slots(self, frame):
        result = []
        # Slot badges occupy the right edge of each card. Bell rewards are to
        # the left, and must never be mistaken for a slot with the same number.
        for column, x in enumerate((208, 397, 586)):
            patch = cv2.resize(frame[345:812, x:x+61], None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
            detail = self.tasker.post_recognition(JRecognitionType.OCR,
                JOCR(roi=(0, 0, patch.shape[1], patch.shape[0]), expected=[r'^\d{1,2}$'], threshold=.8), patch).wait().get()
            recognition = detail.nodes[0].recognition if detail and detail.nodes else None
            for hit in recognition.filtered_results if recognition else []:
                text = hit.text.strip()
                if text.isdecimal() and 1 <= int(text) <= 90 and len(hit.box) == 4:
                    _, y, _, height = map(int, hit.box)
                    y, height = 345+y//2, height//2
                    if 345 <= y < 763:
                        if (int(text)-1) % 3 == column:
                            result.append((int(text), 177 + 185*column, y + height//2 + 65))
        return result

    def select_slot(self, slot):
        for _ in range(18):
            frame = self.frame('select_slot')
            if not self.board_visible():
                raise RuntimeError('任务板未确认，禁止滚动或选择')
            if self.text(DETAIL_SLOT, frame) == str(slot):
                return
            visible = self.visible_slots(frame)
            hits = [hit for hit in visible if hit[0] == slot]
            if len(hits) == 1:
                _, x, y = hits[0]
                if not post_click_normalized(self.controller, x, y).wait().succeeded:
                    raise RuntimeError('槽位选择结果不明')
                time.sleep(.6)
                if self.text(DETAIL_SLOT) != str(slot):
                    raise RuntimeError('任务详情槽位未确认，不重发选择')
                return
            if not visible or len(hits) > 1:
                raise RuntimeError('当前可见槽位识别不完整，不猜测位置')
            top, bottom = (790, 450) if slot > max(h[0] for h in visible) else (450, 790)
            if not post_swipe_normalized(self.controller, 360, top, 360, bottom, 450).wait().succeeded:
                raise RuntimeError('任务板滚动结果不明')
            time.sleep(.6)
        raise RuntimeError('有限滚动内未定位任务槽位')

    def check_detail(self, slot):
        if not self.board_visible() or self.text(DETAIL_SLOT) != str(slot):
            raise RuntimeError('动作前任务板／详情槽位未确认')

    def accept(self, task):
        self.check_detail(task['slot'])
        self.click_label('接受', ACCEPT)

    def check_accept(self, task):
        self.check_detail(task['slot'])
        if self.text(ACCEPT) != '接受':
            raise RuntimeError('接受按钮未确认，未记录支付／接取意图')

    def check_upgrade_hint(self, task, cost):
        text = self.text(HINT)
        bells = task['upgrade']['bellReward']['count']
        if (f'消耗{cost}狮王争霸赛礼券升级任务' not in text or f'奖励增加到{bells}铃铛' not in text
                or '珍珠' in text):
            raise RuntimeError('升级确认费用／奖励／礼券文案不符，未确认')

    def prepare_upgrade(self, task, cost):
        self.check_detail(task['slot'])
        self.click_label('升级', UPGRADE)
        self.check_upgrade_hint(task, cost)

    def upgrade(self, task, cost):
        self.check_upgrade_hint(task, cost)
        self.click_label('确认', CONFIRM)

    def cancel_star_warning(self, task):
        if high_bell_warning(self.text(HINT), task['bellReward']['count']):
            self.click_label('取消', CANCEL)
        self.check_detail(task['slot'])

    def open_refresh(self, task):
        if task['requirement']['kind'] not in ('regular_harvest', 'harvest', 'party_takeout', 'star_orders'):
            raise RuntimeError('任务类型未知，不确认高铃铛刷新提示')
        self.check_detail(task['slot'])
        self.click_label('刷新任务', REFRESH)
        hint = self.text(HINT)
        if high_bell_warning(hint, task['bellReward']['count']):
            if task['requirement']['kind'] == 'star_orders':
                self.click_label('取消', CANCEL)
                return False
            self.click_label('确认', CONFIRM)
        if self.text(REFRESH_TITLE) != '刷新出新的任务':
            raise RuntimeError('刷新档位面板未确认，未支付')
        return True

    def check_refresh(self, cost):
        if cost not in (2, 20) or self.text(REFRESH_TITLE) != '刷新出新的任务':
            raise RuntimeError('仅支持已选2／20礼券档位')
        y = 514 if cost == 2 else 660
        frame = self.frame('refresh_currency')
        description = self.text((84, y-4, 345, 74), frame)
        # The static description always says pearls. The actual button icon
        # changes to vouchers when funded; require that icon AND fresh balance.
        if f'消耗{cost}' not in description or '刷新' not in description:
            raise RuntimeError('刷新费用描述不符，禁止支付')
        reference = cv2.imread(str(Path(__file__).resolve().parents[1] / 'assets/resource/image/lion/voucher.png'))
        area = frame[y+31:y+81, 475:530]
        if reference is None or cv2.matchTemplate(area, reference, cv2.TM_CCOEFF_NORMED).max() < .93:
            raise RuntimeError('刷新按钮不是已核对的礼券图标，禁止珍珠支付')
        if self.text((526, y+43, 65, 37), frame) != str(cost):
            raise RuntimeError('刷新按钮费用未确认')

    def refresh(self, cost):
        self.check_refresh(cost)
        y = 514 if cost == 2 else 660
        self.click_label('刷新', (484, y-5, 126, 43))

    def prepare_quit(self, task):
        self.check_detail(task['slot'])
        frame = self.frame('quit_button')
        reference = cv2.imread(str(Path(__file__).resolve().parents[1] / 'assets/resource/image/lion/quit.png'))
        area = frame[880:935, 605:660]
        if reference is None or min(area.shape[:2]) < max(reference.shape[:2]):
            raise RuntimeError('退出按钮模板不可用')
        _, score, _, point = cv2.minMaxLoc(cv2.matchTemplate(area, reference, cv2.TM_CCOEFF_NORMED))
        if score < .93:
            raise RuntimeError('退出按钮未确认')
        if not post_click_normalized(self.controller, 605+point[0]+reference.shape[1]//2,
                                     880+point[1]+reference.shape[0]//2).wait().succeeded:
            raise RuntimeError('退出点击结果不明，不重发')
        time.sleep(.6)
        hint = self.text(HINT)
        if '退出任务有' not in hint or '冷却时间' not in hint or '是否确认退出' not in hint:
            raise RuntimeError('退出确认文案未确认，不重发')

    def quit_task(self, task):
        hint = self.text(HINT)
        if '退出任务有' not in hint or '冷却时间' not in hint or '是否确认退出' not in hint:
            raise RuntimeError('退出确认文案改变，未确认')
        self.click_label('确认', CONFIRM)

    def board_visible(self):
        return self.text((235, 205, 260, 75)) == '狮王争霸'

    def recover_prepared(self, task):
        """Cancel a recognized, unpaid dialog; never confirm payment on restart."""
        if task is None:
            return
        if self.text(REFRESH_TITLE) == '刷新出新的任务':
            self.click_label('点击空白区域继续', (229, 1202, 228, 40))
            if not self.board_visible():
                raise RuntimeError('刷新档位退出未确认，不继续导航')
            return
        hint = self.text(HINT)
        if high_bell_warning(hint, task['bellReward']['count']):
            self.click_label('取消', CANCEL)
        elif '升级任务' in hint:
            self.check_upgrade_hint(task, (task.get('upgradeCost') or {}).get('count'))
            self.click_label('取消', CANCEL)
        elif '退出任务有' in hint and '冷却时间' in hint and '是否确认退出' in hint:
            self.click_label('取消', CANCEL)

    def open_board(self):
        hint = self.text(HINT)
        if (self.text(REFRESH_TITLE) == '刷新出新的任务' or '高铃铛任务' in hint
                or '升级任务' in hint or '是否确认退出' in hint):
            raise RuntimeError('仍在狮王确认／档位面板，不执行其他导航')
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

"""Bounded Lion King navigation through the existing Maa safe-input helpers."""
from pathlib import Path
import time
import re
import cv2
import numpy as np
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
DETAIL_TEXT = (182, 901, 421, 80)
DETAIL_BELLS = (403, 975, 104, 36)
RESERVATION_HINT = (48, 710, 610, 232)
HINT = (82, 470, 562, 151)
CANCEL = (100, 672, 180, 55)
CONFIRM = (435, 672, 180, 55)
REFRESH_TITLE = (132, 244, 473, 65)
TASK_TAB = (86, 1134, 90, 50)
SCORE_TAB = (324, 1134, 91, 50)


def high_bell_warning(text, bells):
    return (f'当前任务铃铛为{bells}' in text and '高铃铛任务' in text
            and '更换或刷新' in text)


class ReservationConflict(RuntimeError):
    """Positive reservation evidence; caller may skip this slot without taking it."""


def back_hit(frame):
    reference=cv2.imread(str(Path(__file__).resolve().parents[1]/'assets/resource/image/lion/back.png'))
    area=frame[1220:1345,0:112]
    if reference is None or min(area.shape[:2])<max(reference.shape[:2]):
        return None
    # The same arrow is drawn over family beige or a dark popup backdrop.
    # Match its foreground, excluding the original flat beige background.
    mask=(np.max(np.abs(reference.astype(np.int16)-reference[0,0]),axis=2)>25).astype(np.uint8)*255
    hits=[]
    for scale in (.9,.95,1,1.05,1.1,1.15,1.2):
        sample=cv2.resize(reference,None,fx=scale,fy=scale)
        foreground=cv2.resize(mask,(sample.shape[1],sample.shape[0]),interpolation=cv2.INTER_NEAREST)
        _,score,_,point=cv2.minMaxLoc(cv2.matchTemplate(area,sample,cv2.TM_CCORR_NORMED,mask=foreground))
        if np.isfinite(score):
            hits.append((score,point[0]+sample.shape[1]//2,1220+point[1]+sample.shape[0]//2))
    best=max(hits,default=(0,0,0))
    return best[1:] if best[0]>=.97 else None


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
        point = (x + width // 2, y + height // 2)
        if not post_click_normalized(self.controller, *point).wait().succeeded:
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
        frame = self.frame('accept_identity')
        compact = lambda value: re.sub(r'[\s，,。；;：:、]', '', value)
        if (not task.get('text') or compact(self.text(DETAIL_TEXT, frame)) != compact(task['text'])
                or self.text(DETAIL_BELLS, frame) != str(task['bellReward']['count'])):
            raise RuntimeError('接受前任务条件／铃铛与当前候选不一致，未接取')
        if self.text(ACCEPT, frame) != '接受':
            raise RuntimeError('接受按钮未确认，未记录支付／接取意图')
        # Clicking the slot badge only toggles the game's reservation tooltip.
        # A stale base cache must not authorize taking another member's upgrade.
        x,y,w,h = DETAIL_SLOT
        if not post_click_normalized(self.controller, x+w//2, y+h//2).wait().succeeded:
            raise RuntimeError('升级人提示点击结果不明，未接取')
        time.sleep(.4)
        hint = self.text(RESERVATION_HINT)
        match = re.search(r'家族成员(.+?)升级了任务', re.sub(r'\s+', '', hint))
        upgrader = task.get('upgrader')
        if match:
            if not post_click_normalized(self.controller, x+w//2, y+h//2).wait().succeeded:
                raise RuntimeError('升级人提示关闭结果不明，未接取')
            time.sleep(.4)
            if not upgrader or not upgrader.get('name') or match[1] != upgrader['name']:
                raise ReservationConflict('当前任务出现他人或未知升级人预留，未接取')
        elif task.get('isUpgraded') or upgrader:
            raise RuntimeError('已升级候选缺少本人升级提示证明，未接取')
        self.check_detail(task['slot'])

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

    def task_board_visible(self):
        return self.board_visible() and re.search(r'\d+/\d+',self.text((82,823,535,49))) is not None

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

    def read_period(self, read):
        from tools.decoded_runtime import DecodeError
        self.open_board()
        self.click_label('贡献', SCORE_TAB)
        previous = None
        second = None
        for _ in range(4):
            try:
                candidate = read()
            except DecodeError:
                previous = None
                time.sleep(.5)
                continue
            value = candidate['family'].get('period')
            if value is not None and value == previous:
                second = candidate
                break
            previous = value
            time.sleep(.5)
        if second is None:
            raise RuntimeError('当前赛季本人贡献次数未取得两次一致读取')
        self.verify_period_row(second['family']['period'])
        self.leave_board()
        after = read()
        if after['family'].get('period') != second['family']['period']:
            raise RuntimeError('返回农场后本人赛季计数改变，重新对账后再执行')
        after['lionCounterUi']={k:after['family']['period'][k] for k in ('season','selfUid','completed','points')}
        return after

    def show_tasks(self):
        if self.task_board_visible():
            return
        for _ in range(2):
            self.click_label('任务', TASK_TAB)
            badge=self.text(DETAIL_SLOT)
            if self.task_board_visible() and badge.isdecimal() and 1<=int(badge)<=90:
                return
        raise RuntimeError('任务页签切换未取得详情槽位证明，不继续导航')

    def verify_period_row(self, counter):
        name=counter.get('selfName')
        numeric=counter.get('identityTripleUnique') is True and type(counter.get('selfLevel')) is int
        if not numeric and (not name or counter.get('selfNameUnique') is not True):
            raise RuntimeError('本人贡献昵称缺失或不唯一，不能猜测列表行')
        for _ in range(40):
            frame=self.frame('own_contribution_row')
            if numeric:
                patch=cv2.resize(frame[350:1100,420:478],None,fx=2,fy=2,interpolation=cv2.INTER_CUBIC)
                detail=self.tasker.post_recognition(JRecognitionType.OCR,JOCR(roi=(0,0,patch.shape[1],patch.shape[0]),
                    expected=['^'+str(counter['completed'])+'$'],threshold=.8),patch).wait().get()
                recognition=detail.nodes[0].recognition if detail and detail.nodes else None
                matches=[]
                for hit in recognition.filtered_results if recognition else []:
                    _,y,_,height=map(int,hit.box);center=350+(y+height//2)//2
                    level=self.text((90,center-78,40,38),frame)
                    points=self.text((551,center-17,75,34),frame)
                    if level==str(counter['selfLevel']) and points==str(counter['points']):matches.append(center)
                if len(matches)==1:return
                if len(matches)>1:raise RuntimeError('当前可见贡献行身份组合重复，不能猜测本人')
                if not self.board_visible():raise RuntimeError('贡献页面改变，不继续滚动')
                if not post_swipe_normalized(self.controller,530,980,530,500,700).wait().succeeded:
                    raise RuntimeError('贡献列表滚动结果不明')
                time.sleep(1)
                continue
            strip=cv2.resize(frame[350:1100,170:380],None,fx=2,fy=2,interpolation=cv2.INTER_CUBIC)
            detail=self.tasker.post_recognition(JRecognitionType.OCR,JOCR(
                roi=(0,0,strip.shape[1],strip.shape[0]),expected=['^'+re.escape(name)+'$'],threshold=.6),strip).wait().get()
            recognition=detail.nodes[0].recognition if detail and detail.nodes else None
            hits=list(recognition.filtered_results) if recognition else []
            if len(hits)==1:
                _,y,_,height=map(int,hits[0].box)
                center=350+(y+height//2)//2
                count=self.text((420,center,58,35),frame)
                points=self.text((551,center,75,35),frame)
                if count!=str(counter['completed']) or points!=str(counter['points']):
                    raise RuntimeError(f'本人贡献行次数／积分与当前解码不一致：{count}/{points}')
                return
            if len(hits)>1 or not self.board_visible():
                raise RuntimeError('本人贡献行不唯一或页面改变，未继续滚动')
            if not post_swipe_normalized(self.controller,530,980,530,500,700).wait().succeeded:
                raise RuntimeError('贡献列表滚动结果不明')
            time.sleep(1)
        raise RuntimeError('有限滚动内未识别本人贡献行，不能按旧计数继续')

    def back(self):
        frame=self.frame('back_button')
        point=back_hit(frame)
        if point is None:
            raise RuntimeError('家族返回按钮未确认')
        if not post_click_normalized(self.controller,*point).wait().succeeded:
            raise RuntimeError('家族返回结果不明')
        time.sleep(.7)

    def leave_board(self):
        if self.board_visible():
            self.back()
        if self.board_visible():
            raise RuntimeError('任务板关闭未确认，不继续点击其他页面')
        frame = self.frame()
        if self.text((309, 199, 101, 43), frame) == '功能':
            self.back()
        if phone_menu_visible(self.frame()):
            self.click_label('点击空白区域继续', (229, 1202, 228, 40))
        from tools.enter_farm import enter_farm
        enter_farm(self.controller)

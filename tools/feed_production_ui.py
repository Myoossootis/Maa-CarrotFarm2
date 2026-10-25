"""Vision and background input for the dedicated feed-production loop."""
from __future__ import annotations

import re
import time
import json
from datetime import datetime

import cv2
try:
    from . import decoded_runtime as decoded
except ImportError:
    import decoded_runtime as decoded
from maa.resource import Resource
from maa.tasker import Tasker

try:
    from . import configure_field_employee as employees
    from .atom_cli_common import connect_controller, release_controller
    from .ensure_ranch_feed import (open_mill, read_mill_state, mill_remaining_seconds,
                                   close_mill_popup, recognized_text, locate_scaled,
                                   _save_navigation_frame)
    from .maa_frame_normalize import post_swipe_normalized
    from .order_template_click import template_hit
    from .enter_farm import enter_farm
    from .task_control import check_cancelled
    from .run_feed_production import ITEMS, MUTATION_RULES, all_crops_complete, combine_crop_progress
except ImportError:
    import configure_field_employee as employees
    from atom_cli_common import connect_controller, release_controller
    from ensure_ranch_feed import (open_mill, read_mill_state, mill_remaining_seconds,
                                  close_mill_popup, recognized_text, locate_scaled,
                                  _save_navigation_frame)
    from maa_frame_normalize import post_swipe_normalized
    from order_template_click import template_hit
    from enter_farm import enter_farm
    from task_control import check_cancelled
    from run_feed_production import ITEMS, MUTATION_RULES, all_crops_complete, combine_crop_progress

IMAGES = employees.IMAGES


def text(tasker, frame, roi):
    return recognized_text(tasker, frame, roi, (r'.*',))


def integer(value):
    value = value.strip().replace('O', '0').replace('o', '0')
    return int(value) if re.fullmatch(r'\d+', value) else None


def fraction(value, expected):
    pairs = re.findall(r'(\d+)\s*/\s*(\d+)', value)
    valid = {(int(a), int(b)) for a, b in pairs if int(b) > 0 and (expected is None or int(b) == expected)}
    return next(iter(valid)) if len(valid) == 1 else None


def mill_capacity(value):
    maximum = re.search(r'最大生产次数[:：]?(\d+)', value)
    speed = re.search(r'生产速度[:：]?((?:\d+(?:小时|时|分|秒))+)', value)
    if not maximum or not speed:
        return None
    seconds = mill_remaining_seconds('预计可生产' + speed[1])
    if seconds is None or seconds <= 0 or not 1 <= int(maximum[1]) <= 99:
        return None
    return int(maximum[1]), seconds


def hit(frame, filename, roi, threshold=.86, scales=(1.0,)):
    image = cv2.imread(str(IMAGES / filename))
    if image is None:
        raise RuntimeError(f'缺少饲料循环识别模板：{filename}')
    return locate_scaled(frame, image, scales, threshold, roi)


def mutation_hit(frame, rule, header_y):
    candidate = hit(frame, rule['template'].removeprefix('production/'),
                    (35, header_y + 90, 400, 115), .88, scales=(.9, 1.0, 1.1))
    if candidate and rule['name'] == '红皮土豆':
        # Shape alone cross-matches an ordinary yellow potato (~0.88).
        # Require the actual red body as well as the icon geometry.
        _, x, y, w, h = candidate
        hsv = cv2.cvtColor(frame[y:y+h,x:x+w], cv2.COLOR_BGR2HSV)
        red = cv2.inRange(hsv, (0,120,80), (15,255,255))
        if cv2.countNonZero(red) / red.size < .45:
            return None
    return candidate


def refill_button(tasker, frame):
    raw = text(tasker, frame, (420, 818, 210, 75))
    label = re.fullmatch(r'补货(?:(\d+)次)?', raw)
    patch = frame[805:910, 405:635]
    green = cv2.inRange(cv2.cvtColor(patch, cv2.COLOR_BGR2HSV), (25,80,120), (100,255,255))
    if label is None:
        raise RuntimeError(f'磨坊补货按钮文字不可读：{raw!r}')
    count = int(label[1]) if label[1] is not None else 1
    enabled = cv2.countNonZero(green) / green.size >= .4
    return count, enabled, raw


def checked_full(frame):
    roi = (565, 910, 60, 60)
    yes = hit(frame, 'mill_full_checked.png', roi, .8)
    no = hit(frame, 'mill_full_unchecked.png', roi, .8)
    if yes and (not no or yes[0] > no[0] + .05):
        return True
    if no and (not yes or no[0] > yes[0] + .05):
        return False
    return None


class FeedProductionUI:
    def __enter__(self):
        self.controller = connect_controller()
        self.resource, self.tasker = Resource(), Tasker()
        try:
            self.tasker.set_save_draw(False)
            self.tasker.set_save_on_error(False)
            if not self.resource.post_bundle(employees.RESOURCE).wait().succeeded:
                raise RuntimeError('饲料循环资源加载失败')
            if not self.tasker.bind(self.resource, self.controller):
                raise RuntimeError('饲料循环 Maa 绑定失败')
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *args):
        for obj in (self.tasker, self.resource):
            obj.__del__()
            obj._handle = None
        release_controller(self.controller)

    def frame(self):
        check_cancelled()
        self.last_frame = employees.screenshot(self.controller)
        return self.last_frame

    def click(self, x, y):
        employees.click(self.controller, int(x), int(y))
        time.sleep(.35)

    def swipe(self, x, y1, y2):
        if not post_swipe_normalized(self.controller, x, y1, x, y2, 550).wait().succeeded:
            raise RuntimeError('饲料循环滚动失败')
        time.sleep(.5)

    def audit(self, stage, frame, **values):
        record = dict(time=datetime.now().astimezone().isoformat(), stage=stage, **values)
        print('[饲料识别] ' + json.dumps(record, ensure_ascii=False), flush=True)
        folder = getattr(self, 'evidence_dir', None)
        if folder is not None:
            _save_navigation_frame(folder / f'{stage}_latest.png', frame)
            if stage in {'crop_collect_before', 'crop_collect_after', 'mill_collect_before',
                         'mill_collect_after', 'mill_refill_before', 'mill_refilled', 'plan_arranged'}:
                stamp = datetime.now().strftime('%Y%m%dT%H%M%S%f')
                _save_navigation_frame(folder / f'{stage}_{stamp}.png', frame)
            with (folder / 'vision.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + '\n')

    def wait_on_farm(self):
        employees.close_employee_menu(self.controller)
        enter_farm(self.controller)
        self.tasker.clear_cache()

    def save_failure(self, path):
        # Cancellation must not suppress the original error by taking another
        # cancellable screenshot. Keep the last frame as fallback if available.
        try:
            _save_navigation_frame(path, employees.screenshot(self.controller))
        except BaseException:
            if getattr(self, 'last_frame', None) is not None:
                _save_navigation_frame(path, self.last_frame)

    def inventory(self):
        self.wait_on_farm()
        if decoded.enabled():
            return decoded.item_count(19)
        frame = self.frame()
        entry = hit(frame, 'feed_backpack_entry.png', (590, 1135, 130, 115), .86)
        if entry is None:
            raise RuntimeError('背包入口未识别，未点击')
        _, x, y, w, h = entry
        self.click(x + w // 2, y + h // 2)
        time.sleep(.5)
        if template_hit(self.frame(), 'VerifyBackpackOpened') is None:
            raise RuntimeError('背包未打开')
        # Materials (house) tab, never the sell/upgrade buttons.
        self.click(230, 420)
        self.swipe(360, 520, 870)
        frame = self.frame()
        if hit(frame, 'feed_material_tab.png', (155, 350, 145, 110)) is None:
            raise RuntimeError('背包物资页未确认，不能推断饲料数量')
        bag = hit(frame, 'feed_inventory_icon.png', (65, 475, 590, 450))
        if bag is None:
            # Feed is the first material in the game's stable item order. Only
            # a confirmed top-of-materials page may prove absence/zero.
            another_material = hit(frame, 'feed_material_empty_anchor.png', (70, 470, 590, 130))
            # Completely empty material inventory is also a valid zero. The
            # selected-tab anchor above rules out a not-yet-opened backpack.
            grid = frame[480:910, 75:645]
            background = cv2.inRange(grid, (195, 235, 240), (225, 255, 255))
            blank = cv2.countNonZero(background) / background.size > .995
            if another_material is None and not blank:
                raise RuntimeError('物资页未找到饲料，也无法确认零库存')
            confirm = self.frame()
            if hit(confirm, 'feed_inventory_icon.png', (65, 475, 590, 450), .65) is not None:
                raise RuntimeError('疑似饲料图标，不能推断为零库存')
            count = 0
            self.audit('inventory_zero', confirm, count=0, empty_grid=blank,
                       other_material=another_material)
        else:
            _, x, y, w, h = bag
            self.click(x + w // 2, y + h // 2)
            detail = self.frame()
            detail_text = text(self.tasker, detail, (15, 80, 290, 350))
            owned = re.search(r'已拥有[:：]?(\d+)', detail_text)
            if '饲料' not in detail_text or owned is None:
                raise RuntimeError(f'饲料详情库存无法确认：{detail_text!r}')
            count = int(owned[1])
            confirmed_text = text(self.tasker, self.frame(), (15, 80, 290, 350))
            confirmed = re.search(r'已拥有[:：]?(\d+)', confirmed_text)
            if '饲料' not in confirmed_text or confirmed is None or int(confirmed[1]) != count:
                raise RuntimeError(f'饲料库存两次读数不一致：{detail_text!r} / {confirmed_text!r}')
            self.audit('inventory', detail, count=count, raw_text=detail_text)
        employees.close_employee_menu(self.controller)
        return count

    def mill_ready(self):
        employees.open_employee_list(self.controller)
        for _ in range(14):
            frame = self.frame()
            header = hit(frame, 'mill_employee_title.png', (140, 95, 220, 1030), .9)
            if header:
                _, _, y, _, _ = header
                if 100 <= y <= 830:
                    icon = hit(frame, 'mill_employee_feed.png', (35, y + 90, 110, 200))
                    if icon:
                        _, x, iy, w, h = icon
                        roi = (30, iy + h, 130, 42)
                        raw = text(self.tasker, frame, roi)
                        value = integer(raw)
                        confirmed = integer(text(self.tasker, self.frame(), roi))
                        if value is not None and value == confirmed:
                            self.mill_box = (x, iy, w, h)
                            self.audit('mill_employee', frame, count=value, icon=icon, raw_text=raw)
                            return value
            # Product icons consume drags that begin on them. The right card
            # border is outside both portraits and action buttons.
            self.swipe(690, 1050, 300)
        raise RuntimeError('未找到列表底部磨坊员工或饲料数量不可读')

    def collect_mill(self, expected):
        if expected <= 0 or self.mill_ready() != expected:
            raise RuntimeError('磨坊待领取数量变化，未点击')
        frame = self.frame()
        header = hit(frame, 'mill_employee_title.png', (140, 95, 220, 1030), .9)
        if header is None:
            raise RuntimeError('磨坊员工标题消失，未领取')
        button_y = header[2] + 292
        if employees.match(frame, 'collect_supplies_button.png', (115, button_y - 60, 240, 120)) < .75:
            raise RuntimeError('磨坊员工有饲料，但领取按钮未确认；未点击产物图标')
        self.audit('mill_collect_before', frame, count=expected, click=[230, button_y])
        self.click(230, button_y)
        # The worker counter falling to zero confirms this click even if farm
        # employees consume feed immediately after it enters the backpack.
        after = self.mill_ready()
        if after != 0:
            raise RuntimeError(f'磨坊领取未确认：{expected} -> {after}；不重试点击')
        self.audit('mill_collect_after', self.frame(), before=expected, after=after)
        return dict(before=expected, after=after)

    def arrange(self):
        self.field_action()
        result = employees.configure_items(self.controller, self.tasker, 'field_3', ITEMS, repeat=False)
        employees.verify_saved_plan(self.controller, self.tasker, 'field_3', ITEMS)
        self.audit('plan_arranged', self.frame(), result=result)
        return result

    def ensure_idle(self):
        self.field_action()
        empty = employees.plan_cleared_on_list(self.frame(), 'field_3')
        if not empty['cleared']:
            raise RuntimeError(f'农田3已有计划或待领物资，保留原计划：{empty}')
        return empty

    def field_action(self):
        employees.open_employee_list(self.controller)
        for _ in range(8):
            frame = self.frame()
            visible = employees.visible_slot_action(frame, 'field_3')
            if visible:
                return visible
            if employees.slot_match(frame, 'field_1')[0] >= .88:
                break
            self.swipe(690, 340, 1070)
        for _ in range(14):
            visible = employees.visible_slot_action(self.frame(), 'field_3')
            if visible:
                return visible
            self.swipe(690, 1000, 690)
        raise RuntimeError('未找到农田3可见操作按钮')

    def crop_progress(self):
        if decoded.enabled():
            data = decoded.snapshot()
            if decoded.plan_items('field_3', data) != ITEMS:
                raise decoded.DecodeError('Decoded field_3 plan differs from the feed-production assignment')
            progress = decoded.progress_items('field_3', data)
            entry = decoded.plan('field_3', data)
            mutations = {name: sum(row['count'] for row in entry['heldProducts'] + entry['accumulated'] if row['name'] == name)
                         for name in MUTATION_RULES}
            self.last_crop_evidence = dict(ordinary_progress=progress, mutations=mutations, data_source='decoded')
            return combine_crop_progress(progress, mutations)
        self.field_action()
        frame = self.frame()
        score, (_, y) = employees.slot_match(frame, 'field_3')
        if score < .88:
            raise RuntimeError('农田3未定位')
        progress = {}
        for name, expected in ITEMS.items():
            filename = employees.CROPS[name][0]
            icon = hit(frame, filename, (35, y + 90, 365, 115), .75,
                       scales=(.8, .9, 1.0, 1.1, 1.2))
            if not icon:
                raise RuntimeError(f'农田3缺少 {name} 图标；未领取')
            _, x, iy, w, h = icon
            progress[name] = fraction(text(self.tasker, frame, (x - 10, y + 190, w + 30, 50)), expected)
        if any(value is None for value in progress.values()):
            raise RuntimeError(f'农田3三项进度不可读：{progress}；未领取')
        mutations = {}
        for name, rule in MUTATION_RULES.items():
            icon = mutation_hit(frame, rule, y)
            if icon:
                _, x, _, w, _ = icon
                raw = text(self.tasker, frame, (x - 8, y + 190, w + 16, 50))
                quantity = integer(raw)
                if quantity is None:
                    raise RuntimeError(f'{name} 变异数量不可读：{raw!r}；未领取')
                mutations[name] = quantity
        effective = combine_crop_progress(progress, mutations)
        self.last_crop_evidence = dict(ordinary_progress=progress, mutations=mutations)
        self.audit('crop_progress', frame, progress=effective, slot_score=score,
                   **self.last_crop_evidence)
        return effective

    def collect_crops(self):
        progress = self.crop_progress()
        if not all_crops_complete(progress):
            raise RuntimeError('三种作物尚未全部完成，未领取')
        button_y, state = self.field_action()
        if state != 'collect_supplies':
            raise RuntimeError('农田3领取按钮未确认')
        self.audit('crop_collect_before', self.frame(), progress=progress, click=[230, button_y],
                   **self.last_crop_evidence)
        self.click(230, button_y)
        for _ in range(6):
            after = employees.plan_cleared_on_list(self.frame(), 'field_3')
            if after['cleared']:
                self.audit('crop_collect_after', self.frame(), clearance=after)
                return after
            time.sleep(.3)
        raise RuntimeError(f'农田3领取后空计划未确认：{after}；不重试点击')

    def refill(self):
        self.wait_on_farm()
        opened = open_mill(self.controller, self.tasker)
        frame = self.frame()
        state = checked_full(frame)
        if state is None:
            raise RuntimeError('磨坊一键补满勾选状态不可读')
        if state is False:
            self.click(596, 949)
            frame = self.frame()
        if checked_full(frame) is not True:
            raise RuntimeError('磨坊一键补满未确认勾选；未补货')
        self.audit('mill_full_checked', frame, checked=True)
        before = mill_remaining_seconds(read_mill_state(self.tasker, frame))
        if before is None:
            raise RuntimeError('补货前磨坊剩余时间不可读')
        # OCR may return labels and values in separate reading-order groups.
        # Read each value's row rather than joining unrelated numbers.
        maximum_text = text(self.tasker, frame, (570, 488, 90, 62))
        speed_text = text(self.tasker, frame, (490, 598, 175, 65))
        production_text = '最大生产次数:' + maximum_text + '生产速度:' + speed_text
        limits = mill_capacity(production_text)
        if limits is None:
            raise RuntimeError(f'磨坊生产容量/速度不可读：{production_text!r}')
        maximum, seconds = limits
        self.audit('mill_capacity', frame, maximum=maximum, seconds_per_batch=seconds,
                   remaining_seconds=before, raw_text=production_text)
        if before > (maximum - 1) * seconds:
            close_mill_popup(self.controller)
            return dict(status='waiting', reason='mill_full', remaining_seconds=before)
        batches, enabled, button_text = refill_button(self.tasker, frame)
        if batches == 0 or not enabled:
            close_mill_popup(self.controller)
            return dict(status='waiting', reason='refill_disabled', button_text=button_text)
        if not 1 <= batches <= maximum:
            raise RuntimeError(f'磨坊补货次数不合法：{button_text!r}')
        materials = {}
        for name, (x, required) in {'玉米': (95, 5), '甘蔗': (225, 4), '土豆': (355, 3)}.items():
            raw = text(self.tasker, frame, (x, 1090, 140, 40))
            reading = fraction(raw, required * batches)
            self.audit('mill_material', frame, product=name, raw_text=raw,
                       required=required * batches, progress=reading)
            if reading is None:
                raise RuntimeError(f'磨坊 {name} 材料数量不可读：{raw!r}，要求分母{required * batches}；未补货')
            if reading[0] < reading[1]:
                close_mill_popup(self.controller)
                return dict(status='waiting', reason='materials_short', product=name, progress=reading)
            materials[name] = reading
        self.audit('mill_refill_before', frame, materials=materials, remaining_seconds=before,
                   batches=batches, button_text=button_text)
        self.click(520, 858)
        after = mill_remaining_seconds(read_mill_state(self.tasker, self.frame()))
        if after is None or after <= before:
            raise RuntimeError(f'磨坊补货未确认：剩余秒数 {before} -> {after}；不重复补货')
        self.audit('mill_refilled', self.frame(), before_seconds=before, after_seconds=after)
        close_mill_popup(self.controller)
        return dict(status='started', before_seconds=before, after_seconds=after,
                    full_checked=True, batches=batches, navigation=opened['status'])

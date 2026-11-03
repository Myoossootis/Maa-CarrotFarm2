"""Read-only operations desk modeled on a sidebar HUD.

The window shows monitor, orders, run templates, settings, and diagnostics.
It never imports a game controller and never sends input.
"""

from __future__ import annotations

import argparse
import base64
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
import queue
import subprocess
import sys
import threading

try:
    from .decoded_dashboard import SNAPSHOT, local_time, read_live
except ImportError:
    from decoded_dashboard import SNAPSHOT, local_time, read_live


ROOT = Path(__file__).resolve().parents[1]
TITLE = '萝卜家园 · 操作台'
MONITOR_FRAME = ROOT / '.tmp/maa_monitor/live.png'
CAPTURE_SCRIPT = ROOT / 'tools/capture_current_frame_with_maa.py'
FONT = 'Microsoft YaHei UI'
BG = '#f3f6fb'
WHITE = '#ffffff'
INK = '#233047'
MUTED = '#718096'
NAV = '#4a5568'
NAV_BG = '#e8f1ff'
NAV_FG = '#2f6bff'
LINE = '#e3e9f2'
BLUE = '#3974e8'
STOP = '#e15b6a'

PAGES = (
    ('monitor', '实时监控'),
    ('harvest', '订单与生产'),
    ('autorun', '自动运行设置'),
    ('settings', '设置'),
)
DEV_PAGE = ('dev', '诊断步骤')
PAGE_TITLES = dict(PAGES + (DEV_PAGE,))
SHELL_ENTRIES = frozenset({'OpenFarmHud', 'RuntimePreflight'})
TEMPLATE_META = (
    dict(entry='RunDailyBatchOrders', label='聚会与外卖（自动调度）', kind='订单调度', loop='各日 12 次',
         detail='先做还能做的批次。两类都冷却时才等待。'),
    dict(entry='RunDailyPartyOrders', label='聚会订单', kind='订单', loop='各日 12 次，冷却时退出',
         detail='只跑聚会。冷却后让出队列，不等待另一类。'),
    dict(entry='RunDailyTakeoutOrders', label='外卖订单', kind='订单', loop='各日 12 次，冷却时退出',
         detail='只跑外卖。冷却后让出队列，不等待另一类。'),
    dict(entry='RunDailyNormalOrders', label='普通订单一轮', kind='订单', loop='每次一单',
         detail='普通订单没有每日上限。这个入口做完一单就停。'),
    dict(entry='RunNormalOrderLoop', label='普通订单3倍持续', kind='订单循环', loop='0 表示持续',
         detail='每单做满 3 次并确认领奖后继续。缺料先交给员工补产。'),
    dict(entry='CloseOfflineIncome', label='关闭离线收益', kind='面板', loop='一次',
         detail='确认离线收益后点空白关掉。不领取，不排产。'),
    dict(entry='RefreshOrder', label='刷新指定订单', kind='订单', loop='一张',
         detail='按位置更换一张订单并免费加速。位置在任务选项里填。'),
    dict(entry='ManualPlantingOnce', label='手动种植一次', kind='种植', loop='一次',
         detail='按所选农田、作物和格数种一次。结果未确认就停止。'),
    dict(entry='ManualPlantingPreflight', label='手动种植预检', kind='预检', loop='只预检',
         detail='只检查空位、等级和消耗。预检通过也不种植。'),
    dict(entry='RunFeedProduction', label='饲料生产（农田3）', kind='饲料', loop='直到停止',
         detail='占用农田3做饲料。放到任务队列最后，点停止才结束。'),
)
LINK_COLOR = {'未读取': '#9aa3af', '读取中': BLUE, '已读取': '#1f9d55', '读取失败': STOP}


def load_interface(path=None):
    return json.loads((path or (ROOT / 'assets/interface.json')).read_text(encoding='utf-8'))


def templates(interface):
    by_entry = {task['entry']: task for task in interface['task']}
    return [dict(item, task_name=by_entry[item['entry']]['name']) for item in TEMPLATE_META]


def dev_task_names(interface):
    used = {item['entry'] for item in TEMPLATE_META} | SHELL_ENTRIES
    return [task['name'] for task in interface['task'] if task['entry'] not in used]


def allowed_labels(interface):
    option = interface['option']['一号位允许品类']
    labels = {case['name']: case.get('label', case['name']) for case in option['cases']}
    return [labels.get(name, name) for name in option['default_case']]


def settings_lines(interface):
    preflight = next(task['name'] for task in interface['task'] if task['entry'] == 'RuntimePreflight')
    controller = interface['controller'][0]['name']
    return [
        f'控制器    {controller}',
        '游戏不要最小化。本窗口不发送点击。',
        '一号位默认允许    ' + '、'.join(allowed_labels(interface)),
        f'连接检查仍是任务「{preflight}」，在任务列表勾选后执行。',
    ]


def config_rows(template):
    return (
        ('脚本类型', template['kind']),
        ('循环次数', template['loop']),
        ('对应任务', template['task_name']),
    )


def next_control(action, phase, template):
    """Update only the desk's own phase. No command line is returned."""
    name = template['task_name']
    if action == 'start':
        return dict(phase='running', status='已选择，未发送输入',
                    message=f'已选择「{name}」。本操作台不点击游戏，请在任务列表勾选这项后点开始。')
    if action == 'pause':
        if phase == 'running':
            return dict(phase='paused', status='本地选择已暂停', message='已暂停本地选择。Maa任务如在运行，请在任务列表停止。')
        if phase == 'paused':
            return dict(phase='paused', status='本地选择已暂停', message='本地选择已经暂停；不控制Maa任务的运行状态。')
        return dict(phase=phase, status='尚未选择', message='操作台尚未选择任务；不控制Maa任务的运行状态。')
    if action == 'stop':
        return dict(phase='idle', status='本地选择已结束', message='已结束本地选择，没有向游戏发送输入。Maa任务如在运行，请在任务列表停止。')
    raise ValueError(action)


def farm_status(snapshot):
    if not snapshot:
        return [('库存品类', '—'), ('订单', '—'), ('员工计划', '—'), ('生产槽位', '—'),
                ('采集时间', '—'), ('输入状态', '未发送'), ('读取状态', '未读取')]
    products = (snapshot.get('inventory') or {}).get('products') or []
    orders = snapshot.get('orders')
    plans = (snapshot.get('employees') or {}).get('plans') or []
    buildings = (snapshot.get('production') or {}).get('buildings') or []
    slots = sum(len(building.get('slots') or []) for building in buildings)
    captured = snapshot.get('capturedAt')
    return [
        ('库存品类', str(len(products))),
        ('订单', str(len(orders)) if isinstance(orders, list) else '未读取'),
        ('员工计划', str(len(plans))),
        ('生产槽位', str(slots) if buildings else '未读取'),
        ('采集时间', local_time(captured) if captured else '—'),
        ('输入状态', '未发送'),
        ('读取状态', '已读取'),
    ]


def overview_lines(snapshot):
    if not snapshot:
        return ['尚未读取', '', '点左下角「读取」。', '只读解码，不点击游戏。']
    lines = ['当前快照']
    for label, value in farm_status(snapshot):
        if label not in ('输入状态', '读取状态'):
            lines.append(f'{label}    {value}')
    return lines


def _amount_text(items):
    if not items:
        return '无'
    return '、'.join(f"{item.get('name', '未命名')}×{item.get('count', '—')}" for item in items)


def _slot_label(slot):
    return slot.get('statusLabel') or {
        'empty': '空位', 'growing': '生长中', 'mature': '已成熟', 'unknown': '未判定',
    }.get(slot.get('status'), slot.get('status') or '未判定')


def _finish_time(slots):
    future = []
    known = []
    for slot in slots:
        mature = slot.get('matureAtUnix')
        if not isinstance(mature, (int, float)):
            continue
        remaining = slot.get('remainingSeconds')
        if isinstance(remaining, (int, float)) and remaining > 0:
            future.append(mature)
        else:
            known.append(mature)
    if future:
        return local_time(min(future))
    if known:
        return local_time(max(known))
    return '—'


def post_rows(snapshot):
    """One row per post. Plans and production slots for the same building stay together."""
    if not snapshot:
        return []
    plans = (snapshot.get('employees') or {}).get('plans') or []
    buildings = (snapshot.get('production') or {}).get('buildings') or []
    grouped = {}
    order = []

    def identity(item):
        uid = item.get('buildingUid')
        if uid is not None:
            return ('uid', uid)
        return ('name', item.get('buildingName') or '')

    for plan in plans:
        key = identity(plan)
        grouped[key] = {'plan': plan, 'building': None}
        order.append(key)
    for building in buildings:
        key = identity(building)
        if key not in grouped:
            grouped[key] = {'plan': None, 'building': building}
            order.append(key)
        else:
            grouped[key]['building'] = building
    rows = []
    for index, key in enumerate(order, 1):
        plan = grouped[key]['plan'] or {}
        building = grouped[key]['building'] or {}
        place = plan.get('buildingName') or building.get('buildingName') or f"岗位 {plan.get('buildingItemId', index)}"
        people = '、'.join(person.get('name', '未提供') for person in plan.get('employees') or []) or '未安排'
        targets = plan.get('targets') or []
        accumulated = {item.get('name'): item.get('count') for item in plan.get('accumulated') or []}
        slots = building.get('slots') or []
        progress = []
        if targets:
            progress.append('、'.join(
                f"{item.get('name', '未命名')} {accumulated.get(item.get('name'), '—')}/{item.get('count', '—')}"
                for item in targets))
        if slots:
            counts = Counter(_slot_label(slot) for slot in slots)
            progress.append(' · '.join(f'{label} {count}' for label, count in counts.items()))
        rows.append({
            'title': f'{index}  {place} · {people}',
            'plan': _amount_text(targets),
            'progress': ' · '.join(progress) if progress else '未读取',
            'finish': _finish_time(slots),
        })
    return rows


def post_block(snapshot):
    rows = post_rows(snapshot)
    if not rows:
        return '等待岗位数据'
    return '\n\n'.join('\n'.join((
        row['title'],
        f"计划      {row['plan']}",
        f"进度      {row['progress']}",
        f"完成时间  {row['finish']}",
    )) for row in rows)


def post_lines(snapshot):
    rows = post_rows(snapshot)
    if not rows:
        return ['等待岗位数据']
    return [f"{row['title']}  计划 {row['plan']}  进度 {row['progress']}  完成时间 {row['finish']}" for row in rows]


def post_caption(snapshot):
    if not snapshot:
        return '等待数据'
    count = len(post_rows(snapshot))
    return f'{count} 个岗位' if count else '等待岗位数据'


CARD_COLUMNS = {'orders': 3, 'inventory': 4, 'posts': 3, 'posts_narrow': 2}
ORDER_KIND = {'party': '聚会', 'takeout': '外卖', 'normal': '普通'}
ORDER_STATE = {2: '进行中', 3: '完成', 4: '刷新', 5: '失败'}
FEED_ITEM_ID = 19


def order_rows(snapshot):
    orders = snapshot.get('orders') if snapshot else None
    if not isinstance(orders, list):
        return []
    rows = []
    for order in orders:
        costs = order.get('costs') or []
        rows.append({
            'title': ' '.join(piece for piece in (
                ORDER_KIND.get(order.get('category'), ''),
                str(order.get('slot', '—')),
                ORDER_STATE.get(order.get('statusCode'), ''),
            ) if piece),
            'materials': '、'.join(
                f"{item.get('name', '未命名')}×{item.get('count', '—')}" for item in costs) or '无材料',
        })
    return rows


def order_lines(snapshot):
    if not snapshot:
        return ['等待读取']
    rows = order_rows(snapshot)
    if not rows:
        return ['这张快照没有订单']
    return [f"{row['title']}    {row['materials']}" for row in rows]


def feed_count(snapshot):
    """Backpack feed is item 19 and is not one of the crop products."""
    inventory = (snapshot or {}).get('inventory') or {}
    records = inventory.get('records')
    if not isinstance(records, list):
        return None
    return sum(int(row.get('count') or 0) for row in records
               if row.get('itemId') == FEED_ITEM_ID or row.get('name') == '饲料')


def inventory_rows(snapshot):
    if not snapshot:
        return []
    rows = [{'name': item.get('name', '未命名'), 'count': item.get('count', '—')}
            for item in (snapshot.get('inventory') or {}).get('products') or []
            if item.get('itemId') != FEED_ITEM_ID and item.get('name') != '饲料']
    count = feed_count(snapshot)
    if count is not None:
        rows.insert(0, {'name': '饲料', 'count': count})
    return rows


def inventory_lines(snapshot):
    if not snapshot:
        return ['等待读取']
    rows = inventory_rows(snapshot)
    if not rows:
        return ['这张快照没有库存']
    return [f"{row['name']}    × {row['count']}" for row in rows]


def monitor_stream_command(python):
    """One resident FramePool process. It never receives a click or an apply flag."""
    return [python, '-u', '-X', 'utf8', str(CAPTURE_SCRIPT), '--stream', '--fps', '8', '--max-width', '144']


def read_exact(stream, size):
    buffer = bytearray()
    while len(buffer) < size:
        chunk = stream.read(size - len(buffer))
        if not chunk:
            return None
        buffer.extend(chunk)
    return bytes(buffer)


class MonitorStream:
    """Read paced preview packets. The newest frame replaces one that is still waiting."""

    def __init__(self, python=None):
        self.python = python or sys.executable
        self.process = None
        self.frames = queue.Queue(maxsize=1)

    def start(self):
        if self.process is not None and self.process.poll() is None:
            return
        self.process = subprocess.Popen(
            monitor_stream_command(self.python), cwd=ROOT, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        threading.Thread(target=self._read, daemon=True).start()

    def stop(self):
        process = self.process
        self.process = None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()

    def _read(self):
        process = self.process
        if process is None or process.stdout is None:
            return
        stream = process.stdout
        while process.poll() is None:
            kind = read_exact(stream, 1)
            size_raw = read_exact(stream, 4) if kind else None
            if not kind or not size_raw:
                break
            size = int.from_bytes(size_raw, 'little')
            if size <= 0 or size > 8_000_000:
                break
            payload = read_exact(stream, size)
            if payload is None:
                break
            packet = (kind, payload)
            try:
                self.frames.put_nowait(packet)
            except queue.Full:
                try:
                    self.frames.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self.frames.put_nowait(packet)
                except queue.Full:
                    pass


def monitor_error_text(error):
    text = str(error)
    if 'expected one game window' in text or 'found 0' in text:
        return '没有找到游戏窗口「萝卜家园2」。打开游戏后，这一页会显示画面，不点击。'
    return '画面暂时截不到：' + text.replace('\n', ' ')[:160]


def fit_frame_png(path, width, height):
    """Shrink a screenshot so it fits the monitor card. Portrait frames stay portrait."""
    import cv2
    image = cv2.imread(str(path))
    if image is None:
        raise RuntimeError('画面文件不可读')
    width, height = max(1, int(width)), max(1, int(height))
    src_h, src_w = image.shape[:2]
    scale = min(width / src_w, height / src_h)
    size = (max(1, int(src_w * scale)), max(1, int(src_h * scale)))
    resized = image if size == (src_w, src_h) else cv2.resize(image, size, interpolation=cv2.INTER_AREA)
    ok, encoded = cv2.imencode('.png', resized)
    if not ok:
        raise RuntimeError('画面缩放失败')
    return encoded.tobytes()


def stamp():
    now = datetime.now()
    return now.strftime('%H:%M:%S.') + f'{now.microsecond // 1000:03d}'


def launch_hud(page='autorun'):
    """Spawn only this window. Used by the Maa custom action and the CLI."""
    if page not in PAGE_TITLES:
        raise ValueError(page)
    pythonw = Path(sys.executable).with_name('pythonw.exe')
    interpreter = str(pythonw) if pythonw.exists() else sys.executable
    log = ROOT / '.tmp/farm_hud/window.log'
    log.parent.mkdir(parents=True, exist_ok=True)
    command = [interpreter, '-X', 'utf8', str(Path(__file__).resolve()), '--page', page]
    with log.open('a', encoding='utf-8') as stream:
        process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=stream, stderr=stream,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return process.pid


def _draw_icon(canvas, kind, color):
    canvas.delete('all')
    if kind == 'monitor':
        canvas.create_line(2, 11, 5, 11, 8, 4, 11, 13, 14, 6, fill=color, width=1.6, capstyle='round', joinstyle='round')
    elif kind == 'harvest':
        canvas.create_rectangle(3, 3, 13, 13, outline=color, width=1.3)
        canvas.create_line(3, 7, 13, 7, fill=color, width=1.3)
    elif kind == 'autorun':
        canvas.create_oval(2, 2, 14, 14, outline=color, width=1.3)
        canvas.create_polygon(6, 5, 6, 11, 11, 8, fill=color, outline=color)
    elif kind == 'settings':
        canvas.create_oval(5, 5, 11, 11, outline=color, width=1.3)
        for start, end in (((8, 1), (8, 3)), ((8, 13), (8, 15)), ((1, 8), (3, 8)), ((13, 8), (15, 8))):
            canvas.create_line(*start, *end, fill=color, width=1.3)
    else:
        canvas.create_rectangle(3, 4, 13, 12, outline=color, width=1.3)
        canvas.create_line(6, 8, 8, 10, 11, 6, fill=color, width=1.3)


class FarmHud:
    def __init__(self, root, interface=None, snapshot=None, snapshot_path=SNAPSHOT,
                 load_cache=True, start_read=False, initial_page='autorun', capture_frames=True):
        import tkinter as tk
        from tkinter import ttk
        self.tk = tk
        self.root = root
        self.interface = interface or load_interface()
        self.templates = templates(self.interface)
        self.template_index = 0
        self.snapshot_path = snapshot_path
        self.snapshot = snapshot
        self.page = initial_page if initial_page in PAGE_TITLES else 'autorun'
        self.phase = 'idle'
        self.link = '已读取' if snapshot else '未读取'
        self.generation = 0
        self.reading = False
        self.capture_frames = capture_frames
        self.monitor = None
        self.results = queue.Queue()
        self.screen_label = None
        self.monitor_photo = None
        self.last_frame_error = None
        self.log_lines = []
        self.nav_rows = {}
        self.log_box = None
        self.template_var = tk.StringVar(value=self.templates[0]['label'])
        self.status_var = tk.StringVar(value='尚未启动')
        self.detail_var = tk.StringVar(value='从模板里选一项。开始、暂停、结束只改变这里的状态，不点击游戏。')
        self.link_var = tk.StringVar(value=self.link)
        self.read_caption = tk.StringVar(value='读取')
        root.title(TITLE)
        root.geometry('1360x820')
        root.minsize(1100, 700)
        root.configure(bg=BG)
        icon = ROOT / 'assets/carrot_farm.ico'
        if icon.exists():
            try:
                root.iconbitmap(str(icon))
            except tk.TclError:
                pass
        style = ttk.Style(root)
        try:
            style.theme_use('clam')
        except tk.TclError:
            pass
        style.configure('Hud.TCombobox', padding=6)
        self._build_shell()
        if snapshot is None and load_cache and snapshot_path.exists():
            try:
                self.snapshot = json.loads(snapshot_path.read_text(encoding='utf-8'))
                self.link = '已读取'
            except (OSError, json.JSONDecodeError) as error:
                self.log_lines.append(f'{stamp()} · 上次快照不可用：{error}')
        self.link_var.set(self.link)
        self._paint_link()
        self.log('操作台已打开。未向游戏发送输入。')
        root.protocol('WM_DELETE_WINDOW', self.close)
        self.show(self.page)
        root.after(100, self.poll)
        if start_read:
            root.after(200, self.refresh)

    def current_template(self):
        return self.templates[self.template_index]

    def log(self, message):
        self.log_lines.append(f'{stamp()} · {message}')
        self.log_lines = self.log_lines[-200:]
        if self.log_box is not None:
            follow = self.log_box.yview()[1] > 0.98
            self._fill_text(self.log_box, '\n'.join(self.log_lines))
            if follow:
                self.log_box.see('end')

    def show(self, page):
        self.stop_monitor()
        self.screen_label = None
        self.monitor_photo = None
        self.page = page
        for index in range(4):
            self.stage.rowconfigure(index, weight=0, minsize=0)
            self.stage.columnconfigure(index, weight=0, minsize=0)
        self.title_var.set(PAGE_TITLES[page])
        if page == 'autorun':
            self.controls.pack(side='right')
        else:
            self.controls.pack_forget()
        self._paint_nav()
        for child in self.stage.winfo_children():
            child.destroy()
        self.log_box = None
        {'monitor': self._page_monitor, 'harvest': self._page_harvest, 'autorun': self._page_autorun,
         'settings': self._page_settings, 'dev': self._page_dev}[page]()

    def refresh(self):
        if self.reading:
            return
        self.reading = True
        self.link = '读取中'
        self.link_var.set(self.link)
        self.read_caption.set('读取中')
        self._paint_link()
        self.log('开始只读解码。')
        token = self.generation

        def work():
            try:
                self.results.put((token, read_live(self.snapshot_path), None))
            except Exception as error:
                self.results.put((token, None, str(error)))

        threading.Thread(target=work, daemon=True).start()

    def stop_monitor(self):
        if self.monitor is not None:
            self.monitor.stop()
            self.monitor = None

    def close(self):
        self.stop_monitor()
        self.root.destroy()

    def stop_all(self):
        if self.reading:
            self.generation += 1
            # The subprocess still writes to the shared snapshot path. Drain its
            # result before allowing another read, even though this one is cancelled.
            self.read_caption.set('等待读取结束')
            self.link = '已读取' if self.snapshot else '未读取'
            self.link_var.set(self.link)
            self._paint_link()
            self.log('已取消这次读取。')
        self.apply_control('stop')

    def apply_control(self, action):
        result = next_control(action, self.phase, self.current_template())
        self.phase = result['phase']
        self.status_var.set(result['status'])
        self.detail_var.set(result['message'])
        self.log(result['message'])

    def poll(self):
        try:
            token, snapshot, error = self.results.get_nowait()
        except queue.Empty:
            pass
        else:
            self.reading = False
            self.read_caption.set('读取')
            if token != self.generation:
                pass
            elif error:
                self.link = '读取失败'
                self.log('读取失败：' + str(error)[:180])
            else:
                self.snapshot = snapshot
                self.link = '已读取'
                self.log('读取完成。未执行游戏操作。')
            self.link_var.set(self.link)
            self._paint_link()
            if token == self.generation:
                self.show(self.page)
        if self.page == 'monitor' and self.monitor is not None:
            process = self.monitor.process
            if (process is not None and process.poll() is not None and self.monitor_photo is None
                    and self.last_frame_error is None and self.screen_label is not None):
                self.last_frame_error = '画面进程已退出'
                self.screen_label.configure(text='画面进程已退出。重新进入这一页再试。')
                self.log(self.last_frame_error)
            packet = None
            while True:
                try:
                    packet = self.monitor.frames.get_nowait()
                except queue.Empty:
                    break
            if packet:
                kind, payload = packet
                if kind == b'E':
                    message = monitor_error_text(payload.decode('utf-8', 'replace'))
                    if self.screen_label is not None and self.monitor_photo is None:
                        self.screen_label.configure(text=message)
                    if message != self.last_frame_error:
                        self.last_frame_error = message
                        self.log(message)
                elif self.screen_label is not None:
                    self.last_frame_error = None
                    self.monitor_photo = self.tk.PhotoImage(data=payload)
                    self.screen_label.configure(image=self.monitor_photo, text='')
        self.root.after(100, self.poll)

    def _build_shell(self):
        tk = self.tk
        sidebar = tk.Frame(self.root, bg=WHITE, width=212)
        sidebar.pack(side='left', fill='y')
        sidebar.pack_propagate(False)
        bottom = tk.Frame(sidebar, bg=WHITE)
        bottom.pack(side='bottom', fill='x', padx=14, pady=14)
        identity = tk.Frame(sidebar, bg=WHITE)
        identity.pack(side='top', fill='x', padx=18, pady=(22, 12))
        tk.Label(identity, text='萝卜家园', bg=WHITE, fg=INK, anchor='w',
                 font=(FONT, 18, 'bold')).pack(fill='x')
        tk.Label(identity, text=self.interface.get('version', ''), bg=WHITE, fg=MUTED, anchor='w',
                 font=(FONT, 9)).pack(fill='x', pady=(2, 0))
        for key, title in PAGES:
            self._nav_row(sidebar, key, title)
        tk.Frame(sidebar, bg=WHITE).pack(side='top', fill='both', expand=True)
        tk.Label(sidebar, text='开发工具', bg=WHITE, fg='#9aa3af', anchor='w',
                 font=(FONT, 9)).pack(side='top', fill='x', padx=22, pady=(8, 2))
        self._nav_row(sidebar, *DEV_PAGE)
        actions = tk.Frame(bottom, bg=WHITE)
        actions.pack(fill='x')
        read_border = tk.Frame(actions, bg=BLUE, padx=0, pady=0)
        read_border.pack(side='left', fill='x', expand=True, padx=(0, 8))
        read = tk.Label(read_border, textvariable=self.read_caption, bg=BLUE, fg=WHITE,
                        padx=12, pady=7, font=(FONT, 10), cursor='hand2')
        read.pack(fill='x')
        read.bind('<Button-1>', lambda _event: self.refresh())
        stop_border = tk.Frame(actions, bg='#f3c3c8', padx=1, pady=1)
        stop_border.pack(side='left', fill='x', expand=True)
        stop = tk.Label(stop_border, text='停止', bg=WHITE, fg=STOP, padx=12, pady=6,
                        font=(FONT, 10), cursor='hand2')
        stop.pack(fill='x')
        stop.bind('<Button-1>', lambda _event: self.stop_all())
        pill = tk.Frame(bottom, bg=WHITE, highlightbackground=LINE, highlightthickness=1)
        pill.pack(fill='x', pady=(12, 0))
        self.link_dot = tk.Canvas(pill, width=14, height=14, bg=WHITE, highlightthickness=0)
        self.link_dot.pack(side='left', padx=(10, 4), pady=8)
        tk.Label(pill, textvariable=self.link_var, bg=WHITE, fg=NAV, anchor='w',
                 font=(FONT, 9)).pack(side='left', pady=8)
        main = tk.Frame(self.root, bg=BG)
        main.pack(side='left', fill='both', expand=True)
        header = tk.Frame(main, bg=BG)
        header.pack(fill='x', padx=22, pady=(24, 16))
        self.title_var = tk.StringVar()
        tk.Label(header, textvariable=self.title_var, bg=BG, fg=INK, font=(FONT, 18, 'bold')).pack(side='left')
        self.controls = tk.Frame(header, bg=BG)
        for action, text in (('stop', '结束'), ('pause', '暂停'), ('start', '开始')):
            self._ghost(self.controls, text, action)
        self.stage = tk.Frame(main, bg=BG)
        self.stage.pack(fill='both', expand=True, padx=22, pady=(0, 18))

    def _nav_row(self, parent, key, title):
        tk = self.tk
        row = tk.Frame(parent, bg=WHITE, cursor='hand2')
        row.pack(side='top', fill='x', padx=12, pady=2)
        inner = tk.Frame(row, bg=WHITE)
        inner.pack(fill='x')
        icon = tk.Canvas(inner, width=16, height=16, bg=WHITE, highlightthickness=0)
        icon.pack(side='left', padx=(10, 6), pady=8)
        label = tk.Label(inner, text=title, bg=WHITE, fg=NAV, anchor='w', font=(FONT, 10))
        label.pack(side='left', fill='x', expand=True, pady=8)
        for widget in (row, inner, icon, label):
            widget.bind('<Button-1>', lambda _event, page=key: self.show(page))
        self.nav_rows[key] = dict(inner=inner, icon=icon, label=label, kind=key if key != 'dev' else 'dev')

    def _paint_nav(self):
        for key, row in self.nav_rows.items():
            active = key == self.page
            bg = NAV_BG if active else WHITE
            fg = NAV_FG if active else NAV
            row['inner'].configure(bg=bg)
            row['icon'].configure(bg=bg)
            row['label'].configure(bg=bg, fg=fg)
            _draw_icon(row['icon'], row['kind'], fg)

    def _paint_link(self):
        self.link_dot.delete('all')
        self.link_dot.create_oval(3, 3, 11, 11, fill=LINK_COLOR.get(self.link, MUTED), outline='')

    def _ghost(self, parent, text, action):
        tk = self.tk
        fill = BLUE if action == 'start' else WHITE
        fg = WHITE if action == 'start' else STOP if action == 'stop' else NAV
        border = tk.Frame(parent, bg=BLUE if action == 'start' else LINE, padx=1, pady=1)
        border.pack(side='right', padx=(8, 0))
        label = tk.Label(border, text=text, bg=fill, fg=fg, padx=19, pady=7,
                         font=(FONT, 10), cursor='hand2')
        label.pack()
        label.bind('<Button-1>', lambda _event, name=action: self.apply_control(name))
        label.bind('<Enter>', lambda _event: label.configure(bg='#2e65d3' if action == 'start' else '#f7f9fc'))
        label.bind('<Leave>', lambda _event: label.configure(bg=fill))

    def _card(self, parent, **grid):
        card = self.tk.Frame(parent, bg=WHITE, highlightbackground=LINE, highlightthickness=1)
        card.grid(**grid)
        return card

    def _page_monitor(self):
        stage = self.stage
        stage.rowconfigure(0, weight=3)
        stage.rowconfigure(1, weight=2)
        stage.columnconfigure(0, weight=0, minsize=280)
        stage.columnconfigure(1, weight=1, minsize=360)
        left = self._card(stage, row=0, column=0, rowspan=2, sticky='nsew', padx=(0, 12))
        right = self._card(stage, row=0, column=1, sticky='nsew', pady=(0, 12))
        status = self._card(stage, row=1, column=1, sticky='nsew')
        self._card_title(left, '游戏画面', '只截图，不点击')
        pane = self.tk.PanedWindow(left, orient='vertical', sashwidth=8, sashrelief='flat',
                                   bg=LINE, opaqueresize=True, bd=0)
        pane.pack(fill='both', expand=True, padx=8, pady=(0, 8))
        screen_holder = self.tk.Frame(pane, bg='#f7f9fc', width=250, height=250)
        log_holder = self.tk.Frame(pane, bg=WHITE)
        pane.add(screen_holder, stretch='never', minsize=180, height=250)
        pane.add(log_holder, stretch='always', minsize=120)
        self.screen_label = self.tk.Label(screen_holder, bg='#f7f9fc', fg=MUTED, text='正在截取游戏画面…',
                                          font=(FONT, 10))
        self.screen_label.pack(fill='both', expand=True)
        header = self.tk.Frame(log_holder, bg=WHITE)
        header.pack(fill='x', padx=8, pady=(6, 0))
        self.tk.Label(header, text='信息栏', bg=WHITE, fg=INK, font=(FONT, 10, 'bold')).pack(side='left')
        self.tk.Label(header, text='拖动上沿调整高度', bg=WHITE, fg=MUTED, font=(FONT, 9)).pack(side='left', padx=8)
        jump = self.tk.Label(header, text='翻到底部', bg=WHITE, fg=NAV_FG, cursor='hand2', font=(FONT, 9))
        jump.pack(side='right')
        self.log_box = self._scroll_text(log_holder)
        self._fill_text(self.log_box, '\n'.join(self.log_lines))
        self.log_box.see('end')
        jump.bind('<Button-1>', lambda _event: self.log_box.see('end'))
        if self.capture_frames:
            self.monitor = MonitorStream()
            self.monitor.start()
        self._card_title(right, '岗位进度', post_caption(self.snapshot))
        self._post_cards(right, post_rows(self.snapshot), 'posts')
        self._card_title(status, '农场状态')
        self._pairs(status, farm_status(self.snapshot))

    def _page_harvest(self):
        stage = self.stage
        stage.rowconfigure(0, weight=1)
        stage.columnconfigure(0, weight=3, minsize=360)
        stage.columnconfigure(1, weight=2, minsize=320)
        orders = self._card(stage, row=0, column=0, sticky='nsew', padx=(0, 12))
        side = self.tk.Frame(stage, bg=BG)
        side.grid(row=0, column=1, sticky='nsew')
        side.rowconfigure(0, weight=2)
        side.rowconfigure(1, weight=3)
        side.columnconfigure(0, weight=1)
        bag = self._card(side, row=0, column=0, sticky='nsew', pady=(0, 12))
        posts = self._card(side, row=1, column=0, sticky='nsew')
        order_rows_now = order_rows(self.snapshot)
        self._card_title(orders, '订单', f'{len(order_rows_now)} 张' if self.snapshot else '等待读取')
        self._order_cards(orders, order_rows_now)
        products = inventory_rows(self.snapshot)
        self._card_title(bag, '背包', f'{len(products)} 种' if self.snapshot else '等待读取')
        self._inventory_cards(bag, products)
        self._card_title(posts, '岗位', post_caption(self.snapshot))
        self._post_cards(posts, post_rows(self.snapshot), 'posts_narrow')

    def _page_autorun(self):
        from tkinter import ttk
        stage = self.stage
        stage.rowconfigure(2, weight=1)
        stage.columnconfigure(0, weight=1)
        stage.columnconfigure(1, minsize=340)
        self.tk.Label(stage, text='自动运行模板', bg=BG, fg=MUTED, anchor='w',
                      font=(FONT, 9)).grid(row=0, column=0, sticky='w')
        picker = ttk.Combobox(stage, textvariable=self.template_var, state='readonly', width=34,
                              style='Hud.TCombobox', values=[item['label'] for item in self.templates])
        picker.grid(row=1, column=0, sticky='w', pady=(4, 10))
        picker.bind('<<ComboboxSelected>>', self._template_changed)
        area = self._card(stage, row=2, column=0, sticky='nsew', padx=(0, 12))
        self._card_title(area, '运行区域')
        chip = self.tk.Label(area, text=self.current_template()['label'], bg='#f7f9fc', fg=INK,
                             anchor='w', padx=12, pady=8, font=(FONT, 10))
        chip.pack(fill='x', padx=16, pady=(0, 8))
        self._body(area, '\n'.join(overview_lines(self.snapshot)))
        config = self._card(stage, row=2, column=1, sticky='nsew')
        self._card_title(config, '脚本配置')
        self.tk.Label(config, text=self.current_template()['detail'], bg=WHITE, fg=MUTED,
                      anchor='w', justify='left', wraplength=300, font=(FONT, 10)).pack(fill='x', padx=16, pady=(0, 12))
        self._pairs(config, config_rows(self.current_template()))
        self.tk.Label(config, text='处理状态', bg=WHITE, fg=MUTED, anchor='w',
                      font=(FONT, 9)).pack(fill='x', padx=16, pady=(16, 2))
        self.tk.Label(config, textvariable=self.status_var, bg=WHITE, fg=INK, anchor='w',
                      font=(FONT, 11, 'bold')).pack(fill='x', padx=16)
        self.tk.Label(config, textvariable=self.detail_var, bg=WHITE, fg=NAV, anchor='w',
                      justify='left', wraplength=300, font=(FONT, 10)).pack(fill='x', padx=16, pady=(6, 16))

    def _template_changed(self, _event=None):
        label = self.template_var.get()
        index = next(i for i, item in enumerate(self.templates) if item['label'] == label)
        if index == self.template_index:
            return
        self.template_index = index
        self.show('autorun')

    def _page_settings(self):
        card = self.tk.Frame(self.stage, bg=WHITE, highlightbackground=LINE, highlightthickness=1)
        card.pack(fill='both', expand=True)
        self._card_title(card, '运行约定')
        self._body(card, '\n'.join(settings_lines(self.interface)))

    def _page_dev(self):
        card = self.tk.Frame(self.stage, bg=WHITE, highlightbackground=LINE, highlightthickness=1)
        card.pack(fill='both', expand=True)
        self._card_title(card, '诊断步骤')
        names = dev_task_names(self.interface)
        intro = '导航和诊断留在这里，不放进日常模板。这些名称对应任务列表，本页不能点击游戏。\n'
        self._body(card, intro + '\n'.join(names))

    def _scroller(self, parent):
        shell = self.tk.Frame(parent, bg=WHITE)
        shell.pack(fill='both', expand=True, padx=8, pady=(0, 8))
        canvas = self.tk.Canvas(shell, bg=WHITE, highlightthickness=0, borderwidth=0, width=240, height=120)
        bar = self.tk.Scrollbar(shell, orient='vertical', command=canvas.yview)
        canvas.configure(yscrollcommand=bar.set)
        bar.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)
        inner = self.tk.Frame(canvas, bg=WHITE)
        window = canvas.create_window((0, 0), window=inner, anchor='nw')

        def fit(event):
            canvas.configure(scrollregion=canvas.bbox('all') or (0, 0, 0, 0))
            if event.widget is canvas:
                canvas.itemconfigure(window, width=event.width)

        inner.bind('<Configure>', lambda _event: canvas.configure(scrollregion=canvas.bbox('all') or (0, 0, 0, 0)))
        canvas.bind('<Configure>', fit)

        def wheel(event):
            canvas.yview_scroll(int(-event.delta / 120), 'units')
            return 'break'

        def bind_wheel(widget):
            widget.bind('<MouseWheel>', wheel)
            for child in widget.winfo_children():
                bind_wheel(child)

        canvas.bind('<MouseWheel>', wheel)
        return inner, bind_wheel

    def _order_cards(self, parent, rows):
        columns = CARD_COLUMNS['orders']
        inner, bind_wheel = self._scroller(parent)
        if not rows:
            self.tk.Label(inner, text='等待读取' if not self.snapshot else '这张快照没有订单',
                          bg=WHITE, fg=MUTED, anchor='w', font=(FONT, 10)).pack(fill='x', padx=4, pady=4)
        else:
            self._use_columns(inner, columns)
            for index, row in enumerate(rows):
                card = self.tk.Frame(inner, bg=WHITE, highlightbackground=LINE, highlightthickness=1)
                card.grid(row=index // columns, column=index % columns, sticky='nsew', padx=4, pady=4)
                self.tk.Label(card, text=row['title'], bg=WHITE, fg=INK, anchor='w',
                              font=(FONT, 10, 'bold')).pack(fill='x', padx=10, pady=(9, 3))
                materials = self.tk.Label(card, text=row['materials'].replace('、', '\n'), bg=WHITE, fg=NAV, anchor='w',
                                          justify='left', font=(FONT, 9), wraplength=96)
                materials.pack(fill='x', padx=10, pady=(0, 9))
                card.bind('<Configure>', lambda event, label=materials: label.configure(
                    wraplength=max(64, event.width - 24)))
        bind_wheel(inner)

    def _inventory_cards(self, parent, rows):
        columns = CARD_COLUMNS['inventory']
        inner, bind_wheel = self._scroller(parent)
        if not rows:
            self.tk.Label(inner, text='等待读取' if not self.snapshot else '这张快照没有库存',
                          bg=WHITE, fg=MUTED, anchor='w', font=(FONT, 10)).pack(fill='x', padx=4, pady=4)
        else:
            self._use_columns(inner, columns)
            for index, row in enumerate(rows):
                card = self.tk.Frame(inner, bg='#f7f9fc', highlightbackground=LINE, highlightthickness=1)
                card.grid(row=index // columns, column=index % columns, sticky='nsew', padx=4, pady=4)
                self.tk.Label(card, text=f"{row['name']}  ×{row['count']}", bg='#f7f9fc', fg=INK, anchor='w',
                              font=(FONT, 9)).pack(fill='x', padx=9, pady=7)
        bind_wheel(inner)

    def _post_cards(self, parent, rows, layout='posts'):
        columns = CARD_COLUMNS[layout]
        inner, bind_wheel = self._scroller(parent)
        if not rows:
            self.tk.Label(inner, text='等待岗位数据', bg=WHITE, fg=MUTED, anchor='w',
                          font=(FONT, 10)).pack(fill='x', padx=4, pady=4)
        else:
            self._use_columns(inner, columns)
            for index, row in enumerate(rows):
                card = self.tk.Frame(inner, bg='#f7f9fc', highlightbackground=LINE, highlightthickness=1)
                card.grid(row=index // columns, column=index % columns, sticky='nsew', padx=4, pady=4)
                self.tk.Label(card, text=row['title'], bg='#f7f9fc', fg=INK, anchor='w',
                              font=(FONT, 10, 'bold')).pack(fill='x', padx=10, pady=(9, 4))
                for label, value in (('计划', row['plan']), ('进度', row['progress']), ('完成', row['finish'])):
                    line = self.tk.Frame(card, bg='#f7f9fc')
                    line.pack(fill='x', padx=10)
                    self.tk.Label(line, text=label, bg='#f7f9fc', fg=MUTED, anchor='w',
                                  font=(FONT, 9)).pack(side='left')
                    detail = self.tk.Label(line, text=value, bg='#f7f9fc', fg=INK, anchor='w', justify='left',
                                           font=(FONT, 9))
                    detail.pack(side='left', fill='x', expand=True, padx=(4, 0))
                    card.bind('<Configure>', lambda event, item=detail: item.configure(
                        wraplength=max(64, event.width - 52)), add='+')
                self.tk.Frame(card, bg='#f7f9fc', height=3).pack()
        bind_wheel(inner)

    def _use_columns(self, inner, columns):
        for column in range(columns):
            inner.columnconfigure(column, weight=1, uniform='cards')

    def _card_title(self, card, title, caption=''):
        row = self.tk.Frame(card, bg=WHITE)
        row.pack(fill='x', padx=16, pady=(17, 12))
        self.tk.Label(row, text=title, bg=WHITE, fg=INK, font=(FONT, 11, 'bold')).pack(side='left')
        if caption:
            self.tk.Label(row, text=caption, bg=WHITE, fg=MUTED, font=(FONT, 9)).pack(side='right')

    def _body(self, parent, content):
        widget = self._text(parent)
        self._fill_text(widget, content)
        return widget

    def _scroll_text(self, parent):
        shell = self.tk.Frame(parent, bg=WHITE)
        shell.pack(fill='both', expand=True, padx=4, pady=(0, 8))
        widget = self.tk.Text(shell, bg=WHITE, fg='#323b48', font=(FONT, 10), relief='flat',
                              wrap='word', width=16, height=4, padx=8, pady=6, highlightthickness=0, borderwidth=0)
        bar = self.tk.Scrollbar(shell, orient='vertical', command=widget.yview)
        widget.configure(yscrollcommand=bar.set)
        bar.pack(side='right', fill='y')
        widget.pack(side='left', fill='both', expand=True)
        widget.bind('<MouseWheel>', lambda event, box=widget: box.yview_scroll(int(-event.delta / 120), 'units'))
        return widget

    def _text(self, parent):
        widget = self.tk.Text(parent, bg=WHITE, fg='#323b48', font=(FONT, 10), relief='flat',
                              wrap='word', padx=16, pady=8, highlightthickness=0, borderwidth=0)
        widget.pack(fill='both', expand=True, padx=4, pady=(0, 8))
        return widget

    def _fill_text(self, widget, content):
        widget.configure(state='normal')
        widget.delete('1.0', 'end')
        widget.insert('1.0', content)
        widget.configure(state='disabled')

    def _slot(self, parent, line):
        slot = self.tk.Frame(parent, bg=WHITE, highlightbackground=LINE, highlightthickness=1)
        slot.pack(fill='x', padx=16, pady=4)
        self.tk.Label(slot, text=line, bg=WHITE, fg=INK, anchor='w', padx=10, pady=8,
                      font=(FONT, 10)).pack(fill='x')

    def _pairs(self, parent, rows):
        table = self.tk.Frame(parent, bg=WHITE)
        table.pack(fill='x', padx=16, pady=(0, 8))
        table.columnconfigure(1, weight=1)
        for index, (label, value) in enumerate(rows):
            self.tk.Label(table, text=label, bg=WHITE, fg=MUTED, anchor='w',
                          font=(FONT, 10)).grid(row=index, column=0, sticky='w', pady=3)
            self.tk.Label(table, text=value, bg=WHITE, fg=INK, anchor='e',
                          font=(FONT, 10)).grid(row=index, column=1, sticky='e', pady=3, padx=(16, 0))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--launch', action='store_true', help='open a detached read-only window')
    parser.add_argument('--live', action='store_true', help='read the local cache once at startup')
    parser.add_argument('--page', choices=tuple(PAGE_TITLES), default='autorun')
    parser.add_argument('--snapshot', type=Path, default=SNAPSHOT)
    args = parser.parse_args()
    if args.launch:
        print(json.dumps({'pid': launch_hud(args.page), 'window': TITLE, 'read_only': True}, ensure_ascii=False))
        return
    import tkinter as tk
    root = tk.Tk()
    FarmHud(root, snapshot_path=args.snapshot, start_read=args.live, initial_page=args.page)
    root.mainloop()


if __name__ == '__main__':
    main()

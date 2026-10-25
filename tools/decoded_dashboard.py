"""Maa's read-only decoded-data window. No game controller is imported."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import queue
import subprocess
import sys
import threading

try:
    from .decoded_state_adapter import adapt_snapshot
except ImportError:
    from decoded_state_adapter import adapt_snapshot


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / '.tmp/decoded_shadow/current_state.json'
BEIJING = timezone(timedelta(hours=8))
TITLE = 'Maa · 萝卜家园解码数据（只读）'
KINDS = {'聚会': 'party', '外卖': 'takeout', '普通': 'normal'}
TABS = ('inventory', 'orders', 'employees', 'family', 'production', 'manual_planting')
FIELDS = {'农田1': 'field_1', '农田2': 'field_2', '农田3': 'field_3'}


def planting_rows(plan):
    """Format the preflight's costs without inferring prices in the viewer."""
    return [(item['name'], item['perSlot'], item['total'],
             item['available'] if item['available'] is not None else '未知（缓存未提供）',
             item['shortfall'] if item['shortfall'] is not None else '未知')
            for item in plan.get('costs', [])]


def planting_summary(plan):
    slots = '、'.join(map(str, plan.get('selectedSlots', []))) or '—'
    status = '预检通过；本窗口仅展示，不执行种植' if plan.get('ready') else '预检阻断：' + '；'.join(plan.get('blockers', []))
    return f"空位 {len(plan.get('emptySlots', []))} 格 · 拟用槽位 {slots}", status


def local_time(value):
    if not value:
        return '—'
    date = datetime.fromtimestamp(value, BEIJING) if isinstance(value, (int, float)) else datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(BEIJING)
    return date.strftime('%m-%d %H:%M:%S')


def amounts(items):
    return '、'.join(f"{item['name']} × {item['count']}" for item in items) or '—'


def production_rows(snapshot):
    """Display decoder conclusions; do not infer growth or feeding rules here."""
    rows = []
    for building in (snapshot.get('production') or {}).get('buildings', []):
        for slot in building.get('slots', []):
            charge = slot.get('feedLabel')
            if charge is None and slot.get('chargeCds') is not None:
                charge = json.dumps(slot['chargeCds'], ensure_ascii=False, separators=(',', ':'))
            reliable = '公式已核对' if slot.get('timingReliable') else '不确定'
            if slot.get('timingReason'):
                reliable += '：' + slot['timingReason']
            rows.append((building['buildingName'], slot['slot'],
                         slot.get('productName') or ('空位' if slot.get('outputId') == 0 else '未读取' if slot.get('outputId') is None else f"编号 {slot['outputId']}"),
                         slot.get('statusLabel') or slot.get('status') or '未判定',
                         local_time(slot.get('startedAtUnix')), local_time(slot.get('matureAtUnix')),
                         slot.get('remainingSeconds') if slot.get('remainingSeconds') is not None else '—',
                         slot.get('cdRatePercent') if slot.get('cdRatePercent') is not None else '—',
                         slot.get('cycleIndex') if slot.get('cycleIndex') is not None else '—',
                         charge or '—', reliable))
    return rows


def table_rows(snapshot, category='聚会', batch_count=6):
    """Build all visible values without touching a GUI or the game."""
    counts = dict(normal=3, party=6, takeout=6)
    counts[KINDS[category]] = batch_count
    adapted = adapt_snapshot(snapshot, counts)
    inventory = [(x['name'], x['quantity'], x['game_item_id'], x['id'] or '未映射') for x in adapted['inventory']['items']]
    orders = []
    for order in adapted['orders'][KINDS[category]]:
        for item in order['items']:
            orders.append((order['order'], item['name'], item['single_required'], item['required'],
                           item['actual'], item['missing'],
                           {1: '待接取', 2: '进行中', 3: '已完成', 4: '刷新中', 5: '失败'}.get(order['status_code'], '其他'),
                           order['game_order_uid']))
    employees = []
    for plan in snapshot['employees']['plans']:
        employees.append((plan['buildingName'],
                          '、'.join(x.get('name', '未提供') for x in plan['employees']) or '未安排',
                          ' / '.join(str(x.get('level', '—')) for x in plan['employees']) or '—',
                          amounts(plan['targets']), amounts(plan['accumulated']), amounts(plan['heldProducts'])))
    tasks = []
    for task in (snapshot.get('family') or {}).get('tasks', []):
        state = {'occupied': '成员占用', 'unclaimed': '未接取', 'cooling_down': '冷却中'}.get(task['status'], task['status'])
        progress = task['progress']
        display = f"{progress['current']} / {progress['target']}" if progress['available'] else '未回传' if task['acceptor'] else '—'
        rare = task.get('isRare', task['quality'] > 0)
        upgraded = task.get('isUpgraded', task['quality'] == 2)
        bells = (task.get('bellReward') or {}).get('count', '未读取')
        tasks.append((task['slot'], '稀有' if rare else '普通', bells, task['text'], (task['acceptor'] or {}).get('name') or '—',
                      (task['upgrader'] or {}).get('name') or '—', display,
                      local_time(task['deadlineUnix']), state,
                      '已升级' if upgraded else '未升级'))
    return {'inventory': inventory, 'orders': orders, 'employees': employees, 'family': tasks,
            'production': production_rows(snapshot)}, adapted


def read_live(snapshot_path=SNAPSHOT, mode='all'):
    if mode not in ('all', 'automation'):
        raise ValueError('unsupported read-only snapshot mode')
    result = subprocess.run(['node', str(ROOT / 'tools/read_only_game_state.cjs'), mode,
                             '--python', sys.executable, '--output', str(snapshot_path)],
                            cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=120,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or '解码读取失败')
    return json.loads(snapshot_path.read_text(encoding='utf-8'))


def launch_dashboard(tab='orders', planting_request=None):
    """Spawn only this viewer; used by the Maa custom action and standalone CLI."""
    pythonw = Path(sys.executable).with_name('pythonw.exe')
    interpreter = str(pythonw) if pythonw.exists() else sys.executable
    log = ROOT / '.tmp/decoded_shadow/window.log'
    log.parent.mkdir(parents=True, exist_ok=True)
    command = [interpreter, '-X', 'utf8', str(Path(__file__).resolve()), '--live', '--tab', tab]
    if planting_request is not None:
        command.extend(['--field', str(planting_request.get('field_id', planting_request.get('fieldId', 'field_1'))),
                        '--crop-item-id', str(planting_request.get('crop_item_id', planting_request.get('cropItemId', 14))),
                        '--plant-count', str(planting_request.get('count', 1))])
    with log.open('a', encoding='utf-8') as stream:
        process = subprocess.Popen(command,
                                   cwd=ROOT, stdin=subprocess.DEVNULL, stdout=stream, stderr=stream,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return process.pid


class Dashboard:
    def __init__(self, root, snapshot_path=SNAPSHOT, live=False, initial_tab='orders', planting_request=None):
        import tkinter as tk
        from tkinter import ttk
        self.root, self.snapshot_path = root, snapshot_path
        self.read_mode = 'automation' if initial_tab in ('production', 'manual_planting') else 'all'
        self.snapshot = None
        self.mapping_issue_count = 0
        self.results = queue.Queue()
        self.reading = False
        self.category = tk.StringVar(value='聚会')
        self.batch = tk.StringVar(value='6')
        self.search = tk.StringVar()
        self.upgrade_filter = tk.StringVar(value='全部升级状态')
        self.rarity_filter = tk.StringVar(value='全部稀有度')
        self.family_summary = tk.StringVar()
        self.production_summary = tk.StringVar()
        request = planting_request or {}
        field_id = request.get('field_id', request.get('fieldId', 'field_1'))
        self.planting_field = tk.StringVar(value=next((name for name, value in FIELDS.items() if value == field_id), field_id))
        self.planting_crop_id = int(request.get('crop_item_id', request.get('cropItemId', 14)))
        self.planting_crop = tk.StringVar(value=f'物品 {self.planting_crop_id}')
        self.planting_count = tk.StringVar(value=str(request.get('count', 1)))
        self.planting_slots = tk.StringVar(value='尚未读取生产槽位')
        self.planting_status = tk.StringVar()
        self.planting_captured = tk.StringVar(value='采集时刻：—；快照预检不能代替操作前的即时解码校验')
        self.planting_crop_choices = {}
        self.planting_plan = None
        self.notice = tk.StringVar(value='正在载入解码数据…')
        self.subtitle = tk.StringVar(value='本地缓存 · 北京时间 · 按需刷新')
        self.cards = []
        self.trees = {}
        root.title(TITLE)
        root.geometry('1240x800')
        root.minsize(1020, 650)
        root.configure(bg='#eef2f6')
        style = ttk.Style(root)
        style.theme_use('clam')
        style.configure('.', font=('Microsoft YaHei UI', 10))
        style.configure('TFrame', background='#eef2f6')
        style.configure('TNotebook', background='#eef2f6', borderwidth=0)
        style.configure('TNotebook.Tab', padding=(22, 10), background='#e3e9ef')
        style.map('TNotebook.Tab', background=[('selected', '#ffffff')])
        style.configure('Treeview', rowheight=34, background='white', fieldbackground='white', foreground='#253347', borderwidth=0)
        style.configure('Treeview.Heading', background='#e8edf3', foreground='#42526a', padding=(8, 10), font=('Microsoft YaHei UI', 10, 'bold'))
        style.map('Treeview', background=[('selected', '#dcefe8')], foreground=[('selected', '#123d34')])
        style.configure('Refresh.TButton', padding=(18, 10), background='#17685b', foreground='white')
        header = tk.Frame(root, bg='#ffffff', padx=24, pady=18)
        header.pack(fill='x')
        tk.Label(header, text='萝卜家园 · 解码数据', font=('Microsoft YaHei UI', 21, 'bold'), bg='white', fg='#18372f').pack(side='left')
        tk.Label(header, text='  只读  ', bg='#e6f3eb', fg='#23734d', padx=10, pady=5).pack(side='left', padx=16)
        self.refresh_button = ttk.Button(header, text='只读刷新', style='Refresh.TButton', command=self.refresh)
        self.refresh_button.pack(side='right')
        tk.Label(root, textvariable=self.subtitle, bg='#eef2f6', fg='#65758a', anchor='w').pack(fill='x', padx=26, pady=(12, 4))
        cards = tk.Frame(root, bg='#eef2f6')
        cards.pack(fill='x', padx=24, pady=(6, 14))
        for label in ('库存品类', '订单总数', '员工计划', '共享任务', '生产槽位'):
            frame = tk.Frame(cards, bg='white', padx=18, pady=10)
            frame.pack(side='left', expand=True, fill='x', padx=(0, 10))
            tk.Label(frame, text=label, bg='white', fg='#738095', anchor='w').pack(fill='x')
            value = tk.StringVar(value='—')
            tk.Label(frame, textvariable=value, bg='white', fg='#1b5144', anchor='w', font=('Microsoft YaHei UI', 24, 'bold')).pack(fill='x')
            self.cards.append(value)
        toolbar = ttk.Frame(root)
        toolbar.pack(fill='x', padx=24, pady=(0, 10))
        ttk.Label(toolbar, text='搜索').pack(side='left')
        ttk.Entry(toolbar, textvariable=self.search, width=30).pack(side='left', padx=(10, 20))
        ttk.Label(toolbar, text='材料需求 = 单次数量 × 完成次数；员工能力沿用现有配置').pack(side='left')
        notebook = ttk.Notebook(root)
        notebook.pack(fill='both', expand=True, padx=24)
        self.notebook = notebook
        specifications = [
            ('inventory', '库存', [('名称', 260), ('背包数量', 150), ('游戏物品 ID', 180), ('自动化物品 ID', 200)]),
            ('orders', '订单', [('位置', 65), ('材料', 145), ('单次需求', 100), ('6 次需求', 110), ('背包', 100), ('缺口', 100), ('订单状态', 110), ('订单 UID', 170)]),
            ('employees', '员工计划', [('岗位', 100), ('员工', 120), ('等级', 65), ('计划目标', 250), ('累计产量', 200), ('员工持有产品', 250)]),
            ('family', '狮王任务', [('槽位', 45), ('稀有度', 65), ('铃铛奖励', 80), ('任务内容', 300), ('接取成员', 95), ('升级成员', 95), ('进度', 85), ('截止（北京时间）', 155), ('状态', 85), ('升级', 70)]),
            ('production', '农田 / 牧场 / 果园', [('岗位', 105), ('槽位', 55), ('产品', 100), ('状态', 130), ('计时锚点（北京时间）', 170), ('成熟（北京时间）', 155), ('剩余秒', 90), ('减时百分比', 100), ('生产轮次标记', 120), ('供料 / 加速数据', 220), ('时间判定', 280)]),
            ('manual_planting', '手动种植预检', [('消耗物品', 220), ('每格消耗', 150), ('预计总消耗', 170), ('当前拥有', 150), ('缺口', 150)]),
        ]
        for key, label, columns in specifications:
            frame = ttk.Frame(notebook)
            notebook.add(frame, text=label)
            if key == 'orders':
                controls = ttk.Frame(frame, padding=(8, 10))
                controls.pack(fill='x')
                ttk.Label(controls, text='订单类型').pack(side='left')
                selector = ttk.Combobox(controls, textvariable=self.category, values=list(KINDS), state='readonly', width=8)
                selector.pack(side='left', padx=(10, 24))
                selector.bind('<<ComboboxSelected>>', self.category_changed)
                ttk.Label(controls, text='完成次数').pack(side='left')
                self.batch_selector = ttk.Combobox(controls, textvariable=self.batch, values=('1', '6'), state='readonly', width=5)
                self.batch_selector.pack(side='left', padx=10)
                self.batch_selector.bind('<<ComboboxSelected>>', lambda _: self.render())
                ttk.Label(controls, text='仅切换展示口径，不修改游戏勾选状态').pack(side='left', padx=16)
            if key == 'family':
                controls = ttk.Frame(frame, padding=(8, 10))
                controls.pack(fill='x')
                selector = ttk.Combobox(controls, textvariable=self.upgrade_filter, values=('全部升级状态', '已升级', '未升级'), state='readonly', width=12)
                selector.pack(side='left')
                selector.bind('<<ComboboxSelected>>', lambda _: self.render())
                rarity = ttk.Combobox(controls, textvariable=self.rarity_filter, values=('全部稀有度', '稀有', '普通'), state='readonly', width=12)
                rarity.pack(side='left', padx=12)
                rarity.bind('<<ComboboxSelected>>', lambda _: self.render())
                ttk.Label(controls, textvariable=self.family_summary).pack(side='left', padx=8)
            if key == 'production':
                controls = ttk.Frame(frame, padding=(8, 10))
                controls.pack(fill='x')
                ttk.Label(controls, textvariable=self.production_summary).pack(side='left')
            if key == 'manual_planting':
                controls = ttk.Frame(frame, padding=(8, 10))
                controls.pack(fill='x')
                ttk.Label(controls, text='农田').pack(side='left')
                selector = ttk.Combobox(controls, textvariable=self.planting_field, values=list(FIELDS), state='readonly', width=9)
                selector.pack(side='left', padx=(8, 20))
                selector.bind('<<ComboboxSelected>>', lambda _: self.render_planting())
                ttk.Label(controls, text='作物').pack(side='left')
                self.planting_crop_selector = ttk.Combobox(controls, textvariable=self.planting_crop, state='readonly', width=22)
                self.planting_crop_selector.pack(side='left', padx=(8, 20))
                self.planting_crop_selector.bind('<<ComboboxSelected>>', self.planting_crop_changed)
                ttk.Label(controls, text='数量').pack(side='left')
                ttk.Spinbox(controls, from_=1, to=36, textvariable=self.planting_count, width=6).pack(side='left', padx=8)
                ttk.Label(controls, text='数量按格数，不按产量，不乘以6；仅预检').pack(side='left', padx=12)
                for variable in (self.planting_slots, self.planting_status, self.planting_captured):
                    ttk.Label(frame, textvariable=variable, wraplength=1120, padding=(8, 5)).pack(fill='x')
            body = ttk.Frame(frame)
            body.pack(fill='both', expand=True)
            identifiers = [str(i) for i in range(len(columns))]
            tree = ttk.Treeview(body, columns=identifiers, show='headings', selectmode='browse')
            for index, (heading, width) in enumerate(columns):
                tree.heading(str(index), text=heading)
                tree.column(str(index), width=width, minwidth=40, anchor='w', stretch=(index == (3 if key == 'family' else 1)))
            vertical = ttk.Scrollbar(body, orient='vertical', command=tree.yview)
            horizontal = ttk.Scrollbar(body, orient='horizontal', command=tree.xview)
            tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
            tree.grid(row=0, column=0, sticky='nsew')
            vertical.grid(row=0, column=1, sticky='ns')
            horizontal.grid(row=1, column=0, sticky='ew')
            body.rowconfigure(0, weight=1)
            body.columnconfigure(0, weight=1)
            tree.tag_configure('alternate', background='#f7f9fb')
            tree.tag_configure('highlight', background='#e9f4ed', foreground='#145f41')
            tree.tag_configure('rare', background='#fff4d6', foreground='#765100')
            self.trees[key] = tree
        footer = tk.Frame(root, bg='#eef2f6', padx=24, pady=12)
        footer.pack(fill='x')
        tk.Label(footer, textvariable=self.notice, bg='#eef2f6', fg='#52667c', anchor='w').pack(fill='x')
        self.search.trace_add('write', lambda *_: self.render())
        self.planting_count.trace_add('write', lambda *_: self.render_planting())
        notebook.select(TABS.index(initial_tab))
        if snapshot_path.exists():
            try:
                self.accept(json.loads(snapshot_path.read_text(encoding='utf-8')))
                self.notice.set('已载入上次快照。点“只读刷新”读取当前缓存。'
                                + ('本窗口未读取狮王任务。' if self.snapshot.get('family') is None else ''))
            except (ValueError, KeyError) as error:
                self.notice.set('上次快照不可用：' + str(error))
        root.after(100, self.poll)
        if live:
            root.after(200, self.refresh)

    def category_changed(self, _=None):
        choices = ('1', '3') if self.category.get() == '普通' else ('1', '6')
        self.batch_selector.configure(values=choices)
        self.batch.set(choices[-1])
        self.render()

    def planting_crop_changed(self, _=None):
        self.planting_crop_id = self.planting_crop_choices[self.planting_crop.get()]
        self.render_planting()

    def render_planting(self):
        if self.snapshot is None:
            return
        try:
            try:
                from .manual_planting import build_preflight
            except ImportError:
                from manual_planting import build_preflight
            count = int(self.planting_count.get())
            plan = build_preflight(self.snapshot, field_id=FIELDS.get(self.planting_field.get(), self.planting_field.get()),
                                   crop_item_id=self.planting_crop_id, count=count)
        except ValueError as error:
            plan = dict(ready=False, blockers=[str(error)], costs=[], emptySlots=[], selectedSlots=[])
        self.planting_plan = plan
        slots, status = planting_summary(plan)
        self.planting_slots.set(slots)
        self.planting_status.set(status)
        self.planting_captured.set(f"采集时刻：{local_time(self.snapshot.get('capturedAt'))}（北京时间）；修改参数仅重新计算，点“只读刷新”读取新缓存。快照预检不能代替操作前的即时解码校验。")
        tree = self.trees['manual_planting']
        tree.delete(*tree.get_children())
        for row in planting_rows(plan):
            tree.insert('', 'end', values=row)

    def accept(self, snapshot):
        # Validate before replacing the currently visible successful snapshot.
        _, adapted = table_rows(snapshot, self.category.get(), int(self.batch.get()))
        self.mapping_issue_count = len(adapted['mapping_issues'])
        self.snapshot = snapshot
        catalog = (snapshot.get('production') or {}).get('plantingCatalog', [])
        self.planting_crop_choices = {f"{crop['name']}（{crop['itemId']}）": crop['itemId'] for crop in catalog}
        self.planting_crop_selector.configure(values=list(self.planting_crop_choices))
        self.planting_crop.set(next((name for name, item_id in self.planting_crop_choices.items()
                                    if item_id == self.planting_crop_id), f'物品 {self.planting_crop_id}（未提供配置）'))
        self.cards[0].set(str(len(snapshot['inventory']['products'])))
        self.cards[1].set(str(len(snapshot['orders'])))
        self.cards[2].set(str(len(snapshot['employees']['plans'])))
        family = snapshot.get('family')
        self.cards[3].set(str(len(family.get('tasks', []))) if family is not None else '未读取')
        buildings = (snapshot.get('production') or {}).get('buildings', [])
        slot_count = sum(len(building.get('slots', [])) for building in buildings)
        self.cards[4].set(str(slot_count))
        self.production_summary.set(f'{len(buildings)} 个区域 · {slot_count} 个槽位 · 本机时钟推算，未读取服务器校时 · 点“只读刷新”更新'
                                    if buildings else '此快照尚未包含逐槽位数据，请只读刷新')
        tasks = (snapshot.get('family') or {}).get('tasks', [])
        rare_count = sum(task.get('isRare', task['quality'] > 0) for task in tasks)
        upgraded_count = sum(task.get('isUpgraded', task['quality'] == 2) for task in tasks)
        self.family_summary.set(f'普通 {len(tasks) - rare_count} · 稀有 {rare_count}（已升级 {upgraded_count}） · 铃铛为完成奖励'
                                if family is not None else '本窗口未读取狮王任务；空表不表示当前没有任务')
        self.subtitle.set(f"Maa 只读数据源  ·  采集于 {local_time(snapshot['capturedAt'])}（北京时间）  ·  客户端缓存快照")
        self.render()

    def render(self):
        if self.snapshot is None:
            return
        rows, adapted = table_rows(self.snapshot, self.category.get(), int(self.batch.get()))
        query = self.search.get().strip().casefold()
        self.trees['orders'].heading('3', text=f'{self.batch.get()} 次需求')
        for key, values in rows.items():
            tree = self.trees[key]
            tree.delete(*tree.get_children())
            for row in values:
                if query and query not in ' '.join(map(str, row)).casefold():
                    continue
                if key == 'family' and self.upgrade_filter.get() != '全部升级状态' and row[9] != self.upgrade_filter.get():
                    continue
                if key == 'family' and self.rarity_filter.get() != '全部稀有度' and row[1] != self.rarity_filter.get():
                    continue
                tag = 'highlight' if (key == 'inventory' and row[2] in (160, 161)) or (key == 'family' and row[8] == '成员占用') else 'rare' if key == 'family' and row[1] == '稀有' else 'alternate' if len(tree.get_children()) % 2 else ''
                tree.insert('', 'end', values=row, tags=(tag,) if tag else ())
        self.render_planting()

    def refresh(self):
        if self.reading:
            return
        self.reading = True
        self.refresh_button.configure(state='disabled')
        mode = 'automation' if self.notebook.index(self.notebook.select()) == TABS.index('manual_planting') else self.read_mode
        self.notice.set('正在只读解码库存、订单、员工计划与生产槽位；本窗口未读取狮王任务…'
                        if mode == 'automation' else '正在只读解码库存、订单、员工计划、生产槽位与 90 个任务…')

        def work():
            try:
                self.results.put((read_live(self.snapshot_path, mode), None))
            except Exception as error:
                self.results.put((None, str(error)))
        threading.Thread(target=work, daemon=True).start()

    def poll(self):
        try:
            snapshot, error = self.results.get_nowait()
        except queue.Empty:
            pass
        else:
            self.reading = False
            self.refresh_button.configure(state='normal')
            try:
                if error:
                    raise ValueError(error)
                self.accept(snapshot)
                mapping = '映射完整' if not self.mapping_issue_count else f'待补映射 {self.mapping_issue_count} 项'
                family = snapshot.get('family')
                family_notice = f"狮王任务 {family.get('slotCount', 0)}/90" if family is not None else '本窗口未读取狮王任务'
                self.notice.set(f"读取完成  ·  {mapping}  ·  {family_notice}  ·  用时 {snapshot['timings']['totalMs'] / 1000:.2f} 秒  ·  未执行游戏操作")
            except (ValueError, KeyError) as failure:
                self.notice.set('本次读取失败，保留上次快照：' + str(failure)[:220])
        self.root.after(100, self.poll)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='refresh local cache once at startup')
    parser.add_argument('--snapshot', type=Path, default=SNAPSHOT)
    parser.add_argument('--launch', action='store_true', help='start a detached window with read-only refresh')
    parser.add_argument('--tab', choices=TABS, default='orders')
    parser.add_argument('--field', choices=tuple(FIELDS.values()), default='field_1')
    parser.add_argument('--crop-item-id', type=int, default=14)
    parser.add_argument('--plant-count', type=int, default=1)
    args = parser.parse_args()
    planting_request = dict(field_id=args.field, crop_item_id=args.crop_item_id, count=args.plant_count)
    if args.launch:
        print(json.dumps({'pid': launch_dashboard(args.tab, planting_request), 'window': TITLE, 'read_only': True}, ensure_ascii=False))
        return
    import tkinter as tk
    root = tk.Tk()
    Dashboard(root, args.snapshot, args.live, args.tab, planting_request)
    root.mainloop()


if __name__ == '__main__':
    main()

import ast
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch, Mock

from agent.farm_hud_action import OpenFarmHud
from test_decoded_state_adapter import snapshot
from tools import farm_hud
from tools.prepare_maa_ui import _ensure_monitor_top_left


ROOT = Path(__file__).resolve().parents[1]
LAYOUT_KEY = 'UI.DashboardCardGrid.Layout.TaskQueueView'
HASH_KEY = 'UI.DashboardCardGrid.ResourceLayoutHash.TaskQueueView'


def tk_available():
    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        root.destroy()
        return True
    except Exception:
        return False


class FarmHudModelTests(unittest.TestCase):
    def test_cancelled_read_stays_single_flight_until_its_result_is_drained(self):
        import queue
        desk = object.__new__(farm_hud.FarmHud)
        desk.reading = True
        desk.generation = 0
        desk.snapshot = None
        desk.read_caption = Mock()
        desk.link_var = Mock()
        desk._paint_link = Mock()
        desk.log = Mock()
        desk.apply_control = Mock()
        desk.results = queue.Queue()
        desk.root = Mock()
        desk.page = 'autorun'
        desk.show = Mock()
        desk.stop_all()
        self.assertTrue(desk.reading)
        with patch.object(farm_hud.threading, 'Thread') as thread:
            desk.refresh()
        thread.assert_not_called()
        desk.results.put((0, {'old': True}, None))
        desk.poll()
        self.assertFalse(desk.reading)
        self.assertIsNone(desk.snapshot)
        desk.show.assert_not_called()

    def setUp(self):
        self.interface = farm_hud.load_interface()

    def test_every_task_is_a_template_a_shell_entry_or_a_diagnostic(self):
        entries = {task['entry'] for task in self.interface['task']}
        templates = {item['entry'] for item in farm_hud.TEMPLATE_META}
        diagnostics = {task['entry'] for task in self.interface['task']
                       if task['name'] in farm_hud.dev_task_names(self.interface)}
        self.assertEqual(templates | farm_hud.SHELL_ENTRIES | diagnostics, entries)
        self.assertFalse(templates & farm_hud.SHELL_ENTRIES & diagnostics)

    def test_daily_template_points_at_the_visible_task_name(self):
        item = next(task for task in farm_hud.templates(self.interface) if task['entry'] == 'RunDailyBatchOrders')
        self.assertEqual(item['label'], '聚会与外卖（自动调度）')
        self.assertEqual(item['task_name'], '聚会与外卖订单（自动调度）')
        self.assertEqual(dict(farm_hud.config_rows(item))['对应任务'], item['task_name'])

    def test_controls_do_not_describe_a_game_command(self):
        template = farm_hud.templates(self.interface)[0]
        started = farm_hud.next_control('start', 'idle', template)
        self.assertEqual(started['phase'], 'running')
        self.assertIn(template['task_name'], started['message'])
        self.assertIn('不点击游戏', started['message'])
        self.assertNotIn('argv', started)
        paused = farm_hud.next_control('pause', 'running', template)
        self.assertEqual(paused['phase'], 'paused')
        self.assertIn('任务列表停止', paused['message'])
        idle = farm_hud.next_control('pause', 'idle', template)
        self.assertEqual(idle['phase'], 'idle')
        stopped = farm_hud.next_control('stop', 'running', template)
        self.assertEqual(stopped['phase'], 'idle')
        self.assertIn('没有向游戏发送输入', stopped['message'])

    def test_empty_monitor_matches_the_waiting_desk(self):
        self.assertIn('尚未读取', farm_hud.overview_lines(None))
        self.assertEqual(farm_hud.post_lines(None), ['等待岗位数据'])
        self.assertEqual(farm_hud.post_rows(None), [])
        self.assertEqual(farm_hud.farm_status(None)[-1], ('读取状态', '未读取'))

    def test_snapshot_monitor_uses_decoded_names_without_changing_them(self):
        source = snapshot()
        before = json.dumps(source, ensure_ascii=False)
        self.assertIn('普通 1    苹果×2、甘蔗×3', ''.join(farm_hud.order_lines(source)))
        self.assertNotIn('待接', ''.join(farm_hud.order_lines(source)))
        self.assertIn('苹果    × 2', ''.join(farm_hud.inventory_lines(source)))
        self.assertEqual(farm_hud.post_lines(source)[0].split('  计划')[0], '1  岗位 12 · 员工甲')
        self.assertEqual(farm_hud.farm_status(source)[0], ('库存品类', '2'))
        self.assertEqual(json.dumps(source, ensure_ascii=False), before)

    def test_posts_keep_plan_progress_and_finish_for_every_building(self):
        source = snapshot(production=True)
        plan = source['employees']['plans'][0]
        plan['buildingName'] = '农田1'
        plan['targets'] = [{'name': '番茄', 'count': 4}]
        plan['accumulated'] = [{'name': '番茄', 'count': 1}]
        building = source['production']['buildings'][0]
        building['slots'] = [dict(slot=1, status='growing', statusLabel='生长中', productName='番茄',
                                  matureAtUnix=1792330681, remainingSeconds=90)]
        rows = farm_hud.post_rows(source)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['plan'], '番茄×4')
        self.assertIn('番茄 1/4', rows[0]['progress'])
        self.assertIn('生长中 1', rows[0]['progress'])
        self.assertEqual(rows[0]['finish'], '10-18 21:38:01')
        self.assertIn('2 个岗位', farm_hud.post_caption(source))
        self.assertNotIn('前', farm_hud.post_caption(source))

    def test_order_rows_include_all_twenty_seven(self):
        source = {'orders': [
            {'category': 'normal', 'slot': index, 'statusCode': 1,
             'costs': [{'name': '苹果', 'count': 1}, {'name': '甘蔗', 'count': 2}]}
            for index in range(1, 28)
        ]}
        rows = farm_hud.order_rows(source)
        self.assertEqual(len(rows), 27)
        self.assertEqual(rows[0]['title'], '普通 1')
        self.assertEqual(rows[0]['materials'], '苹果×1、甘蔗×2')
        self.assertEqual(rows[-1]['title'], '普通 27')
        self.assertNotIn('待接', ''.join(row['title'] for row in rows))

    def test_backpack_includes_feed_from_records(self):
        source = {'inventory': {
            'products': [{'itemId': 29, 'name': '苹果', 'count': 2}],
            'records': [{'itemId': 19, 'name': '饲料', 'count': 4}, {'itemId': 19, 'name': '饲料', 'count': 5}],
        }}
        rows = farm_hud.inventory_rows(source)
        self.assertEqual(rows[0], {'name': '饲料', 'count': 9})
        self.assertEqual(rows[1]['name'], '苹果')
        self.assertIn('饲料    × 9', ''.join(farm_hud.inventory_lines(source)))
        self.assertNotIn('其余', '\n'.join(farm_hud.order_lines(source)))

    def test_preview_frame_is_a_bounded_ppm(self):
        import numpy as np
        from tools.capture_current_frame_with_maa import encode_preview
        ppm = encode_preview(np.zeros((800, 600, 3), dtype=np.uint8), 480)
        self.assertTrue(ppm.startswith(b'P6\n480 '))
        self.assertLess(len(ppm), 800 * 600 * 3)

    def test_monitor_capture_is_a_resident_preview_stream(self):
        command = farm_hud.monitor_stream_command('python')
        self.assertIn('capture_current_frame_with_maa.py', ' '.join(command))
        self.assertIn('--stream', command)
        self.assertIn('--max-width', command)
        self.assertIn('144', command)
        self.assertIn('8', command)
        self.assertNotIn('--apply', command)
        self.assertNotIn('--output', command)
        self.assertIn('没有找到游戏窗口', farm_hud.monitor_error_text('expected one game window, found 0'))

    def test_frame_is_shrunk_to_the_monitor_card(self):
        import cv2
        import numpy as np
        folder = ROOT / '.tmp' / 'farm_hud_test'
        folder.mkdir(parents=True, exist_ok=True)
        source = folder / 'frame.png'
        try:
            self.assertTrue(cv2.imwrite(str(source), np.zeros((800, 400, 3), dtype=np.uint8)))
            encoded = farm_hud.fit_frame_png(source, 100, 100)
            image = cv2.imdecode(np.frombuffer(encoded, dtype=np.uint8), cv2.IMREAD_COLOR)
            self.assertLessEqual(image.shape[1], 100)
            self.assertLessEqual(image.shape[0], 100)
            self.assertGreater(image.shape[0], 1)
        finally:
            source.unlink(missing_ok=True)

    def test_source_does_not_import_a_controller_or_apply_flag(self):
        source = (ROOT / 'tools/farm_hud.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        names = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.append(node.module)
        self.assertFalse(any('controller' in name.lower() or name.startswith('maa') for name in names))
        self.assertNotIn('post_click', source)
        self.assertNotIn('--apply', source)

    def test_interface_and_pipeline_are_default_off(self):
        task = next(item for item in self.interface['task'] if item['entry'] == 'OpenFarmHud')
        self.assertFalse(task['default_check'])
        node = json.loads((ROOT / 'assets/resource/pipeline/carrot_farm.json').read_text(encoding='utf-8'))['OpenFarmHud']
        self.assertEqual(node['custom_action'], 'OpenFarmHud')
        self.assertNotIn('next', node)
        prepare = (ROOT / 'tools/prepare_maa_ui.py').read_text(encoding='utf-8')
        self.assertIn("'OpenFarmHud'", prepare)


class FarmHudWindowTests(unittest.TestCase):
    @unittest.skipUnless(tk_available(), 'tkinter display is unavailable')
    def test_autorun_is_the_first_page_and_monitor_keeps_the_log(self):
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        try:
            desk = farm_hud.FarmHud(root, interface=farm_hud.load_interface(), load_cache=False,
                                     capture_frames=False)
            root.update_idletasks()
            self.assertEqual(desk.page, 'autorun')
            self.assertEqual(desk.title_var.get(), '自动运行设置')
            labels = []
            def walk(widget):
                try:
                    labels.append(widget.cget('text'))
                except Exception:
                    pass
                for child in widget.winfo_children():
                    walk(child)
            walk(desk.root)
            self.assertIn('脚本配置', labels)
            self.assertIn('运行区域', labels)
            self.assertIn('开始', labels)
            self.assertIn('暂停', labels)
            self.assertIn('结束', labels)
            desk.show('monitor')
            self.assertEqual(desk.title_var.get(), '实时监控')
            labels.clear()
            def walk_again(widget):
                try:
                    labels.append(widget.cget('text'))
                except Exception:
                    pass
                for child in widget.winfo_children():
                    walk_again(child)
            walk_again(desk.root)
            self.assertIn('游戏画面', labels)
            self.assertIn('正在截取游戏画面…', labels)
            self.assertIn('等待岗位数据', labels)
            self.assertEqual(str(desk.screen_label.cget('image')), '')
            self.assertIn('未向游戏发送输入', desk.log_box.get('1.0', 'end'))
            desk.apply_control('start')
            self.assertIn('不点击游戏', desk.detail_var.get())
        finally:
            root.destroy()

    @unittest.skipUnless(tk_available(), 'tkinter display is unavailable')
    def test_order_cards_use_three_columns(self):
        import tkinter as tk
        orders = [{'category': 'normal', 'slot': index, 'statusCode': 1,
                   'costs': [{'name': name, 'count': index} for name in
                             ['苹果', '玉米', '甘蔗'][:count]]}
                  for index, count in enumerate((1, 3, 2, 1), start=1)]
        root = tk.Tk()
        root.attributes('-alpha', 0)
        try:
            desk = farm_hud.FarmHud(root, interface=farm_hud.load_interface(), load_cache=False,
                                     capture_frames=False, snapshot={'orders': orders, 'inventory': {'products': []},
                                                                    'employees': {'plans': []}})
            desk.show('harvest')
            found = {}

            def walk(widget):
                try:
                    text = widget.cget('text')
                except Exception:
                    text = ''
                if text in ('普通 2', '普通 4'):
                    found[text] = widget.master.grid_info()
                if text in ('普通 1', '普通 2', '普通 3'):
                    found[text + ' bounds'] = (widget.master.winfo_y(), widget.master.winfo_height())
                for child in widget.winfo_children():
                    walk(child)

            for width in (1360, 1100):
                with self.subTest(width=width):
                    root.geometry(f'{width}x820')
                    root.update_idletasks()
                    walk(desk.root)
                    self.assertEqual(found['普通 2']['column'], 1)
                    self.assertEqual(found['普通 4']['column'], 0)
                    self.assertEqual(found['普通 4']['row'], 1)
                    self.assertEqual(found['普通 1 bounds'], found['普通 2 bounds'])
                    self.assertEqual(found['普通 2 bounds'], found['普通 3 bounds'])
        finally:
            root.destroy()


class FarmHudLaunchTests(unittest.TestCase):
    def test_launch_cli_does_not_open_tk(self):
        with patch.object(farm_hud.sys, 'argv', ['farm_hud', '--launch', '--page', 'monitor']), \
             patch.object(farm_hud, 'launch_hud', return_value=77) as launch, \
             contextlib.redirect_stdout(io.StringIO()) as out:
            farm_hud.main()
        launch.assert_called_once_with('monitor')
        self.assertIn('"read_only": true', out.getvalue())

    def test_maa_action_opens_the_desk_without_a_controller(self):
        context = SimpleNamespace(tasker=SimpleNamespace(stopping=False))
        with patch.object(farm_hud, 'launch_hud', return_value=55) as launch, contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(OpenFarmHud().run(context, SimpleNamespace()))
        launch.assert_called_once_with()

    def test_maa_cancel_does_not_launch(self):
        context = SimpleNamespace(tasker=SimpleNamespace(stopping=True))
        with patch.object(farm_hud, 'launch_hud') as launch:
            self.assertFalse(OpenFarmHud().run(context, SimpleNamespace()))
        launch.assert_not_called()


class DashboardLayoutTests(unittest.TestCase):
    def write_config(self, folder, payload):
        config = Path(folder) / 'config'
        config.mkdir()
        (config / 'config.json').write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        return config / 'config.json'

    def test_saved_layout_is_not_replaced(self):
        original = [{'Id': 'task_list', 'Col': 1, 'Row': 2, 'ColSpan': 4, 'RowSpan': 8}]
        with tempfile.TemporaryDirectory() as folder:
            path = self.write_config(folder, {LAYOUT_KEY: original, HASH_KEY: 'KEEP'})
            _ensure_monitor_top_left(Path(folder))
            data = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(data[LAYOUT_KEY], original)
        self.assertEqual(data[HASH_KEY], 'KEEP')

    def test_missing_layout_puts_the_task_list_on_the_left(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.write_config(folder, {HASH_KEY: 'OLD'})
            _ensure_monitor_top_left(Path(folder))
            data = json.loads(path.read_text(encoding='utf-8'))
        by_id = {item['Id']: item for item in data[LAYOUT_KEY]}
        self.assertEqual(by_id['task_list']['Col'], 0)
        self.assertEqual(by_id['task_list']['RowSpan'], 8)
        self.assertEqual(by_id['live_view']['Col'], 8)
        self.assertEqual(by_id['log']['Row'], 4)
        self.assertEqual(data[HASH_KEY], 'TASK_LIST_LEFT_MONITOR_RIGHT_V1')


if __name__ == '__main__':
    unittest.main()

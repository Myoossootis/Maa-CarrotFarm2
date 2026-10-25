import contextlib
import ast
import copy
import io
import json
from pathlib import Path
import subprocess
import tempfile
import queue
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent.decoded_dashboard_action import OpenDecodedDashboard
from tools import decoded_dashboard as dashboard
from test_decoded_state_adapter import snapshot


def window_snapshot():
    source = snapshot()
    source['orders'][0]['category'] = 'party'
    for plan in source['employees']['plans']:
        plan['buildingName'] = '岗位' + str(plan['buildingItemId'])
        plan['accumulated'] = []
    return source


class DashboardDataTests(unittest.TestCase):
    def test_planting_cost_display_uses_preflight_quantities(self):
        plan = dict(ready=True, emptySlots=[1, 3, 5], selectedSlots=[1, 3], costs=[
            dict(itemId=1, name='金币', perSlot=12, total=24, available=500, shortfall=0)])
        before = copy.deepcopy(plan)
        self.assertEqual(dashboard.planting_rows(plan), [('金币', 12, 24, 500, 0)])
        slots, status = dashboard.planting_summary(plan)
        self.assertEqual(slots, '空位 3 格 · 拟用槽位 1、3')
        self.assertIn('仅展示', status)
        self.assertEqual(plan, before)

    def test_blocked_planting_explains_reason_without_execution(self):
        plan = dict(ready=False, emptySlots=[], selectedSlots=[], blockers=['空位不足', '库存不足'])
        self.assertEqual(dashboard.planting_rows(plan), [])
        self.assertEqual(dashboard.planting_summary(plan), ('空位 0 格 · 拟用槽位 —', '预检阻断：空位不足；库存不足'))

    def test_unknown_planting_balance_is_explicitly_unknown(self):
        plan = dict(costs=[dict(name='金币', perSlot=10, total=20, available=None, shortfall=None)])
        self.assertEqual(dashboard.planting_rows(plan), [('金币', 10, 20, '未知（缓存未提供）', '未知')])

    def test_six_times_display_and_shortage_use_single_counts(self):
        source = window_snapshot()
        rows, adapted = dashboard.table_rows(source)
        self.assertEqual(rows['orders'][0][1:6], ('苹果', 2, 12, 2, 10))
        self.assertEqual(rows['orders'][1][1:6], ('甘蔗', 3, 18, 1, 17))
        self.assertFalse(adapted['execution_eligible'])

    def test_one_time_display_changes_only_presentation(self):
        source = window_snapshot()
        before = copy.deepcopy(source)
        rows, _ = dashboard.table_rows(source, batch_count=1)
        self.assertEqual(rows['orders'][0][2:6], (2, 2, 2, 0))
        self.assertEqual(source, before)

    def test_old_snapshot_has_no_production_rows(self):
        rows, _ = dashboard.table_rows(window_snapshot())
        self.assertEqual(rows['production'], [])

    def test_production_rows_preserve_zero_values_and_decoder_conclusions(self):
        source = window_snapshot()
        source['production'] = dict(buildings=[dict(buildingName='农田1', slots=[dict(
            slot=1, outputId=105, productName='甘蔗', status='growing', statusLabel='生长中',
            startedAtUnix=1792330081, matureAtUnix=1792330681, remainingSeconds=0,
            cdRatePercent=0, cycleIndex=1126, timingReliable=True, chargeCds=None)])])
        before = copy.deepcopy(source)
        rows, _ = dashboard.table_rows(source)
        self.assertEqual(rows['production'][0], ('农田1', 1, '甘蔗', '生长中',
            '10-18 21:28:01', '10-18 21:38:01', 0, 0, 1126, '—', '公式已核对'))
        self.assertEqual(source, before)

    def test_paused_ranch_does_not_invent_maturity_time(self):
        source = window_snapshot()
        source['production'] = dict(buildings=[dict(buildingName='牧场2', slots=[dict(
            slot=2, outputId=205, productName='鸡蛋', status='paused', statusLabel='供料不足',
            startedAtUnix=1792330081, matureAtUnix=None, remainingSeconds=None,
            cdRatePercent=1, cycleIndex=0, timingReliable=False,
            timingReason='等待供料', feedLabel='供料生产秒 1800 / 10800', chargeCds=[1800, 10800])])])
        rows, _ = dashboard.table_rows(source)
        self.assertEqual(rows['production'][0][5:11],
                         ('—', '—', 1, 0, '供料生产秒 1800 / 10800', '不确定：等待供料'))

    def test_unmapped_production_and_raw_charge_remain_visible(self):
        source = window_snapshot()
        source['production'] = dict(buildings=[dict(buildingName='果园1', slots=[
            dict(slot=1, outputId=9999, chargeCds=[0, 10800]), dict(slot=2, outputId=0), dict(slot=3, outputId=None)])])
        rows, _ = dashboard.table_rows(source)
        self.assertEqual(rows['production'][0][2], '编号 9999')
        self.assertEqual(rows['production'][0][9], '[0,10800]')
        self.assertEqual(rows['production'][1][2:4], ('空位', '未判定'))
        self.assertEqual(rows['production'][2][2:4], ('未读取', '未判定'))

    def test_family_occupied_missing_progress_and_beijing_deadline(self):
        source = window_snapshot()
        tasks = [dict(slot=i, text='任务', acceptor=None, upgrader=None, status='unclaimed',
                      progress=dict(current=None, target=172, available=False),
                      deadlineUnix=None, quality=0) for i in range(1, 91)]
        tasks[0].update(acceptor=dict(name='成员甲'), upgrader=dict(name='成员乙'), status='occupied', deadlineUnix=1792330081,
                        quality=2, isRare=True, isUpgraded=True, bellReward=dict(count=82))
        tasks[1].update(acceptor=dict(name='自己'), status='occupied', progress=dict(current=42, target=172, available=True))
        source['family'] = {'tasks': tasks}
        rows, _ = dashboard.table_rows(source)
        self.assertEqual(rows['family'][0][1:3], ('稀有', 82))
        self.assertEqual(rows['family'][0][4:9], ('成员甲', '成员乙', '未回传', '10-18 21:28:01', '成员占用'))
        self.assertEqual(rows['family'][0][9], '已升级')
        self.assertEqual(rows['family'][1][6], '42 / 172')

    def test_refresh_failure_cannot_return_old_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / 'old.json'
            file.write_text(json.dumps(window_snapshot()), encoding='utf-8')
            with patch.object(dashboard.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', 'decode failed')):
                with self.assertRaisesRegex(RuntimeError, 'decode failed'):
                    dashboard.read_live(file)

    def test_refresh_command_is_only_the_read_only_decoder(self):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / 'state.json'
            file.write_text(json.dumps(window_snapshot()), encoding='utf-8')
            with patch.object(dashboard.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')) as run:
                dashboard.read_live(file)
            command = run.call_args.args[0]
            self.assertEqual(command[:3], ['node', str(dashboard.ROOT / 'tools/read_only_game_state.cjs'), 'all'])
            self.assertNotIn('--apply', command)

    def test_production_refresh_uses_automation_without_family_scan(self):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / 'state.json'
            file.write_text(json.dumps(window_snapshot()), encoding='utf-8')
            with patch.object(dashboard.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')) as run:
                dashboard.read_live(file, 'automation')
            self.assertEqual(run.call_args.args[0][2], 'automation')

    def test_refresh_mode_rejects_any_game_action(self):
        with patch.object(dashboard.subprocess, 'run') as run:
            with self.assertRaises(ValueError):
                dashboard.read_live(mode='apply')
            run.assert_not_called()


class DashboardMaaTests(unittest.TestCase):
    def test_manual_tab_refresh_uses_only_automation_decoder(self):
        notice = SimpleNamespace(set=lambda _: None)
        view = SimpleNamespace(reading=False, refresh_button=SimpleNamespace(configure=lambda **_: None),
                               notice=notice, read_mode='all', results=queue.Queue(), snapshot_path=Path('unused.json'),
                               notebook=SimpleNamespace(select=lambda: 'selected', index=lambda _: dashboard.TABS.index('manual_planting')))
        with patch.object(dashboard, 'read_live', return_value={'readOnly': True}) as read, \
             patch.object(dashboard.threading, 'Thread', side_effect=lambda target, **_: SimpleNamespace(start=target)):
            dashboard.Dashboard.refresh(view)
        read.assert_called_once_with(Path('unused.json'), 'automation')
        self.assertEqual(view.results.get_nowait(), ({'readOnly': True}, None))

    def test_launch_preserves_old_call_and_forwards_planting_parameters(self):
        with patch.object(dashboard.subprocess, 'Popen', return_value=SimpleNamespace(pid=501)) as launch:
            self.assertEqual(dashboard.launch_dashboard(), 501)
            old_command = launch.call_args.args[0]
            self.assertEqual(old_command[-3:], ['--live', '--tab', 'orders'])
            dashboard.launch_dashboard('manual_planting', dict(field_id='field_3', crop_item_id=161, count=4))
            command = launch.call_args.args[0]
            self.assertEqual(command[-9:], ['--live', '--tab', 'manual_planting', '--field', 'field_3',
                                           '--crop-item-id', '161', '--plant-count', '4'])
            self.assertNotIn('--apply', command)

    def test_launch_accepts_json_preflight_parameter_names(self):
        with patch.object(dashboard.subprocess, 'Popen', return_value=SimpleNamespace(pid=502)) as launch:
            dashboard.launch_dashboard('manual_planting', dict(fieldId='field_2', cropItemId=160, count=3))
        self.assertEqual(launch.call_args.args[0][-6:], ['--field', 'field_2', '--crop-item-id', '160', '--plant-count', '3'])

    def test_manual_tab_has_no_controller_import_or_execute_button(self):
        source = Path(dashboard.__file__).read_text(encoding='utf-8')
        parsed = ast.parse(source)
        imports = [node.module or '' for node in ast.walk(parsed) if isinstance(node, ast.ImportFrom)]
        imports.extend(alias.name for node in ast.walk(parsed) if isinstance(node, ast.Import) for alias in node.names)
        self.assertFalse(any('controller' in name or name.startswith('maa') for name in imports))
        buttons = [node for node in ast.walk(parsed) if isinstance(node, ast.Call)
                   and isinstance(node.func, ast.Attribute) and node.func.attr == 'Button']
        self.assertEqual(len(buttons), 1)
        self.assertIn('manual_planting', dashboard.TABS)

    def test_manual_launch_cli_forwards_requested_parameters(self):
        with patch.object(dashboard.sys, 'argv', ['decoded_dashboard', '--launch', '--tab', 'manual_planting',
                                                '--field', 'field_2', '--crop-item-id', '161', '--plant-count', '5']), \
             patch.object(dashboard, 'launch_dashboard', return_value=901) as launch, \
             contextlib.redirect_stdout(io.StringIO()):
            dashboard.main()
        launch.assert_called_once_with('manual_planting', dict(field_id='field_2', crop_item_id=161, count=5))

    def test_maa_action_opens_viewer_without_using_a_controller(self):
        context = SimpleNamespace(tasker=SimpleNamespace(stopping=False))
        with patch.object(dashboard, 'launch_dashboard', return_value=101) as launch, contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(OpenDecodedDashboard().run(context, SimpleNamespace()))
            launch.assert_called_once_with()

    def test_maa_cancel_does_not_launch(self):
        context = SimpleNamespace(tasker=SimpleNamespace(stopping=True))
        with patch.object(dashboard, 'launch_dashboard') as launch:
            self.assertFalse(OpenDecodedDashboard().run(context, SimpleNamespace()))
            launch.assert_not_called()

    def test_interface_and_pipeline_are_independent_and_default_off(self):
        root = dashboard.ROOT
        interface = json.loads((root / 'assets/interface.json').read_text(encoding='utf-8'))
        task = next(x for x in interface['task'] if x['entry'] == 'OpenDecodedDashboard')
        self.assertFalse(task['default_check'])
        node = json.loads((root / 'assets/resource/pipeline/carrot_farm.json').read_text(encoding='utf-8'))['OpenDecodedDashboard']
        self.assertEqual(node['custom_action'], 'OpenDecodedDashboard')
        self.assertNotIn('next', node)
        self.assertNotIn('on_error', node)


if __name__ == '__main__':
    unittest.main()

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent import task_settings as settings
from agent import production_actions
from agent.manual_planting_action import ManualPlantingOnce
from tools import farm_hud


class TaskSettingsTests(unittest.TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / 'tasks.json'
        self.instance = Path(self.folder.name) / 'maa.json'
        self.addCleanup(patch.stopall)
        patch.object(settings, 'SETTINGS', self.path).start()
        patch.object(settings, 'MAA_INSTANCE', self.instance).start()
        self.catalog = settings.interface()
        self.pipeline = json.loads((settings.ROOT / 'assets/resource/pipeline/carrot_farm.json').read_text(encoding='utf-8'))

    def save(self, entry, **changes):
        values = {name: settings.option_default(option) for name, option in
                  settings.task_options(entry, self.catalog).items()}
        values.update(changes)
        settings.save_task(entry, values)
        return values

    def invoke(self, entry, action):
        node = deepcopy(self.pipeline[entry])
        # Native Maa sends parameters separately from normalized node metadata.
        context = SimpleNamespace(tasker=SimpleNamespace(stopping=False), get_node_data=lambda _: node)
        argv = SimpleNamespace(node_name=entry, custom_action_param=json.dumps(node.get('custom_action_param', {})))
        with patch.object(production_actions, 'run_cycle_worker', return_value=True) as worker, \
                patch.object(production_actions, 'wait_for_background_ready', return_value=True), \
                patch.object(production_actions, 'confirm_cycle_start', return_value=True), patch('builtins.print'):
            result = action.run(context, argv)
        return result, worker

    def test_every_maa_option_and_configurable_entry_has_a_template(self):
        entries = {template['entry'] for template in farm_hud.TEMPLATE_META}
        configurable = {task['entry'] for task in self.catalog['task'] if task.get('option')}
        self.assertTrue(configurable <= entries)
        names = {name for entry in entries for name in settings.task_options(entry, self.catalog)}
        self.assertEqual(names, set(self.catalog['option']))
        for entry in configurable:
            self.save(entry)
        self.assertEqual(set(settings.load_settings()), configurable)

    def test_maa_existing_choices_and_inputs_are_preserved_before_local_save(self):
        self.instance.write_text(json.dumps({'TaskItems': [
            {'entry': 'RunDailyNormalOrders', 'option': [
                {'name': '一号位允许品类', 'selected_cases': ['苹果', '小麦']}]},
            {'entry': 'RunNormalOrderLoop', 'option': [
                {'name': '普通循环设置', 'data': {'maximum': '2'}}]},
            {'entry': 'ManualPlantingOnce', 'option': [
                {'name': '手动种植农田', 'index': 2}]}]}), encoding='utf-8')
        values, source = settings.editor_values('RunDailyNormalOrders')
        self.assertEqual(values['一号位允许品类'], ['苹果', '小麦'])
        self.assertEqual(source, 'Maa任务配置')
        self.assertEqual(settings.editor_values('RunNormalOrderLoop')[0]['普通循环设置'], {'maximum': '2'})
        self.assertEqual(settings.editor_values('ManualPlantingOnce')[0]['手动种植农田'], '农田3')
        self.assertFalse(self.path.exists())

    def test_preparing_maa_preserves_existing_option_values(self):
        from tools.prepare_maa_ui import _sync_instance_task_items
        ui = Path(self.folder.name) / 'ui'
        instance = ui / 'config/instances/default.json'
        instance.parent.mkdir(parents=True)
        instance.write_text(json.dumps({'TaskItems': [
            {'entry': 'RunDailyNormalOrders', 'option': [
                {'name': '一号位允许品类', 'selected_cases': ['苹果', '小麦']}]},
            {'entry': 'ManualPlantingOnce', 'option': [
                {'name': '手动种植农田', 'index': 2},
                {'name': '手动种植数量', 'data': {'count': '5'}}]}]}), encoding='utf-8')
        _sync_instance_task_items(ui, self.catalog)
        migrated = settings.maa_settings(instance)
        self.assertEqual(migrated['RunDailyNormalOrders']['一号位允许品类'], ['苹果', '小麦'])
        self.assertEqual(migrated['ManualPlantingOnce']['手动种植农田'], '农田3')
        self.assertEqual(migrated['ManualPlantingOnce']['手动种植数量'], {'count': '5'})

    def test_roundtrip_is_per_template_and_clear_restores_maa_priority(self):
        self.save('RunDailyNormalOrders', **{'一号位允许品类': ['小麦']})
        self.save('RunDailyPartyOrders', **{'一号位允许品类': ['鸡蛋']})
        self.assertEqual(settings.editor_values('RunDailyNormalOrders')[0]['一号位允许品类'], ['小麦'])
        settings.save_task('RunDailyNormalOrders', {})
        self.assertNotIn('RunDailyNormalOrders', settings.load_settings())
        self.assertEqual(settings.load_settings()['RunDailyPartyOrders']['一号位允许品类'], ['鸡蛋'])

    def test_complete_local_settings_are_readable_when_unused_maa_cache_is_corrupt(self):
        saved = self.save('RunDailyNormalOrders', **{'一号位允许品类': ['小麦']})
        self.instance.write_text('{', encoding='utf-8')
        self.assertEqual(settings.editor_values('RunDailyNormalOrders'), (saved, '本地保存'))

    def test_bad_input_cannot_replace_valid_settings(self):
        self.save('RunNormalOrderLoop')
        before = self.path.read_bytes()
        cases = [({'普通循环设置': {'maximum': '-1'}}, 'RunNormalOrderLoop'),
                 ({'一号位允许品类': []}, 'RunNormalOrderLoop'),
                 ({'一号位允许品类': ['不存在']}, 'RunNormalOrderLoop'),
                 ({'周期参数': {'rounds': '1', 'poll': '20', 'timeout': '10'}}, 'RunShortCycle'),
                 ({'未知选项': 'x'}, 'RunNormalOrderLoop')]
        for values, entry in cases:
            with self.subTest(values=values), self.assertRaises(ValueError):
                settings.save_task(entry, values)
            self.assertEqual(self.path.read_bytes(), before)

    def test_local_selection_removes_stale_maa_flags_and_preserves_continuous_mode(self):
        self.save('RunNormalOrderLoop', **{'一号位允许品类': ['小麦'], '普通循环设置': {'maximum': '2'}})
        self.pipeline['RunNormalOrderLoop']['attach']['first_order_allow_crop_012'] = True
        result, worker = self.invoke('RunNormalOrderLoop', production_actions.RunDailyOrders())
        self.assertTrue(result)
        args = worker.call_args.args[0]
        self.assertEqual(args[args.index('--max-orders') + 1], '2')
        self.assertEqual(args[args.index('--allowed-products') + 1:-1], ['小麦'])
        self.assertEqual(worker.call_args.kwargs['script'], 'run_normal_order_loop.py')

    def test_all_parameterized_workers_receive_saved_values(self):
        self.save('RunFeedProduction', **{'饲料循环设置': {'maximum': '240', 'interval': '90'}})
        self.save('RefreshOrder', **{'刷新订单类型': '普通订单', '刷新订单位置': {'order_number': '9'}})
        self.save('RunShortCycle', **{'周期运行模式': '仅预览',
                                    '周期参数': {'rounds': '2', 'poll': '30', 'timeout': '900'}})
        for entry, action, expected in [
            ('RunFeedProduction', production_actions.RunFeedProduction(), ['--max-feed', '240', '--poll-seconds', '90']),
            ('RefreshOrder', production_actions.RefreshOrder(), ['--order-type', 'normal', '--order-number', '9']),
            ('RunShortCycle', production_actions.RunShortCycle(), ['--rounds', '2', '--poll-seconds', '30', '--timeout-seconds', '900', '--skip-checklist'])]:
            with self.subTest(entry=entry):
                result, worker = self.invoke(entry, action)
                self.assertTrue(result)
                self.assertEqual(worker.call_args.args[0], expected)

    def test_planting_field_crop_and_count_reach_existing_action(self):
        from agent import cycle_bridge
        self.save('ManualPlantingOnce', **{'手动种植农田': '农田3', '手动种植作物': '白菜',
                                          '手动种植数量': {'count': '5'}})
        context = SimpleNamespace(tasker=SimpleNamespace(stopping=False),
                                  get_node_data=lambda _: self.pipeline['ManualPlantingOnce'])
        argv = SimpleNamespace(node_name='ManualPlantingOnce', custom_action_param='{"count":1}')
        with patch.object(cycle_bridge, 'run_cycle_worker', return_value=True) as worker:
            self.assertTrue(ManualPlantingOnce().run(context, argv))
        self.assertEqual(worker.call_args.args[0], ['--field', 'field_3', '--crop-item-id', '161', '--count', '5', '--apply'])

    def test_corrupt_or_unknown_version_stops_before_worker(self):
        for data in ('{', '[]', '{"schema_version":99,"tasks":{}}',
                     '{"schema_version":1,"tasks":{"missing":{}}}'):
            self.path.write_text(data, encoding='utf-8')
            result, worker = self.invoke('RunNormalOrderLoop', production_actions.RunDailyOrders())
            self.assertFalse(result)
            worker.assert_not_called()

    def test_saved_settings_do_not_mutate_running_parameters(self):
        self.save('RunNormalOrderLoop', **{'普通循环设置': {'maximum': '2'}})
        context = SimpleNamespace(get_node_data=lambda _: self.pipeline['RunNormalOrderLoop'])
        argv = SimpleNamespace(node_name='RunNormalOrderLoop', custom_action_param='{"order_type":"normal","continuous":true}')
        parameters, _ = settings.action_values(context, argv)
        self.save('RunNormalOrderLoop', **{'普通循环设置': {'maximum': '9'}})
        self.assertEqual(parameters['max_orders'], 2)
        self.assertEqual(settings.action_values(context, argv)[0]['max_orders'], 9)

    def test_windows_app_identity_uses_own_taskbar_group(self):
        if farm_hud.sys.platform != 'win32':
            self.skipTest('Windows taskbar API')
        import ctypes
        with patch.object(ctypes.windll.shell32, 'SetCurrentProcessExplicitAppUserModelID', return_value=0) as set_id:
            farm_hud.set_app_identity()
        set_id.assert_called_once_with('Myoossootis.CarrotFarm2.Automation')
        self.assertEqual(farm_hud.TITLE, '萝卜家园自动化')

    def test_template_forms_render_save_and_reload_on_desktop_and_narrow_widths(self):
        try:
            import tkinter as tk
            root = tk.Tk()
        except Exception:
            self.skipTest('tkinter display is unavailable')
        root.attributes('-alpha', 0)
        try:
            desk = farm_hud.FarmHud(root, load_cache=False, start_read=False, capture_frames=False)

            def find_buttons(widget):
                result = []
                if widget.winfo_class() == 'TButton' and widget.cget('text') == '保存配置':
                    result.append(widget)
                for child in widget.winfo_children():
                    result.extend(find_buttons(child))
                return result

            for width in (1360, 1100):
                root.geometry(f'{width}x820')
                for index, template in enumerate(desk.templates):
                    with self.subTest(width=width, entry=template['entry']):
                        desk.template_index = index
                        desk.show('autorun')
                        root.update()
                        self.assertEqual(set(desk.task_option_vars),
                                         set(settings.task_options(template['entry'])))
                        buttons = find_buttons(root)
                        if desk.task_option_vars:
                            self.assertEqual(len(buttons), 1)
                            buttons[0].invoke()
                            root.update_idletasks()
                            self.assertIn(template['entry'], settings.load_settings())
                            self.assertGreater(buttons[0].winfo_height(), 10)
                            self.assertGreater(buttons[0].winfo_width(), 30)
                desk.show('settings')
                root.update_idletasks()
                self.assertIn('一号位允许品类', desk.task_option_vars)
        finally:
            root.destroy()


if __name__ == '__main__':
    unittest.main()

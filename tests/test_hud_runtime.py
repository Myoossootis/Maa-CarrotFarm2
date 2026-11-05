import json
from contextlib import nullcontext, contextmanager
import os
from pathlib import Path
import tempfile
import time
import threading
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from agent import cycle_bridge, production_actions, task_settings
from tools import farm_hud, hud_task_runner as runner, ui_worker
from tools.hud_runtime import TaskRun
from tools import task_control, maa_frame_normalize as frames

ROOT = Path(__file__).resolve().parents[1]


def defaults(entry):
    return {name: task_settings.option_default(option) for name, option in task_settings.task_options(entry).items()}


class TaskDispatchTests(unittest.TestCase):
    def test_unverified_package_or_batch_semantics_block_before_action(self):
        from tools import decoded_runtime as decoded
        cases = [('RunNormalOrderLoop', '999'), ('RunDailyPartyOrders', '98')]
        with tempfile.TemporaryDirectory() as temp:
            request_file = Path(temp) / 'request.json'
            for entry, package in cases:
                request_file.write_text(json.dumps(runner.make_request(entry, defaults(entry))), encoding='utf-8')
                with self.subTest(entry=entry), \
                        patch.object(sys, 'argv', ['runner', '--request', str(request_file)]), \
                        patch.object(runner, 'workflow_lock', return_value=nullcontext()), \
                        patch.object(runner, '_check_stop'), \
                        patch.object(decoded, 'snapshot', return_value={'packageVersion': package}), \
                        patch.object(decoded, 'validate'), patch.object(runner, 'execute') as execute, patch('builtins.print'):
                    self.assertEqual(runner.main(), 2)
                    execute.assert_not_called()
                    self.assertEqual(json.loads(request_file.with_name('result.json').read_text(encoding='utf-8'))['phase'], 'failed')

    def test_all_thirteen_templates_dispatch_to_original_workers_with_correct_mode(self):
        expected = {
            'RunDailyBatchOrders': ('run_daily_orders.py', True),
            'RunDailyPartyOrders': ('run_daily_orders.py', True),
            'RunDailyTakeoutOrders': ('run_daily_orders.py', True),
            'RunDailyNormalOrders': ('run_daily_orders.py', True),
            'RunNormalOrderLoop': ('run_normal_order_loop.py', True),
            'RefreshOrder': ('refresh_order.py', None), 'CloseOfflineIncome': ('dismiss_offline_income.py', None),
            'RunFeedProduction': ('run_feed_production.py', None),
            'RunShortCyclePreview': ('cycle', False), 'RunShortCycleApply': ('cycle', True),
            'RunShortCycle': ('cycle', False), 'ManualPlantingOnce': ('run_manual_planting.py', True),
            'ManualPlantingPreflight': ('run_manual_planting.py', False),
        }
        self.assertEqual(set(expected), set(runner.SUPPORTED))
        for entry, (script, apply) in expected.items():
            with self.subTest(entry=entry):
                worker = Mock(return_value=True)
                with patch.object(production_actions, 'run_cycle_worker', worker), \
                        patch.object(cycle_bridge, 'run_cycle_worker', worker), \
                        patch.object(production_actions, 'confirm_cycle_start', return_value=True), \
                        patch.object(production_actions, 'wait_for_background_ready', return_value=True), \
                        patch('builtins.print'):
                    self.assertTrue(runner.execute(runner.make_request(entry, defaults(entry))))
                worker.assert_called_once()
                self.assertEqual(worker.call_args.kwargs.get('script', 'cycle'), script)
                if apply is not None:
                    self.assertEqual('--apply' in worker.call_args.args[0], apply)

    def test_runtime_request_freezes_unsaved_form_values_and_ignores_later_settings(self):
        values = defaults('RunNormalOrderLoop')
        values.update({'普通循环设置': {'maximum': '2'}, '一号位允许品类': ['小麦']})
        request = runner.make_request('RunNormalOrderLoop', values)
        values['一号位允许品类'].append('苹果')
        with patch.object(task_settings, 'load_settings', side_effect=AssertionError('must not reload mutable preferences')), \
                patch.object(production_actions, 'run_cycle_worker', return_value=True) as worker, \
                patch.object(production_actions, 'wait_for_background_ready', return_value=True), patch('builtins.print'):
            self.assertTrue(runner.execute(request))
        arguments = worker.call_args.args[0]
        self.assertEqual(arguments[arguments.index('--max-orders') + 1], '2')
        self.assertEqual(arguments[arguments.index('--allowed-products') + 1:-1], ['小麦'])

    def test_unknown_missing_or_stale_request_never_starts_a_worker(self):
        good = runner.make_request('RunNormalOrderLoop', defaults('RunNormalOrderLoop'))
        requests = [dict(good, app_version='0.0.0'), dict(good, entry='OpenPhoneBackground'),
                    dict(good, schema_version=True), dict(good, options={}),
                    dict(good, options={'一号位允许品类': []})]
        with patch.object(production_actions, 'run_cycle_worker') as worker:
            for request in requests:
                with self.subTest(request=request), self.assertRaises(ValueError):
                    runner.execute(request)
        worker.assert_not_called()


FIXTURE = '''
import json,sys,os
from pathlib import Path
sys.path.insert(0, ROOT)
from tools.task_control import install_interruptible_waits,check_cancelled,TaskCancelled
from tools.ui_worker import workflow_lock
from tools.runtime_state import write_json
import time
request=Path(sys.argv[sys.argv.index('--request')+1]);folder=request.parent
install_interruptible_waits()
try:
 with workflow_lock():
  n=0
  while True:
   check_cancelled();n+=1;write_json(folder/'beat.json',{'count':n})
   print('[任务] heartbeat '+str(n),flush=True)
   time.sleep(.05)
except TaskCancelled:
 write_json(folder/'result.json',{'phase':'stopped','message':'fixture stopped'})
 sys.exit(130)
'''


class TaskProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        fixture = self.folder / 'worker.py'
        fixture.write_text(FIXTURE.replace('ROOT', repr(str(ROOT))), encoding='utf-8')
        self.run = TaskRun(self.folder / 'runs', fixture)
        self.addCleanup(self.cleanup_worker)

    def cleanup_worker(self):
        self.run.stop()
        self.wait(lambda: not self.run.active)

    def wait(self, condition, timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.run.poll()
            if condition():
                return
            time.sleep(.02)
        self.fail(f'timeout: {self.run.phase}, {self.run.message}')

    def beat(self):
        try:
            return json.loads((self.run.folder / 'beat.json').read_text(encoding='utf-8'))['count']
        except FileNotFoundError:
            return 0

    def start_fixture(self):
        self.run.start('CloseOfflineIncome', {})
        self.wait(lambda: self.beat() > 0)

    def test_real_process_pauses_resumes_same_pid_and_stops_with_shared_lock(self):
        self.start_fixture()
        process, folder = self.run.process, self.run.folder
        with self.assertRaises(RuntimeError):
            self.run.start('CloseOfflineIncome', {})
        self.run.pause()
        self.wait(lambda: self.run.phase == 'paused')
        beat = self.beat()
        time.sleep(.2)
        self.assertEqual(self.beat(), beat)
        with self.assertRaises(RuntimeError):
            with ui_worker.cycle_lock():
                self.fail('paused task released the single-writer lock')
        self.run.resume()
        self.wait(lambda: self.beat() > beat)
        self.assertIs(self.run.process, process)
        self.assertEqual(self.run.folder, folder)
        self.run.stop()
        self.wait(lambda: not self.run.active)
        self.assertEqual(self.run.phase, 'stopped')
        with ui_worker.cycle_lock():
            pass

    def test_stop_is_effective_while_paused_and_intent_is_retained(self):
        self.start_fixture()
        self.run.pause()
        self.wait(lambda: self.run.phase == 'paused')
        self.run.stop()
        self.wait(lambda: not self.run.active)
        self.assertEqual(self.run.phase, 'stopped')
        self.assertTrue((self.run.folder / 'request.json').is_file())
        self.assertTrue((self.run.folder / 'output.log').is_file())

    def test_mismatched_interface_is_blocked_before_process_creation(self):
        self.run.app_version = '0.0.0'
        with self.assertRaises(RuntimeError):
            self.run.start('CloseOfflineIncome', {})
        self.assertIsNone(self.run.process)

    def test_closing_the_gui_stops_its_owned_task_and_releases_the_lock(self):
        import tkinter as tk
        try:
            root = tk.Tk()
        except Exception:
            self.skipTest('tkinter unavailable')
        root.attributes('-alpha', 0)
        with patch.object(task_settings, 'SETTINGS', self.folder / 'prefs.json'), \
                patch.object(task_settings, 'MAA_INSTANCE', self.folder / 'missing.json'):
            try:
                desk = farm_hud.FarmHud(root, load_cache=False, capture_frames=False, task_run=self.run)
                root.update()
                desk.apply_control('start')
                self.wait(lambda: self.beat() > 0)
                desk.close()
                self.wait(lambda: not self.run.active)
                self.assertEqual(self.run.phase, 'stopped')
                with ui_worker.cycle_lock():
                    pass
            finally:
                try:
                    root.destroy()
                except tk.TclError:
                    pass

    def test_unconfirmed_exit_is_not_reported_as_completed(self):
        fixture = self.folder / 'unexpected_exit.py'
        fixture.write_text('raise SystemExit(0)', encoding='utf-8')
        self.run.runner = fixture
        self.run.start('CloseOfflineIncome', {})
        self.wait(lambda: not self.run.active)
        self.assertEqual(self.run.phase, 'failed')
        self.assertIn('结果未确认', self.run.message)

    def test_real_preview_uses_original_cli_with_inherited_lock_and_no_game_input(self):
        self.run.runner = ROOT / 'tools/hud_task_runner.py'
        self.run.start('RunShortCyclePreview', defaults('RunShortCyclePreview'))
        self.wait(lambda: not self.run.active)
        self.assertEqual(self.run.phase, 'completed', (self.run.folder / 'output.log').read_text(encoding='utf-8'))
        self.assertIn('dry run (no game connection)', (self.run.folder / 'output.log').read_text(encoding='utf-8'))


class PauseInputTests(unittest.TestCase):
    def test_pause_acknowledgment_waits_for_game_window_to_be_reenabled(self):
        with tempfile.TemporaryDirectory() as temp:
            pause = Path(temp) / 'pause'
            acknowledgment = Path(temp) / 'workers' / f'{os.getpid()}.paused'
            enabled = {'value': True}
            errors = []
            real_sleep = time.sleep

            @contextmanager
            def window_guard(_):
                enabled['value'] = False
                try:
                    yield 2
                finally:
                    enabled['value'] = True

            def send(*_):
                pause.touch()
                return True

            def click():
                try:
                    frames.post_click_normalized(None, 1, 1)
                except BaseException as error:
                    errors.append(error)

            with patch.dict(os.environ, {task_control.PAUSE_ENV: str(pause)}), \
                    patch.object(task_control, '_check_stop'), \
                    patch.object(frames, 'install_foreground_safe_inputs'), \
                    patch.object(frames, '_controller_hwnd', return_value=1), \
                    patch.object(frames, '_client_size', return_value=(720, 1358)), \
                    patch.object(frames, '_lock_foreground', window_guard), \
                    patch.object(frames, '_render_input_target', return_value=(1, 1, 1)), \
                    patch.object(frames, '_foreground_window', return_value=2), \
                    patch.object(frames, 'post_click_postmessage_only', side_effect=send), \
                    patch.object(frames.time, 'sleep', side_effect=lambda _: task_control.check_cancelled()):
                thread = threading.Thread(target=click)
                thread.start()
                deadline = time.monotonic() + 2
                while not acknowledgment.exists() and time.monotonic() < deadline:
                    real_sleep(.01)
                try:
                    self.assertTrue(acknowledgment.exists())
                    self.assertTrue(enabled['value'])
                finally:
                    pause.unlink(missing_ok=True)
                    thread.join(timeout=2)
                self.assertFalse(thread.is_alive())
                self.assertEqual(errors, [])

    def test_root_stop_prevents_input_before_child_bridge_cancellation_arrives(self):
        with tempfile.TemporaryDirectory() as temp:
            root_cancel = Path(temp) / 'root_cancel'
            root_cancel.touch()
            with patch.dict(os.environ, {task_control.RUN_CANCEL_ENV: str(root_cancel),
                                         task_control.CANCEL_ENV: str(Path(temp) / 'not_yet_forwarded')}):
                with self.assertRaises(task_control.TaskCancelled):
                    task_control.check_cancelled()

    def test_pause_during_click_or_drag_waits_for_mouse_release(self):
        for gesture in ('click', 'drag'):
            with self.subTest(gesture=gesture), tempfile.TemporaryDirectory() as temp:
                pause = Path(temp) / 'pause'
                acknowledgment = Path(temp) / 'workers' / f'{os.getpid()}.paused'
                native = Mock()
                messages = []
                native.PostMessageW.side_effect = lambda _, message, *__: messages.append(message) or True
                real_sleep = time.sleep
                errors = []

                def pause_inside_press(seconds):
                    if frames._WM_LBUTTONDOWN in messages:
                        pause.touch()
                        task_control.check_cancelled()
                        self.assertFalse(acknowledgment.exists())

                def act():
                    try:
                        if gesture == 'click':
                            frames.post_click_postmessage_only(1, 10, 10)
                        else:
                            frames.post_swipe_postmessage_only(1, (0, 0), (10, 10), 100)
                    except BaseException as error:
                        errors.append(error)

                with patch.dict(os.environ, {task_control.PAUSE_ENV: str(pause)}), \
                        patch.object(task_control, '_check_stop'), \
                        patch.object(frames.ctypes, 'WinDLL', return_value=native), \
                        patch.object(frames.time, 'sleep', side_effect=pause_inside_press):
                    thread = threading.Thread(target=act)
                    thread.start()
                    deadline = time.monotonic() + 2
                    while not acknowledgment.exists() and time.monotonic() < deadline:
                        real_sleep(.01)
                    try:
                        self.assertTrue(acknowledgment.exists())
                        self.assertEqual(messages[-1], frames._WM_LBUTTONUP)
                        self.assertTrue(thread.is_alive())
                    finally:
                        pause.unlink(missing_ok=True)
                        thread.join(timeout=2)
                    self.assertFalse(thread.is_alive())
                    self.assertEqual(errors, [])


class TaskButtonsTests(unittest.TestCase):
    def test_background_snapshot_does_not_rebuild_or_erase_an_edited_task_form(self):
        import tkinter as tk
        try:
            root = tk.Tk()
        except Exception:
            self.skipTest('tkinter unavailable')
        root.withdraw()
        try:
            desk = farm_hud.FarmHud(root, load_cache=False, capture_frames=False)
            variable = desk.task_option_vars['普通循环设置']['maximum']
            variable.set('7')
            desk.reading = True
            desk.results.put((desk.generation, {'orders': []}, None))
            desk.poll()
            self.assertIs(desk.task_option_vars['普通循环设置']['maximum'], variable)
            self.assertEqual(variable.get(), '7')
        finally:
            root.destroy()

    def test_every_template_buttons_control_the_runtime_and_save_edited_values(self):
        import tkinter as tk
        try:
            root = tk.Tk()
        except Exception:
            self.skipTest('tkinter unavailable')
        root.attributes('-alpha', 0)
        run = SimpleNamespace(active=False, phase='idle', message='ready', poll=lambda: [])

        def phase(value):
            run.phase = value
            run.active = value in ('running', 'paused')

        run.start = Mock(side_effect=lambda *_: phase('running'))
        run.pause = Mock(side_effect=lambda: phase('paused'))
        run.resume = Mock(side_effect=lambda: phase('running'))
        run.stop = Mock(side_effect=lambda: phase('stopped'))
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(task_settings, 'SETTINGS', Path(temp) / 'settings.json'), \
                patch.object(task_settings, 'MAA_INSTANCE', Path(temp) / 'missing.json'):
            try:
                desk = farm_hud.FarmHud(root, load_cache=False, capture_frames=False, task_run=run)
                for index, template in enumerate(desk.templates):
                    with self.subTest(entry=template['entry']):
                        desk.template_index = index
                        desk.show('autorun');root.update()
                        desk.control_buttons['start'].event_generate('<Button-1>');root.update()
                        self.assertEqual(run.start.call_args.args[0], template['entry'])
                        self.assertEqual(run.phase, 'running')
                        self.assertEqual(str(desk.template_picker.cget('state')), 'disabled')
                        desk.control_buttons['pause'].event_generate('<Button-1>');root.update()
                        self.assertEqual(run.phase, 'paused')
                        desk.control_buttons['start'].event_generate('<Button-1>');root.update()
                        self.assertEqual(run.phase, 'running')
                        desk.control_buttons['stop'].event_generate('<Button-1>');root.update()
                        self.assertEqual(run.phase, 'stopped')
                        self.assertFalse(run.active)
            finally:
                root.destroy()


if __name__ == '__main__':
    unittest.main()

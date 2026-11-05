"""Own one isolated task process; retain run intent, logs and cooperative controls."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
from uuid import uuid4

from agent.cycle_bridge import show_in_ui
from agent import task_settings
from tools.hud_task_runner import make_request
from tools.runtime_state import write_json

ROOT = Path(__file__).resolve().parents[1]
PHASE_LABELS = {'idle': '尚未启动', 'starting': '启动中', 'running': '运行中', 'pausing': '等待安全暂停',
                'paused': '已暂停', 'stopping': '正在结束', 'stopped': '已结束',
                'completed': '执行结束', 'failed': '任务受阻'}


class TaskRun:
    def __init__(self, runs=None, runner=None):
        self.runs = runs or ROOT / '.tmp/hud_runs'
        self.runner = runner or ROOT / 'tools/hud_task_runner.py'
        self.process = None
        self.folder = None
        self.phase = 'idle'
        self.message = '选择模板、调整参数后点击开始。'
        self.entry = None
        self.lines = queue.Queue(maxsize=500)
        self.reader = None
        self.stop_at = None
        self.app_version = task_settings.interface()['version']

    @property
    def active(self):
        return self.process is not None and self.process.poll() is None

    def _state(self, phase, message):
        self.phase, self.message = phase, message
        if self.folder:
            write_json(self.folder / 'status.json', dict(phase=phase, entry=self.entry, message=message,
                       pid=self.process.pid if self.process else None,
                       updated_at=datetime.now(timezone.utc).isoformat()))

    def start(self, entry, options):
        self.poll()
        if self.active:
            raise RuntimeError('已有任务运行，先暂停或结束当前任务')
        request = make_request(entry, options)
        if request['app_version'] != self.app_version:
            raise RuntimeError('程序版本已更新，请重新打开自动化界面')
        self.entry = entry
        self.folder = self.runs / uuid4().hex
        self.folder.mkdir(parents=True)
        self.lines = queue.Queue(maxsize=500)
        self.stop_at = None
        write_json(self.folder / 'request.json', request)
        self._state('starting', '正在启动执行器并核对共享运行锁。')
        env = {**os.environ, 'PYTHONIOENCODING': 'utf-8', 'PYTHONUNBUFFERED': '1',
               'CARROT_STATE_READER': 'decoded', 'CARROT_UI_PARENT_PID': str(os.getpid()),
               'CARROT_UI_CANCEL_FILE': str(self.folder / 'cancel'),
               'CARROT_UI_RUN_CANCEL_FILE': str(self.folder / 'cancel'),
               'CARROT_UI_PAUSE_FILE': str(self.folder / 'pause')}
        # A prior shell/parent's lock identity never grants ownership to a new run.
        env.pop('CARROT_UI_CYCLE_OWNER', None)
        try:
            interpreter = Path(sys.executable).with_name('python.exe') if os.name == 'nt' else Path(sys.executable)
            self.process = subprocess.Popen([str(interpreter), '-u', str(self.runner), '--request', str(self.folder / 'request.json')],
                cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding='utf-8', errors='replace',
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except OSError as error:
            self._state('failed', f'执行器启动失败：{error}')
            raise
        process, folder, lines = self.process, self.folder, self.lines

        def read_output():
            try:
                with (folder / 'output.log').open('w', encoding='utf-8') as output:
                    for line in process.stdout:
                        output.write(line)
                        output.flush()
                        text = line.rstrip()
                        if text.startswith('{'):
                            try:
                                data = json.loads(text)
                                if data.get('mode') == 'preflight' and data.get('readOnly') is True:
                                    text = '[预检] ' + ('通过' if data.get('ready') is True else
                                                       '；'.join(data.get('blockers') or ['未通过']))
                                elif data.get('task') == 'close_offline_income':
                                    text = '[任务] 离线收益面板：' + ('已关闭' if data.get('status') == 'closed' else '未出现')
                            except (ValueError, AttributeError, TypeError):
                                pass
                        if 'Error:' in text or 'Exception:' in text:
                            text = '[错误] ' + text
                        if show_in_ui(text) or text.startswith(('[任务]', '[狮王]', '[预检]', '[排产]', '[员工安排]', '[刷新]', '[一号位]')):
                            if lines.full():
                                try:
                                    lines.get_nowait()
                                except queue.Empty:
                                    pass
                            lines.put_nowait(text[:1200])
            finally:
                process.stdout.close()

        self.reader = threading.Thread(target=read_output, daemon=True)
        self.reader.start()
        self._state('starting', f'正在启动 {entry}，核对运行锁和冻结配置。')

    def pause(self):
        if not self.active or self.phase not in ('starting', 'running'):
            return
        (self.folder / 'pause').touch()
        self._state('pausing', '已请求暂停，等待当前原子操作释放输入；游戏生产仍会继续。')

    def resume(self):
        if not self.active or self.phase not in ('pausing', 'paused'):
            return
        (self.folder / 'pause').unlink(missing_ok=True)
        self._state('running', '继续同一次运行，按原回执和最新游戏状态核对。')

    def stop(self):
        if not self.active or self.phase == 'stopping':
            return
        (self.folder / 'cancel').touch()
        (self.folder / 'pause').unlink(missing_ok=True)
        self.stop_at = time.monotonic()
        self._state('stopping', '正在停止本次任务，保留已保存的计划与回执。')

    def poll(self):
        if self.process is not None:
            if self.active and self.phase == 'starting' and (self.folder / 'started.json').is_file():
                self._state('running', f'正在执行 {self.entry}；详细进度见任务日志。')
            if self.active and self.phase == 'pausing':
                workers = list((self.folder / 'workers').glob('*.active'))
                # Unregistered processes can only do setup/read-only work; every
                # input worker registers and checks pause before dispatch.
                if all(marker.with_suffix('.paused').exists() for marker in workers):
                    self._state('paused', '执行进程已在安全边界暂停，点击开始继续同一次运行。')
            if self.active and self.stop_at is not None and time.monotonic() - self.stop_at >= 15:
                # Kill only this owned worker tree after cooperative cancellation timed out.
                if os.name == 'nt':
                    subprocess.run(['taskkill', '/PID', str(self.process.pid), '/T', '/F'], capture_output=True,
                                   creationflags=subprocess.CREATE_NO_WINDOW, check=False)
                else:
                    self.process.terminate()
                self.stop_at = None
            if not self.active:
                code = self.process.returncode
                if self.reader:
                    self.reader.join(timeout=.1)
                try:
                    result = json.loads((self.folder / 'result.json').read_text(encoding='utf-8'))
                    phase = result['phase']
                    if (phase not in ('completed', 'failed', 'stopped')
                            or not isinstance(result.get('message'), str)
                            or result.get('entry', self.entry) != self.entry):
                        raise ValueError('unknown task result')
                    if code and phase == 'completed':
                        raise ValueError('exit code conflicts with completion')
                    message = result['message']
                except (OSError, ValueError, KeyError, TypeError):
                    phase, message = 'failed', f'进程退出（{code}），结果未确认；请核对运行记录，勿重复未决操作。'
                self._state(phase, message)
                self.process = None
        messages = []
        while True:
            try:
                messages.append(self.lines.get_nowait())
            except queue.Empty:
                return messages

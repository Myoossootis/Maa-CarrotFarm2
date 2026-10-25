"""Cancellable CLI bridge; logs are streamed with a bounded retained tail."""

from collections import deque
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time


ROOT = Path(__file__).resolve().parents[1]


def run_cycle_worker(arguments, *, is_stopping, script='cycle'):
    if is_stopping():
        return False
    work = ROOT / '.tmp' / 'maa_ui_runs'
    work.mkdir(parents=True, exist_ok=True)
    log_dir = ROOT / '.tmp' / 'worker_logs'
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.log')
    print(f'[日志] 工作进程完整日志：{log_path}', flush=True)
    with tempfile.TemporaryDirectory(prefix='cycle-', dir=work) as folder:
        cancel = Path(folder) / 'cancel'
        env = {**os.environ, 'PYTHONIOENCODING': 'utf-8', 'PYTHONUNBUFFERED': '1',
               'CARROT_STATE_READER': 'decoded',
               'CARROT_UI_CANCEL_FILE': str(cancel), 'CARROT_UI_PARENT_PID': str(os.getpid())}
        process = subprocess.Popen(
            [sys.executable, str(ROOT / 'tools/ui_worker.py'), script, *arguments],
            cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace',
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
        )
        tail = deque(maxlen=80)

        def forward_output():
            with log_path.open('w', encoding='utf-8') as log:
                log.write(f'script={script} arguments={arguments!r}\n')
                for line in iter(lambda: process.stdout.readline(2048), ''):
                    tail.append(line)
                    log.write(line)
                    log.flush()
                    print(line.rstrip(), flush=True)

        reader = threading.Thread(target=forward_output, daemon=True)
        reader.start()
        stopped = False
        stop_at = None
        try:
            while process.poll() is None:
                if is_stopping():
                    if stop_at is None:
                        cancel.touch()
                        stop_at = time.monotonic()
                        stopped = True
                        print('停止请求已发送；保留已保存计划，不重新排产。', flush=True)
                    if time.monotonic() - stop_at >= 10:
                        break
                time.sleep(.1)
        finally:
            if process.poll() is None:
                # Only the process tree started above, never WeChat/game/UI.
                if os.name == 'nt':
                    subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                                   capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, check=False)
                else:
                    process.terminate()
                process.wait(timeout=10)
            reader.join(timeout=2)
            process.stdout.close()
        with log_path.open('a', encoding='utf-8') as log:
            log.write(f'\nexit_code={process.returncode} cancelled={stopped}\n')
        if stopped or is_stopping():
            return False
        if process.returncode != 0:
            print(f'[错误] script={script} 退出码={process.returncode}；完整日志={log_path}', flush=True)
            return False
        return True

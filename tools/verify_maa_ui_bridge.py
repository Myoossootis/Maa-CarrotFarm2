"""Real Framework→Agent IPC and preview verification, without a game window."""

import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import tempfile

ROOT = Path(__file__).resolve().parents[1]
os.environ['MAAFW_BINARY_PATH'] = str(ROOT / '.tmp/maa_ui/runtimes/win-x64/native')

import numpy as np
from maa.agent_client import AgentClient
from maa.controller import CustomController
from maa.resource import Resource
from maa.tasker import Tasker


def main():
    # Real child-process wait cancellation, separate from modal confirmation.
    with tempfile.TemporaryDirectory(dir=ROOT / '.tmp', prefix='ui-stop-test-') as folder:
        cancel = Path(folder) / 'cancel'
        code = (
            f"import sys; sys.path.insert(0, {str(ROOT / 'tools')!r}); "
            "import time; from task_control import install_interruptible_waits; "
            "install_interruptible_waits(); print('READY', flush=True); time.sleep(60)"
        )
        waiting = subprocess.Popen([sys.executable, '-c', code], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   env={**os.environ, 'CARROT_UI_CANCEL_FILE': str(cancel),
                                        'CARROT_UI_PARENT_PID': str(os.getpid()), 'PYTHONIOENCODING': 'utf-8'},
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            assert waiting.stdout.readline().strip() == b'READY'
            cancel.touch()
            output = waiting.communicate(timeout=5)[0]
            assert waiting.returncode != 0 and b'TaskCancelled' in output
        finally:
            if waiting.poll() is None:
                waiting.terminate()
                waiting.communicate(timeout=5)
    inputs = []

    def reject(self, *args):
        inputs.append(args)
        return False

    implementations = {name: reject for name in dir(CustomController)
                       if getattr(getattr(CustomController, name), '__isabstractmethod__', False)}
    implementations.update(connect=lambda self: True, request_uuid=lambda self: 'offline-ui-test',
                           screencap=lambda self: np.zeros((1358, 720, 3), dtype=np.uint8))
    controller = type('NoGameController', (CustomController,), implementations)()
    resource = Resource()
    tasker = Tasker()
    client = AgentClient()
    process = None
    try:
        Tasker.set_log_dir('')
        assert controller.post_connection().wait().succeeded
        assert resource.post_bundle(ROOT / 'assets/resource').wait().succeeded
        assert client.bind(resource)
        assert client.set_timeout(10000)
        process = subprocess.Popen([sys.executable, '-u', str(ROOT / 'agent/main.py'), client.identifier],
                                   cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, encoding='utf-8', errors='replace',
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        assert client.connect(), 'Agent IPC connection failed'
        assert tasker.bind(resource, controller)
        assert client.register_sink(resource, controller, tasker)
        assert tasker.post_task('RuntimePreflight').wait().succeeded
        assert tasker.post_task('RunShortCycle').wait().succeeded
        started = time.monotonic()
        timer = threading.Timer(1.5, tasker.post_stop)
        timer.start()
        try:
            cancelled = tasker.post_task('RunShortCycle', {'RunShortCycle': {'attach': {'ui_apply': True}}}).wait()
            assert not cancelled.succeeded, 'stop during confirmation must not approve a cycle'
            assert time.monotonic() - started < 10, 'confirmation did not close on stop'
        finally:
            timer.cancel()
        assert not inputs, 'offline task unexpectedly requested game input'
        print('PASS: actual UI-native Framework -> Agent -> preview worker, stop closes confirmation; zero game input')
    finally:
        client.disconnect()
        if process:
            try:
                output, _ = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.terminate()
                output, _ = process.communicate(timeout=5)
            print(output[-4000:])
        for value in (tasker, client, resource, controller):
            value.__del__()
            value._handle = None


if __name__ == '__main__':
    main()

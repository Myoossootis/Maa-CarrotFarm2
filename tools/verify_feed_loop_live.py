"""Run the real Maa node until feed production closes N cycles, then cancel.

This is a live acceptance tool: it uses the existing game and field-3 plan.
It never substitutes simulated counters or resets plans/checkpoints.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

from atom_cli_common import connect_controller, release_controller
from runtime_state import write_json

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cycles', type=int, default=2)
    parser.add_argument('--timeout-seconds', type=int, default=10800)
    parser.add_argument('--poll-seconds', type=int, default=60)
    parser.add_argument('--prior-log', type=Path, action='append', default=[],
                        help='Continue a live acceptance after a repair, retaining actual action receipts')
    args = parser.parse_args()
    if args.cycles < 1 or args.timeout_seconds < 60:
        parser.error('positive cycles and timeout >= 60 required')
    for prior in args.prior_log:
        if not prior.is_file():
            parser.error(f'prior live log does not exist: {prior}')
    from maa.agent_client import AgentClient
    from maa.resource import Resource
    from maa.tasker import Tasker

    folder = ROOT / '.tmp/feed_acceptance' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    folder.mkdir(parents=True)
    write_json(ROOT / '.tmp/feed_acceptance/latest.json', dict(evidence_dir=str(folder)))
    controller = connect_controller()
    resource, tasker, client = Resource(), Tasker(), AgentClient()
    process = None
    runner = None
    finished = threading.Event()
    task_result = {}
    started = time.monotonic()
    result = dict(status='running', actual_framework_agent_worker=True,
                  target_cycles=args.cycles, evidence_dir=str(folder),
                  prior_worker_logs=[str(path.resolve()) for path in args.prior_log])
    counts = dict(collect_crops=0, refill=0, arrange=0, collect_mill=0)
    worker_path = None
    last_print = 0
    try:
        Tasker.set_log_dir('')
        Tasker.set_stdout_level(2)
        assert resource.post_bundle(ROOT / 'assets/resource').wait().succeeded
        assert client.bind(resource)
        client.set_timeout((args.timeout_seconds + 120) * 1000)
        stream = (folder / 'agent.log').open('w', encoding='utf-8')
        process = subprocess.Popen([sys.executable, '-u', str(ROOT / 'agent/main.py'), client.identifier],
                                   cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT,
                                   env={**os.environ, 'PYTHONIOENCODING': 'utf-8'},
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        stream.close()
        assert client.connect()
        assert tasker.bind(resource, controller)
        assert client.register_sink(resource, controller, tasker)
        existing = set((ROOT / '.tmp/worker_logs').glob('*.log'))

        def run():
            try:
                task_result['succeeded'] = tasker.post_task('RunFeedProduction', {
                    'RunFeedProduction': {'custom_action_param': {
                        'max_feed': 120, 'poll_seconds': args.poll_seconds}}}).wait().succeeded
            except BaseException as error:
                task_result['error'] = repr(error)
            finally:
                finished.set()

        runner = threading.Thread(target=run)
        runner.start()
        while time.monotonic() - started < args.timeout_seconds:
            if worker_path is None:
                fresh = set((ROOT / '.tmp/worker_logs').glob('*.log')) - existing
                if fresh:
                    worker_path = max(fresh, key=lambda path: path.stat().st_mtime)
                    result['worker_log'] = str(worker_path)
            events = []
            if worker_path:
                worker_text = '\n'.join(path.read_text(encoding='utf-8', errors='replace')
                                        for path in [*args.prior_log, worker_path])
                for line in worker_text.splitlines():
                    if line.startswith('[饲料循环] '):
                        try:
                            events.append(json.loads(line.split(' ', 1)[1]))
                        except json.JSONDecodeError:
                            pass # The worker may currently be writing this line.
                counts = {name: sum(event.get('event') == 'action_done'
                                   and event.get('action') == name
                                   and (name != 'refill' or event['result']['status'] == 'started')
                                   for event in events) for name in counts}
                last_refill = max((i for i, event in enumerate(events)
                                   if event.get('event') == 'action_done' and event.get('action') == 'refill'
                                   and event['result']['status'] == 'started'), default=-1)
                collected_after_refill = any(event.get('event') == 'action_done'
                                            and event.get('action') == 'collect_mill'
                                            for event in events[last_refill + 1:]) if last_refill >= 0 else False
                result.update(counts=counts, elapsed_seconds=round(time.monotonic() - started, 1),
                              latest_event=events[-1] if events else None,
                              foreground_changes=worker_text.count('[前台变化]'),
                              collected_after_last_refill=collected_after_refill)
                write_json(folder / 'progress.json', result)
                if (all(counts[name] >= args.cycles for name in counts)
                        and collected_after_refill and events[-1].get('event') == 'waiting'):
                    result['status'] = 'passed'
                    break
            if finished.is_set():
                result.update(status='failed', reason='Maa node ended before acceptance criteria', **task_result)
                break
            if time.monotonic() - last_print >= 30:
                print(json.dumps(result, ensure_ascii=False), flush=True)
                last_print = time.monotonic()
            time.sleep(1)
        else:
            result.update(status='timeout', reason='Live production did not close requested cycles in time')
    except BaseException as error:
        result.update(status='failed', error=repr(error))
        raise
    finally:
        tasker.post_stop()
        if runner:
            runner.join(timeout=20)
        if worker_path:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                text = worker_path.read_text(encoding='utf-8', errors='replace')
                if 'exit_code=' in text:
                    result['worker_cancelled'] = 'exit_code=130 cancelled=True' in text
                    break
                time.sleep(.2)
        result['elapsed_seconds'] = round(time.monotonic() - started, 1)
        write_json(folder / 'result.json', result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        client.disconnect()
        if process:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=5)
        for obj in (tasker, client, resource):
            obj.__del__()
            obj._handle = None
        release_controller(controller)
    return 0 if result['status'] == 'passed' and result.get('worker_cancelled') else 1


if __name__ == '__main__':
    raise SystemExit(main())

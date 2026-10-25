"""Opt-in normal triple loop with bound employee production and recovery."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from uuid import uuid4

try:
    from . import normal_order_delivery as delivery
    from . import employee_operation as employees
    from . import decoded_runtime as decoded
    from .run_short_cycle import run_script, COMPLETION
    from .runtime_state import write_json
    from .task_control import check_cancelled, TaskCancelled
    from .ui_worker import cycle_lock
except ImportError:
    import normal_order_delivery as delivery
    import employee_operation as employees
    import decoded_runtime as decoded
    from run_short_cycle import run_script, COMPLETION
    from runtime_state import write_json
    from task_control import check_cancelled, TaskCancelled
    from ui_worker import cycle_lock

from agent.order_preferences import validate_allowed_products

ROOT = Path(__file__).resolve().parents[1]


def loop(step, folder, *, max_orders=0, read_receipt=delivery.load, produce=None):
    """Only a confirmed rewarded triple counts; production always leads to a fresh scan."""
    summary = dict(run_id=folder.name, status='running', completed_orders=0, completed_count=0, operations=[])
    seen = set()
    try:
        while True:
            check_cancelled()
            current = step('--prepare-only')
            if current.get('submitted_count'):
                result = current  # Resume a receipt without submitting again.
            else:
                if current.get('status') != 'prepared' or not current.get('returned_to_farm'):
                    raise RuntimeError('Normal preparation was not confirmed; stopped')
                target = current.get('target_order')
                if not target:
                    summary.update(status='no_actionable_orders')
                    break
                if target.get('missing_items') or target.get('production_targets'):
                    summary.update(status='waiting_for_materials', target_order=target,
                                   next_stage='employee_plan_reconciliation')
                    write_json(folder / 'summary.json', summary)
                    if produce is None:
                        break
                    produce()
                    summary.update(status='running', next_stage='fresh_order_scan')
                    continue
                path = folder / f'order_{summary["completed_orders"] + 1:04d}.json'
                write_json(path, {'target': target})
                result = step('--target-file', str(path))
            receipt = read_receipt()
            operation = result.get('operation_id')
            if (not operation or operation in seen or not receipt or receipt.get('operation_id') != operation
                    or receipt.get('status') != 'completed' or result.get('status') != 'completed'
                    or result.get('batch_count') != 3 or result.get('submitted_count') != 1
                    or result.get('reward_claimed') is not True or result.get('returned_to_farm') is not True
                    or not all(row.get('acceptance_confirmed') is True for row in result.get('submitted', []))
                    or len(result.get('submitted', [])) != 1):
                raise RuntimeError('Normal completion receipt missing, duplicate or inconsistent; no next order')
            seen.add(operation)
            summary['operations'].append(operation)
            summary['completed_orders'] += 1
            summary['completed_count'] += 3
            write_json(folder / 'summary.json', summary)
            print(f'[普通3倍] 已确认交单并领奖 {summary["completed_orders"]}单／{summary["completed_count"]}次', flush=True)
            if max_orders and summary['completed_orders'] >= max_orders:
                summary['status'] = 'complete'
                break
    except TaskCancelled:
        summary['status'] = 'cancelled'
        raise
    except BaseException as error:
        summary.update(status='failed', error=str(error))
        raise
    finally:
        summary['updated_at_utc'] = datetime.now(timezone.utc).isoformat()
        write_json(folder / 'summary.json', summary)
    return summary


def finish_production(poll, timeout, *, create=False):
    """Resume canonical operations before consulting shared latest projections."""
    operation = employees.active()
    if operation and operation['status'] == 'collected':
        operation = None
    if not operation and not create:
        return
    receipt = delivery.load()
    if receipt and receipt['status'] != 'completed':
        raise decoded.DecodeError('Production and pending delivery conflict; reconcile before new inputs')
    if operation is None or operation['status'] == 'applying':
        run_script('run_employee_order_plan.py', *(['--resume'] if operation else []), '--allow-partial', '--apply')
        operation = employees.active()
    if not operation or operation['status'] != 'saved':
        raise decoded.DecodeError('Employee save was not confirmed; no monitoring or new order')
    identity = operation['assignment_id']
    run_script('monitor_employee_products.py', '--assignment-file',
               str(Path(operation['operation_dir']) / 'assignment.json'),
               '--poll-seconds', str(poll), '--timeout-seconds', str(timeout), '--apply')
    after = employees.active()
    if not after or after['assignment_id'] != identity or after['status'] != 'collected':
        raise decoded.DecodeError('Bound production collection not confirmed; no new order')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--max-orders', type=int, default=0, help='0=until cancelled or blocked; positive=finite validation')
    parser.add_argument('--poll-seconds', type=int, default=20)
    parser.add_argument('--timeout-seconds', type=int, default=7200)
    parser.add_argument('--allowed-products', nargs='*', default=None)
    args = parser.parse_args()
    if args.max_orders < 0:
        parser.error('--max-orders must be nonnegative')
    if not 5 <= args.poll_seconds <= 300 or not args.poll_seconds <= args.timeout_seconds <= 7200:
        parser.error('invalid poll/timeout interval')
    if args.allowed_products is not None:
        try:
            args.allowed_products = validate_allowed_products(args.allowed_products)
        except ValueError as error:
            parser.error(str(error))
    if not args.apply:
        print('Preview: normal triples; employee-first gaps; recover saved plans/claims; no game input.')
        return 0
    if os.environ.get('CARROT_STATE_READER') != 'decoded':
        parser.error('Run through ui_worker.py to require decoded state and safe inputs')
    folder = ROOT / '.tmp/normal_order_loop' / uuid4().hex
    folder.mkdir(parents=True)
    # The shared runner uses the safe worker when a cancel file is declared.
    # A console invocation gets its own cancellation path; Maa supplies one.
    previous_cancel = os.environ.get('CARROT_UI_CANCEL_FILE')
    os.environ.setdefault('CARROT_UI_CANCEL_FILE', str(folder / 'cancel'))
    def step(*arguments):
        if arguments == ('--prepare-only',):
            if decoded.snapshot()['packageVersion'] != '90':
                raise decoded.DecodeError('Normal loop version changed; no UI input')
            pending = delivery.load()
            if not pending or (pending['status'] == 'completed' and pending.get('returned_to_farm', True) is not False):
                policy = ['--order-type', 'normal']
                if args.allowed_products is not None:
                    policy.extend(['--allowed-products', *sorted(args.allowed_products)])
                run_script('check_first_orders.py', *policy)
        run_script('complete_normal_orders.py', *arguments)
        return json.loads(COMPLETION.read_text(encoding='utf-8'))
    write_json(folder / 'summary.json', dict(status='recovering_production',completed_orders=0,completed_count=0))
    try:
        with cycle_lock():
            data = decoded.snapshot()
            if data['packageVersion'] != '90':
                raise decoded.DecodeError('Normal loop version is unverified; no UI input')
            finish_production(args.poll_seconds, args.timeout_seconds)
            summary = loop(step, folder, max_orders=args.max_orders,
                           produce=lambda: finish_production(args.poll_seconds, args.timeout_seconds, create=True))
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    except BaseException as error:
        state = json.loads((folder / 'summary.json').read_text(encoding='utf-8'))
        state.update(status='cancelled' if isinstance(error, TaskCancelled) else 'failed', error=str(error))
        write_json(folder / 'summary.json', state)
        raise
    finally:
        if previous_cancel is None:
            os.environ.pop('CARROT_UI_CANCEL_FILE', None)


if __name__ == '__main__':
    raise SystemExit(main())

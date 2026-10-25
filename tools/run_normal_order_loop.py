"""Opt-in normal triple loop: complete and reward stocked orders, wait on gaps."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from uuid import uuid4

try:
    from . import normal_order_delivery as delivery
    from .run_short_cycle import run_script, COMPLETION
    from .runtime_state import write_json
    from .task_control import check_cancelled, TaskCancelled
    from .ui_worker import cycle_lock
except ImportError:
    import normal_order_delivery as delivery
    from run_short_cycle import run_script, COMPLETION
    from runtime_state import write_json
    from task_control import check_cancelled, TaskCancelled
    from ui_worker import cycle_lock

ROOT = Path(__file__).resolve().parents[1]


def loop(step, folder, *, max_orders=0, read_receipt=delivery.load):
    """A completed operation counts once; a gap yields before any production."""
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
                    break
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--max-orders', type=int, default=0, help='0=until cancelled or blocked; positive=finite validation')
    args = parser.parse_args()
    if args.max_orders < 0:
        parser.error('--max-orders must be nonnegative')
    if not args.apply:
        print('Preview: normal triple loop; stocked orders only; wait on shortages; no game input.')
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
        run_script('complete_normal_orders.py', *arguments)
        return json.loads(COMPLETION.read_text(encoding='utf-8'))
    try:
        with cycle_lock():
            summary = loop(step, folder, max_orders=args.max_orders)
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    finally:
        if previous_cancel is None:
            os.environ.pop('CARROT_UI_CANCEL_FILE', None)


if __name__ == '__main__':
    raise SystemExit(main())

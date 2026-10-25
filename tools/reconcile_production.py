"""Read-only reconciliation; explicit --collect-slot performs at most one claim."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

try:
    from . import decoded_runtime as decoded
    from .production_reconciliation import reconcile, collection_candidate, confirm_collection
    from .runtime_state import write_json
except ImportError:
    import decoded_runtime as decoded
    from production_reconciliation import reconcile, collection_candidate, confirm_collection
    from runtime_state import write_json

ROOT = Path(__file__).resolve().parents[1]
SLOTS = ('field_1', 'field_2', 'field_3', 'ranch_1', 'ranch_2', 'orchard_1', 'orchard_2')
REPORTS = ROOT / '.tmp/production_reconciliation'


def read_optional(path):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None


def collect_once(slot_id, reader, collect, folder):
    """Caller holds the shared UI lock. Durable unresolved intent blocks retries."""
    folder = Path(folder)
    journal = folder / 'collections' / (slot_id + '.json')
    current = reader()
    decoded.validate(current)
    previous = read_optional(journal)
    if previous and previous['status'] in ('intent', 'unconfirmed'):
        if confirm_collection(previous, current):
            previous.update(status='confirmed', recovered_by_readback=True, after=current)
            write_json(journal, previous)
            return dict(status='confirmed', recovered_by_readback=True, game_operations=0)
        raise decoded.DecodeError('Unresolved collection; no retry: ' + slot_id)
    candidate = collection_candidate(slot_id, current)
    intent = dict(status='prepared', candidate=candidate, before=current, input_reserved=False,
                  automatic_retry_allowed=False, created_at=datetime.now(timezone.utc).isoformat())
    run_dir = folder / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    run_dir.mkdir(parents=True)
    write_json(run_dir / 'before.json', current)
    write_json(journal, intent)

    def reserve(before):
        decoded.validate(before)
        if (before['packageVersion'] != current['packageVersion'] or
                collection_candidate(slot_id, before) != candidate):
            raise decoded.DecodeError('Collection identity changed; no click: ' + slot_id)
        intent.update(status='intent', input_reserved=True, before=before)
        write_json(journal, intent)
        write_json(run_dir / 'intent.json', intent)

    try:
        result = collect(candidate, reserve, run_dir)
        if not intent['input_reserved']:
            raise decoded.DecodeError('Collect button was not confirmed; no business input reserved')
        after = reader()
        decoded.validate(after)
        if not result.get('collected') or not confirm_collection(intent, after):
            raise decoded.DecodeError('Collection outcome not confirmed; no retry')
        intent.update(status='confirmed', after=after, result=result)
        write_json(run_dir / 'after.json', after)
        write_json(run_dir / 'result.json', intent)
        write_json(journal, intent)
        return dict(status='confirmed', slot_id=slot_id, result=result, evidence_dir=str(run_dir))
    except BaseException as error:
        intent.update(status='unconfirmed' if intent['input_reserved'] else 'blocked_before_input', error=str(error))
        write_json(journal, intent)
        write_json(run_dir / 'result.json', intent)
        raise


def live_collect(candidate, reserve, folder):
    # Loaded only in the explicit apply branch; report mode has no Maa imports.
    import cv2
    try:
        from .feed_production_ui import FeedProductionUI
        from . import configure_field_employee as employees
    except ImportError:
        from feed_production_ui import FeedProductionUI
        import configure_field_employee as employees
    with FeedProductionUI() as ui:
        cv2.imwrite(str(folder / 'ui_before.png'), ui.frame())
        result = employees.collect_finished_slot(ui.controller, candidate['slot_id'], ui.tasker,
            candidate['target'], before_click=reserve)
        cv2.imwrite(str(folder / 'ui_after.png'), ui.frame())
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--collect-slot', choices=SLOTS)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if args.apply and not args.collect_slot:
        parser.error('--apply requires exactly one --collect-slot')
    if args.collect_slot and not args.apply:
        parser.error('--collect-slot requires --apply; omit both for read-only reconciliation')
    if args.apply:
        try:
            from .ui_worker import cycle_lock
        except ImportError:
            from ui_worker import cycle_lock
        with cycle_lock():
            result = collect_once(args.collect_slot, decoded.snapshot, live_collect, REPORTS)
    else:
        data = decoded.snapshot()
        result = reconcile(data,
            feed=read_optional(ROOT / '.tmp/feed_production/state.json'),
            assignment=read_optional(ROOT / 'assets/catalog/employee_work/latest_assignment.json'),
            monitor=read_optional(ROOT / 'assets/catalog/employee_work/latest_monitor.json'),
            completion=read_optional(ROOT / 'assets/catalog/normal_orders/latest_completion.json'),
            manual=read_optional(ROOT / '.tmp/manual_planting_execution/active.json'),
            deliveries={kind: read_optional(ROOT / 'assets/catalog/normal_orders' / f'latest_{kind}_delivery.json')
                        for kind in ('party', 'takeout')})
        write_json(REPORTS / 'snapshot.json', data)
        write_json(REPORTS / 'latest.json', result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

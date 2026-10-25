"""One finite manual planting request. No harvest, retries, or production loop."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time

try:
    from .manual_planting import ROOT, FIELDS, PlantingError, PlantingPending, build_preflight, validate_before, verify_after
except ImportError:
    from manual_planting import ROOT, FIELDS, PlantingError, PlantingPending, build_preflight, validate_before, verify_after


def write_json(path, value):
    temporary = path.with_suffix('.writing')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def slot_key(plan, before):
    building = next(b for b in before['production']['buildings'] if b['carrierUid'] == plan['carrierUid'])
    slot = next(s for s in building['slots'] if s['slot'] == plan['selectedSlots'][0])
    # Bind the physical slot's old cycle, independent of crop, quantity and cache sequence.
    value = [plan['buildingUid'], plan['carrierUid'], slot['slot'], slot['raw']['tuple']]
    return hashlib.sha256(json.dumps(value, separators=(',', ':')).encode()).hexdigest()


def execute_once(request, ui, read_snapshot, folder, *, check_cancelled=lambda: None,
                 sleep=time.sleep, clock=time.monotonic, now=None):
    """Dependency injection permits full offline validation without a controller.

    Caller must hold the shared cycle lock. Any interrupted/uncertain batch stays
    blocked on disk, including after worker termination. Never resumes implicitly.
    """
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    journal = folder / 'active.json'
    if journal.exists() and json.loads(journal.read_text(encoding='utf-8')).get('status') not in ('confirmed', 'blocked_before_input'):
        raise PlantingError('上次种植未确认或被中断；保留记录，禁止重跑或自动补点')
    check_cancelled()
    initial = read_snapshot()
    plan = build_preflight(initial, **request, now=now)
    if not plan['ready']:
        raise PlantingError('；'.join(plan['blockers']))
    # Validate location coverage before any navigation or planting input.
    ui.check_supported(plan)
    run_dir = folder / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    run_dir.mkdir()
    report = dict(status='preparing', request=request, plannedSlots=plan['selectedSlots'],
                  seedInputsReserved=0, confirmedSlots=[], continuousLoopStarted=False,
                  evidenceDirectory=str(run_dir))
    write_json(run_dir / 'plan.json', plan)
    write_json(run_dir / 'before.json', initial)
    write_json(journal, report)
    try:
        ui.prepare(plan)
        # Navigation/seed selection must not change any bound game data.
        validate_before(plan, read_snapshot(), now=now)
        for number in plan['selectedSlots']:
            check_cancelled()
            before = read_snapshot()
            one = build_preflight(before, request['field_id'], request['crop_item_id'], 1, now=now)
            if one['selectedSlots'] != [number]:
                raise PlantingError('空位顺序与原计划不同；停止，不能改选其他地块')
            validate_before(one, before, now=now)
            ui.locate(one)
            # locate may pan the camera; require fresh state and UI checks at the final position.
            current = read_snapshot()
            validate_before(one, current, now=now)
            ui.assert_ready(one)
            check_cancelled()
            write_json(run_dir / f'slot_{number}_before.json', current)
            ledger = folder / 'sent'
            ledger.mkdir(exist_ok=True)
            marker = ledger / (slot_key(one, current) + '.json')
            # Exclusive durable reservation precedes the click. Even a failed click is never retried.
            with marker.open('x', encoding='utf-8') as stream:
                json.dump(dict(fieldId=one['fieldId'], slot=number, cropItemId=one['cropItemId'],
                               oldStateToken=one['stateToken'], retryAllowed=False), stream)
                stream.flush()
                os.fsync(stream.fileno())
            report.update(status='input_reserved', seedInputsReserved=report['seedInputsReserved'] + 1)
            write_json(journal, report)
            started = clock()
            ui.plant(one)
            proof = None
            # Only cache polling is repeated. No input is sent from this bounded loop.
            for delay in (0, .4, .8, 1.6, 2.4):
                check_cancelled()
                if delay:
                    sleep(delay)
                after = read_snapshot()
                write_json(run_dir / f'slot_{number}_after.json', after)
                try:
                    # Production tuples can change without advancing storageSequence.
                    # Only an unchanged physical state is pending; contradictions are terminal.
                    proof = verify_after(one, current, after, now=now)
                except PlantingPending:
                    continue
                break
            if proof is None:
                raise PlantingError('缓存等待结束仍未确认；停止，不补点、不继续下一格')
            report['confirmedSlots'].append(dict(slot=number, decodeSeconds=round(clock() - started, 3)))
            report['status'] = 'slot_confirmed'
            write_json(journal, report)
        report.update(status='confirmed', validation=verify_after(plan, initial, after, now=now))
        write_json(run_dir / 'after.json', after)
        write_json(journal, report)
        write_json(run_dir / 'result.json', report)
        return report
    except BaseException as error:
        report.update(status='unconfirmed' if report['seedInputsReserved'] else 'blocked_before_input',
                      error=str(error), automaticRetryAllowed=False)
        # A navigation-only failure is safe to start again. An input reservation is not.
        if not report['seedInputsReserved']:
            report['outcome'] = 'no_planting_input'
        write_json(journal, report)
        write_json(run_dir / 'result.json', report)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--field', choices=FIELDS, default='field_1')
    parser.add_argument('--crop-item-id', type=int, default=14)
    parser.add_argument('--count', type=int, default=1)
    parser.add_argument('--apply', action='store_true', help='Explicit single finite execution')
    args = parser.parse_args()
    request = dict(field_id=args.field, crop_item_id=args.crop_item_id, count=args.count)
    if not args.apply:
        try:
            from .manual_planting import save_preflight
        except ImportError:
            from manual_planting import save_preflight
        result = save_preflight(**request)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return 0 if result['ready'] else 2
    # Import controllers only in the explicit execution branch.
    try:
        from .manual_planting_ui import ManualPlantingUI
        from .decoded_runtime import snapshot
        from .task_control import check_cancelled
        from .ui_worker import cycle_lock
    except ImportError:
        from manual_planting_ui import ManualPlantingUI
        from decoded_runtime import snapshot
        from task_control import check_cancelled
        from ui_worker import cycle_lock
    with cycle_lock(), ManualPlantingUI() as ui:
        result = execute_once(request, ui, snapshot, ROOT / '.tmp/manual_planting_execution',
                              check_cancelled=check_cancelled)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

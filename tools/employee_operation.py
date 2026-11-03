"""Bound one-shot production operations; persistence precedes each save/claim."""
from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4

try:
    from . import decoded_runtime as decoded
    from .runtime_state import write_json
    from .reconcile_production import collect_once
except ImportError:
    import decoded_runtime as decoded
    from runtime_state import write_json
    from reconcile_production import collect_once

ROOT = Path(__file__).resolve().parents[1] / '.tmp/employee_operations'


def read(path):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None


TERMINAL_STATUSES = frozenset({'collected', 'superseded_plan_cleared'})


def owner(slot, data):
    decoded.validate(data)
    decoded.require_normal_package(data)
    entry = decoded.plan(slot, data)
    if any(type(worker.get(key)) is not int or worker[key] <= 0
           for worker in entry['employees'] for key in ('uid', 'itemId', 'level')):
        raise decoded.DecodeError('Employee identity/level is incomplete')
    return {key: entry[key] for key in ('buildingUid', 'buildingItemId', 'employees')}


def unfinished_active(root=None):
    """A collected or superseded record stays on disk, but must not be resumed."""
    record = active(root)
    if record and record.get('status') in TERMINAL_STATUSES:
        return None
    return record


def active(root=None):
    root = Path(root or ROOT)
    pointer = read(root / 'active.json')
    if not pointer:
        return None
    identity = pointer.get('assignment_id', '')
    if len(identity) != 32 or any(c not in '0123456789abcdef' for c in identity):
        raise decoded.DecodeError('Invalid production operation pointer')
    record = read(root / identity / 'assignment.json')
    if (not record or record.get('assignment_id') != identity
            or Path(record.get('operation_dir', '')) != root / identity):
        raise decoded.DecodeError('Production operation record missing; preserve pointer')
    return record


def begin(allocation, root=None):
    root = Path(root or ROOT)
    old = active(root)
    if old and old['status'] not in TERMINAL_STATUSES:
        raise decoded.DecodeError('Previous employee operation unresolved; resume before new plans')
    identity = uuid4().hex
    folder = root / identity
    record = dict(schemaVersion=1, assignment_id=identity, operation_dir=str(folder),
                  status='applying', allocation=allocation,
                  created_at_utc=datetime.now(timezone.utc).isoformat())
    write_json(folder / 'assignment.json', record)
    write_json(root / 'active.json', {'assignment_id': identity})
    return record


def validate_target(allocation, data):
    decoded.validate(data)
    target = allocation.get('bound_target') or {}
    order = next((row for row in data['orders'] if row['category'] == 'normal'
                  and row['uid'] == target.get('game_order_uid')), None)
    decoded.require_normal_package(data)
    if (target.get('batch_count') != 3
            or target.get('data_source') != 'decoded' or not order or order['statusCode'] != 1
            or {row['name']: row['count'] * 3 for row in order['costs']}
                != {row['name']: row['required'] for row in target.get('items', [])}
            or any(row['actual'] != decoded.item_count(cost['itemId'], data)
                   for cost in order['costs'] for row in target['items'] if row['name'] == cost['name'])):
        raise decoded.DecodeError('Production source UID, triple recipe or stock changed; rescan before new plans')


def job_path(record, slot):
    if slot not in {job['slot_id'] for job in record['allocation']['jobs']}:
        raise decoded.DecodeError('Slot outside the bound assignment')
    return Path(record['operation_dir']) / 'saves' / (slot + '.json')


def begin_edit(record, slot, items, data):
    path = job_path(record, slot)
    previous = read(path)
    if previous:
        return reconcile_save(record, slot, data)
    identity = owner(slot, data)
    blocker = decoded.assignment_blocker(slot, data)
    if blocker or data['employees']['repeatEnabled'] is not False:
        raise decoded.DecodeError(f'{slot}: production/repeat mode is busy or unconfirmed: {blocker}')
    expected = next(job['items'] for job in record['allocation']['jobs'] if job['slot_id'] == slot)
    if items != expected:
        raise decoded.DecodeError('Requested items differ from bound production operation')
    entry = dict(status='editing', slot_id=slot, items=items, owner=identity,
                 package_version=data['packageVersion'],
                 assignment_id=record['assignment_id'], automatic_retry_allowed=False)
    write_json(path, entry)
    return entry


def reserve_save(record, slot, data):
    path = job_path(record, slot)
    entry = read(path)
    if not entry or entry['status'] != 'editing' or owner(slot, data) != entry['owner']:
        raise decoded.DecodeError('Save identity changed; no save input')
    if decoded.assignment_blocker(slot, data) or data['employees']['repeatEnabled'] is not False:
        raise decoded.DecodeError('Production changed while editing; no save input')
    entry.update(status='save_intent')
    write_json(path, entry)
    return entry


def reconcile_save(record, slot, data):
    path = job_path(record, slot)
    entry = read(path)
    if not entry or owner(slot, data) != entry['owner']:
        raise decoded.DecodeError('Saved production owner/worker changed; no replay')
    if (entry['status'] not in ('save_intent', 'saved') or decoded.plan_items(slot, data) != entry['items']
            or data['employees']['repeatEnabled'] is not False):
        raise decoded.DecodeError('Unresolved save/edit; no repeated editor or save input')
    entry.update(status='saved', verified=True, repeat=False, one_shot=True)
    write_json(path, entry)
    return entry


def verify_owner(record, slot, data):
    entry = read(job_path(record, slot))
    if not entry or entry['status'] != 'saved' or owner(slot, data) != entry['owner']:
        raise decoded.DecodeError('Collection must match bound building and employees')
    return entry


def collection_record(record, slot):
    return read(Path(record['operation_dir']) / 'claims/collections' / (slot + '.json'))


def collect(record, slot, reader, action):
    before = reader()
    saved = verify_owner(record, slot, before)
    previous = collection_record(record, slot)
    if previous and previous['status'] == 'confirmed':
        return dict(collected=True, recovered_by_readback=True, game_operations=0)
    if not previous or previous['status'] not in ('intent', 'unconfirmed'):
        if decoded.plan_items(slot, before) != saved['items']:
            raise decoded.DecodeError('Collection recipe differs from bound save')
    result = collect_once(slot, reader, action, Path(record['operation_dir']) / 'claims')
    return dict(collected=True, **result)


def mark(record, status):
    if status == 'collected' and not all(
            (collection_record(record, job['slot_id']) or {}).get('status') == 'confirmed'
            for job in record['allocation']['jobs']):
        raise decoded.DecodeError('Not every bound collection is confirmed')
    record = {**record, 'status': status}
    if status == 'saved':
        record['saved'] = [read(job_path(record, job['slot_id'])) for job in record['allocation']['jobs']]
        record['state_reader'] = 'decoded'
    write_json(Path(record['operation_dir']) / 'assignment.json', record)
    return record


def supersede_cleared_plans(record, data):
    """Close a saved op when bound recipes are gone and the employee plan is idle.

    Physical crops on the land may still exist (manual planting). This only
    authorizes skipping our bound tomato/corn-style collection; it never clicks.
    """
    decoded.validate(data)
    decoded.require_normal_package(data)
    if record.get('status') != 'saved':
        raise decoded.DecodeError('Only a saved production operation can be superseded')
    evidence = []
    for job in record['allocation']['jobs']:
        slot = job['slot_id']
        if collection_record(record, slot) is not None:
            raise decoded.DecodeError(f'{slot}: collection history must be reconciled before superseding')
        saved = read(job_path(record, slot))
        if not saved or saved.get('status') != 'saved':
            raise decoded.DecodeError(f'{slot}: missing saved job while superseding')
        identity = owner(slot, data)
        def stable(entry):
            return {
                'buildingUid': entry['buildingUid'],
                'buildingItemId': entry['buildingItemId'],
                'employees': [{'uid': worker['uid'], 'itemId': worker['itemId']}
                              for worker in entry['employees']],
            }
        if stable(identity) != stable(saved['owner']):
            raise decoded.DecodeError(f'{slot}: worker/building changed; keep unresolved')
        current = decoded.plan_items(slot, data)
        entry = decoded.plan(slot, data)
        if current == saved['items']:
            raise decoded.DecodeError(f'{slot}: bound recipe still present; collect instead')
        if (entry['targets'] or entry['heldProducts'] or entry.get('productionOperations')
                or any(row['count'] > 0 for row in entry.get('accumulated', []))):
            raise decoded.DecodeError(f'{slot}: employee plan still busy; cannot supersede')
        evidence.append({
            'slot_id': slot,
            'saved_items': saved['items'],
            'current_items': current,
            'owner': identity,
            'saved_owner': saved['owner'],
            'heldProducts': entry['heldProducts'],
            'targets': entry['targets'],
        })
    record = {
        **record,
        'status': 'superseded_plan_cleared',
        'supersede_evidence': evidence,
        'superseded_at_utc': datetime.now(timezone.utc).isoformat(),
        'package_version_at_supersede': data['packageVersion'],
        'automatic_retry_allowed': False,
    }
    write_json(Path(record['operation_dir']) / 'assignment.json', record)
    write_json(Path(record['operation_dir']) / 'supersede_cleared.json', {
        'assignment_id': record['assignment_id'],
        'evidence': evidence,
        'captured_at_utc': datetime.now(timezone.utc).isoformat(),
    })
    return record

"""Compare decoded cache with saved OCR evidence and preview existing pure planning."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

try:
    from .allocate_employee_work import plan_decoded
    from .decoded_state_adapter import adapt_snapshot
    from .runtime_state import write_json
except ImportError:
    from allocate_employee_work import plan_decoded
    from decoded_state_adapter import adapt_snapshot
    from runtime_state import write_json


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / '.tmp/decoded_shadow'


def read_baseline(path: Path) -> tuple[dict, dict | None]:
    metadata = {'path': str(path.resolve()), 'available': path.exists(),
                'same_moment_verified': False}
    if not path.exists():
        return metadata, None
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    metadata.update(generated_at_utc=data.get('generated_at_utc'),
                    file_modified_at_utc=datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
                    timestamp_note='File mtime is provenance only, not proof of capture time.')
    return metadata, data


def compare_inventory(adapted: dict, baseline: dict | None) -> dict:
    if baseline is None:
        return {'status': 'no_baseline'}
    current = {entry['id']: entry for entry in adapted['inventory']['items'] if entry['id']}
    rows = []
    for old in baseline.get('items', []):
        new = current.get(old.get('id'))
        if not new or old.get('name') != new['name']:
            rows.append({'id': old.get('id'), 'name': old.get('name'), 'status': 'identity_not_mapped'})
            continue
        previous = old.get('quantity')
        rows.append({'id': new['id'], 'game_item_id': new['game_item_id'], 'name': new['name'],
                     'legacy': previous, 'decoded': new['quantity'],
                     'delta': new['quantity'] - previous if type(previous) is int else None,
                     'status': 'unreadable_baseline' if type(previous) is not int else
                               'equal' if previous == new['quantity'] else 'different'})
    return {'status': 'saved_evidence_only', 'equal': sum(x['status'] == 'equal' for x in rows),
            'different': sum(x['status'] == 'different' for x in rows),
            'unmapped': sum(x['status'] == 'identity_not_mapped' for x in rows),
            'baseline_unknown_slots': len(baseline.get('unknown_slots', [])), 'items': rows}


def signature(items: list[dict]):
    if not items or any(type(x.get('required')) is not int or x['required'] < 1 or not x.get('name') for x in items):
        return None
    if len({x['name'] for x in items}) != len(items):
        return None
    return tuple(sorted((x['name'], x['required']) for x in items))


def compare_orders(adapted: dict, baseline: dict | None) -> dict:
    if baseline is None:
        return {'status': 'no_baseline'}
    kind = baseline.get('order_type', 'normal')
    if kind not in adapted['orders']:
        return {'status': 'unknown_order_category', 'order_type': kind}
    count = baseline.get('batch_count', baseline.get('completion', {}).get('required'))
    expected = adapted['batch_counts_assumed'][kind]
    if type(count) is not int or count != expected:
        return {'status': 'batch_count_unknown_or_different', 'order_type': kind,
                'baseline_batch_count': count, 'decoded_assumed_batch_count': expected}
    entries = baseline.get('orders') or baseline.get('skipped') or []
    if not entries and isinstance(baseline.get('target_order'), dict):
        entries = [baseline['target_order']]
    rows = []
    for old in entries:
        key = signature(old.get('items', []))
        if key is not None and any(required % count for _, required in key):
            rows.append({'legacy_slot': old.get('order'), 'recipe': [list(x) for x in key],
                         'decoded_candidate_slots': [], 'decoded_candidate_uids': [],
                         'status': 'batch_metadata_inconsistent',
                         'note': 'Recorded quantities are not divisible by the recorded batch count; checkbox/batch evidence is insufficient.'})
            continue
        candidates = [x for x in adapted['orders'][kind] if key is not None and signature(x['items']) == key]
        row = {'legacy_slot': old.get('order'), 'recipe': [list(x) for x in key] if key else None,
               'decoded_candidate_slots': [x['order'] for x in candidates],
               'decoded_candidate_uids': [x['game_order_uid'] for x in candidates],
               'status': 'unreadable_baseline' if key is None else 'recipe_absent' if not candidates else
                         'ambiguous_recipe' if len(candidates) > 1 else 'unique_recipe_match'}
        if len(candidates) == 1:
            stock = {x['name']: x['actual'] for x in candidates[0]['items']}
            row['inventory_differences'] = [dict(name=x['name'], legacy=x.get('actual'), decoded=stock[x['name']])
                                           for x in old['items'] if stock[x['name']] != x.get('actual')]
        rows.append(row)
    return {'status': 'saved_evidence_only', 'order_type': kind, 'batch_count': count,
            'recipe_matches': sum(bool(x['decoded_candidate_slots']) for x in rows), 'orders': rows,
            'identity_note': 'Match complete recipes, not slot numbers; duplicate recipes remain ambiguous.'}


def compare_employees(adapted: dict, baseline: dict | None) -> dict:
    if baseline is None:
        return {'status': 'no_baseline'}
    current = {entry['slot_id']: entry for entry in adapted['employees']}
    rows = []
    for old in baseline.get('saved', []):
        new = current.get(old.get('slot_id'))
        rows.append({'slot_id': old.get('slot_id'), 'legacy_targets': old.get('items'),
                     'decoded_targets': new['items'] if new else None,
                     'status': 'slot_not_mapped' if new is None else
                               'equal' if old.get('items') == new['items'] else 'different',
                     'cached_state': new['cached_state'] if new else None})
    return {'status': 'historical_assignment_not_live_observation', 'plans': rows}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--decoded', type=Path, help='use an existing decoder JSON without reading game memory')
    source.add_argument('--live', action='store_true', help='run only the read-only Node decoder once')
    parser.add_argument('--normal-count', type=int, choices=(1, 2, 3), default=3)
    parser.add_argument('--party-count', type=int, choices=range(1, 7), default=6)
    parser.add_argument('--takeout-count', type=int, choices=range(1, 7), default=6)
    parser.add_argument('--legacy-inventory', type=Path, default=ROOT / 'assets/catalog/backpack_crops/latest_scan.json')
    parser.add_argument('--legacy-orders', type=Path, action='append', help='saved OCR JSON; repeat for multiple sources')
    parser.add_argument('--legacy-employees', type=Path, default=ROOT / 'assets/catalog/employee_work/latest_assignment.json')
    args = parser.parse_args()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    decoded_path = args.decoded
    if args.live:
        decoded_path = OUTPUT / 'current_state.json'
        # Fixed command: no OCR scan, workflow, controller, or game-action executable.
        completed = subprocess.run(['node', str(ROOT / 'tools/read_only_game_state.cjs'), 'all',
                                    '--python', sys.executable, '--output', str(decoded_path)],
                                   capture_output=True, text=True, encoding='utf-8', timeout=120,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if completed.returncode:
            raise RuntimeError('read-only decoder failed: ' + completed.stderr)
    snapshot = json.loads(decoded_path.read_text(encoding='utf-8-sig'))
    counts = dict(normal=args.normal_count, party=args.party_count, takeout=args.takeout_count)
    adapted = adapt_snapshot(snapshot, counts)
    preview = plan_decoded(snapshot, args.normal_count)
    inv_meta, inv = read_baseline(args.legacy_inventory)
    emp_meta, emp = read_baseline(args.legacy_employees)
    order_paths = args.legacy_orders or [ROOT / 'assets/catalog/normal_orders/latest_scan.json',
                                       ROOT / 'assets/catalog/normal_orders/latest_completion.json']
    comparisons = []
    for file in order_paths:
        metadata, baseline = read_baseline(file)
        comparisons.append({'baseline': metadata, **compare_orders(adapted, baseline)})
    report = {'mode': 'read_only_shadow_validation', 'execution_eligible': False,
              'generated_at_utc': datetime.now(timezone.utc).isoformat(),
              'decoded_path': str(decoded_path.resolve()), 'decoded_captured_at': snapshot['capturedAt'],
              'live_memory_read_this_run': args.live, 'structure_validation': 'passed',
              'same_moment_ocr_cross_validation': 'not_performed', 'live_reader_replaced': False,
              'batch_counts_assumed': counts, 'ui_batch_controls_verified': False,
              'counts': {'inventory_products': len(adapted['inventory']['items']),
                         'orders': {kind: len(entries) for kind, entries in adapted['orders'].items()},
                         'employee_plans': len(adapted['employees']),
                         'family_slots': len((adapted.get('family') or {}).get('tasks', []))},
              'mapping_issues': adapted['mapping_issues'],
              'inventory_comparison': {'baseline': inv_meta, **compare_inventory(adapted, inv)},
              'order_comparisons': comparisons,
              'employee_comparison': {'baseline': emp_meta, **compare_employees(adapted, emp)},
              'planning_preview': preview,
              'limitations': ['Saved OCR data is historical; numerical differences are not decoder errors.',
                             'Quantity multipliers are planning assumptions; UI checkbox state is unknown.',
                             'Employee capabilities use the existing catalog as requested; allocation is preview only.',
                             'No same-time screenshots or submission, collection, refresh, or plan-save verification.',
                             'Family board is a new data source; no existing OCR consumer was found.']}
    write_json(OUTPUT / 'compatibility.json', adapted)
    write_json(OUTPUT / 'planning_preview.json', preview)
    write_json(OUTPUT / 'comparison.json', report)
    print(json.dumps({'report': str(OUTPUT / 'comparison.json'), 'counts': report['counts'],
                      'mapping_issues': report['mapping_issues'],
                      'inventory_equal': report['inventory_comparison'].get('equal'),
                      'inventory_different': report['inventory_comparison'].get('different'),
                      'same_moment_ocr_cross_validation': report['same_moment_ocr_cross_validation'],
                      'execution_eligible': False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, KeyError, OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(json.dumps({'error': str(error), 'execution_eligible': False}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)

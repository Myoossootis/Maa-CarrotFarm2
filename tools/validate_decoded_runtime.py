"""Read-only validation of the data backend used by formal Maa workers."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path

try:
    from . import decoded_runtime as decoded
    from .decoded_state_adapter import adapt_snapshot
    from .runtime_state import write_json
except ImportError:
    import decoded_runtime as decoded
    from decoded_state_adapter import adapt_snapshot
    from runtime_state import write_json


def main():
    os.environ['CARROT_STATE_READER'] = 'decoded'
    data = decoded.snapshot()
    adapted = adapt_snapshot(data, dict(normal=3, party=6, takeout=6))
    orders = []
    for raw in data['orders']:
        count = 3 if raw['category'] == 'normal' else 6
        decoded.select_order(raw['category'], raw['slot'], count)
        try:
            if raw['statusCode'] != 1:
                items, status = [], 'non_waiting_' + str(raw['statusCode'])
            else:
                items = decoded.read_selected(data)
                status = 'decoded'
        except decoded.OrderUnavailable as error:
            items, status = [], str(error)
        orders.append({'category': raw['category'], 'slot': raw['slot'], 'uid': raw['uid'],
                       'batch_count': count, 'status': status, 'items': items})
    employees = []
    for slot, entry in decoded.plans(data).items():
        employees.append({'slot': slot, 'items': decoded.plan_items(slot, data),
                          'idle': decoded.is_idle(slot, data), 'progress': decoded.progress(slot, data=data)})
    report = {'mode': 'formal_backend_read_only_validation', 'read_only': True,
              'generated_at_utc': datetime.now(timezone.utc).isoformat(),
              'game_operations_executed': 0, 'formal_action_chain_executed': False,
              'operation_feedback_verified': False, 'state_reader': 'decoded',
              'family_scan_required': False, 'storage_sequence': data['storageSequence'],
              'snapshot_captured_at': data['capturedAt'], 'timings': data['timings'],
              'inventory_products': len(adapted['inventory']['items']), 'mapping_issues': adapted['mapping_issues'],
              'orders': orders, 'employees': employees,
              'limitations': ['Local cache is not an atomic server snapshot.',
                             'No click, save, accept, claim or post-operation cache flush tested.',
                             'Unsaved editor drafts are checked only after save, against persisted decoded data.']}
    folder = Path(__file__).resolve().parents[1] / '.tmp/decoded_runtime'
    write_json(folder / 'snapshot.json', data)
    write_json(folder / 'validation.json', report)
    print(json.dumps({'report': str(folder / 'validation.json'), 'state_reader': 'decoded',
                      'inventory_products': report['inventory_products'], 'orders': len(orders),
                      'employee_plans': len(employees), 'mapping_issues': report['mapping_issues'],
                      'game_operations_executed': 0}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

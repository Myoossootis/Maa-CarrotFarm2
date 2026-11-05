"""Pure adapters for shadow validation. No controllers, clicks, or game imports."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
import json
from pathlib import Path

try:
    from .production_targets import load_decoded_products
except ImportError:
    from production_targets import load_decoded_products


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / 'assets/catalog/backpack_crops/registry.json'
SLOT_BY_BUILDING = {
    12: 'field_1', 284: 'field_2', 285: 'field_3', 17: 'ranch_1',
    723: 'ranch_2', 28: 'orchard_1', 286: 'orchard_2', 32: 'mill', 878: 'street',
}
ORDER_TYPES = ('normal', 'party', 'takeout')


def integer(value, label: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f'{label}: expected integer >= {minimum}')
    return value


def adapt_snapshot(snapshot: dict, batch_counts: dict[str, int], registry: dict | None = None) -> dict:
    """Map game IDs to existing crop/slot IDs; batch size is an explicit assumption."""
    if snapshot.get('schemaVersion') != 1 or snapshot.get('readOnly') is not True or snapshot.get('source') != 'local_client_cache':
        raise ValueError('unsupported decoder schema or source')
    captured = datetime.fromisoformat(snapshot['capturedAt'].replace('Z', '+00:00'))
    if captured.tzinfo is None:
        raise ValueError('decoder timestamp must have a timezone')
    for kind in ORDER_TYPES:
        integer(batch_counts[kind], f'{kind} batch count', 1)
        if batch_counts[kind] > (3 if kind == 'normal' else 6):
            raise ValueError(f'{kind}: batch count exceeds known UI range')
    if registry is None:
        registry = json.loads(REGISTRY.read_text(encoding='utf-8'))
    by_name = {entry['name']: entry['id'] for entry in registry['products']}
    if len(by_name) != len(registry['products']):
        raise ValueError('ambiguous names in legacy crop registry')
    extensions = load_decoded_products()
    by_game = {entry['game_item_id']: entry for entry in extensions}
    by_name.update({entry['name']: entry['id'] for entry in extensions})

    def mapped_id(game_id, name):
        if game_id in by_game and name != by_game[game_id]['name']:
            raise ValueError(f'decoded product name conflicts with configured ID: {game_id}')
        return by_name.get(name) if name not in repeated_names else None
    products = snapshot['inventory']['products']
    repeated_names = {name for name, count in Counter(x['name'] for x in products).items() if count > 1}
    stock, issues, items, names = {}, [], [], {}
    for product in products:
        game_id = integer(product['itemId'], 'game item ID', 1)
        count = integer(product['count'], 'inventory count')
        if game_id in stock:
            raise ValueError(f'duplicate inventory game ID: {game_id}')
        stock[game_id] = count
        names[game_id] = product['name']
        legacy_id = mapped_id(game_id, product['name'])
        if legacy_id is None:
            issues.append({'kind': 'unmapped_product', 'game_item_id': game_id, 'name': product['name']})
        items.append({'id': legacy_id, 'game_item_id': game_id, 'name': product['name'],
                      'quantity': count, 'source': 'decoded_cache'})

    orders = {kind: [] for kind in ORDER_TYPES}
    seen = set()
    for order in snapshot['orders']:
        kind = order['category']
        if kind not in orders:
            raise ValueError(f'unknown order category: {kind}')
        slot = integer(order['slot'], 'order slot', 1)
        if (kind, slot) in seen:
            raise ValueError(f'duplicate order slot: {kind}/{slot}')
        seen.add((kind, slot))
        normalized = []
        mapped = True
        for cost in order['costs']:
            game_id = integer(cost['itemId'], 'cost game item ID', 1)
            if game_id in names and names[game_id] != cost['name']:
                raise ValueError(f'cost name conflicts with inventory game ID: {game_id}')
            required = integer(cost['count'], 'single order requirement', 1) * batch_counts[kind]
            actual = stock.get(game_id, 0)
            legacy_id = mapped_id(game_id, cost['name'])
            if legacy_id is None:
                mapped = False
            normalized.append({'id': legacy_id, 'game_item_id': game_id, 'name': cost['name'],
                               'actual': actual, 'required': required, 'missing': max(0, required - actual),
                               'satisfied': actual >= required, 'single_required': cost['count']})
        if len({x['game_item_id'] for x in normalized}) != len(normalized):
            raise ValueError('duplicate cost IDs in order')
        reason = ('unmapped_product' if not mapped else 'unreadable' if not normalized else
                  'non_waiting' if order['statusCode'] != 1 else
                  'ready' if all(x['satisfied'] for x in normalized) else 'insufficient')
        orders[kind].append({'order': slot, 'game_order_uid': order['uid'], 'items': normalized,
                             'reason': reason, 'status_code': order['statusCode'],
                             'batch_count': batch_counts[kind], 'batch_count_verified_in_ui': False})
    for entries in orders.values():
        entries.sort(key=lambda entry: entry['order'])

    employees = []
    seen_slots = set()
    for plan in snapshot['employees']['plans']:
        slot_id = SLOT_BY_BUILDING.get(plan['buildingItemId'])
        if slot_id is None:
            issues.append({'kind': 'unmapped_building', 'building_item_id': plan['buildingItemId']})
        elif slot_id in seen_slots:
            raise ValueError(f'duplicate employee building: {slot_id}')
        seen_slots.add(slot_id)
        targets = {}
        for target in plan['targets']:
            if target['name'] in targets:
                raise ValueError('ambiguous employee target names')
            targets[target['name']] = integer(target['count'], 'employee target')
        # Empty plans are only a cache observation, not authority to configure a worker.
        busy = bool(targets or plan['heldProducts'] or plan['productionOperations'])
        worker_available = any(worker.get('available') is not False for worker in plan['employees'])
        employees.append({'slot_id': slot_id, 'building_game_id': plan['buildingItemId'],
                          'workers': plan['employees'], 'items': targets, 'held_products': plan['heldProducts'],
                          'cached_state': 'busy' if busy else 'no_plan_in_cache' if worker_available else 'worker_unavailable',
                          'idle_verified_for_execution': False})
    family = snapshot.get('family')
    lion_counter = None
    if family is not None and snapshot.get('mode') == 'lion_counter':
        if set(family) != {'period'}:
            raise ValueError('counter-only report must not impersonate a task board')
        lion_counter = family['period']
        family = None
    if family is not None:
        tasks = family['tasks']
        if len(tasks) != 90 or {x['slot'] for x in tasks} != set(range(1, 91)):
            raise ValueError('family board must contain exactly 90 unique slots')
        for task in tasks:
            if task['acceptor'] and task['status'] != 'occupied':
                raise ValueError('occupied task has inconsistent status')
            if not task['progress']['available'] and task['progress']['current'] is not None:
                raise ValueError('unavailable task progress must remain null')
    return {'source': 'decoded_cache_shadow', 'generated_at_utc': snapshot['capturedAt'],
            'quantity_rule': 'single_requirement_times_selected_completion_count',
            'quantity_rule_source': 'client_code_and_user_confirmation',
            'execution_eligible': False, 'batch_counts_assumed': dict(batch_counts),
            'ui_batch_controls_verified': False,
            'inventory': {'items': items, 'unknown_slots': [x for x in issues if x['kind'] == 'unmapped_product']},
            'orders': orders, 'employees': employees, 'family': family, 'lion_counter': lion_counter, 'mapping_issues': issues}

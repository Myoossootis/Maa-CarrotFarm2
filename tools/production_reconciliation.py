"""Pure current-production and historical-checkpoint reconciliation. No UI access."""
from collections import Counter
from datetime import datetime, timezone

try:
    from . import decoded_runtime as decoded
except ImportError:
    import decoded_runtime as decoded


def collection_identity(entry):
    """Bind the current owner, workers, recipe and held items, not a slot label."""
    return {key: entry.get(key) for key in ('buildingUid', 'buildingItemId', 'employees',
                                           'targets', 'heldProducts', 'accumulated', 'productionOperations')}


def collection_candidate(slot_id, data):
    entry = decoded.plan(slot_id, data)
    production = data.get('production') or {}
    areas = [row for row in production.get('buildings', []) if row['buildingItemId'] == entry['buildingItemId']]
    if (not production.get('semanticsPackageVersion') or
            production['semanticsPackageVersion'] != data.get('packageVersion') or
            len(areas) != 1 or not entry.get('buildingUid') or areas[0].get('buildingUid') != entry['buildingUid']):
        raise decoded.DecodeError('Production version or ownership unconfirmed: ' + slot_id)
    if production.get('complete') is not True and areas[0].get('complete') is not True:
        raise decoded.DecodeError('Target production area is incomplete: ' + slot_id)
    if data['employees'].get('repeatEnabled') is not False:
        raise decoded.DecodeError('Repeat production must be confirmed off')
    targets = entry['targets']
    if not targets or any(type(row['count']) is not int or row['count'] <= 0 for row in targets):
        raise decoded.DecodeError('No positive production target: ' + slot_id)
    if len({row['itemId'] for row in targets}) != len(targets):
        raise decoded.DecodeError('Duplicate target product: ' + slot_id)
    target = sum(row['count'] for row in targets)
    if decoded.progress(slot_id, target, data) != (target, target) or not entry['heldProducts']:
        raise decoded.DecodeError('Production is not fully ready: ' + slot_id)
    if entry.get('productionOperations'):
        raise decoded.DecodeError('Production operations still pending: ' + slot_id)
    return dict(slot_id=slot_id, target=target, identity=collection_identity(entry))


def confirm_collection(intent, current):
    """Used after restart as well: no clearance proof means no repeated input."""
    before = intent['before']
    candidate = intent['candidate']
    old = candidate['identity']
    entry = decoded.plan(candidate['slot_id'], current)
    if any(entry.get(key) != old.get(key) for key in ('buildingUid', 'buildingItemId', 'employees')):
        return False
    if current.get('packageVersion') != before.get('packageVersion'):
        return False
    if (not decoded.is_idle(candidate['slot_id'], current) or
            any(row['count'] > 0 for row in entry.get('accumulated', []))):
        return False
    held = Counter()
    for row in old['heldProducts']:
        held[row['itemId']] += row['count']
    return bool(held) and all(decoded.item_count(item, current) - decoded.item_count(item, before) >= count
                              for item, count in held.items())


def reconcile(data, *, feed=None, assignment=None, monitor=None, completion=None, manual=None, deliveries=None):
    decoded.validate(data)
    entries = []
    for slot_id, entry in decoded.plans(data).items():
        if slot_id in ('mill', 'street'):
            continue
        blocker = decoded.assignment_blocker(slot_id, data)
        try:
            candidate = collection_candidate(slot_id, data)
            state = 'ready_to_collect'
        except decoded.DecodeError:
            candidate = None
            state = 'available' if blocker is None else 'preserved'
        entries.append(dict(slot_id=slot_id, state=state, assignment_blocker=blocker,
                            identity=collection_identity(entry), candidate=candidate,
                            progress=decoded.progress_items(slot_id, data)))
    history = []
    if feed and feed.get('pending'):
        current = decoded.plan_items('field_3', data)
        # An old journal lacks account/cycle binding. Even equal items aren't proof.
        history.append(dict(source='feed', action=feed['pending'], status='unresolved',
                            expected=feed.get('items'), current=current,
                            reason='recipe_conflict' if current != feed.get('items') else 'missing_operation_identity',
                            automatic_retry_allowed=False))
    if assignment:
        slots = []
        for saved in assignment.get('saved', []):
            entry = decoded.plans(data).get(saved.get('slot_id'))
            actual = {row['name']: row['count'] for row in entry['targets']} if entry else None
            slots.append(dict(slot_id=saved.get('slot_id'), expected=saved.get('items'), current=actual,
                              recipe_matches=actual == saved.get('items')))
        history.append(dict(source='assignment', status='historical_unbound', slots=slots,
                            reason='legacy_checkpoint_has_no_bound_operation_identity', automatic_retry_allowed=False))
    if monitor:
        history.append(dict(source='monitor', status=monitor.get('status'),
                            automatic_retry_allowed=False, reason='must_match_current_bound_assignment'))
    if completion:
        history.append(dict(source='order', status=completion.get('status'),
                            captured_at=completion.get('generated_at_utc'),
                            automatic_retry_allowed=False, reason='fresh_order_selection_required'))
    if manual and manual.get('status') not in ('confirmed', 'blocked_before_input'):
        history.append(dict(source='manual_planting', status='unresolved', request=manual.get('request'),
                            automatic_retry_allowed=False, reason='historical_unconfirmed_seed_input'))
    for kind, delivery in (deliveries or {}).items():
        if delivery and delivery.get('status') in ('accept_intent', 'accepted'):
            history.append(dict(source='delivery', order_type=kind, status=delivery['status'],
                                automatic_retry_allowed=False, reason='receipt_or_reward_reconciliation_required'))
    return dict(schemaVersion=1, read_only=True, game_operations=0, captured_at=data['capturedAt'],
                generated_at=datetime.now(timezone.utc).isoformat(), package_version=data['packageVersion'],
                production_complete=data.get('production', {}).get('complete'),
                current=entries, historical=history,
                ready_to_collect=[row['slot_id'] for row in entries if row['candidate']],
                available=[row['slot_id'] for row in entries if row['state'] == 'available'])

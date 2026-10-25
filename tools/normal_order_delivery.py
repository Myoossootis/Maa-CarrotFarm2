"""Durable single normal-order receipt; uncertain inputs are never replayed."""
from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4

try:
    from . import decoded_runtime as decoded
    from .runtime_state import write_json
except ImportError:
    import decoded_runtime as decoded
    from runtime_state import write_json

PATH = Path(__file__).resolve().parents[1] / '.tmp/normal_order_delivery/active.json'


def load(path=PATH):
    if not path.exists():
        return None
    receipt = json.loads(path.read_text(encoding='utf-8'))
    if (receipt.get('schemaVersion') != 1 or receipt.get('order_type') != 'normal'
            or receipt.get('batch_count') != 3 or not receipt.get('operation_id')
            or receipt.get('status') not in {'accept_intent', 'accepted', 'reward_intent', 'completed'}):
        raise decoded.DecodeError('Invalid normal delivery receipt; preserve it and stop')
    return receipt


def reserve(items, number, data, path=PATH):
    old = load(path)
    if old and old['status'] != 'completed':
        raise decoded.DecodeError('Unresolved normal delivery; no new acceptance')
    decoded.validate(data)
    if data['packageVersion'] != '90':
        raise decoded.DecodeError('Normal delivery package version is unverified')
    uids = {item['game_order_uid'] for item in items}
    if len(uids) != 1 or not 2 <= len(items) <= 4:
        raise decoded.DecodeError('Normal delivery needs one UID and a complete recipe')
    uid = next(iter(uids))
    current = next((row for row in data['orders'] if row['uid'] == uid and row['category'] == 'normal'), None)
    expected = {row['itemId']: row['count'] * 3 for row in current['costs']} if current else {}
    actual = {row['game_item_id']: row['required'] for row in items}
    if (not current or current['statusCode'] != 1 or current['slot'] != number or actual != expected
            or len(actual) != len(items) or any(row.get('batch_count') != 3 or not row.get('satisfied')
                or row['actual'] != decoded.item_count(row['game_item_id'], data) for row in items)):
        raise decoded.DecodeError('Normal delivery identity, triple costs or stock changed')
    receipt = dict(schemaVersion=1, operation_id=uuid4().hex, order_type='normal', batch_count=3,
                   status='accept_intent', order=number, uid=uid, items=items,
                   package_version=data['packageVersion'], automatic_retry_allowed=False,
                   created_at_utc=datetime.now(timezone.utc).isoformat())
    write_json(path, receipt)
    return receipt


def recover(receipt, data, path=PATH):
    decoded.validate(data)
    if data['packageVersion'] != '90' or receipt['package_version'] != data['packageVersion']:
        raise decoded.DecodeError('Normal delivery version changed; no replay')
    if receipt['status'] == 'reward_intent':
        if receipt.get('reward_navigation_reserved') is True:
            try:
                return complete(receipt, data, path)
            except decoded.DecodeError:
                pass
        raise decoded.DecodeError('Unresolved normal reward input; no repeated claim or acceptance')
    if receipt['status'] == 'accept_intent':
        if not decoded.submission_confirmed(receipt['items'], data):
            raise decoded.DecodeError('Unresolved normal acceptance; no repeated input')
        receipt = {**receipt, 'status': 'accepted', 'acceptance_confirmed': True}
        write_json(path, receipt)
    return receipt


def reserve_reward(receipt, data, path=PATH):
    decoded.validate(data)
    if data['packageVersion'] != '90' or receipt['package_version'] != data['packageVersion']:
        raise decoded.DecodeError('Normal reward package version changed')
    order = next((row for row in data['orders'] if row['uid'] == receipt['uid'] and row['category'] == 'normal'), None)
    if (receipt['status'] != 'accepted' or not order or order['statusCode'] != 3
            or order['slot'] != receipt['order']
            or {row['itemId']: row['count'] * 3 for row in order['costs']}
               != {row['game_item_id']: row['required'] for row in receipt['items']}):
        raise decoded.DecodeError('Normal reward UID or success state not confirmed')
    receipt = {**receipt, 'status': 'reward_intent', 'reward_navigation_reserved': True}
    write_json(path, receipt)
    return receipt


def complete(receipt, data, path=PATH):
    decoded.validate(data)
    if data['packageVersion'] != '90' or receipt['package_version'] != data['packageVersion']:
        raise decoded.DecodeError('Normal reward package version changed')
    order = next((row for row in data['orders'] if row['uid'] == receipt['uid']), None)
    slot = next((row for row in data['orders'] if row['category'] == 'normal' and row['slot'] == receipt['order']), None)
    if (receipt['status'] != 'reward_intent' or not slot or slot['statusCode'] != 1
            or order and order['statusCode'] != 1):
        raise decoded.DecodeError('Normal reward outcome unconfirmed; no new order')
    receipt = {**receipt, 'status': 'completed', 'reward_claimed': True,
               'completed_at_utc': datetime.now(timezone.utc).isoformat()}
    write_json(path.parent / 'receipts' / (receipt['operation_id'] + '.json'), receipt)
    write_json(path, receipt)
    return receipt

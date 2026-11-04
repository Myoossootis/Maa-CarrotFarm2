"""Data backend for formal Maa workers. No controller, OCR, or game actions."""
from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime, timezone
import atexit
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time

try:
    from .decoded_state_adapter import adapt_snapshot, SLOT_BY_BUILDING
except ImportError:
    from decoded_state_adapter import adapt_snapshot, SLOT_BY_BUILDING

# CLI modules and package imports in the same Maa worker share selection identity.
sys.modules.setdefault('decoded_runtime', sys.modules[__name__])
sys.modules.setdefault('tools.decoded_runtime', sys.modules[__name__])
ROOT = Path(__file__).resolve().parents[1]
_selection = ContextVar('decoded_order_selection', default=None)

# Ordinary-order / employee-plan path. Package 99 reuses the 98 ViewFarm timing
# (byte-identical getOutputProgress, CROP_IDX, OUTPUT_TYPE, SRATE).
# Party/takeout capacity uses the same cfg contract on 90 and 99. Package 98
# stays out until that contract is rechecked for it.
VERIFIED_NORMAL_PACKAGES = frozenset({'90', '98', '99'})
VERIFIED_CAPACITY_PACKAGES = frozenset({'90', '99'})


class DecodeError(RuntimeError):
    """Stop the workflow; never silently fall back to OCR or stale disk output."""


class OrderUnavailable(DecodeError):
    pass


def enabled():
    mode = os.environ.get('CARROT_STATE_READER', 'ocr')
    if mode not in ('ocr', 'decoded'):
        raise DecodeError('Unknown CARROT_STATE_READER: ' + mode)
    return mode == 'decoded'


def require_normal_package(data):
    version = data.get('packageVersion')
    if version not in VERIFIED_NORMAL_PACKAGES:
        raise DecodeError(f'Normal-order package {version!r} is unverified; no UI input')
    return version


class DecoderSession:
    """One Node process. The key and game config stay in that process.

    Each request reads the MMKV file again. A response never reuses the
    previous bag, and the key is not returned to Python.
    """

    def __init__(self):
        self.proc = subprocess.Popen(
            ['node', str(ROOT / 'tools/read_only_game_state.cjs'), 'serve', '--python', sys.executable],
            cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding='utf-8',
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.seq = 0

    def request(self, mode, timeout=30):
        if self.proc.poll() is not None:
            raise DecodeError('解码进程已退出，未使用上一份背包')
        self.seq += 1
        ident = self.seq
        assert self.proc.stdin is not None and self.proc.stdout is not None
        self.proc.stdin.write(json.dumps({'id': ident, 'mode': mode}) + '\n')
        self.proc.stdin.flush()
        line = _read_line(self.proc.stdout, timeout)
        if line is None:
            self.close()
            raise DecodeError('解码读取超时，未使用上一份背包')
        if not line:
            self.close()
            raise DecodeError('解码进程没有返回，未使用上一份背包')
        payload = json.loads(line)
        if payload.get('id') != ident or payload.get('ok') is not True:
            raise DecodeError('解码读取失败，未退回识图：' + str(payload.get('error') or 'empty report'))
        report = payload.get('report')
        if not isinstance(report, dict) or 'key' in report:
            raise DecodeError('解码结果无效，未使用上一份背包')
        return report

    def close(self):
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=5)


def _read_line(pipe, timeout):
    box = queue.Queue(maxsize=1)
    threading.Thread(target=lambda: box.put(pipe.readline()), daemon=True).start()
    try:
        return box.get(timeout=timeout)
    except queue.Empty:
        return None


_session = None
_session_lock = threading.Lock()


def _close_session():
    global _session
    with _session_lock:
        if _session is not None:
            _session.close()
            _session = None


atexit.register(_close_session)


def _request_report(mode='automation'):
    """Ask the resident decoder for one fresh report."""
    global _session
    with _session_lock:
        if _session is None or _session.proc.poll() is not None:
            _session = DecoderSession()
        try:
            return _session.request(mode)
        except DecodeError:
            raise
        except (OSError, ValueError) as error:
            _close_unlocked()
            raise DecodeError('解码快照不可用：' + str(error)) from error


def _close_unlocked():
    global _session
    if _session is not None:
        _session.close()
        _session = None


def snapshot():
    """Fresh storage snapshot. Excludes the unrelated family heap scan."""
    try:
        data = _request_report('automation')
        validate(data)
        return data
    except DecodeError:
        raise
    except (OSError, ValueError, KeyError) as error:
        raise DecodeError('解码快照不可用：' + str(error)) from error


def validate(data):
    captured = datetime.fromisoformat(data['capturedAt'].replace('Z', '+00:00'))
    if captured.tzinfo is None:
        raise DecodeError('Decoded snapshot timestamp lacks timezone')
    age = (datetime.now(timezone.utc) - captured).total_seconds()
    if not -2 <= age <= 10:
        raise DecodeError(f'Decoded snapshot is stale: {age:.1f}s')
    adapt_snapshot(data, dict(normal=1, party=1, takeout=1))


def inventory(data=None):
    data = snapshot() if data is None else data
    return adapt_snapshot(data, dict(normal=1, party=1, takeout=1))['inventory']


def item_count(item_id, data=None):
    data = snapshot() if data is None else data
    return sum(x['count'] for x in data['inventory']['records'] if x['itemId'] == item_id)


def order_capacity(kind, data=None):
    data = snapshot() if data is None else data
    validate(data)
    capacity = data.get('orderCapacity') or {}
    version = data.get('packageVersion')
    if not isinstance(capacity, dict):
        raise DecodeError('Order capacity schema is invalid')
    entries = capacity.get('entries', [])
    if kind not in ('party', 'takeout') or version not in VERIFIED_CAPACITY_PACKAGES:
        raise DecodeError(f'Order capacity package {version!r} is unverified')
    if (capacity.get('complete') is not True or capacity.get('semanticsPackageVersion') != version
            or not isinstance(entries, list) or len(entries) != 2 or not all(isinstance(row, dict) for row in entries)
            or {row.get('category') for row in entries} != {'party', 'takeout'}):
        reason = capacity.get('reason') or 'incomplete'
        raise DecodeError('Order capacity is missing, incomplete or unverified: ' + str(reason))
    for row in entries:
        if (row.get('tokenItemId') != {'party': 103, 'takeout': 104}[row['category']]
                or type(row.get('remaining')) is not int or not 0 <= row['remaining'] <= 2**53 - 1
                or type(row.get('limit')) is not int or not 1 <= row['limit'] <= 6):
            raise DecodeError('Order capacity count or identity is invalid')
    return next(row for row in entries if row['category'] == kind)


def select_order(kind, number, count, expected_uid=None):
    if kind not in ('normal', 'party', 'takeout') or type(number) is not int or not 1 <= number <= 9:
        raise DecodeError('Explicit decoded order category and slot are required')
    if type(count) is not int or not 1 <= count <= (3 if kind == 'normal' else 6):
        raise DecodeError('Invalid completion multiplier')
    _selection.set((kind, number, count, expected_uid))


def set_batch_count(count):
    selected = _selection.get()
    if selected is None:
        raise DecodeError('No selected order')
    select_order(selected[0], selected[1], count, selected[3])


def selected_order():
    selected = _selection.get()
    if selected is None:
        raise DecodeError('No selected decoded order')
    return selected


def read_selected(data=None):
    selected = _selection.get()
    if selected is None:
        raise DecodeError('Order read requires an explicit selected slot; refusing to guess from an image')
    kind, number, count, uid = selected
    data = snapshot() if data is None else data
    raw = next((x for x in data['orders'] if x['category'] == kind and x['slot'] == number), None)
    if raw is None or (uid is not None and raw['uid'] != uid):
        raise DecodeError('Selected order disappeared or UID changed; no accept should be sent')
    if raw['statusCode'] != 1:
        raise DecodeError('订单不是待接取状态，停止且不自动更换：' + str(raw['statusCode']))
    eligibility = raw.get('menuEligibility')
    if not eligibility or eligibility['levelSufficient'] is None:
        raise DecodeError('Menu eligibility is missing from decoded order')
    if not eligibility['unlocked'] or not eligibility['levelSufficient']:
        raise OrderUnavailable('dish_level_locked: 菜品未解锁或等级太低')
    counts = dict(normal=1, party=1, takeout=1)
    counts[kind] = count
    entry = next(x for x in adapt_snapshot(data, counts)['orders'][kind] if x['order'] == number)
    if not 2 <= len(entry['items']) <= 4:
        raise OrderUnavailable('Recipe outside existing workflow ingredient-count policy')
    _selection.set((kind, number, count, raw['uid']))
    return [{**item, 'game_order_uid': raw['uid'], 'batch_count': count, 'data_source': 'decoded'} for item in entry['items']]


def assert_order_unchanged(items):
    current = read_selected()
    key = lambda rows: sorted((x['game_item_id'], x['required']) for x in rows)
    if key(current) != key(items) or not all(x['satisfied'] for x in current):
        raise DecodeError('Order recipe or stock changed before accept; no click sent')
    return current


def plans(data=None):
    data = snapshot() if data is None else data
    result = {}
    for plan in data['employees']['plans']:
        slot = SLOT_BY_BUILDING.get(plan['buildingItemId'])
        if slot:
            if slot in result:
                raise DecodeError('Duplicate decoded employee slot: ' + slot)
            result[slot] = plan
    return result


def plan(slot_id, data=None):
    entry = plans(data).get(slot_id)
    if entry is None or not entry['employees'] or any(x.get('available') is False for x in entry['employees']):
        raise DecodeError('Missing employee plan or worker: ' + slot_id)
    return entry


def plan_items(slot_id, data=None):
    rows = plan(slot_id, data)['targets']
    if len({x['name'] for x in rows}) != len(rows):
        raise DecodeError('Ambiguous product names in employee plan')
    return {x['name']: x['count'] for x in rows}


def is_idle(slot_id, data=None):
    entry = plan(slot_id, data)
    return not (entry['targets'] or entry['heldProducts'] or entry.get('productionOperations'))


def assignment_blocker(slot_id, data=None):
    """An empty employee plan alone does not authorize using planted ground."""
    data = snapshot() if data is None else data
    entry = plans(data).get(slot_id)
    if not entry or not entry['employees'] or any(x.get('available') is False for x in entry['employees']):
        return 'missing_employee'
    if not is_idle(slot_id, data):
        return 'employee_plan_busy'
    if any(x['count'] > 0 for x in entry.get('accumulated', [])):
        return 'employee_products_pending'
    production = data.get('production') or {}
    if (production.get('complete') is not True or not production.get('semanticsPackageVersion') or
            production['semanticsPackageVersion'] != data.get('packageVersion')):
        return 'production_state_incomplete'
    areas = [area for area in production.get('buildings', [])
             if area['buildingItemId'] == entry['buildingItemId']]
    owner = entry.get('buildingUid')
    if type(owner) is not int or owner <= 0 or len(areas) != 1 or areas[0].get('buildingUid') != owner:
        return 'production_owner_unconfirmed'
    area = areas[0]
    slots = area.get('slots', [])
    numbers = [slot.get('slot') for slot in slots]
    if (not slots or len(slots) != area.get('slotCount') or
            any(type(number) is not int for number in numbers) or
            sorted(numbers) != list(range(1, len(slots) + 1))):
        return 'production_state_incomplete'
    if any(slot.get('outputId') != 0 or slot.get('status') != 'empty' for slot in slots):
        return 'physical_production_busy'
    return None


def progress_items(slot_id, data=None):
    entry = plan(slot_id, data)
    gained = {x['itemId']: x['count'] for x in entry['accumulated']}
    held = {x['itemId']: x['count'] for x in entry['heldProducts']}
    return {x['name']: (gained.get(x['itemId'], 0) + held.get(x['itemId'], 0), x['count']) for x in entry['targets']}


def progress(slot_id, expected=None, data=None):
    products = progress_items(slot_id, data)
    if not products:
        return None
    total = sum(target for _, target in products.values())
    if expected is not None and total != expected:
        raise DecodeError(f'{slot_id}: decoded target {total} differs from assigned target {expected}')
    # Extra production of one crop cannot hide a shortage of another crop.
    return sum(min(current, target) for current, target in products.values()), total


def verify_plan(slot_id, items, repeat=False, timeout=8):
    deadline = time.monotonic() + timeout
    while True:
        data = snapshot()
        actual = plan_items(slot_id, data)
        if actual == items and (repeat is None or data['employees']['repeatEnabled'] is repeat):
            return {'data_source': 'decoded', 'storage_sequence': data['storageSequence'], 'items': actual}
        if time.monotonic() >= deadline:
            raise DecodeError(f'{slot_id}: saved plan not confirmed in cache; expected {items}, got {actual}. No retry of save click.')
        time.sleep(.25)


def verify_collection(slot_id, before, timeout=8):
    """Confirm one claim by both employee clearance and all held products arriving."""
    held = plan(slot_id, before)['heldProducts']
    if not held:
        raise DecodeError('No held products to confirm collection: ' + slot_id)
    started = time.monotonic()
    while True:
        data = snapshot()
        if is_idle(slot_id, data) and all(
            item_count(x['itemId'], data) - item_count(x['itemId'], before) >= x['count'] for x in held
        ):
            return dict(cleared=True, reason='decoded_plan_clear_and_inventory_received',
                        elapsed_seconds=round(time.monotonic() - started, 3),
                        storage_sequence=data['storageSequence'])
        if time.monotonic() - started >= timeout:
            raise DecodeError(f'{slot_id}: collection not confirmed in cache; no automatic retry')
        time.sleep(.25)


def submission_confirmed(items, data):
    """A dismissed prompt or a successful mouse call is never submission proof."""
    uids = {x['game_order_uid'] for x in items}
    if len(uids) != 1:
        raise DecodeError('Submission items must identify exactly one order')
    order = next((x for x in data['orders'] if x['uid'] in uids), None)
    return bool(order and order['statusCode'] in (2, 3, 99) and all(
        x['actual'] - item_count(x['game_item_id'], data) == x['required'] for x in items
    ))

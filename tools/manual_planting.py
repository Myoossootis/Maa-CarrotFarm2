"""Single manual-planting plan and decode guards. All public entry points are read-only."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

FIELDS = {'field_1': 12, 'field_2': 284, 'field_3': 285}
ROOT = Path(__file__).resolve().parents[1]


class PlantingError(ValueError):
    pass


class PlantingPending(PlantingError):
    """Fresh read, but no physical production slot has changed yet. Never retry input."""
    pass


def captured_time(snapshot):
    value = datetime.fromisoformat(snapshot['capturedAt'].replace('Z', '+00:00'))
    if value.tzinfo is None:
        raise PlantingError('快照缺少时区')
    return value.timestamp()


def source_check(snapshot):
    if snapshot.get('schemaVersion') != 1 or snapshot.get('readOnly') is not True or snapshot.get('source') != 'local_client_cache':
        raise PlantingError('需要本地只读解码快照')
    captured_time(snapshot)


def state_token(snapshot):
    """Bind physical slots, employee plans and actual bag balances, not an image coordinate."""
    value = {key: snapshot.get(key) for key in ('packageVersion', 'storageSequence', 'inventory', 'employees', 'production')}
    # Exclude advancing derived countdowns; preserve raw tuples, config and costs.
    production = value.get('production') or {}
    value['production'] = {'manualControl': production.get('manualControl'),
                           'plantingCatalog': production.get('plantingCatalog'),
                           'buildings': [{key: b.get(key) for key in ('buildingUid', 'carrierUid', 'slotCount', 'raw')}
                                         for b in production.get('buildings', [])]}
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def build_preflight(snapshot, field_id='field_1', crop_item_id=14, count=1, *, now=None):
    source_check(snapshot)
    if field_id not in FIELDS:
        raise PlantingError('农田必须为 field_1、field_2 或 field_3')
    if type(crop_item_id) is not int or crop_item_id <= 0 or type(count) is not int or not 1 <= count <= 36:
        raise PlantingError('作物ID必须为正整数，数量必须为1–36的整数（格数）')
    now = datetime.now(timezone.utc).timestamp() if now is None else now
    blockers = []
    if not -2 <= now - captured_time(snapshot) <= 10:
        blockers.append('快照超过10秒或时钟异常，请只读刷新；旧计划不能用于操作')
    production = snapshot.get('production') or {}
    if snapshot.get('packageVersion') != '87' or production.get('semanticsPackageVersion') != '87':
        blockers.append('客户端规则版本未核对')
    if production.get('complete') is not True:
        blockers.append('逐槽位解码不完整')
    control = production.get('manualControl') or {}
    if control.get('totalControlEnabled') is not False:
        blockers.append('一键全区域模式开启或未知，不能保证只种指定农田')
    matches = [b for b in production.get('buildings', []) if b.get('buildingItemId') == FIELDS[field_id] and b.get('kind') == 'field']
    field = matches[0] if len(matches) == 1 else None
    if field is None:
        blockers.append('目标农田缺失或存在重复载体')
    slots = field.get('slots', []) if field else []
    if field and (len(slots) != field.get('slotCount') or
                  sorted(s.get('slot') for s in slots) != list(range(1, len(slots) + 1)) or
                  any(s.get('status') == 'unknown' for s in slots)):
        blockers.append('目标农田槽位缺失、重复或状态未知')
    empty = [s['slot'] for s in slots if s.get('status') == 'empty' and s.get('outputId') == 0]
    if len(empty) < count:
        blockers.append(f'空位不足：需要{count}格，当前{len(empty)}格；不会铲除或领取已有作物')
    plans = [p for p in (snapshot.get('employees') or {}).get('plans', []) if p.get('buildingItemId') == FIELDS[field_id]]
    if len(plans) != 1 or field and plans[0].get('buildingUid') != field.get('buildingUid'):
        blockers.append('目标员工计划缺失或归属不一致')
    elif (plans[0].get('targets') or plans[0].get('heldProducts') or plans[0].get('productionOperations') or
          any(x.get('count', 0) > 0 for x in plans[0].get('accumulated', []))):
        blockers.append('该农田有员工计划、待领产品或未完成操作，避免争用槽位')
    crops = [c for c in production.get('plantingCatalog', []) if c.get('itemId') == crop_item_id]
    crop = crops[0] if len(crops) == 1 else None
    if crop is None:
        blockers.append('作物不在已核对的普通农田作物目录中')
    elif crop.get('unlocked') is not True:
        blockers.append('作物未解锁或玩家等级未确认')
    costs = []
    if crop is not None:
        if crop.get('costKnown') is not True:
            blockers.append('种植价格不是已核对的固定价格，无法确定消耗')
        for cost in crop.get('costs', []):
            unit, available = cost.get('perSlot'), cost.get('available')
            if type(unit) is not int or unit < 0 or available is not None and (type(available) is not int or available < 0):
                raise PlantingError('无效的种植费用或余额')
            total = unit * count
            shortfall = 0 if total == 0 else max(0, total - available) if available is not None else None
            costs.append({**cost, 'total': total, 'shortfall': shortfall})
            if total > 0 and available is None:
                blockers.append(f"{cost['name']}余额未知（缓存未提供），不能确认足够")
            elif shortfall:
                blockers.append(f"{cost['name']}不足：缺少{shortfall}")
    return {'schemaVersion': 1, 'readOnly': True, 'executionEnabled': False,
            'mode': 'preflight', 'capturedAt': snapshot['capturedAt'],
            'storageSequence': snapshot.get('storageSequence'), 'stateToken': state_token(snapshot),
            'fieldId': field_id, 'fieldName': field['buildingName'] if field else field_id,
            'buildingUid': field.get('buildingUid') if field else None,
            'carrierUid': field.get('carrierUid') if field else None,
            'cropItemId': crop_item_id, 'cropName': crop['name'] if crop else f'#{crop_item_id}',
            'acceptedOutputIds': crop.get('acceptedOutputIds', []) if crop else [],
            'count': count, 'emptySlots': empty, 'selectedSlots': sorted(empty)[:count], 'costs': costs,
            'ready': not blockers, 'blockers': blockers,
            'notes': ['数量按种植格数计算，不是产量，不乘以6',
                      '预检通过仅代表缓存条件满足；执行路径关闭，未种植',
                      '正式操作前必须重新读快照校验；结果未确认不得自动补点']}


def validate_before(plan, current, *, now=None):
    """Single-action guard: a changed state invalidates the plan instead of silently replanning."""
    fresh = build_preflight(current, plan['fieldId'], plan['cropItemId'], plan['count'], now=now)
    keys = ('stateToken', 'buildingUid', 'carrierUid', 'selectedSlots', 'acceptedOutputIds', 'costs')
    if not fresh['ready'] or not plan.get('ready') or any(fresh[k] != plan.get(k) for k in keys):
        raise PlantingError('操作前校验失败：计划状态、槽位或费用已变化；不得发送输入')
    return {'status': 'verified_before', 'readOnly': True, 'gameOperations': 0}


def verify_after(plan, before, after, *, now=None):
    """Check captured snapshots only. Never clicks, polls, retries or invokes a controller."""
    validate_before(plan, before, now=captured_time(before))
    source_check(after)
    now = datetime.now(timezone.utc).timestamp() if now is None else now
    if not -2 <= now - captured_time(after) <= 10 or captured_time(after) <= captured_time(before):
        raise PlantingError('操作后快照陈旧或早于操作前快照')
    if after.get('packageVersion') != before.get('packageVersion') or (after.get('production') or {}).get('complete') is not True:
        raise PlantingError('操作后版本改变或槽位解码不完整')
    if after.get('employees') != before.get('employees'):
        raise PlantingError('员工计划发生变化，无法归因本次操作')
    def slot_map(snapshot):
        result = {}
        for building in snapshot['production']['buildings']:
            for slot in building['slots']:
                key = (building['buildingUid'], building['carrierUid'], slot['slot'])
                if key in result:
                    raise PlantingError('重复的生产槽位')
                result[key] = slot
        return result
    old, new = slot_map(before), slot_map(after)
    expected = {(plan['buildingUid'], plan['carrierUid'], s) for s in plan['selectedSlots']}
    if old.keys() != new.keys():
        raise PlantingError('建筑或槽位身份发生变化')
    changed = {key for key in old if old[key]['raw']['tuple'] != new[key]['raw']['tuple']}
    if not changed:
        raise PlantingPending('目标槽位尚未更新，不能确认种植；不得自动重试')
    if changed != expected:
        raise PlantingError('实际变化槽位与计划不符：可能部分成功或发生额外操作；不得自动重试')
    for key in expected:
        prior, current = old[key], new[key]
        if (prior['outputId'] != 0 or current['outputId'] not in plan['acceptedOutputIds'] or
                not current.get('startedAtUnix') or not current.get('cycleIndex') or
                current['cycleIndex'] == prior['cycleIndex'] or current.get('status') not in ('growing', 'mature')):
            raise PlantingError('目标作物或新生产轮次未确认')
    for cost in plan['costs']:
        if cost['total'] == 0 and cost['available'] is None:
            continue
        def balance(snapshot):
            return sum(r['count'] for r in snapshot['inventory']['records'] if r['itemId'] == cost['itemId'])
        if balance(before) - balance(after) != cost['total']:
            raise PlantingError('实际扣除与预计消耗不一致，不能确认')
    return {'status': 'confirmed', 'readOnly': True, 'gameOperations': 0,
            'plantedSlots': plan['selectedSlots'], 'count': plan['count'],
            'storageSequenceAdvanced': after.get('storageSequence') != before.get('storageSequence'),
            'confirmationBasis': 'exact_target_slot_tuple_and_new_cycle',
            'verifiedFrom': 'two_decoded_snapshots', 'liveAcceptancePerformed': False}


def save_preflight(field_id='field_1', crop_item_id=14, count=1, output_dir=None):
    try:
        from .decoded_runtime import snapshot
    except ImportError:
        from decoded_runtime import snapshot
    current = snapshot()
    plan = build_preflight(current, field_id, crop_item_id, count)
    folder = Path(output_dir) if output_dir else ROOT / '.tmp/manual_planting'
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'current_state.json').write_text(json.dumps(current, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (folder / 'preflight.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--field', choices=FIELDS, default='field_1')
    parser.add_argument('--crop-item-id', type=int, default=14)
    parser.add_argument('--count', type=int, default=1)
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args()
    try:
        plan = save_preflight(args.field, args.crop_item_id, args.count, args.output_dir)
        print(json.dumps(plan, ensure_ascii=False))
        return 0 if plan['ready'] else 2
    except (ValueError, KeyError, OSError, RuntimeError) as error:
        print(json.dumps({'readOnly': True, 'executionEnabled': False, 'error': str(error)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

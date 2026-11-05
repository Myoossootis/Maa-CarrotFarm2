"""Lion selection policy and durable, read-back-only recovery of board actions."""
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

from tools import decoded_runtime as decoded
from tools.employee_operation import read
from tools.runtime_state import write_json

ROOT = Path(__file__).resolve().parents[1] / '.tmp/lion_acquisition'
KNOWN_KINDS = {'harvest', 'party_takeout', 'regular_harvest', 'star_orders'}


def context(data):
    decoded.validate(data)
    ctx = data.get('familyContext') or {}
    tasks = (data.get('family') or {}).get('tasks', [])
    if data.get('packageVersion') != '100':
        raise decoded.DecodeError('狮王接取／刷新仅核对客户端100，未发送输入')
    if any(type(ctx.get(k)) is not int or ctx[k] <= 0 for k in ('selfUid', 'familyUid', 'season')):
        raise decoded.DecodeError('本人、家族或期数不完整')
    known=[t for t in tasks if not (t.get('status')=='unknown' and t.get('cacheConflict') is True)]
    if (len(tasks) != 90 or {t.get('slot') for t in tasks} != set(range(1, 91))
            or len({t.get('uid') for t in known}) != len(known)):
        raise decoded.DecodeError('需要90个唯一的当前狮王任务')
    for task in tasks:
        if task.get('status')=='unknown' and task.get('cacheConflict') is True:
            if (not isinstance(ctx.get('personalTasks'),list) or task.get('uid') is not None
                    or task.get('taskItemId') is not None or not task.get('conflictingUids')
                    or any(type(x) is not int or x<=0 for x in task['conflictingUids'])):
                raise decoded.DecodeError('冲突槽位不能作为空位，且需要当前个人任务列表证明')
            continue
        if any(type(task.get(k)) is not int or task[k] <= 0 for k in ('uid', 'taskItemId')):
            raise decoded.DecodeError('狮王任务身份无效')
        for key in ('acceptor', 'upgrader'):
            owner = task.get(key)
            if owner is not None and (not isinstance(owner, dict) or type(owner.get('uid')) is not int or owner['uid'] <= 0):
                raise decoded.DecodeError('任务占用／升级人未知')
    if not isinstance(ctx.get('staff'), list):
        raise decoded.DecodeError('员工拥有列表未读取')
    return ctx


def identity(data):
    ctx = context(data)
    return {**{k: ctx[k] for k in ('selfUid', 'familyUid', 'season')}, 'packageVersion': data['packageVersion']}


def owned(task, ctx):
    return (task.get('acceptor') or {}).get('uid') == ctx['selfUid']


def available(task, ctx):
    return (task.get('status') == 'unclaimed' and task.get('acceptor') is None
            and (task.get('upgrader') is None or task['upgrader']['uid'] == ctx['selfUid']))


def suitability(task, ctx):
    """Evaluate the resulting task, never the price printed on Upgrade."""
    final = task.get('upgrade') if task.get('canUpgrade') is True else task
    if not isinstance(final, dict):
        return False, '升级后的任务定义未知'
    req = final.get('requirement') or {}
    bells = (final.get('bellReward') or {}).get('count')
    if req.get('kind') == 'star_orders':
        return False, '餐厅星级任务留给家族'
    if req.get('kind') not in ('harvest', 'party_takeout'):
        return False, '不是可执行UP'
    if not final.get('isRare') or type(bells) is not int or bells < 80:
        return False, '升级后奖励不足80铃铛'
    if not any(s.get('itemId') == req.get('staffItemId') for s in ctx['staff']):
        return False, '未拥有指定UP员工'
    return True, '可做UP'


def executable(task, ctx):
    from tools.lion_task_operation import supports
    final = task.get('upgrade') or task
    req = final.get('requirement') or {}
    return (supports(final) and any(s.get('itemId') == req.get('staffItemId')
            and any(p.get('buildingItemId') in (12,284,285,17,723,28,286) for p in s.get('posts',[])) for s in ctx['staff']))


def choose(data, *, skipped=(), supported_only=False):
    ctx = context(data)
    own = [t for t in data['family']['tasks'] if owned(t, ctx)]
    personal=ctx.get('personalTasks')
    if personal is not None:
        if (not isinstance(personal,list) or any(type(p.get('status')) is not int or p['status'] not in (1,2,3,4)
                or type(p.get('slotUid')) is not int or p['slotUid']<=0 for p in personal)):
            raise decoded.DecodeError('本人个人任务状态或槽位引用未知，不能接新任务')
        pending=[p for p in personal if p['status'] in (1,2)]
        if (len(pending)>1 or len(own)!=len(pending)
                or pending and (own[0]['uid']!=pending[0]['slotUid'] or own[0]['taskItemId']!=pending[0]['taskItemId'])):
            raise decoded.DecodeError('本人在途任务与看板不一致或落在冲突槽，不能接取新任务')
    if len(own) > 1:
        raise decoded.DecodeError('发现多个本人任务，不自动选择')
    if own:
        if own[0].get('deadlinePassed') is True:
            raise decoded.DecodeError('本人任务已超时，保留任务，不刷新或退出')
        return 'own', own[0]
    if decoded.item_count(3956, data) < 1:
        return 'quota_empty', None
    tasks = [t for t in data['family']['tasks'] if available(t, ctx) and t['slot'] not in skipped
             and t.get('deadlinePassed') is not True]
    good = [t for t in tasks if suitability(t, ctx)[0] and (not supported_only or executable(t, ctx))]
    if good:
        # Prefer the already verified executor when both tasks otherwise qualify.
        from tools.lion_task_operation import supports
        return 'candidate', min(good, key=lambda t: (not t.get('isUpgraded'), not supports(t.get('upgrade') or t), t['slot']))
    garbage = [t for t in tasks if (t.get('requirement') or {}).get('kind') in KNOWN_KINDS
               and (t.get('requirement') or {}).get('kind') != 'star_orders' and not suitability(t, ctx)[0]]
    if garbage:
        return 'garbage', min(garbage, key=lambda t: ((t.get('bellReward') or {}).get('count', 9999), t['slot']))
    return 'no_candidate', None


def personal(task, data):
    rows = [r for r in data['inventory']['records'] if r['itemId'] == task['taskItemId']
            and str(r.get('extra', {}).get('r_id')) == str(task['uid'])]
    if len(rows) != 1 or str(rows[0].get('extra', {}).get('status')) not in ('1', '2'):
        raise decoded.DecodeError('个人任务实例未同步，不能继续点击')
    return rows[0]


def bound_task(record,data):
    """Current personal bag + the confirmed physical slot, never a chosen heap version."""
    ctx=data.get('familyContext') or {}
    items=ctx.get('personalTasks')
    uid=record.get('managedSlotUid')
    if not uid or items is None:
        return None
    current=[p for p in items if p.get('slotUid')==uid and p.get('status') in (1,2)]
    if (len(current)!=1 or sum(p.get('status') in (1,2) for p in items)!=1
            or any(p.get('status') not in (1,2,3,4) for p in items)):
        raise decoded.DecodeError('已接任务的当前个人实例缺失或重复，不能接新任务')
    proof=next((a for a in reversed(record['actions']) if a['slotUid']==uid and a['status']=='confirmed'),None)
    if proof is None:
        raise decoded.DecodeError('没有已确认槽位的操作证据')
    task=deepcopy(current[0])
    task.update(uid=uid,slot=proof['slot'],personalUid=current[0]['uid'],status='occupied',
                acceptor=dict(uid=ctx['selfUid']),upgrader=None,source='personal_bag_with_confirmed_slot')
    if type(task.get('deadlineUnix')) is not int or task['deadlineUnix']<=0:
        raise decoded.DecodeError('个人任务截止时间未确认')
    return task


def save(record):
    write_json(Path(record['folder']) / 'acquisition.json', record)


def begin(data, options, root=ROOT):
    bound = identity(data)
    root = Path(root)
    pointer = read(root / 'active.json')
    if pointer:
        key = pointer.get('id')
        if not isinstance(key, str) or len(key) != 32 or any(c not in '0123456789abcdef' for c in key):
            raise decoded.DecodeError('狮王选取记录指针损坏')
        record = read(root / key / 'acquisition.json')
        if not record or record.get('id') != key or record.get('folder') != str(root / key) or record.get('identity') != bound:
            raise decoded.DecodeError('狮王选取记录与账户／期数不符，保留原记录')
        if record['options'] != options:
            raise decoded.DecodeError('恢复须使用原选取配置；预算不能通过重启重置')
        return record
    key = uuid4().hex
    record = dict(id=key, folder=str(root / key), identity=bound, options=deepcopy(options),
                  actions=[], skipped=[], spent=0, refreshes=0, status='searching', managedSlotUid=None)
    save(record)
    write_json(root / 'active.json', dict(id=key))
    return record


def verify_identity(record, data):
    if identity(data) != record['identity']:
        raise decoded.DecodeError('狮王账户、家族、期数或版本改变')


def intent(record, action, task, before, cost=0):
    verify_identity(record, before)
    if record['actions'] and record['actions'][-1]['status'] == 'intent':
        raise decoded.DecodeError('上次操作尚未确认，禁止再次点击')
    entry = dict(action=action, status='intent', slot=task['slot'], slotUid=task['uid'],
                 taskItemId=task['taskItemId'], cost=cost, before=before,sourceTask=deepcopy(task))
    if action in ('refresh', 'upgrade', 'quit'):
        entry['personalUid'] = personal(task, before)['uid']
    record['actions'].append(entry)
    save(record)  # Must precede the first business click.
    return entry


def confirm(record, after):
    """Never click on recovery. A same-ID refresh needs personal-instance evidence."""
    verify_identity(record, after)
    entry = record['actions'][-1]
    if entry['status'] != 'intent':
        return True
    before = entry['before']
    ctx = after['familyContext']
    current = next((t for t in (after.get('family') or {}).get('tasks',[]) if t['uid'] == entry['slotUid']),None)
    # The personal MMKV instance is authoritative for our result even when a
    # different family's board row is changing or our old heap tuple survives.
    personal_rows=[p for p in ctx.get('personalTasks') or [] if p.get('slotUid')==entry['slotUid'] and p.get('status') in (1,2)]
    if len(personal_rows)>1:
        raise decoded.DecodeError('本人操作后的个人实例重复，禁止重发')
    if personal_rows:
        current={**personal_rows[0], 'uid':entry['slotUid'],'acceptor':dict(uid=ctx['selfUid']),
                 'upgrader':dict(uid=ctx['selfUid']) if entry['action']=='upgrade' else None}
    quota_same = decoded.item_count(3956, before) == decoded.item_count(3956, after)
    voucher_delta = decoded.item_count(4030, before) - decoded.item_count(4030, after)
    # No pearl fallback, purchases, or unrelated account changes may be hidden.
    pearls_same = decoded.item_count(2, before) == decoded.item_count(2, after)
    action = entry['action']
    proven = False
    if action == 'quit':
        prior_cd = (before.get('familyContext') or {}).get('quitStartedUnix')
        cd = ctx.get('quitStartedUnix')
        gone = not any(r['uid'] == entry['personalUid'] for r in after['inventory']['records'])
        cleared=(ctx.get('personalTasks') is not None and not personal_rows or
                 current is not None and current.get('acceptor') is None and current['taskItemId']==entry['taskItemId'])
        proven = (cleared and gone and type(cd) is int and cd > (prior_cd or 0)
                  and quota_same and voucher_delta == 0 and pearls_same)
    elif current is not None and owned(current, ctx):
        try:
            row = personal(current, after)
        except decoded.DecodeError:
            return False
        if action == 'accept':
            proven = current['taskItemId'] == entry['taskItemId'] and quota_same and voucher_delta == 0 and pearls_same
        elif action == 'upgrade':
            source = entry.get('sourceTask') or next((t for t in before['family']['tasks'] if t['uid'] == entry['slotUid']),None)
            proven = (source is not None and current['taskItemId'] == source['upgradeItemId'] and current.get('isUpgraded') is True
                      and (current.get('upgrader') or {}).get('uid') == ctx['selfUid'])
        elif action == 'refresh':
            proven = current['taskItemId'] != entry['taskItemId'] or row['uid'] != entry['personalUid']
        if action in ('upgrade', 'refresh'):
            proven = proven and quota_same and pearls_same and voucher_delta == entry['cost']
    if not proven:
        return False
    entry.update(status='confirmed', after=after)
    record['spent'] += entry['cost']
    if action == 'refresh':
        record['refreshes'] += 1
    if action == 'accept':
        record['managedSlotUid'] = entry['slotUid']
    if action == 'quit':
        record['skipped'].append(entry['slot'])
        record['managedSlotUid'] = None
    save(record)
    return True


def cost_allowed(record, cost, data):
    if type(cost) is not int or cost <= 0:
        raise decoded.DecodeError('礼券费用未知')
    if record['spent'] + cost > record['options']['budget']:
        raise decoded.DecodeError('本次礼券预算已达上限，保留当前任务')
    if decoded.item_count(4030, data) < cost:
        raise decoded.DecodeError('礼券不足，禁止改用珍珠')


def refresh_tier(record, data):
    if record['refreshes'] >= record['options']['max_refreshes']:
        raise decoded.DecodeError('刷新次数已达上限，保留当前任务')
    tiers = (data['familyContext'].get('rules') or {}).get('refreshTiers') or []
    selected = [t['cost'] for t in tiers if t.get('cost') in record['options']['tiers']]
    if set(selected) != set(record['options']['tiers']):
        raise decoded.DecodeError('当前等级刷新档位尚未核对')
    # Explicitly selected lower tiers are a fallback when higher tiers exceed funds/budget.
    for cost in sorted(selected, reverse=True):
        if record['spent'] + cost <= record['options']['budget'] and decoded.item_count(4030, data) >= cost:
            return cost
    raise decoded.DecodeError('所选刷新档位预算／礼券不足，不使用珍珠')

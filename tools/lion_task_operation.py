"""Bind an existing personal UP to durable production and reward evidence."""
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from tools import decoded_runtime as decoded
from tools.runtime_state import write_json

ROOT = Path(__file__).resolve().parents[1] / '.tmp/lion_tasks'


def supports(task):
    requirement = task.get('requirement') or {}
    legacy = ('completionCounter' not in task and task.get('taskItemId') == 3875 and requirement.get('productItemId') == 29
              and (task.get('bellReward') or {}).get('count') == 82)
    counter = task.get('completionCounter')
    return (task.get('isUpgraded') is True and requirement.get('kind') == 'harvest'
            and type((task.get('bellReward') or {}).get('count')) is int and task['bellReward']['count'] >= 80
            and (legacy or counter == dict(itemId=5740, count=1, carrierItemId=2739))
            and bool(production_slots(dict(productName=product_name(task)))))


def product_name(task):
    req = task.get('requirement') or {}
    # Compatibility with the original apple receipt, which predates productName.
    return req.get('productName') or ('苹果' if req.get('productItemId') == 29 else None)


def production_slots(binding):
    from tools.configure_field_employee import CROPS, SLOTS
    from tools.employee_capabilities import slot_can_produce
    name = binding.get('productName')
    if name not in CROPS:
        return ()
    return tuple(slot for slot, entry in SLOTS.items() if slot_can_produce(entry, name))


def acquisition_root(data):
    """Link a selected task to a new execution record; retain the old receipt."""
    from tools import lion_acquisition as acquisition
    from tools.employee_operation import read
    pointer = read(acquisition.ROOT / 'active.json')
    if pointer is None:
        return ROOT
    key = pointer.get('id')
    if not isinstance(key, str) or len(key) != 32 or any(c not in '0123456789abcdef' for c in key):
        raise decoded.DecodeError('狮王选取指针损坏，不能改绑执行')
    record = read(acquisition.ROOT / key / 'acquisition.json')
    if (not record or record.get('folder') != str(acquisition.ROOT / key)
            or record.get('id') != key or record.get('status') != 'ready'):
        raise decoded.DecodeError('狮王选取尚未完成，先从选取入口恢复')
    acquisition.verify_identity(record, data)
    task = record['readyTask']
    if not supports(task):
        raise decoded.DecodeError('该UP已选定，但条件执行器尚未接入；保留任务，不刷新或退出')
    root = acquisition.ROOT / key / 'execution'
    pointer = read(root / 'active.json')
    if not pointer:
        current = bind(data)
        if current['slotUid'] != task['uid'] or current['personalUid'] != record['readyPersonalUid']:
            raise decoded.DecodeError('选取回执与本人任务实例不一致，不能绑定执行')
    return root


def personal(binding, data):
    rows = [r for r in data['inventory']['records'] if r['uid'] == binding['personalUid']]
    if len(rows) != 1 or rows[0]['itemId'] != binding['taskItemId'] or int(rows[0]['extra'].get('r_id', 0)) != binding['slotUid']:
        raise decoded.DecodeError('本人狮王任务实例已改变或消失，停止且不接新任务')
    row = rows[0]
    checks = str(row['extra'].get('tk_c', '')).split('#')
    if len(checks) != 1 or not checks[0].startswith('3944_') or not checks[0][5:].isdigit():
        raise decoded.DecodeError('本人UP进度未确认，不按0处理')
    return row, int(checks[0][5:])


def validate_task(binding, data, *, producing=False):
    decoded.validate(data)
    context = data.get('familyContext') or {}
    if data['packageVersion'] != binding['packageVersion'] or binding['packageVersion'] != '100':
        raise decoded.DecodeError('狮王任务版本改变或未核对')
    if any(context.get(key) != binding[key] for key in ('selfUid', 'familyUid', 'season')):
        raise decoded.DecodeError('本人、家族或狮王期数改变')
    if datetime.now(timezone.utc).timestamp() >= binding['deadlineUnix']:
        raise decoded.DecodeError('本人任务已超时，不自动放弃或重接')
    row, current = personal(binding, data)
    state = int(row['extra'].get('status', -1))
    if state not in (1, 2) or current < 0 or current > 2**53 - 1 or state == 2 and current < binding['target']:
        raise decoded.DecodeError('本人任务状态或进度超出已核对范围')
    if producing and (state != 1 or current >= binding['target']):
        raise decoded.DecodeError('任务已满足或不可生产，禁止新增排产')
    staff = [s for s in context.get('staff', []) if s['itemId'] == binding['staffItemId']]
    if not any(p['buildingItemId'] in (12, 284, 285, 17, 723, 28, 286) for s in staff for p in s.get('posts', [])):
        raise decoded.DecodeError('指定UP员工不在农场上阵，停止生产及领奖')
    return dict(current=current, status=state)


def bind(data, *, selected=None):
    decoded.validate(data)
    context = data.get('familyContext') or {}
    if any(type(context.get(key)) is not int or context[key] <= 0 for key in ('selfUid', 'familyUid', 'season')):
        raise decoded.DecodeError('本人、家族或期数未确认')
    if selected is None:
        tasks = (data.get('family') or {}).get('tasks', [])
        if len(tasks) != 90:
            raise decoded.DecodeError('本人任务绑定需要完整当前看板')
        own = [t for t in tasks if (t.get('acceptor') or {}).get('uid') == context.get('selfUid')]
        if len(own) != 1:
            raise decoded.DecodeError('需要唯一的本人已接任务；不自动接新任务')
        task = own[0]
    else:
        task=selected['readyTask']
        if (selected.get('status')!='ready' or (task.get('acceptor') or {}).get('uid')!=context['selfUid']
                or any(selected.get('identity',{}).get(k)!=context[k] for k in ('selfUid','familyUid','season'))
                or selected['identity'].get('packageVersion')!=data['packageVersion']):
            raise decoded.DecodeError('已确认选取回执不属于当前本人任务')
        current=[p for p in context.get('personalTasks') or [] if p.get('uid')==selected['readyPersonalUid']]
        if context.get('personalTasks') is not None:
            if len(current)!=1 or current[0]['slotUid']!=task['uid'] or current[0]['taskItemId']!=task['taskItemId']:
                raise decoded.DecodeError('已选任务的当前个人元数据改变')
            task={**task,**current[0], 'uid':current[0]['slotUid'],'slot':task['slot'],
                  'acceptor':dict(uid=context['selfUid'])}
    requirement = task.get('requirement') or {}
    if not supports(task):
        raise decoded.DecodeError('该UP产物／奖励契约未接入已核对的收获执行器，保留任务')
    if type(task.get('target')) is not int or task['target'] <= 0:
        raise decoded.DecodeError('UP目标数量未知')
    rows = [r for r in data['inventory']['records'] if r['itemId'] == task['taskItemId'] and int(r['extra'].get('r_id', 0)) == task['uid']]
    if len(rows) != 1 or not task.get('deadlineUnix'):
        raise decoded.DecodeError('个人任务实例或截止时间缺失')
    if selected is not None and rows[0]['uid']!=selected['readyPersonalUid']:
        raise decoded.DecodeError('选取后本人任务实例改变，不沿用旧绑定')
    binding = {k: context[k] for k in ('selfUid', 'familyUid', 'season')}
    binding.update(packageVersion=data['packageVersion'], slot=task['slot'], slotUid=task['uid'],
                   personalUid=rows[0]['uid'], taskItemId=task['taskItemId'], staffItemId=requirement['staffItemId'],
                   productItemId=requirement['productItemId'], productName=product_name(task), target=task['target'], deadlineUnix=task['deadlineUnix'],
                   bellReward=task['bellReward']['count'])
    validate_task(binding, data)
    return binding


def begin(data, root=ROOT, *, selected=None):
    from tools.employee_operation import read
    root = Path(root)
    pointer = read(root / 'active.json')
    if pointer:
        identity = pointer.get('id') if isinstance(pointer, dict) else None
        if not isinstance(identity, str) or len(identity) != 32 or any(c not in '0123456789abcdef' for c in identity):
            raise decoded.DecodeError('狮王操作指针损坏，保留原记录')
        record = read(root / pointer['id'] / 'task.json')
        if not record or record['id'] != pointer['id'] or Path(record.get('folder', '')) != root / identity:
            raise decoded.DecodeError('狮王操作记录损坏，保留指针')
        if record['status'] not in ('completed', 'reward_intent', 'reward_unconfirmed', 'reward_claimed'):
            validate_task(record['binding'], data)
        return record
    binding = bind(data,selected=selected)
    identity = uuid4().hex
    record = dict(id=identity, folder=str(root / identity), status='bound', binding=binding, waves=[])
    write_json(root / identity / 'task.json', record)
    write_json(root / 'active.json', dict(id=identity))
    return record


def save(record):
    write_json(Path(record['folder']) / 'task.json', record)


def reconcile_partial_save(operation, slot, before, after):
    """Adopt a proven smaller single-product save. Never sends editor input."""
    from tools import employee_operation as employees
    binding = operation['allocation']['bound_lion']
    validate_task(binding, before, producing=True)
    validate_task(binding, after, producing=True)
    path = employees.job_path(operation, slot)
    entry = employees.read(path)
    current, repeated = decoded.plan_items(slot, before), decoded.plan_items(slot, after)
    product = binding['productName']
    if (not entry or entry['status'] != 'save_intent' or current != repeated
            or set(current) != {product} or set(entry['items']) != {product}
            or not 0 < current[product] < entry['items'][product]
            or employees.owner(slot, before) != entry['owner'] or employees.owner(slot, after) != entry['owner']
            or before['employees']['repeatEnabled'] is not False or after['employees']['repeatEnabled'] is not False
            or employees.collection_record(operation, slot) is not None):
        raise decoded.DecodeError('不能证明部分计划保存，保留原意图，不重发保存')
    evidence = Path(operation['operation_dir']) / f'partial_save_{slot}.json'
    write_json(evidence, dict(original=entry, before=before, after=after, adopted=current, game_operations=0))
    requested = entry['items']
    entry.update(items=current, status='saved', verified=True, repeat=False, one_shot=True,
                 requested_items=requested, partial_save_evidence=str(evidence))
    write_json(path, entry)
    for job in operation['allocation']['jobs']:
        if job['slot_id'] == slot:
            job.update(items=current, requested_items=requested)
    write_json(Path(operation['operation_dir']) / 'assignment.json', operation)
    return entry


def reward_proxy_count(data):
    """100: hidden TASK_COUNT5740 is stored in real item2739.it_rc."""
    rows = [r for r in data['inventory']['records'] if r['itemId'] == 2739]
    if len(rows) != 1:
        raise decoded.DecodeError('狮王额外奖励计数载体缺失或重复')
    raw = rows[0].get('extra', {}).get('it_rc')
    if not isinstance(raw, str):
        raise decoded.DecodeError('额外奖励计数格式未知')
    counts = {}
    for pair in raw.split('#'):
        parts = pair.split('_')
        if len(parts) != 2 or any(not value.isdecimal() for value in parts) or parts[0] in counts:
            raise decoded.DecodeError('额外奖励计数损坏或重复')
        counts[parts[0]] = int(parts[1])
    if '5740' not in counts or counts['5740'] > 2**53 - 1:
        raise decoded.DecodeError('额外奖励5740计数未确认')
    return counts['5740']


def reward_confirmed(binding, before, after):
    decoded.validate(after)
    return reward_changes_match(binding, before, after)


def reward_changes_match(binding, before, after):
    """Exact immutable reward evidence; no freshness authorization for new input."""
    if any((before.get('familyContext') or {}).get(key) != binding[key] for key in ('selfUid', 'familyUid', 'season')):
        return False
    if before.get('packageVersion') != binding['packageVersion']:
        return False
    if any((after.get('familyContext') or {}).get(key) != binding[key] for key in ('selfUid', 'familyUid', 'season')):
        return False
    if after.get('packageVersion') != binding['packageVersion']:
        return False
    old = next((r for r in after['inventory']['records'] if r['uid'] == binding['personalUid']), None)
    gone = old is None or (old['itemId'] == binding['taskItemId'] and str(old['extra'].get('status')) == '3')
    return (gone and decoded.item_count(3956, before) - decoded.item_count(3956, after) == 1
            and decoded.item_count(3638, after) - decoded.item_count(3638, before) == binding['bellReward']
            and reward_proxy_count(after) - reward_proxy_count(before) == 1)


def completed_receipt(record):
    if (record.get('status') != 'completed' or record.get('returned_to_farm') is not True
            or type(record.get('completed_tasks')) is not int or record['completed_tasks'] != 1):
        return False
    before, after = record.get('reward_before'), record.get('reward_after')
    if not isinstance(before, dict) or not isinstance(after, dict):
        return False
    for snapshot in (before, after):
        if snapshot.get('readOnly') is not True or snapshot.get('source') != 'local_client_cache':
            return False
    times = [datetime.fromisoformat(d['capturedAt'].replace('Z','+00:00')) for d in (before, after)]
    if any(t.tzinfo is None for t in times) or not times[0] <= times[1] <= datetime.now(timezone.utc):
        return False
    return reward_changes_match(record['binding'], before, after)

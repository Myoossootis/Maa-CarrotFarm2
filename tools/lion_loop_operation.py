"""One season-bound Lion campaign. Round receipts and budgets never reset on resume."""
from copy import deepcopy
from pathlib import Path
import time
from uuid import uuid4

from tools import decoded_runtime as decoded, lion_acquisition as acquisition, lion_task_operation as lion
from tools.employee_operation import read, unfinished_active
from tools.runtime_state import write_json

ROOT = Path(__file__).resolve().parents[1] / '.tmp/lion_loop'


def valid_id(value):
    return isinstance(value, str) and len(value) == 32 and all(c in '0123456789abcdef' for c in value)


def active(root=ROOT):
    root = Path(root)
    pointer = read(root / 'active.json')
    if pointer is None:
        return None
    if not isinstance(pointer, dict) or not valid_id(pointer.get('id')):
        raise decoded.DecodeError('狮王循环指针损坏')
    record = read(root / pointer['id'] / 'loop.json')
    if not record or record.get('id') != pointer['id'] or record.get('folder') != str(root / pointer['id']):
        raise decoded.DecodeError('狮王循环记录缺失，不能创建重复循环')
    return record


def guard_independent():
    record = active()
    if record and record.get('current') is not None:
        raise decoded.DecodeError('狮王循环已有在途任务，请从“狮王连续循环”恢复，不能另开单任务入口')


def period(data):
    identity=counter_identity(data)
    ctx = data['familyContext']
    value = (data.get('family') or {}).get('period')
    if (not isinstance(value, dict) or value.get('source') != 'shared_3921_and_3972'
            or value.get('season') != ctx['season'] or value.get('selfUid') != ctx['selfUid']
            or value.get('status') != 1 or type(value.get('endsUnix')) is not int or value['endsUnix'] <= time.time()
            or type(value.get('completed')) is not int or not 0 <= value['completed'] <= 26
            or type(value.get('points')) is not int or value['points'] < 0):
        raise decoded.DecodeError('当前赛季本人贡献次数未确认；不由3956或累计5740推算')
    return value


def save(record):
    write_json(Path(record['folder']) / 'loop.json', record)


def validate(record, data):
    if counter_identity(data) != record['identity']:
        raise decoded.DecodeError('循环本人／家族／期数／客户端改变，保留旧轮次')
    value = period(data)
    if value['endsUnix'] != record['seasonEndsUnix']:
        raise decoded.DecodeError('狮王赛季结束时间改变，停止旧循环')
    return value


def counter_identity(data):
    decoded.validate(data)
    ctx=data.get('familyContext') or {}
    if data.get('packageVersion')!='100' or any(type(ctx.get(k)) is not int or ctx[k]<=0 for k in ('selfUid','familyUid','season')):
        raise decoded.DecodeError('本人计数的客户端／身份／期数未确认')
    return {**{k:ctx[k] for k in ('selfUid','familyUid','season')},'packageVersion':data['packageVersion']}


def begin(data, options, root=ROOT):
    value = period(data)
    require_ui_counter(data)
    root = Path(root)
    old = active(root)
    previous_id=None
    if old and counter_identity(data)!=old['identity'] and can_rollover(old,counter_identity(data)):
        previous_id=old['id']
        old=None
    if old:
        validate(old, data)
        check_options(old,options)
        if old['options']!=options:
            old.setdefault('limitChanges',[]).append(dict(before=deepcopy(old['options']),after=deepcopy(options)))
            old['options']=deepcopy(options)
            save(old)
        return old
    old_task = read(lion.ROOT / 'active.json')
    if old_task and lion.begin(data)['status'] != 'completed':
        raise decoded.DecodeError('早期本人UP仍有未决操作，请先恢复，不创建并行循环')
    key = uuid4().hex
    record = dict(schemaVersion=1, id=key, folder=str(root/key), identity=counter_identity(data),
                  options=deepcopy(options), seasonEndsUnix=value['endsUnix'], initialPeriod=deepcopy(value),
                  observedPeriod=deepcopy(value), completed=[], rounds=[], current=None, status='running',previousCampaignId=previous_id)
    save(record)
    write_json(root / 'active.json', dict(id=key))
    return record


def check_options(record,options):
    previous=record['options']
    if (set(previous)!=set(options) or previous['tiers']!=options['tiers']
            or any(options[k]<previous[k] for k in ('budget','max_refreshes','max_tasks'))):
        raise decoded.DecodeError('恢复可提高原任务／礼券／刷新总上限；不能换档位、降低上限或重置已用量')


def require_ui_counter(data):
    value=period(data)
    if data.get('lionCounterUi')!={k:value[k] for k in ('season','selfUid','completed','points')}:
        raise decoded.DecodeError('缺少本人当前贡献行的次数／积分核对，不能按旧计数启动或推进')


def can_rollover(record,identity):
    return (record['current'] is None and record['status'] in ('period_target','quota_exhausted','run_limit')
        and all(r['status']=='counted' for r in record['rounds'])
        and all(identity.get(k)==record['identity'][k] for k in ('selfUid','familyUid','packageVersion'))
        and type(identity.get('season')) is int and identity['season']>record['identity']['season'])


def round_folder(record, entry):
    if type(entry.get('number')) is not int or not 1 <= entry['number'] <= len(record['rounds']):
        raise decoded.DecodeError('循环轮次编号损坏')
    return Path(record['folder']) / 'rounds' / f"{entry['number']:04d}"


def acquisition_record(record, entry):
    folder = round_folder(record, entry)
    if entry.get('source') == 'legacy_acquisition':
        root = acquisition.ROOT
    elif entry.get('source') == 'campaign':
        root = folder / 'acquisition'
    else:
        raise decoded.DecodeError('选取记录来源未知')
    key = entry.get('acquisitionId')
    if not valid_id(key):
        raise decoded.DecodeError('循环选取记录ID损坏')
    result = read(root/key/'acquisition.json')
    if not result or result.get('id') != key or result.get('folder') != str(root/key) or result.get('identity') != record['identity']:
        raise decoded.DecodeError('循环选取记录身份／路径不符')
    return result


def totals(record):
    spent = refreshes = 0
    for entry in record['rounds']:
        if entry.get('acquisitionId'):
            saved = acquisition_record(record, entry)
            if (type(saved.get('spent')) is not int or saved['spent']<0 or type(saved.get('refreshes')) is not int
                    or saved['refreshes']<0 or not isinstance(saved.get('actions'),list)):
                raise decoded.DecodeError('循环费用／刷新账本损坏')
            confirmed=[a for a in saved['actions'] if a.get('status')=='confirmed']
            pending=[a for a in saved['actions'] if a.get('status')=='intent']
            if (any(a.get('status') not in ('confirmed','intent') or type(a.get('cost')) is not int or a['cost']<0 for a in saved['actions'])
                    or len(pending)>1 or pending and saved['actions'][-1] is not pending[0]
                    or sum(a['cost'] for a in confirmed)!=saved['spent']
                    or sum(a.get('action')=='refresh' for a in confirmed)!=saved['refreshes']):
                raise decoded.DecodeError('循环费用与逐次确认记录不一致，不能重置或继续支出')
            spent += saved['spent']
            refreshes += saved['refreshes']
    if spent > record['options']['budget'] or refreshes > record['options']['max_refreshes']:
        raise decoded.DecodeError('循环费用或刷新次数超出已授权总限额，不继续输入')
    return spent, refreshes


def new_round(record, data):
    value = validate(record, data)
    if record['current'] is not None:
        return record['rounds'][record['current']-1]
    if value['completed'] >= 26 or len(record['completed']) >= record['options']['max_tasks']:
        raise decoded.DecodeError('已达到循环停止条件，不能创建下一任务')
    entry = dict(number=len(record['rounds'])+1, status='acquiring', source='campaign', beforePeriod=deepcopy(value))
    # Import only an unfinished/selected acquisition. A historical completed
    # receipt never counts as a newly completed task in this campaign.
    if not record['rounds'] and not record.get('previousCampaignId'):
        pointer = read(acquisition.ROOT/'active.json')
        if pointer:
            if not valid_id(pointer.get('id')):
                raise decoded.DecodeError('历史选取指针损坏')
            previous = read(acquisition.ROOT/pointer['id']/'acquisition.json')
            if (not previous or previous.get('folder') != str(acquisition.ROOT/pointer['id'])
                    or previous.get('identity') != record['identity']):
                raise decoded.DecodeError('历史选取记录与当前身份不符')
            own = [t for t in data['family'].get('tasks',[]) if acquisition.owned(t,data['familyContext'])]
            pending = previous['actions'] and previous['actions'][-1]['status']=='intent'
            selected = previous.get('readyTask')
            matches = len(own)==1 and selected and selected['uid']==own[0]['uid'] and selected['taskItemId']==own[0]['taskItemId']
            if selected and not own:
                try:
                    matches=acquisition.personal(selected,data)['uid']==previous['readyPersonalUid']
                except decoded.DecodeError:
                    matches=False
            if pending or previous['status']!='ready' or matches:
                if previous['status']!='ready' and previous['options'] != {
                        k:record['options'][k] for k in ('tiers','budget','max_refreshes')}:
                    raise decoded.DecodeError('历史选取尚未完成，须先按原预算与档位恢复')
                entry.update(source='legacy_acquisition',acquisitionId=pointer['id'])
    record['rounds'].append(entry)
    record['current']=entry['number']
    record['observedPeriod']=deepcopy(value)
    save(record)  # Round identity exists before any selection UI input.
    totals(record)
    return entry


def prepare_acquisition(record, entry, data):
    validate(record,data)
    if entry.get('acquisitionId'):
        result=acquisition_record(record,entry)
        if result['status']!='ready':
            spent,refreshes=totals(record)
            options=dict(tiers=record['options']['tiers'],budget=record['options']['budget']-spent+result['spent'],
                max_refreshes=record['options']['max_refreshes']-refreshes+result['refreshes'])
            if options!=result['options']:
                if result['actions'] and result['actions'][-1]['status']=='intent':
                    raise decoded.DecodeError('先补证原支付意图，再提高当前选取额度')
                result.setdefault('limitChanges',[]).append(dict(before=deepcopy(result['options']),after=options))
                result['options']=options;acquisition.save(result)
        return result
    spent, refreshes = totals(record)
    options=dict(tiers=record['options']['tiers'],budget=record['options']['budget']-spent,
                 max_refreshes=record['options']['max_refreshes']-refreshes)
    result=acquisition.begin(data,options,round_folder(record,entry)/'acquisition')
    entry['acquisitionId']=result['id'];save(record)
    return result


def execution_record(record, entry):
    selected=acquisition_record(record,entry)
    root=execution_root(record,entry,selected)
    pointer=read(root/'active.json')
    if not pointer or not valid_id(pointer.get('id')):
        return None
    result=read(root/pointer['id']/'task.json')
    if not result or result.get('id')!=pointer['id'] or result.get('folder')!=str(root/pointer['id']):
        raise decoded.DecodeError('循环任务执行记录损坏')
    if any(result['binding'].get(k)!=v for k,v in record['identity'].items()):
        raise decoded.DecodeError('循环任务执行身份不符')
    if (result['binding']['slotUid']!=selected['readyTask']['uid']
            or result['binding']['personalUid']!=selected['readyPersonalUid']):
        raise decoded.DecodeError('执行记录不属于本轮选取的个人任务实例')
    return result


def execution_root(record,entry,selected):
    # Keep new Windows paths short; preserve the already selected task's
    # historical root so existing saves/claims are adopted without copying.
    return (Path(selected['folder'])/'execution' if entry['source']=='legacy_acquisition'
            else round_folder(record,entry)/'execution')


def prepare_execution(record, entry, data):
    validate(record,data)
    selected=acquisition_record(record,entry)
    if selected['status']!='ready' or not lion.supports(selected['readyTask']):
        raise decoded.DecodeError('本轮选取尚未就绪或条件执行器不支持')
    root=execution_root(record,entry,selected)
    result=execution_record(record,entry)
    if result is None:
        result=lion.begin(data,root=root,selected=selected)
        # Reuse the same strict source check after binding, before production.
        result=execution_record(record,entry)
    entry.update(status='executing',executionId=result['id'])
    save(record)
    return result


def count_completed(record, entry, receipt, data):
    after=validate(record,data)
    require_ui_counter(data)
    if entry['status']=='counted':
        return False
    if not lion.completed_receipt(receipt):
        raise decoded.DecodeError('缺少精确领奖与返回回执，不进入下一任务')
    binding=receipt['binding']
    if any(r['personalUid']==binding['personalUid'] for r in record['completed']):
        raise decoded.DecodeError('个人任务实例已计数，拒绝重复轮次')
    prior=entry['beforePeriod']
    if after['completed']!=prior['completed']+1 or after['points']!=prior['points']+binding['bellReward']:
        raise decoded.DecodeError('领奖后的本期次数／积分未恰好增加，不接下一任务')
    if unfinished_active(Path(receipt['folder'])/'production') is not None:
        raise decoded.DecodeError('上一任务仍有未决生产，保留并停止跨任务推进')
    record['completed'].append(dict(personalUid=binding['personalUid'],taskItemId=binding['taskItemId'],
        executionId=receipt['id'],slot=binding['slot'],bells=binding['bellReward']))
    entry.update(status='counted',afterPeriod=deepcopy(after))
    record.update(current=None,observedPeriod=deepcopy(after),status='running')
    totals(record);save(record)
    return True

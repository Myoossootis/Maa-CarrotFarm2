"""Pure read-only Lion King readiness review. Never authorizes UI input."""
from datetime import datetime, timezone


def review(snapshot, *, now=None):
    result = dict(executionEnabled=False, periodTarget=26, completed=None, blockers=[], ownTasks=[],
                  candidates=[], excluded={}, next='只读刷新狮王任务',
                  capturedAt=snapshot.get('capturedAt') if isinstance(snapshot, dict) else None)
    try:
        if not isinstance(snapshot, dict) or snapshot.get('readOnly') is not True or snapshot.get('source') != 'local_client_cache':
            raise ValueError('需要本地只读解码快照')
        captured = datetime.fromisoformat(snapshot['capturedAt'].replace('Z', '+00:00'))
        if captured.tzinfo is None:
            raise ValueError('快照时间缺少时区')
        age = (datetime.now(timezone.utc).timestamp() if now is None else now) - captured.timestamp()
        if not -2 <= age <= 10:
            raise ValueError('快照超过10秒或时钟异常，旧记录不用于选择任务')
        context, board = snapshot.get('familyContext'), snapshot.get('family')
        if not isinstance(context, dict) or any(type(context.get(k)) is not int or context[k] <= 0 for k in ('selfUid', 'familyUid', 'season')):
            raise ValueError('本人UID、家族或期数未确认')
        tasks = board.get('tasks', []) if isinstance(board, dict) else []
        if (len(tasks) != 90 or any(not isinstance(t, dict) or type(t.get('slot')) is not int or type(t.get('uid')) is not int for t in tasks)
                or {t['slot'] for t in tasks} != set(range(1, 91)) or len({t['uid'] for t in tasks}) != 90):
            raise ValueError('狮王看板必须是90个唯一槽位及任务UID')
        staff = context.get('staff')
        if not isinstance(staff, list) or any(not isinstance(s, dict) or type(s.get('itemId')) is not int for s in staff):
            raise ValueError('员工拥有列表未读取')
        result.update(selfUid=context['selfUid'], familyUid=context['familyUid'], season=context['season'])
        result['blockers'].append('本期已完成次数及3956扣减语义未核对，不能启动26次自动循环')
        result['blockers'].append('狮王接取／升级／上阵／提交领奖执行器尚未实现')
        for task in tasks:
            acceptor, upgrader = task.get('acceptor'), task.get('upgrader')
            if acceptor is not None and (not isinstance(acceptor, dict) or type(acceptor.get('uid')) is not int or acceptor['uid'] <= 0):
                raise ValueError('任务接取人身份无效')
            if upgrader is not None and (not isinstance(upgrader, dict) or type(upgrader.get('uid')) is not int or upgrader['uid'] <= 0):
                raise ValueError('任务升级人身份无效')
            own = acceptor is not None and acceptor['uid'] == context['selfUid']
            reason = ('other_acceptor' if acceptor and not own else
                      'other_upgrader_reserved' if not own and upgrader and upgrader['uid'] != context['selfUid'] else
                      'not_unclaimed' if not own and task.get('status') != 'unclaimed' else
                      'not_rare' if not own and task.get('isRare') is not True else None)
            if reason:
                result['excluded'][reason] = result['excluded'].get(reason, 0) + 1
                continue
            row = {key: task.get(key) for key in ('slot', 'uid', 'taskItemId', 'text', 'bellReward', 'progress', 'canUpgrade', 'upgradeItemId')}
            row['blockers'] = []
            requirement = task.get('requirement') or {}
            kind = requirement.get('kind')
            row['requirement'] = requirement
            if kind not in ('harvest', 'party_takeout'):
                row['blockers'].append('首版优先UP；星级或未知条件暂不自动选择')
            if task.get('deadlinePassed') is True:
                row['blockers'].append('任务已超时，保留本人任务，不自动放弃')
            if own and not (task.get('progress') or {}).get('available'):
                row['blockers'].append('本人任务进度未知，不能按0或完成处理')
            if kind in ('harvest', 'party_takeout'):
                owned = [s for s in staff if s['itemId'] == requirement.get('staffItemId')]
                row['staffOwned'] = bool(owned)
                expected = {5} if kind == 'party_takeout' else {12, 284, 285, 17, 723, 28, 286}
                row['staffOnDuty'] = any(p.get('buildingItemId') in expected for s in owned for p in s.get('posts', []))
                if not owned:
                    row['blockers'].append('未拥有指定UP员工')
                elif not row['staffOnDuty']:
                    row['blockers'].append('指定UP员工未在所需区域上阵；不能静默换人')
                if kind == 'harvest':
                    row['blockers'].append('手动一键种植／收获和当前客户端生产规则需专项验收')
            (result['ownTasks'] if own else result['candidates']).append(row)
        result['candidates'].sort(key=lambda r: (bool(r['blockers']), r['slot']))
        if result['ownTasks']:
            result['next'] = '优先核对本人已接任务；不接新任务'
        elif result['candidates']:
            result['next'] = '核对候选UP的升级后条件、费用与执行能力'
        else:
            result['next'] = '无合格候选；等待任务变化，不抢他人升级任务'
    except (ValueError, TypeError, KeyError) as error:
        result.update(ownTasks=[], candidates=[], excluded={})
        result['blockers'].append(str(error))
    return result


def lines(result):
    text = ['狮王检查（只读，不接取／升级／提交）',
            f"每期目标26；已完成：未核对；期数：{result.get('season', '未确认')}",
            f"本人任务 {len(result['ownTasks'])}；待评估候选 {len(result['candidates'])}", result['next'], '']
    for row in result['ownTasks'] + result['candidates']:
        text.append(f"槽位{row['slot']}：{row['text']}")
        text.extend('  · ' + reason for reason in row['blockers'])
    text += ['', '排除统计：' + str(result['excluded']), '', '执行阻塞：']
    text.extend('· ' + reason for reason in result['blockers'])
    return '\n'.join(text)

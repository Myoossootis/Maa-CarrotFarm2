"""Select one eligible Lion UP through Maa; upgrade/refresh with bounded vouchers."""
import argparse
from pathlib import Path
import time

from tools import decoded_runtime as decoded, lion_acquisition as acquisition
from tools.run_lion_task import full_snapshot
from tools.task_control import check_cancelled


def current_task(record, data, expected):
    acquisition.verify_identity(record, data)
    task = next((t for t in data['family']['tasks'] if t['uid'] == expected['uid']), None)
    if record.get('managedSlotUid')==expected['uid']:
        task=acquisition.bound_task(record,data) or task
    if not task or task['slot'] != expected['slot'] or task['taskItemId'] != expected['taskItemId']:
        raise decoded.DecodeError('选取期间槽位任务改变，未发出下一动作')
    return task


def wait_confirmation(record, read=full_snapshot):
    deadline = time.monotonic() + 15
    while True:
        check_cancelled()
        if acquisition.confirm(record, read()):
            return
        if time.monotonic() >= deadline:
            raise decoded.DecodeError('狮王操作结果未确认，保留意图；恢复只回读，禁止重复点击')
        time.sleep(.5)


def recover(record, read=full_snapshot):
    if record['actions'] and record['actions'][-1]['status'] == 'intent':
        if not acquisition.confirm(record, read()):
            raise decoded.DecodeError('上次狮王操作未确认，禁止重发；先对账原意图')


def quit_cooldown(data):
    ctx = acquisition.context(data)
    cd = (ctx.get('rules') or {}).get('quitCooldownSeconds')
    started = ctx.get('quitStartedUnix')
    if cd != 120 or type(started) is not int or started < 0:
        raise decoded.DecodeError('退出冷却字段未知，不退出任务')
    if started > time.time() + 5:
        raise decoded.DecodeError('退出时间在未来，不能用异常时钟持续等待')
    return max(0, started + cd - time.time())


def run(record, ui, *, read=full_snapshot, supported_only=False):
    recover(record, read)
    while True:
        check_cancelled()
        data = read()
        acquisition.verify_identity(record, data)
        task=acquisition.bound_task(record,data)
        choice='own' if task is not None else None
        if task is None:
            choice, task = acquisition.choose(data, skipped=record['skipped'], supported_only=supported_only)
        if record['status'] == 'ready':
            if choice != 'own' or task['uid'] != record['readyTask']['uid'] or task['taskItemId'] != record['readyTask']['taskItemId']:
                raise decoded.DecodeError('已选任务改变，旧回执不能授权新任务')
            if acquisition.personal(task, data)['uid'] != record['readyPersonalUid']:
                raise decoded.DecodeError('已选任务个人实例改变，不沿用旧回执')
            if not acquisition.suitability(task, data['familyContext'])[0] or task.get('canUpgrade'):
                raise decoded.DecodeError('已选UP不再符合条件')
            if supported_only and not acquisition.executable(task, data['familyContext']):
                raise decoded.DecodeError('已选UP尚不支持自动执行，保留任务')
            return task
        if task is None:
            record.update(status='waiting', waitReason=choice)
            acquisition.save(record)
            print('[狮王] 无可接候选／次数不足，保留记录；不抢他人任务、不购买次数。', flush=True)
            return None
        good, reason = acquisition.suitability(task, data['familyContext'])
        print(f"[狮王] 槽位{task['slot']} {choice}：{reason}；已用礼券{record['spent']}/{record['options']['budget']}", flush=True)
        ui.select_slot(task['slot'])
        before = read()
        task = current_task(record, before, task)
        ctx = before['familyContext']
        if choice != 'own':
            # Recheck the entire priority decision after navigation, including a
            # newly accepted own task or another member's just-upgraded reserve.
            choice2, picked = acquisition.choose(before, skipped=record['skipped'], supported_only=supported_only)
            if choice2 != choice or picked != task:
                raise decoded.DecodeError('接取前候选或占用改变，未点击接受')
            if good and task.get('canUpgrade'):
                acquisition.cost_allowed(record, (task.get('upgradeCost') or {}).get('count'), before)
            if not good:
                acquisition.refresh_tier(record, before)  # Do not take garbage without a funded refresh.
            from tools.lion_task_ui import ReservationConflict
            try:
                ui.check_accept(task)
            except ReservationConflict:
                record['skipped'].append(task['slot'])
                acquisition.save(record)
                continue
            # The OCR precheck can take time; ownership must still be fresh.
            before = read()
            latest_choice, latest = acquisition.choose(before, skipped=record['skipped'], supported_only=supported_only)
            acquisition.verify_identity(record, before)
            if latest_choice != choice or latest != task:
                raise decoded.DecodeError('接受按钮核对后任务已改变，未点击')
            if good and task.get('canUpgrade'):
                acquisition.cost_allowed(record, (task.get('upgradeCost') or {}).get('count'), before)
            elif not good:
                acquisition.refresh_tier(record, before)
            acquisition.intent(record, 'accept', task, before)
            ui.accept(task)
            wait_confirmation(record, read)
            continue
        if not acquisition.owned(task, ctx):
            raise decoded.DecodeError('动作前本人占用改变')
        acquisition.personal(task, before)
        good, reason = acquisition.suitability(task, ctx)
        unsupported = supported_only and good and not acquisition.executable(task, ctx)
        if unsupported and record['managedSlotUid'] != task['uid']:
            raise decoded.DecodeError('本人任务产物或指定员工上阵条件尚不支持自动执行，保留任务')
        if good and not unsupported and not task.get('canUpgrade'):
            record.update(status='ready', readyTask=task, readyPersonalUid=acquisition.personal(task, before)['uid'])
            acquisition.save(record)
            print(f"[狮王] 已选定UP：槽位{task['slot']}，{task['bellReward']['count']}铃铛；选取阶段完成，任务尚未完成。", flush=True)
            return task
        if good and not unsupported:
            cost = (task.get('upgradeCost') or {}).get('count')
            acquisition.cost_allowed(record, cost, before)
            ui.prepare_upgrade(task, cost)
            before = read()
            task = current_task(record, before, task)
            if not acquisition.owned(task, before['familyContext']):
                raise decoded.DecodeError('升级确认前本人占用改变')
            acquisition.cost_allowed(record, cost, before)
            acquisition.intent(record, 'upgrade', task, before, cost)
            ui.upgrade(task, cost)
            wait_confirmation(record, read)
            continue
        if record['managedSlotUid'] != task['uid']:
            raise decoded.DecodeError('本人已有不符合新策略的任务，保留；不擅自刷新或放弃历史任务')
        kind = (task.get('requirement') or {}).get('kind')
        if kind not in acquisition.KNOWN_KINDS:
            raise decoded.DecodeError('刷新结果类型未知，保留任务')
        if unsupported or kind == 'star_orders' and task['bellReward']['count'] >= 45:
            # Preserve a valuable star task even if the game's daily warning
            # has already been disabled. If visible, explicitly cancel it.
            ui.cancel_star_warning(task)
            remaining = quit_cooldown(before)
            if remaining:
                print(f'[狮王] 再次退出需等待约{int(remaining)+1}秒；保留任务与原操作记录。', flush=True)
                time.sleep(min(5, remaining))
                continue
            ui.prepare_quit(task)
            before = read()
            task = current_task(record, before, task)
            if not acquisition.owned(task, before['familyContext']) or quit_cooldown(before):
                raise decoded.DecodeError('退出确认前占用／冷却改变，未确认')
            acquisition.intent(record, 'quit', task, before)
            ui.quit_task(task)
            wait_confirmation(record, read)
            continue
        acquisition.refresh_tier(record, before)
        if not ui.open_refresh(task):
            continue
        # A dialog is not proof that payment is still valid. Read again before
        # the sole tier-button click and persist intent with this exact state.
        before = read()
        task = current_task(record, before, task)
        if not acquisition.owned(task, before['familyContext']):
            raise decoded.DecodeError('刷新前本人占用改变')
        cost = acquisition.refresh_tier(record, before)
        acquisition.cost_allowed(record, cost, before)
        ui.check_refresh(cost)
        acquisition.intent(record, 'refresh', task, before, cost)
        ui.refresh(cost)
        wait_confirmation(record, read)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--tiers', type=int, nargs='+', choices=(2, 20), default=[20])
    parser.add_argument('--max-refreshes', type=int, default=20)
    parser.add_argument('--budget', type=int, default=600)
    args = parser.parse_args()
    if not 1 <= args.max_refreshes <= 200 or not 2 <= args.budget <= 10000:
        parser.error('刷新次数1–200，礼券预算2–10000（包括升级）')
    options = dict(tiers=sorted(set(args.tiers)), max_refreshes=args.max_refreshes, budget=args.budget)
    if not args.apply:
        print(f'预览：选取一个≥80铃铛、拥有员工的非星级UP；配置{options}；不点击游戏。')
        return 0
    from tools.lion_task_ui import LionUI
    from tools.ui_worker import workflow_lock
    from tools import employee_operation as employees, lion_task_operation as lion
    with workflow_lock():
        from tools.lion_loop_operation import guard_independent
        guard_independent()
        initial = decoded.snapshot()
        decoded.validate(initial)
        if initial.get('packageVersion') != '100':
            raise decoded.DecodeError('狮王接取／刷新仅核对客户端100，未发送游戏输入')
        old = employees.read(lion.ROOT / 'active.json')
        if old:
            prior = lion.begin(decoded.snapshot())
            if prior['status'] != 'completed':
                raise decoded.DecodeError('旧本人任务仍有未决操作，先从本人UP入口恢复；不接新任务')
        pointer = employees.read(acquisition.ROOT / 'active.json')
        # Recover pending outcomes before attempting navigation into a possibly
        # still-open confirmation dialog. Recovery never repeats business input.
        if pointer:
            record = acquisition.begin(full_snapshot(), options)
            recover(record)
        ui = LionUI(acquisition.ROOT / 'navigation')
        try:
            if pointer:
                data = full_snapshot()
                acquisition.verify_identity(record, data)
                choice, task = acquisition.choose(data, skipped=record['skipped'])
                ui.recover_prepared(task if choice == 'own' else None)
            ui.open_board()
            record = acquisition.begin(full_snapshot(), options)
            task = run(record, ui)
            if task is None:
                return 2
            ui.leave_board()
            record['returned_to_farm'] = True
            acquisition.save(record)
            return 0
        finally:
            ui.close()


if __name__ == '__main__':
    raise SystemExit(main())

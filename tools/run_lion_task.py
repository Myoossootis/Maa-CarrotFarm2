"""Execute exactly one existing apple-harvest UP, never accept or upgrade a task."""
import argparse
from pathlib import Path
import time

from tools import decoded_runtime as decoded, employee_operation as employees
from tools import lion_task_operation as lion
from tools.runtime_state import write_json
from tools.task_control import check_cancelled


def full_snapshot():
    data = decoded._request_report('all')
    decoded.validate(data)
    return data


def read_capacities(binding, slots, folder):
    from tools.lion_task_ui import LionUI
    from tools.configure_field_employee import open_plan, editor_capacity, close_plan_dialog, close_employee_menu
    ui = LionUI(folder)
    result = {}
    try:
        for slot in slots:
            data = decoded.snapshot()
            lion.validate_task(binding, data, producing=True)
            if decoded.assignment_blocker(slot, data):
                continue
            frame, _ = open_plan(ui.controller, slot)
            observed = editor_capacity(ui.tasker, frame, slot)
            if observed is None or observed[0] != 0 or not 1 <= observed[1] <= 1000:
                raise decoded.DecodeError('员工当前空闲容量无法确认：' + slot)
            result[slot] = observed[1]
            close_plan_dialog(ui.controller)
        close_employee_menu(ui.controller)
        return result
    finally:
        ui.close()


def wave_allocation(binding, data, capacities):
    state = lion.validate_task(binding, data, producing=True)
    missing = binding['target'] - state['current']
    available = {s:c for s,c in capacities.items() if s in ('orchard_1', 'orchard_2')
                 and type(c) is int and 0 < c <= 1000 and decoded.assignment_blocker(s, data) is None}
    counts = dict.fromkeys(available, 0)
    while missing and any(counts[s] < c for s,c in available.items()):
        for slot, cap in available.items():
            if missing and counts[slot] < cap:
                counts[slot] += 1
                missing -= 1
    jobs = [dict(slot_id=s, items={'苹果':n}, capacity_limit=available[s]) for s,n in counts.items() if n]
    if not jobs:
        raise decoded.DecodeError('没有已核对空闲果园，保留在产及未知区域，不排产')
    return dict(ready=True, jobs=jobs, bound_lion=binding, target_order=None, target_items=[],
                production_targets=[dict(name='苹果', needed=sum(j['items']['苹果'] for j in jobs))],
                source_generated_at_utc=data['capturedAt'])


def execute(record, *, prepare_only=False):
    from tools.run_employee_order_plan import apply_jobs
    from tools.monitor_employee_products import monitor
    root = Path(record['folder']) / 'production'
    if record['status'] == 'completed':
        print('[狮王] 这一绑定任务已确认领奖，本次不重复执行。', flush=True)
        return True
    if record['status'] in ('reward_intent', 'reward_unconfirmed', 'reward_claimed'):
        return claim(record)
    while True:
        check_cancelled()
        data = decoded.snapshot()
        state = lion.validate_task(record['binding'], data)
        print(f"[狮王] 槽位{record['binding']['slot']} 苹果 {state['current']}/{record['binding']['target']}，个人状态={state['status']}", flush=True)
        operation = employees.unfinished_active(root)
        if operation:
            if operation['allocation'].get('bound_lion') != record['binding']:
                raise decoded.DecodeError('生产记录与本人任务不一致')
        if state['status'] == 2 and state['current'] >= record['binding']['target']:
            record['unfinished_production_preserved'] = operation['assignment_id'] if operation else None
            lion.save(record)
            return claim(record)
        if operation is None:
            old = employees.unfinished_active()
            if old:
                # Only a positively cleared saved record can be archived, never an intent.
                employees.supersede_cleared_plans(old, data)
            caps = read_capacities(record['binding'], ('orchard_1', 'orchard_2'), Path(record['folder']) / 'capacity')
            operation = employees.begin(wave_allocation(record['binding'], decoded.snapshot(), caps), root=root)
            record['waves'].append(operation['assignment_id'])
            lion.save(record)
        elif operation['status'] == 'saved':
            # Complete an interrupted/partial initial wave without re-saving any
            # previously saved slot. Never change a wave after a collection intent.
            jobs = operation['allocation']['jobs']
            pending = record['binding']['target'] - state['current'] - sum(sum(j['items'].values()) for j in jobs)
            extras = [s for s in ('orchard_1', 'orchard_2') if s not in {j['slot_id'] for j in jobs}
                      and decoded.assignment_blocker(s, data) is None]
            if pending > 0 and extras and not any(employees.collection_record(operation, j['slot_id']) for j in jobs):
                caps = read_capacities(record['binding'], extras, Path(record['folder']) / 'capacity_extend')
                data = decoded.snapshot()
                lion.validate_task(record['binding'], data, producing=True)
                added = []
                for slot in extras:
                    if decoded.assignment_blocker(slot, data) is None and caps.get(slot) and pending > 0:
                        count = min(pending, caps[slot])
                        added.append(dict(slot_id=slot, items={'苹果': count}, capacity_limit=caps[slot]))
                        pending -= count
                if added:
                    write_json(Path(operation['operation_dir']) / 'before_extend.json', operation)
                    operation['allocation']['jobs'] = jobs + added
                    operation['status'] = 'applying'
                    write_json(Path(operation['operation_dir']) / 'assignment.json', operation)
        if operation['status'] == 'applying':
            for job in operation['allocation']['jobs']:
                entry = employees.read(employees.job_path(operation, job['slot_id']))
                if entry and entry['status'] == 'save_intent':
                    before = decoded.snapshot()
                    if decoded.plan_items(job['slot_id'], before) != entry['items']:
                        time.sleep(.5)
                        lion.reconcile_partial_save(operation, job['slot_id'], before, decoded.snapshot())
            apply_jobs(operation['allocation'], operation=operation)
            operation = employees.active(root)
        if operation['status'] != 'saved':
            raise decoded.DecodeError('员工计划保存未确认，禁止监控或新排产')
        record.update(status='producing')
        lion.save(record)
        if prepare_only:
            return False
        plans = {j['slot_id']: sum(j['items'].values()) for j in operation['allocation']['jobs']}
        result = monitor(plans, 20, 60, {j['slot_id']: j['items'] for j in operation['allocation']['jobs']}, operation)
        if not result['complete']:
            if result.get('recipe_conflict_slots'):
                raise decoded.DecodeError('狮王在产配方冲突，保留记录，不排新计划')
            continue  # Waiting is not a failed wave or permission to re-save.
        check_cancelled()
        # Never derive task progress from inventory or production counts.


def claim(record):
    from tools.lion_task_ui import LionUI
    binding = record['binding']
    if record['status'] == 'reward_claimed':
        return return_after_reward(record)
    if record['status'] in ('reward_intent', 'reward_unconfirmed'):
        after = decoded.snapshot()
        if lion.reward_confirmed(binding, record['reward_before'], after):
            record.update(status='reward_claimed', completed_tasks=1, reward_recovered=True, reward_after=after)
            lion.save(record)
            return return_after_reward(record)
        raise decoded.DecodeError('上次狮王领奖结果未确认；保留意图，禁止重复领取')
    ui = LionUI(Path(record['folder']) / 'reward_ui')
    try:
        ui.open_board()
        before = full_snapshot()
        state = lion.validate_task(binding, before)
        task = next((t for t in before['family']['tasks'] if t['uid'] == binding['slotUid']), None)
        if (state['status'] != 2 or state['current'] < binding['target'] or not task
                or task['taskItemId'] != binding['taskItemId'] or (task['acceptor'] or {}).get('uid') != binding['selfUid']):
            raise decoded.DecodeError('领奖前本人任务身份／FINISH状态未确认')
        frame = ui.frame('reward_ready')
        slot_label = ui.text((56, 879, 75, 44), frame)
        detail = ui.text((195, 885, 401, 77), frame)
        button = ui.text((245, 1025, 235, 65), frame)
        if slot_label != str(binding['slot']) or f"{binding['target']}/{binding['target']}" not in detail or button not in ('领取', '领取奖励'):
            raise decoded.DecodeError(f'领奖页面不一致，未点击：槽{slot_label}，{detail}，按钮{button}')
        # The immutable before state and intent precede the only reward click.
        record.update(status='reward_intent', reward_before=before, automatic_retry_allowed=False)
        lion.save(record)
        ui.click_label(button, (245, 1025, 235, 65))
        deadline = time.monotonic() + 15
        while True:
            check_cancelled()
            after = decoded.snapshot()
            if lion.reward_confirmed(binding, before, after):
                record.update(status='reward_claimed', completed_tasks=1, reward_after=after)
                lion.save(record)
                print(f"[狮王] 本人任务已确认完成1次，铃铛+{binding['bellReward']}，次数道具-1；不接下一任务。", flush=True)
                break
            if time.monotonic() >= deadline:
                raise decoded.DecodeError('狮王领奖未取得一致回读，不重发')
            time.sleep(.5)
    except BaseException:
        if record['status'] == 'reward_intent':
            record['status'] = 'reward_unconfirmed'
            lion.save(record)
        raise
    finally:
        ui.close()
    return return_after_reward(record)


def return_after_reward(record):
    from tools.lion_task_ui import LionUI
    data = decoded.snapshot()
    decoded.validate(data)
    if any((data.get('familyContext') or {}).get(key) != record['binding'][key] for key in ('selfUid', 'familyUid', 'season')):
        raise decoded.DecodeError('领奖后账户或期数改变，不导航')
    ui = LionUI(Path(record['folder']) / 'return_ui')
    try:
        reward_footer = (170, 1037, 385, 75)
        if ui.text(reward_footer) == '点击空白区域继续':
            ui.click_label('点击空白区域继续', reward_footer)
        if ui.text((229, 1202, 228, 40)) == '点击空白区域继续':
            ui.click_label('点击空白区域继续', (229, 1202, 228, 40))
        ui.leave_board()
        record.update(status='completed', returned_to_farm=True)
        lion.save(record)
        return True
    finally:
        ui.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--prepare-production-only', action='store_true')
    args = parser.parse_args()
    if not args.apply:
        print('预览：仅一个本人已接苹果UP，员工分批生产并回读任务进度，不接新任务。')
        return 0
    from tools.lion_task_ui import LionUI
    from tools.ui_worker import workflow_lock
    with workflow_lock():
        # Resolve a completed acquisition before consulting the older one-task
        # pointer. Old reward/production evidence is never deleted or replayed.
        data = full_snapshot() if employees.read(Path(__file__).resolve().parents[1] / '.tmp/lion_acquisition/active.json') else decoded.snapshot()
        root = lion.acquisition_root(data)
        pointer = employees.read(root / 'active.json')
        if not pointer:
            ui = LionUI(root / 'navigation')
            try:
                ui.open_board()
                data = full_snapshot()
                write_json(root / 'initial.json', data)
                record = lion.begin(data, root=root)
                ui.leave_board()
            finally:
                ui.close()
        else:
            record = lion.begin(decoded.snapshot(), root=root)
        return 0 if execute(record, prepare_only=args.prepare_production_only) else 2


if __name__ == '__main__':
    raise SystemExit(main())

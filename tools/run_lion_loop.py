"""Season-bound Lion acquisition → harvest → reward → next-task campaign."""
import argparse
from pathlib import Path
import time

from tools import decoded_runtime as decoded, lion_loop_operation as campaign
from tools import lion_acquisition as acquisition, run_lion_acquisition as selection
from tools import lion_task_operation as lion, run_lion_task as task_runner
from tools.task_control import check_cancelled, TaskCancelled


def refresh_period(folder):
    from tools.lion_task_ui import LionUI
    ui=LionUI(folder)
    try:
        data=ui.read_period(task_runner.counter_snapshot)
        campaign.period(data)
        return data
    finally:
        ui.close()


def select_round(record, entry, data):
    from tools.lion_task_ui import LionUI
    if entry.get('acquisitionId'):
        saved=campaign.prepare_acquisition(record,entry,data)
        if saved['status']=='ready':
            lion.bind(data,selected=saved)
            return saved['readyTask']
    else:
        data=task_runner.full_snapshot()
        saved=campaign.prepare_acquisition(record,entry,data)
    selection.recover(saved)
    ui=LionUI(campaign.round_folder(record,entry)/'selection_ui')
    try:
        data=task_runner.full_snapshot()
        choice,task=acquisition.choose(data,skipped=saved['skipped'],supported_only=True)
        ui.recover_prepared(task if choice=='own' else None)
        ui.open_board()
        ui.show_tasks()
        chosen=selection.run(saved,ui,supported_only=True)
        ui.leave_board()
        saved['returned_to_farm']=True
        acquisition.save(saved)
        return chosen
    finally:
        ui.close()


def recover_pending(record):
    """Read back uncertain business outcomes before any unrelated navigation."""
    if record['current'] is None:
        return
    entry=record['rounds'][record['current']-1]
    if not entry.get('acquisitionId'):
        return
    saved=campaign.acquisition_record(record,entry)
    selection.recover(saved)
    if entry['status']=='executing':
        task=campaign.execution_record(record,entry)
        if task and task['status'] in ('reward_intent','reward_unconfirmed','reward_claimed'):
            task_runner.execute(task)


def run(record, *, read=task_runner.counter_snapshot, select=select_round, execute=task_runner.execute,
        read_counter=refresh_period, sleep=time.sleep):
    record.update(status='running',error=None)
    campaign.save(record)
    try:
        while True:
            check_cancelled()
            data=read()
            value=campaign.validate(record,data)
            if record['current'] is None:
                if value['completed']>=26:
                    record.update(status='period_target',observedPeriod=value)
                    break
                if len(record['completed'])>=record['options']['max_tasks']:
                    record.update(status='run_limit',observedPeriod=value)
                    break
                if decoded.item_count(3956,data)<1:
                    record.update(status='quota_exhausted',observedPeriod=value)
                    break
                entry=campaign.new_round(record,data)
            else:
                entry=record['rounds'][record['current']-1]
            if entry['status']=='acquiring':
                chosen=select(record,entry,data)
                campaign.totals(record)
                if chosen is None:
                    saved=campaign.acquisition_record(record,entry)
                    if saved.get('waitReason')=='quota_empty':
                        record.update(status='quota_exhausted',observedPeriod=value)
                        break
                    message='当前没有可自动执行且未占用的UP／垃圾候选，保留他人任务，等待刷新。'
                    if record.get('waitMessage')!=message:
                        print('[狮王] '+message,flush=True)
                    record.update(status='waiting',waitMessage=message)
                    campaign.save(record)
                    sleep(record['options']['poll_seconds'])
                    continue
                data=read()
                task=campaign.prepare_execution(record,entry,data)
            else:
                task=campaign.execution_record(record,entry)
                if task is None:
                    raise decoded.DecodeError('本轮执行记录缺失，不重绑或接取下一任务')
            record.update(status='executing',waitMessage=None)
            campaign.save(record)
            if execute(task) is not True:
                raise decoded.DecodeError('本轮任务未取得完成结果，不能推进下一任务')
            # Reload canonical evidence: a click return value never counts.
            task=campaign.execution_record(record,entry)
            if not task or not lion.completed_receipt(task):
                raise decoded.DecodeError('本轮缺少领奖与返回证据，不计数或接新任务')
            data=read_counter(campaign.round_folder(record,entry)/'counter_ui')
            campaign.count_completed(record,entry,task,data)
            spent,refreshes=campaign.totals(record)
            print(f"[狮王] 已确认本期{record['observedPeriod']['completed']}/26；本循环完成{len(record['completed'])}次，"
                  f"礼券{spent}/{record['options']['budget']}，刷新{refreshes}/{record['options']['max_refreshes']}。",flush=True)
        spent,refreshes=campaign.totals(record)
        print(f"[狮王] 循环停止：{record['status']}；本期{record['observedPeriod']['completed']}/26，"
              f"本循环完成{len(record['completed'])}次，礼券{spent}，刷新{refreshes}；不购买次数或珍珠。",flush=True)
        return record
    except TaskCancelled:
        record['status']='stopped'
        raise
    except BaseException as error:
        record.update(status='blocked',error=str(error))
        raise
    finally:
        campaign.save(record)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--tiers',type=int,nargs='+',choices=(2,20),default=[20])
    parser.add_argument('--max-refreshes',type=int,default=20)
    parser.add_argument('--budget',type=int,default=600)
    parser.add_argument('--max-tasks',type=int,default=26)
    parser.add_argument('--poll-seconds',type=int,default=60)
    args=parser.parse_args()
    if (not 1<=args.max_tasks<=26 or not 1<=args.max_refreshes<=200 or not 2<=args.budget<=10000
            or not 10<=args.poll_seconds<=3600):
        parser.error('任务1–26，刷新1–200，礼券2–10000，候选等待间隔10–3600秒')
    options=dict(tiers=sorted(set(args.tiers)),max_refreshes=args.max_refreshes,budget=args.budget,
                 max_tasks=args.max_tasks,poll_seconds=args.poll_seconds)
    if not args.apply:
        print(f'预览：狮王收获UP连续循环；{options}；本期到26或次数耗尽停止；不点击游戏。')
        return 0
    from tools.ui_worker import workflow_lock
    with workflow_lock():
        initial=decoded.snapshot()
        decoded.validate(initial)
        if initial.get('packageVersion')!='100':
            raise decoded.DecodeError('狮王循环仅核对客户端100，未发送游戏输入')
        old=campaign.active()
        if old:
            # Identity is checked without asking a stale board to authorize input.
            ctx=initial.get('familyContext') or {}
            identity={k:ctx.get(k) for k in ('selfUid','familyUid','season')}
            identity['packageVersion']=initial['packageVersion']
            if identity!=old['identity']:
                if not campaign.can_rollover(old,identity):
                    raise decoded.DecodeError('旧循环与当前本人／家族／期数不符，旧轮次未清不能跨期')
            else:
                campaign.check_options(old,options)
                recover_pending(old)
        data=refresh_period(campaign.ROOT/'startup_counter')
        record=campaign.begin(data,options)
        print(f"[狮王] 当前贡献次数{data['family']['period']['completed']}/26；只接已支持产物且指定员工已上阵的UP。",flush=True)
        run(record)
        return 0


if __name__=='__main__':
    raise SystemExit(main())

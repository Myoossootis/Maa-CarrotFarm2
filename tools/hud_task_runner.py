"""Run a frozen HUD request through the existing Maa actions and CLI workers."""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent import task_settings
from tools.runtime_state import write_json
from tools.task_control import TaskCancelled, _check_stop
from tools.ui_worker import workflow_lock

SUPPORTED = {
    'RunOwnLionUP': 'RunOwnLionUP',
    'RunDailyBatchOrders': 'RunDailyOrders', 'RunDailyPartyOrders': 'RunDailyOrders',
    'RunDailyTakeoutOrders': 'RunDailyOrders', 'RunDailyNormalOrders': 'RunDailyOrders',
    'RunNormalOrderLoop': 'RunDailyOrders', 'RefreshOrder': 'RefreshOrder',
    'CloseOfflineIncome': 'CloseOfflineIncome', 'RunFeedProduction': 'RunFeedProduction',
    'RunShortCyclePreview': 'RunShortCycle', 'RunShortCycleApply': 'RunShortCycle',
    'RunShortCycle': 'RunShortCycle', 'ManualPlantingOnce': 'ManualPlantingOnce',
    'ManualPlantingPreflight': 'ManualPlantingPreflight',
}


def make_request(entry, options):
    catalog = task_settings.interface()
    if entry not in SUPPORTED:
        raise ValueError('此任务没有经过独立界面入口验证')
    checked = task_settings.validate_options(entry, options, catalog)
    if set(checked) != set(task_settings.task_options(entry, catalog)):
        raise ValueError('请完整填写此模板的全部选项')
    return dict(schema_version=1, app_version=catalog['version'], entry=entry, options=checked)


def validate_request(request):
    if not isinstance(request, dict) or type(request.get('schema_version')) is not int or request['schema_version'] != 1:
        raise ValueError('运行请求版本不合法')
    checked = make_request(request.get('entry'), request.get('options'))
    if request.get('app_version') != checked['app_version']:
        raise ValueError('运行请求与当前程序版本不一致，请重新开始')
    return checked


class LocalTasker:
    @property
    def stopping(self):
        try:
            _check_stop()
        except TaskCancelled:
            return True
        return False


class LocalContext:
    def __init__(self, entry, node):
        self.entry, self.node = entry, node
        self.tasker = LocalTasker()

    def get_node_data(self, entry):
        if entry != self.entry:
            raise ValueError('任务身份不一致')
        return deepcopy(self.node)

    def run_task(self, entry, override):
        # Existing actions use this node only to publish log text to MFA.
        if entry != 'UiProgressLog':
            raise ValueError('独立执行器禁止未映射的原生任务')
        return SimpleNamespace()


def check_business_version(request):
    """Open the game if needed, then check semantics before task navigation."""
    entry = request['entry']
    if entry == 'RunShortCyclePreview' or (
            entry == 'RunShortCycle' and request['options']['周期运行模式'] == '仅预览'):
        return
    from tools.game_launcher import ensure_open
    from tools.task_control import check_cancelled
    ensure_open(check_cancelled=check_cancelled)
    if entry in ('ManualPlantingPreflight', 'CloseOfflineIncome'):
        return
    from tools import decoded_runtime as decoded
    data = decoded.snapshot()
    if entry in ('RunDailyBatchOrders', 'RunDailyPartyOrders', 'RunDailyTakeoutOrders'):
        kinds = ('party', 'takeout') if entry == 'RunDailyBatchOrders' else (
            'party' if entry == 'RunDailyPartyOrders' else 'takeout',)
        for kind in kinds:
            try:
                decoded.order_capacity(kind, data)
            except decoded.DecodeError as error:
                raise decoded.DecodeError(f"客户端{data.get('packageVersion')}的聚会／外卖次数尚未核对，未发送游戏输入") from error
    else:
        decoded.require_normal_package(data)


def execute(request):
    from agent import production_actions, manual_planting_action
    from agent.cycle_bridge import run_cycle_worker
    request = validate_request(request)
    entry = request['entry']
    pipeline = json.loads((ROOT / 'assets/resource/pipeline/carrot_farm.json').read_text(encoding='utf-8'))
    node = pipeline[entry]
    context = LocalContext(entry, node)
    argv = SimpleNamespace(node_name=entry, custom_action_param=json.dumps(node.get('custom_action_param', {})),
                           task_options_snapshot=request['options'], ui_start_confirmed=True)
    if entry == 'ManualPlantingPreflight':
        parameters, configured = task_settings.action_values(context, argv)
        planting = manual_planting_action.request_from_node(configured, parameters)
        return run_cycle_worker(['--field', planting['field_id'], '--crop-item-id', str(planting['crop_item_id']),
                                 '--count', str(planting['count'])], is_stopping=lambda: context.tasker.stopping,
                                script='run_manual_planting.py')
    module = manual_planting_action if entry == 'ManualPlantingOnce' else production_actions
    return getattr(module, SUPPORTED[entry])().run(context, argv)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--check', action='store_true', help='validate request without starting a task')
    args = parser.parse_args()
    result = dict(phase='failed', message='运行未确认结束')
    code = 2
    try:
        request = validate_request(json.loads(args.request.read_text(encoding='utf-8')))
        if args.check:
            print(json.dumps(dict(entry=request['entry'], valid=True, game_input=False), ensure_ascii=False))
            return 0
        with workflow_lock():
            _check_stop()
            check_business_version(request)
            _check_stop()
            write_json(args.request.with_name('started.json'), {'entry': request['entry'], 'pid': os.getpid()})
            print(f"[任务] 开始 {request['entry']}；使用本次冻结配置。", flush=True)
            okay = execute(request)
            stopped = LocalTasker().stopping
            result = dict(phase='stopped' if stopped else 'completed' if okay else 'failed',
                          entry=request['entry'], message='已停止，进度保留' if stopped else
                          '执行结束，请查看业务回执和日志' if okay else '任务未完成，请查看日志中的阻塞原因')
            code = 130 if stopped else 0 if okay else 2
    except TaskCancelled:
        result = dict(phase='stopped', message='已停止，未重置计划与回执')
        code = 130
    except Exception as error:
        result = dict(phase='failed', message=str(error))
        print(f'[错误] {error}', flush=True)
    finally:
        if not args.check:
            write_json(args.request.with_name('result.json'), result)
    print('[任务] ' + result['message'], flush=True)
    return code


if __name__ == '__main__':
    raise SystemExit(main())

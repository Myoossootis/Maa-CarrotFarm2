"""Configure a local MFAAvalonia folder to use this project's isolated runtime."""

import argparse
import importlib.metadata
import json
from pathlib import Path
import shutil
import sys


ROOT = Path(__file__).resolve().parents[1]


def _sync_instance_task_items(ui_dir, interface):
    """MFAAvalonia caches TaskItems in config/instances/*.json and keeps stale order.
    Rewrite from interface.json every prepare so new tasks appear at the top.
    """
    instances = ui_dir / 'config' / 'instances'
    if not instances.is_dir():
        return
    tasks = interface.get('task') or []
    task_items = []
    current_tasks = []
    for task in tasks:
        item = {
            'name': task['name'],
            'entry': task['entry'],
            'default_check': bool(task.get('default_check', False)),
        }
        item['description'] = task.get('description') or ''
        if task.get('controller'):
            item['controller'] = list(task['controller'])
        if task.get('option'):
            opts = []
            for opt in task['option']:
                if isinstance(opt, str):
                    opts.append({'name': opt})
                elif isinstance(opt, dict) and opt.get('name'):
                    opts.append({'name': opt['name']})
            if opts:
                item['option'] = opts
        task_items.append(item)
        current_tasks.append(f"{task['name']}<|||>{task['entry']}")
    for path in instances.glob('*.json'):
        data = json.loads(path.read_text(encoding='utf-8'))
        data['TaskItems'] = task_items
        data['CurrentTasks'] = current_tasks
        if interface.get('controller'):
            data['CurrentControllerName'] = interface['controller'][0]['name']
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def _dashboard_card(card_id, col, row, row_span):
    return {
        'Id': card_id, 'Col': col, 'Row': row, 'ColSpan': 4, 'RowSpan': row_span,
        'ExpandedRowSpan': row_span, 'ExpandedColSpan': 4,
        'MaximizedRowSpan': 1, 'MaximizedColSpan': 1,
    }


def _ensure_monitor_top_left(ui_dir):
    """Seed task list on the left and monitor on the right when no layout is saved.

    An existing arrangement is left untouched, including one the user dragged.
    """
    config_file = ui_dir / 'config' / 'config.json'
    if not config_file.is_file():
        return
    data = json.loads(config_file.read_text(encoding='utf-8'))
    key = 'UI.DashboardCardGrid.Layout.TaskQueueView'
    current = data.get(key)
    if isinstance(current, list) and current:
        return
    layout = [
        _dashboard_card('task_list', 0, 0, 8),
        _dashboard_card('settings', 4, 0, 5),
        _dashboard_card('task_desc', 4, 5, 3),
        _dashboard_card('live_view', 8, 0, 4),
        _dashboard_card('log', 8, 4, 4),
    ]
    data[key] = layout
    hash_key = 'UI.DashboardCardGrid.ResourceLayoutHash.TaskQueueView'
    if hash_key in data:
        data[hash_key] = 'TASK_LIST_LEFT_MONITOR_RIGHT_V1'
    config_file.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')



def _assert_daily_pipeline_ready():
    """Hard-fail prepare if party/takeout focus still says not-connected (stale UX)."""
    pipe = ROOT / 'assets/resource/pipeline/carrot_farm.json'
    data = json.loads(pipe.read_text(encoding='utf-8'))
    banned = ('尚未接入', 'order_board_not_implemented')
    for key in ('RunDailyPartyOrders', 'RunDailyTakeoutOrders'):
        node = data.get(key) or {}
        focus = node.get('focus') or {}
        blob = json.dumps(focus, ensure_ascii=False)
        for bad in banned:
            if bad in blob:
                raise RuntimeError(
                    f'{key} focus still contains {bad!r} — update carrot_farm.json before prepare'
                )
        starting = ((focus.get('Node.Action.Starting') or {}).get('content') or '')
        if '页签' not in starting:
            raise RuntimeError(f'{key} Starting focus must describe tab switch (页签)')
        if node.get('custom_action') != 'RunDailyOrders':
            raise RuntimeError(f'{key} must use custom_action RunDailyOrders')
        expect = 'party' if 'Party' in key else 'takeout'
        param = (node.get('custom_action_param') or {}).get('order_type')
        if param != expect:
            raise RuntimeError(f'{key} order_type must be {expect!r}, got {param!r}')


def prepare(ui_dir):
    ui_dir = Path(ui_dir).resolve()
    _assert_daily_pipeline_ready()
    if not (ui_dir / 'MFAAvalonia.exe').is_file():
        raise RuntimeError('MFAAvalonia.exe not found; unpack the official win-x64 UI first')
    if importlib.metadata.version('MaaFw') != '5.13.1':
        raise RuntimeError('Run this command with the project .venv (MaaFw 5.13.1)')
    interface = json.loads((ROOT / 'assets/interface.json').read_text(encoding='utf-8'))
    interface['agent'] = {'child_exec': str(Path(sys.executable).resolve()),
                          'child_args': ['-u', str(ROOT / 'agent/main.py')]}
    interface['resource'][0]['path'] = [str(ROOT / 'assets/resource')]
    # Keep old diagnostic pipelines in source, but do not expose unvalidated
    # native Click/Scroll tasks in the new background-safe local UI.
    allowed = {
        'OpenDecodedDashboard',
        'OpenFarmHud',
        'ManualPlantingPreflight',
        'ManualPlantingOnce',
        'RuntimePreflight',
        'RunDailyBatchOrders', 'RunDailyPartyOrders', 'RunDailyTakeoutOrders', 'RunDailyNormalOrders', 'RunNormalOrderLoop',
        'CloseOfflineIncome',
        'RefreshOrder', 'RunFeedProduction',
        'EnterRestaurantBackground', 'EnterFarmBackground', 'OpenPhoneBackground',
        'OpenEmployeeConfigBackground', 'SelectFarmEmploymentBackground',
        'OpenFieldDetailBackground', 'ReturnEmployeeListBackground', 'LocateField2Background',
        'OpenWorkPlanBackground', 'CloseWorkPlanBackground',
        'VerifyField2WorkPlanBackground', 'VerifyField3WorkPlanBackground',
        'VerifyRanch1WorkPlanBackground', 'VerifyRanch2WorkPlanBackground',
        'VerifyOrchard1WorkPlanBackground', 'VerifyOrchard2WorkPlanBackground',
        'RunShortCycle', 'RecognizeClockStart',
    }
    order = {
        'OpenFarmHud': -2,
        'OpenDecodedDashboard': -1,
        'ManualPlantingPreflight': -0.5,
        'ManualPlantingOnce': -0.4,
        'RuntimePreflight': 0,
        'RunDailyBatchOrders': 1, 'RunDailyPartyOrders': 2, 'RunDailyTakeoutOrders': 3, 'RunDailyNormalOrders': 4,
        'RunNormalOrderLoop': 4.1,
        'CloseOfflineIncome': 4,
        'RefreshOrder': 4,
        'EnterRestaurantBackground': 5, 'EnterFarmBackground': 6, 'OpenPhoneBackground': 7,
        'OpenEmployeeConfigBackground': 8, 'SelectFarmEmploymentBackground': 9,
        'OpenFieldDetailBackground': 10, 'OpenWorkPlanBackground': 11, 'CloseWorkPlanBackground': 12,
        'ReturnEmployeeListBackground': 13, 'LocateField2Background': 14,
        'VerifyField2WorkPlanBackground': 15, 'VerifyField3WorkPlanBackground': 16,
        'VerifyRanch1WorkPlanBackground': 17, 'VerifyRanch2WorkPlanBackground': 18,
        'VerifyOrchard1WorkPlanBackground': 19, 'VerifyOrchard2WorkPlanBackground': 20,
        'RunShortCycle': 21, 'RecognizeClockStart': 22,
        'RunFeedProduction': 100,
    }
    # Force controller name to match interface controller (typos hide tasks in MFA).
    canonical_controller = interface['controller'][0]['name']
    for task in interface['task']:
        if 'controller' in task:
            task['controller'] = [canonical_controller]
    interface['task'] = sorted(
        [task for task in interface['task'] if task['entry'] in allowed],
        key=lambda task: order[task['entry']],
    )
    (ui_dir / 'interface.json').write_text(
        json.dumps(interface, ensure_ascii=False, indent=4) + '\n', encoding='utf-8')
    _sync_instance_task_items(ui_dir, interface)
    _ensure_monitor_top_left(ui_dir)
    import maa
    native = Path(maa.__file__).parent / 'bin'

    def copy_changed(source, destination):
        destination = Path(destination)
        if destination.exists():
            source_stat = Path(source).stat()
            dest_stat = destination.stat()
            if source_stat.st_size == dest_stat.st_size and int(source_stat.st_mtime) == int(dest_stat.st_mtime):
                return destination
        return shutil.copy2(source, destination)

    shutil.copytree(native, ui_dir / 'runtimes/win-x64/native', dirs_exist_ok=True, copy_function=copy_changed)
    config = ui_dir / 'config'
    config.mkdir(exist_ok=True)
    (config / 'maa_option.json').write_text(json.dumps({
        'logging': False, 'save_draw': False, 'save_on_error': False, 'stdout_level': 2,
    }, indent=4) + '\n', encoding='utf-8')
    settings_file = ui_dir / 'appsettings.json'
    settings = json.loads(settings_file.read_text(encoding='utf-8')) if settings_file.exists() else {}
    settings['NoAutoStart'] = 'True'
    settings_file.write_text(json.dumps(settings, ensure_ascii=False, indent=4) + '\n', encoding='utf-8')
    (ui_dir / '.prepare_stamp.json').write_text(
        json.dumps(source_stamp(), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return ui_dir


def file_mark(path):
    stat = Path(path).stat()
    return {'size': stat.st_size, 'mtime': int(stat.st_mtime)}


def source_stamp():
    """Identity of the files copied into the local UI. Seconds, matching the launcher."""
    import maa
    return {
        'interface': file_mark(ROOT / 'assets/interface.json'),
        'pipeline': file_mark(ROOT / 'assets/resource/pipeline/carrot_farm.json'),
        'maafw_dll': file_mark(Path(maa.__file__).parent / 'bin' / 'MaaFramework.dll'),
        'maafw': importlib.metadata.version('MaaFw'),
    }


def up_to_date(ui_dir):
    ui_dir = Path(ui_dir)
    stamp_path = ui_dir / '.prepare_stamp.json'
    if not (ui_dir / 'MFAAvalonia.exe').is_file() or not stamp_path.is_file():
        return False
    try:
        saved = json.loads(stamp_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return False
    return saved == source_stamp()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--ui-dir', type=Path, default=ROOT / '.tmp/maa_ui')
    args = parser.parse_args()
    print(prepare(args.ui_dir))


if __name__ == '__main__':
    main()

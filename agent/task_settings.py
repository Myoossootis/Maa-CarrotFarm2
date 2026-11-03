"""Local task options shared by the HUD and Maa actions; no game I/O."""

from copy import deepcopy
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = ROOT / 'config/task_settings.json'
MAA_INSTANCE = ROOT / '.tmp/maa_ui/config/instances/default.json'


def interface():
    return json.loads((ROOT / 'assets/interface.json').read_text(encoding='utf-8'))


def task_options(entry, catalog=None):
    catalog = catalog or interface()
    task = next((task for task in catalog['task'] if task['entry'] == entry), None)
    if task is None:
        raise ValueError(f'未知任务：{entry}')
    return {name: catalog['option'][name] for name in task.get('option', [])}


def option_default(option):
    if option['type'] == 'input':
        return {item['name']: str(item['default']) for item in option['inputs']}
    return deepcopy(option['default_case'])


def validate_options(entry, values, catalog=None):
    options = task_options(entry, catalog)
    if not isinstance(values, dict) or set(values) - options.keys():
        raise ValueError('任务配置包含未知选项，请重新读取配置')
    for name, value in values.items():
        option = options[name]
        cases = {case['name'] for case in option.get('cases', [])}
        if option['type'] == 'checkbox':
            if not isinstance(value, list) or not value or any(not isinstance(x, str) or x not in cases for x in value):
                raise ValueError(f'{name}：至少选择一项，且必须是已知品类')
        elif option['type'] == 'select':
            if not isinstance(value, str) or value not in cases:
                raise ValueError(f'{name}：请选择有效选项')
        elif option['type'] == 'input':
            inputs = option['inputs']
            if not isinstance(value, dict) or set(value) != {item['name'] for item in inputs}:
                raise ValueError(f'{name}：输入字段不完整')
            for item in inputs:
                text = value[item['name']]
                if not isinstance(text, str) or not re.fullmatch(item['verify'], text):
                    raise ValueError(f"{item.get('label', item['name'])}：输入不合法")
            if name == '周期参数' and not int(value['poll']) <= int(value['timeout']) <= 7200:
                raise ValueError('监控超时须不小于检查间隔，且最多7200秒')
        else:
            raise ValueError(f'尚不支持的选项类型：{option["type"]}')
    return deepcopy(values)


def load_settings(path=None):
    try:
        data = json.loads((path or SETTINGS).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as error:
        raise ValueError('本地任务配置无法读取，请修复配置文件后再运行') from error
    if (not isinstance(data, dict) or type(data.get('schema_version')) is not int
            or data['schema_version'] != 1 or not isinstance(data.get('tasks'), dict)):
        raise ValueError('本地任务配置结构或版本不合法')
    catalog = interface()
    return {entry: validate_options(entry, values, catalog) for entry, values in data['tasks'].items()}


def save_task(entry, values, path=None):
    # Reuse the existing atomic checkpoint writer, including fsync and replace.
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from tools.runtime_state import write_json
    checked = validate_options(entry, values)
    tasks = load_settings(path)
    if checked:
        tasks[entry] = checked
    else:
        tasks.pop(entry, None)
    write_json(path or SETTINGS, {'schema_version': 1, 'tasks': tasks})


def maa_settings(path=None, catalog=None):
    """Read option values only. Never rewrite MFA's running instance file."""
    try:
        data = json.loads((path or MAA_INSTANCE).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as error:
        raise ValueError('Maa任务配置无法读取') from error
    if not isinstance(data, dict) or not isinstance(data.get('TaskItems'), list):
        raise ValueError('Maa任务配置结构不合法')
    catalog = catalog or interface()
    known = {task['entry'] for task in catalog['task']}
    tasks = {}
    for task in data['TaskItems']:
        if not isinstance(task, dict):
            raise ValueError('Maa任务条目不合法')
        entry = task.get('entry')
        if entry not in known:
            continue
        options = task_options(entry, catalog)
        values = {}
        saved_options = task.get('option', [])
        if not isinstance(saved_options, list):
            raise ValueError('Maa任务选项结构不合法')
        for saved in saved_options:
            if not isinstance(saved, dict):
                raise ValueError('Maa任务选项结构不合法')
            name = saved.get('name')
            if name not in options:
                raise ValueError(f'{entry}包含未知Maa选项')
            option = options[name]
            if option['type'] == 'input' and 'data' in saved:
                values[name] = saved['data']
            elif option['type'] == 'checkbox' and 'selected_cases' in saved:
                values[name] = saved['selected_cases']
            elif option['type'] == 'select' and saved.get('index') is not None:
                index = saved['index']
                if type(index) is not int or not 0 <= index < len(option['cases']):
                    raise ValueError(f'{name}的Maa选择不合法')
                values[name] = option['cases'][index]['name']
        if values:
            tasks[entry] = validate_options(entry, values, catalog)
    return tasks


def editor_values(entry, *, path=None, maa_path=None, catalog=None):
    catalog = catalog or interface()
    defaults = {name: option_default(option) for name, option in task_options(entry, catalog).items()}
    saved = load_settings(path).get(entry, {})
    if saved and set(saved) == set(defaults):
        return {**defaults, **saved}, '本地保存'
    imported = maa_settings(maa_path, catalog).get(entry, {})
    return {**defaults, **imported, **saved}, ('本地保存' if saved else 'Maa任务配置' if imported else '默认值')


def option_override(entry, name, value, catalog=None):
    """Compile the existing interface options, using their declared types."""
    catalog = catalog or interface()
    validate_options(entry, {name: value}, catalog)
    option = catalog['option'][name]
    if option['type'] == 'input':
        converted = {item['name']: int(value[item['name']]) if item.get('pipeline_type') == 'int'
                     else value[item['name']] for item in option['inputs']}

        def substitute(item):
            if isinstance(item, dict):
                return {key: substitute(val) for key, val in item.items()}
            if isinstance(item, str) and item.startswith('{') and item.endswith('}'):
                return converted[item[1:-1]]
            return item

        return substitute(option['pipeline_override'].get(entry, {}))
    chosen = value if option['type'] == 'checkbox' else [value]
    result = {}
    if option['type'] == 'checkbox':
        # Explicit false flags prevent old Maa selections from leaking through.
        result['attach'] = {key: False for case in option['cases']
                            for key in case['pipeline_override'].get(entry, {}).get('attach', {})}
    for case in option['cases']:
        if case['name'] in chosen:
            merge(result, case['pipeline_override'].get(entry, {}))
    return result


def merge(target, incoming):
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            merge(target[key], value)
        else:
            target[key] = deepcopy(value)


def action_values(context, argv):
    parameters = json.loads(argv.custom_action_param or '{}')
    node = deepcopy(context.get_node_data(argv.node_name) or {})
    if not isinstance(parameters, dict) or not isinstance(node, dict):
        raise ValueError('任务参数必须为对象')
    overrides = load_settings().get(argv.node_name, {})
    catalog = interface() if overrides else None
    for name, value in overrides.items():
        override = option_override(argv.node_name, name, value, catalog)
        parameters.update(override.pop('custom_action_param', {}))
        merge(node, override)
    return parameters, node

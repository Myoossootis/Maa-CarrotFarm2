"""Separate Maa entries for read-only preview and one finite planting request."""
import json
from pathlib import Path
import sys

from maa.custom_action import CustomAction

try:
    from .task_settings import action_values
except ImportError:
    from task_settings import action_values


def request_from_node(node, parameters):
    if not isinstance(node, dict) or not isinstance(parameters, dict) or not isinstance(node.get('attach', {}), dict):
        raise ValueError('手动种植节点参数须为对象')
    if set(parameters) - {'count'}:
        raise ValueError('手动种植入口只接受数量参数，不支持执行开关')
    attach = node.get('attach', {})
    field = attach.get('manual_field_id', 'field_1')
    crop = attach.get('manual_crop_item_id', 14)
    count = parameters.get('count', 1)
    if type(count) is str and count.isascii() and count.isdecimal():
        count = int(count)
    if field not in ('field_1', 'field_2', 'field_3') or type(crop) is not int or type(count) is not int or not 1 <= count <= 36:
        raise ValueError('需要农田1–3、普通作物ID与1–36格数量')
    return {'field_id': field, 'crop_item_id': crop, 'count': count}


class ManualPlantingPreflight(CustomAction):
    def run(self, context, argv) -> bool:
        if context.tasker.stopping:
            return False
        root = Path(__file__).resolve().parents[1]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from tools.manual_planting import save_preflight
        from tools.decoded_dashboard import launch_dashboard
        try:
            parameters, node = action_values(context, argv)
            request = request_from_node(node, parameters)
            result = save_preflight(**request)
            if context.tasker.stopping:
                return False
            pid = launch_dashboard('manual_planting', planting_request=request)
            print(json.dumps({'task': 'manual_planting_preflight', 'readOnly': True,
                              'executionEnabled': False, 'ready': result['ready'], 'windowPid': pid,
                              'blockers': result['blockers'], 'gameOperations': 0}, ensure_ascii=False), flush=True)
            return result['ready']
        except (ValueError, KeyError, TypeError, OSError, RuntimeError) as error:
            print(f'[手动种植预检] {error}；未发送游戏输入。', flush=True)
            return False


class ManualPlantingOnce(CustomAction):
    def run(self, context, argv) -> bool:
        if context.tasker.stopping:
            return False
        try:
            parameters, node = action_values(context, argv)
            request = request_from_node(node, parameters)
        except (ValueError, TypeError, KeyError, OSError) as error:
            print(f'[单次手动种植] 参数错误：{error}；未发送游戏输入。', flush=True)
            return False
        try:
            from .cycle_bridge import run_cycle_worker
        except ImportError:
            from cycle_bridge import run_cycle_worker
        return run_cycle_worker(['--field', request['field_id'], '--crop-item-id', str(request['crop_item_id']),
                                 '--count', str(request['count']), '--apply'],
                                is_stopping=lambda: context.tasker.stopping, script='run_manual_planting.py')

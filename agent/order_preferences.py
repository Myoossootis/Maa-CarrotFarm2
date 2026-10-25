"""Read selectable order ingredients from Maa configuration, without game I/O."""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OPTION = '一号位允许品类'
PREFIX = 'first_order_allow_'


def registry_products():
    return json.loads((ROOT / 'assets/catalog/backpack_crops/registry.json').read_text(encoding='utf-8'))['products']


def validate_allowed_products(names):
    known = {item['name'] for item in registry_products()}
    if not isinstance(names, (list, tuple, set, frozenset)) or not names:
        raise ValueError('请至少勾选一种一号位允许品类')
    if any(not isinstance(name, str) or name not in known for name in names):
        raise ValueError('一号位允许品类包含未知品名，请重新选择图鉴中的品类')
    return frozenset(names)


def default_allowed_products():
    interface = json.loads((ROOT / 'assets/interface.json').read_text(encoding='utf-8'))
    return validate_allowed_products(interface['option'][OPTION]['default_case'])


def allowed_products_from_node(node):
    attach = node.get('attach', {})
    flags = {key: value for key, value in attach.items() if key.startswith(PREFIX)}
    if not flags:
        return default_allowed_products()
    by_key = {PREFIX + item['id']: item['name'] for item in registry_products()}
    if any(key not in by_key or type(value) is not bool for key, value in flags.items()):
        raise ValueError('一号位品类选项不合法，请重新加载 Maa 配置')
    return validate_allowed_products([by_key[key] for key, value in flags.items() if value])

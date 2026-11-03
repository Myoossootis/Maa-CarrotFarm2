"""Recognize order pipeline templates, but click only the non-activating child."""

import json
import time
import cv2

try:
    from .configure_field_employee import RESOURCE, locate, screenshot
    from .maa_frame_normalize import post_click_normalized
except ImportError:
    from configure_field_employee import RESOURCE, locate, screenshot
    from maa_frame_normalize import post_click_normalized


ORDER_TYPES = ("normal", "party", "takeout")

_READY_NODE = {
    "normal": "VerifyNormalOrdersReady",
    "party": "VerifyPartyOrdersReady",
    "takeout": "VerifyTakeoutOrdersReady",
}
_TAB_CLICK_NODE = {
    "normal": "ClickNormalOrderTab",
    "party": "ClickPartyOrderTab",
    "takeout": "ClickTakeoutOrderTab",
}


def normalize_order_type(raw: str) -> str:
    key = (raw or "normal").strip().lower()
    aliases = {
        "normal": "normal",
        "普通": "normal",
        "普通订单": "normal",
        "party": "party",
        "juhui": "party",
        "聚会": "party",
        "聚会订单": "party",
        "takeout": "takeout",
        "waimai": "takeout",
        "外卖": "takeout",
        "外卖订单": "takeout",
    }
    if key not in aliases:
        raise ValueError(f"unknown order type: {raw!r}")
    return aliases[key]


def template_hit(frame, name):
    node = json.loads((RESOURCE / 'pipeline/carrot_farm.json').read_text(encoding='utf-8'))[name]
    if node.get('recognition') != 'TemplateMatch' or node.get('method', 5) != 5:
        raise ValueError(f'not a supported template node: {name}')
    templates = node['template']
    if isinstance(templates, str):
        templates = [templates]
    thresholds = node.get('threshold', .7)
    if not isinstance(thresholds, list):
        thresholds = [thresholds] * len(templates)
    best = None
    for filename, threshold in zip(templates, thresholds):
        score, point = locate(frame, filename, tuple(node['roi']), image_dir=RESOURCE / 'image')
        if score < threshold or best is not None and score <= best[0]:
            continue
        image = cv2.imread(str(RESOURCE / 'image' / filename))
        target = node.get('target')
        if target is None:
            center = (point[0] + image.shape[1] // 2, point[1] + image.shape[0] // 2)
        elif len(target) == 2:
            center = tuple(target)
        else:
            center = (target[0] + target[2] // 2, target[1] + target[3] // 2)
        best = (score, center, node.get('post_delay', 0) / 1000)
    return best


def click_template_node(controller, name, frame=None):
    hit = template_hit(screenshot(controller) if frame is None else frame, name)
    if hit is None:
        return False
    if not post_click_normalized(controller, *hit[1]).wait().succeeded:
        raise RuntimeError(f'background order click failed: {name}')
    time.sleep(max(.3, hit[2]))
    return True


def open_normal_orders(controller, *, stop_when=None):
    """Backward-compatible alias for the normal-order tab."""
    return open_orders(controller, "normal", stop_when=stop_when)


def open_orders(controller, order_type: str = "normal", *, stop_when=None) -> bool:
    """Open the shared notice-board and switch to party/takeout/normal tab."""
    order_type = normalize_order_type(order_type)
    ready = _READY_NODE[order_type]
    tab = _TAB_CLICK_NODE[order_type]
    for _ in range(12):
        frame = screenshot(controller)
        if stop_when is not None and stop_when(frame):
            return False
        if template_hit(frame, ready) is not None:
            return True
        for name in (
            tab,
            "NormalOrdersClickNoticeBoard",
            "NormalOrdersClickEmptyNoticeBoard",
            "NormalOrdersClickRestaurantEntry",
        ):
            if click_template_node(controller, name, frame):
                time.sleep(0.45)
                break
        else:
            time.sleep(0.3)
    return False

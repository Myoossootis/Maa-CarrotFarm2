"""Read the current batch-order board before selecting cached recipes."""

import re
import time
from pathlib import Path

import cv2
import numpy as np

try:
    from . import decoded_runtime as decoded
    from .ensure_ranch_feed import recognized_text
    from .refresh_order import card_image, card_selected, card_pending
    from .order_template_click import template_hit
    from .scan_normal_orders import screenshot
    from .maa_frame_normalize import EXPECTED_HEIGHT, EXPECTED_WIDTH
except ImportError:
    import decoded_runtime as decoded
    from ensure_ranch_feed import recognized_text
    from refresh_order import card_image, card_selected, card_pending
    from order_template_click import template_hit
    from scan_normal_orders import screenshot
    from maa_frame_normalize import EXPECTED_HEIGHT, EXPECTED_WIDTH

ROOT = Path(__file__).resolve().parents[1]
WAITING_STATES = {'board_unavailable', 'board_loading', 'order_capacity_exhausted', 'order_batch_unavailable'}
# Live FramePool frames are often a few pixels short of 1358. The same drift is
# already accepted by top_letterbox_offset; an exact height check rejected them.
BOARD_HEIGHT_TOLERANCE = 16


def board_frame(frame):
    height, width = frame.shape[:2]
    return width == EXPECTED_WIDTH and abs(height - EXPECTED_HEIGHT) <= BOARD_HEIGHT_TOLERANCE


def empty_grid(frame):
    reference = cv2.imread(str(ROOT / 'assets/resource/image/navigation/batch_empty_grid.png'))
    if reference is None:
        raise RuntimeError('missing_empty_batch_board_template')
    # Compare every card separately. A single occupied card must reject an
    # empty-board match, even if most of the large board is still background.
    for number in range(1, 10):
        actual = card_image(frame, number)
        x, y = 210 * ((number - 1) % 3), 156 * ((number - 1) // 3)
        expected = reference[y:y + 100, x:x + 140]
        if actual.shape != expected.shape or not actual.size:
            return False
        difference = cv2.absdiff(actual, expected)
        if float(difference.mean()) > 8 or float(np.mean(difference.max(axis=2) > 25)) > .03:
            return False
    return True


def read_availability(tasker, frame, kind):
    if kind not in {'party', 'takeout'}:
        raise ValueError('batch board availability only supports party/takeout')
    ready = {'party': 'VerifyPartyOrdersReady', 'takeout': 'VerifyTakeoutOrdersReady'}[kind]
    result = dict(status='board_unknown', reason='unrecognized_board', next_available=None)
    if not board_frame(frame) or template_hit(frame, ready) is None:
        return result
    caption = recognized_text(tasker, frame, (205, 305, 335, 78), expected=(r'.+',))
    counter = re.findall(r'(\d+)\s*/\s*(\d+)', caption)
    result.update(caption=caption, ui_counter=list(map(int, counter[0])) if len(counter) == 1 else None)
    empty = empty_grid(frame)
    result['empty_grid'] = empty
    notice = bool(re.search(r'已达每日(?:恢复|接受)\d+次上限', caption))
    loading = '加载中' in caption or '正在加载' in caption
    if empty:
        if loading:
            result.update(status='board_loading', reason='ui_loading_notice')
        elif notice:
            result.update(status='board_loading', reason='empty_board_requires_recheck')
        else:
            result['reason'] = 'empty_board_without_unambiguous_notice'
    elif loading:
        result['reason'] = 'conflicting_board_evidence'
    elif any(card_selected(frame, number) or card_pending(frame, number) for number in range(1, 10)):
        result.update(status='available', reason='visible_order_card')
    else:
        result['reason'] = 'order_cards_unconfirmed'
    return result


def probe_availability(tasker, controller, kind, *, required_count=None):
    """Bounded fresh reads only: tab-ready can precede card loading.

    The limit caption is also present on populated boards. It cannot establish
    the cause of an empty board, exhausted acceptance quota, or a resume time.
    """
    observations = []
    for attempt in range(3):
        current = read_availability(tasker, screenshot(controller), kind)
        observations.append(current.copy())
        if current['status'] != 'board_loading':
            break
        if attempt < 2:
            time.sleep(.5)
    if (len(observations) == 3
            and all(item['reason'] == 'empty_board_requires_recheck' for item in observations)
            and len({item['caption'] for item in observations}) == 1):
        current.update(status='board_unavailable', reason='confirmed_empty_board')
    current['observations'] = observations
    if current['status'] == 'available' and required_count is not None:
        if type(required_count) is not int or not 1 <= required_count <= 6 or not decoded.enabled():
            raise decoded.DecodeError('Order capacity check requires decoded mode and an explicit valid count')
        capacity = decoded.order_capacity(kind)
        counter = current.get('ui_counter')
        current['capacity'] = capacity
        current['required_count'] = required_count
        if (not isinstance(counter, list) or len(counter) != 2 or any(type(x) is not int for x in counter)
                or counter != [capacity['remaining'], capacity['limit']]):
            current.update(status='board_unknown', reason='capacity_ui_cache_mismatch')
        elif capacity['remaining'] == 0:
            current.update(status='order_capacity_exhausted', reason='confirmed_zero_order_tokens')
        elif capacity['remaining'] < required_count:
            current.update(status='order_batch_unavailable', reason='insufficient_order_tokens_for_requested_batch')
    return current

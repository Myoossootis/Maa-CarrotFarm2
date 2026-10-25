"""Reusable normal-order board recovery helpers (sleep / close / farm return).

Extracted so short-cycle and complete_normal_orders share one path. All clicks
use PostMessage via post_click_normalized; never minimize the game window.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

try:
    from .configure_field_employee import locate_nav, match, match_nav
    from .maa_frame_normalize import post_click_normalized
    from .scan_normal_orders import screenshot
    from .order_template_click import click_template_node, template_hit
except ImportError:
    from configure_field_employee import locate_nav, match, match_nav
    from maa_frame_normalize import post_click_normalized
    from scan_normal_orders import screenshot
    from order_template_click import click_template_node, template_hit

if TYPE_CHECKING:
    from maa.controller import Win32Controller
    from maa.tasker import Tasker


def is_on_farm(frame) -> bool:
    return (
        match(frame, "phone_icon.png", (610, 1210, 105, 140)) >= 0.68
        or match_nav(frame, "backpack.png", (600, 1150, 120, 120)) >= 0.70
    )


def confirm_sleep_or_close_shop(tasker: "Tasker", controller: "Win32Controller") -> bool:
    """Confirm the sleep / close-shop prompt if it is showing."""
    frame = screenshot(controller)
    if not restaurant_closed_prompt(tasker, frame):
        return False
    return click_template_node(controller, 'ClickSleepPromptConfirm', frame)


class RestaurantClosedError(RuntimeError):
    pass


def _prompt_text(tasker, frame, roi):
    try:
        from .ensure_ranch_feed import recognized_text
    except ImportError:
        from ensure_ranch_feed import recognized_text
    return ''.join(recognized_text(tasker, frame, roi, expected=(r'.+',)).split())


def restaurant_closed_prompt(tasker, frame):
    # The 提示 title is shared with the missing-chef dialog. Never use it alone.
    if template_hit(frame, 'ConfirmSleepPromptStart') is None:
        return False
    text = _prompt_text(tasker, frame, (65, 470, 600, 115))
    return '餐厅打烊' in text and '等天亮' in text


def missing_staff_prompt(tasker, frame):
    text = _prompt_text(tasker, frame, (65, 470, 600, 115))
    if '还未拥有' not in text or '双倍奖' not in text or '员工获得方式' not in text:
        return False
    button = _prompt_text(tasker, frame, (65, 610, 270, 125))
    return '继续订单' in ''.join(button.split())


def order_record_reward_prompt(tasker, frame):
    text = _prompt_text(tasker, frame, (150, 440, 440, 170))
    return ('订单解锁历史新高' in text
            and _prompt_text(tasker, frame, (230, 810, 260, 125)) == '领取')


def finish_order_acceptance(tasker, controller, items, timeout=12):
    """Handle the known staff prompt once, then require decoded status + debit."""
    try:
        from . import decoded_runtime as decoded
    except ImportError:
        import decoded_runtime as decoded
    started = time.monotonic()
    staff_confirmed = False
    while True:
        data = decoded.snapshot()
        if decoded.submission_confirmed(items, data):
            return dict(data_source='decoded', storage_sequence=data['storageSequence'],
                        elapsed_seconds=round(time.monotonic() - started, 3),
                        missing_staff_confirmed=staff_confirmed)
        frame = screenshot(controller)
        if restaurant_closed_prompt(tasker, frame):
            confirm_sleep_or_close_shop(tasker, controller)
            raise RestaurantClosedError('Restaurant closed: order not submitted; no debit confirmed and no automatic reaccept')
        if not staff_confirmed and missing_staff_prompt(tasker, frame):
            # Existing foreground-safe click method; never choose the acquire-staff button.
            if not post_click_normalized(controller, 200, 675).wait().succeeded:
                raise decoded.DecodeError('Continue-order click failed; no automatic retry')
            staff_confirmed = True
            time.sleep(.5)
        if time.monotonic() - started >= timeout:
            raise decoded.DecodeError('Order submission not confirmed by status and exact inventory debit; no automatic retry')
        time.sleep(.25)


def close_order_board(controller: "Win32Controller") -> bool:
    """Tap popup close on the notice board; return True if a close control was hit."""
    frame = screenshot(controller)
    close_score, close_xy = locate_nav(frame, "popup_close.png", (0, 1190, 130, 150))
    if close_score < 0.75:
        return False
    if not post_click_normalized(controller, close_xy[0] + 32, close_xy[1] + 38).wait().succeeded:
        raise RuntimeError("order-board close click failed")
    time.sleep(0.8)
    return True


def ensure_returned_to_farm(tasker: "Tasker", controller: "Win32Controller") -> bool:
    """Leave the order UI and verify the farm map chrome is visible."""
    frame = screenshot(controller)
    if is_on_farm(frame):
        return True
    if close_order_board(controller):
        time.sleep(0.5)
        if is_on_farm(screenshot(controller)):
            return True
    frame = screenshot(controller)
    if match_nav(frame, "popup_close.png", (0, 1190, 130, 150)) >= 0.78:
        if not post_click_normalized(controller, 57, 1280).wait().succeeded:
            raise RuntimeError("order-page back click failed")
        time.sleep(1.0)
        frame = screenshot(controller)
    if not is_on_farm(frame):
        raise RuntimeError("return-to-farm verification failed")
    return True


STARTUP_CHECKLIST = """[short-cycle startup checklist]
1. Game open and NOT minimized/shrunk (shrinking drops resolution)
2. Prefer farm map chrome visible (phone / backpack), not restaurant detail
3. Once per run: manually clear leftover crops on the land, old plans and employee supplies; confirm the startup dialog
4. Clicks use post_click_normalized only (FG-safe); never native Maa Click/post_click
5. Evidence under E:\Maa-CarrotFarm2\.tmp\track_b\ (dry_run_plan.json / latest_summary.json / round_XX)
"""


def print_startup_checklist() -> None:
    print(STARTUP_CHECKLIST, flush=True)

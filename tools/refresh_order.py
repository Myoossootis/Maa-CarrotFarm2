"""Replace exactly one selected order through the foreground-safe Maa worker."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time
import traceback

import cv2

from atom_cli_common import connect_controller, release_controller
from maa_frame_normalize import post_click_normalized
from order_template_click import click_template_node, open_orders, template_hit
from scan_normal_orders import screenshot, resolve_rare_abandon_dialog
from task_control import TaskCancelled, check_cancelled


ROOT = Path(__file__).resolve().parents[1]
LABELS = {'party': '聚会', 'takeout': '外卖', 'normal': '普通'}
READY = {'party': 'VerifyPartyOrdersReady', 'takeout': 'VerifyTakeoutOrdersReady',
         'normal': 'VerifyNormalOrdersReady'}


def card_center(number):
    return 155 + 210 * ((number - 1) % 3), 470 + 156 * ((number - 1) // 3)


def card_image(frame, number):
    x, y = card_center(number)
    return frame[y - 50:y + 50, x - 70:x + 70]


def card_selected(frame, number):
    # All three tabs mark the selected card cyan. Empty/locked slots must not
    # leave an old card selected and accidentally replace that other order.
    hsv = cv2.cvtColor(card_image(frame, number), cv2.COLOR_BGR2HSV)
    cyan = cv2.inRange(hsv, (85, 50, 160), (110, 200, 255))
    return cv2.countNonZero(cyan) / cyan.size > .25


def card_pending(frame, number):
    clock = cv2.imread(str(ROOT / 'assets/resource/image/navigation/order_refresh_clock.png'), cv2.IMREAD_GRAYSCALE)
    if clock is None:
        raise RuntimeError('missing_refresh_clock_template')
    card = cv2.cvtColor(card_image(frame, number), cv2.COLOR_BGR2GRAY)
    return cv2.minMaxLoc(cv2.matchTemplate(card, clock, cv2.TM_CCOEFF_NORMED))[1] >= .85


def refresh_order(controller, order_type, number, evidence, progress):
    progress['stage'] = 'open_board'
    if not open_orders(controller, order_type):
        raise RuntimeError('order_board_unavailable: 无法打开指定订单页签')
    progress['stage'] = 'select_card'
    if not post_click_normalized(controller, *card_center(number)).wait().succeeded:
        raise RuntimeError('order_select_failed: 订单选卡失败')
    time.sleep(.4)
    before = screenshot(controller)
    cv2.imwrite(str(evidence / 'before.png'), before)
    if card_pending(before, number):
        progress['resumed_pending'] = True
    else:
        if not card_selected(before, number):
            raise RuntimeError('order_not_selected: 指定位置不存在可选订单，未更换其他订单')
        progress['stage'] = 'replace'
        if not click_template_node(controller, 'ClickNormalOrderReplace', before):
            raise RuntimeError('replace_unavailable: 当前订单无更换按钮，可能进行中或待领奖')
        progress['replace_clicked'] = True
        progress['stage'] = 'confirm_replace'
        progress['rare_confirmation'] = resolve_rare_abandon_dialog(controller, confirm=True)
    progress['stage'] = 'free_accelerate'
    for _ in range(4):
        pending = screenshot(controller)
        if click_template_node(controller, 'ClickNormalOrderFreeAccelerate', pending):
            progress['free_accelerate_clicked'] = True
            cv2.imwrite(str(evidence / 'pending.png'), pending)
            break
        time.sleep(.2)
    else:
        raise RuntimeError('free_accelerate_unavailable: 未找到免费加速按钮，请检查目标订单状态')
    progress['stage'] = 'verify_refresh'
    # The new recipe may not stay selected after acceleration. Select this same
    # position once, then verify it became a real card rather than an hourglass.
    if not post_click_normalized(controller, *card_center(number)).wait().succeeded:
        raise RuntimeError('refreshed_card_select_failed')
    for _ in range(5):
        check_cancelled()
        time.sleep(.3)
        after = screenshot(controller)
        changed = float(cv2.absdiff(card_image(before, number), card_image(after, number)).mean())
        if (changed >= 5 and card_selected(after, number) and not card_pending(after, number)
                and template_hit(after, READY[order_type]) is not None):
            cv2.imwrite(str(evidence / 'after.png'), after)
            return {'status': 'refreshed', 'card_changed': True,
                    'message': '已免费加速，指定位置的新订单已出现'}
    raise RuntimeError('refresh_unconfirmed: 已点击免费加速，但未确认新订单出现；请检查截图后再运行')


def main():
    parser = argparse.ArgumentParser(description='更换指定的一张订单（从左到右、从上到下编号）')
    parser.add_argument('--order-type', choices=tuple(LABELS), default='party')
    parser.add_argument('--order-number', type=int, choices=range(1, 10), default=1)
    args = parser.parse_args()
    evidence = ROOT / '.tmp/order_refresh' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    evidence.mkdir(parents=True, exist_ok=True)
    progress = {'task': 'refresh_order', 'order_type': args.order_type, 'order_number': args.order_number,
                'stage': 'connect', 'replace_clicked': False, 'free_accelerate_clicked': False,
                'evidence_dir': str(evidence)}
    controller = None
    try:
        print(f'[刷新] {LABELS[args.order_type]}订单 第{args.order_number}张；证据={evidence}', flush=True)
        controller = connect_controller()
        progress.update(refresh_order(controller, args.order_type, args.order_number, evidence, progress))
        progress['stage'] = 'done'
        return 0
    except TaskCancelled:
        progress.update(status='cancelled', message='任务已停止；若已点击更换，请检查目标卡片当前状态')
        raise
    except Exception as error:
        trace = traceback.format_exc()
        (evidence / 'error.log').write_text(trace, encoding='utf-8')
        progress.update(status='failed', error=str(error))
        if controller is not None:
            try:
                cv2.imwrite(str(evidence / 'failure.png'), screenshot(controller))
            except Exception as capture_error:
                progress['screenshot_error'] = str(capture_error)
        print(f'[错误] 刷新订单 阶段={progress["stage"]}：{error}\n{trace}', flush=True)
        return 1
    finally:
        (evidence / 'result.json').write_text(json.dumps(progress, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(progress, ensure_ascii=False), flush=True)
        if controller is not None:
            release_controller(controller)


if __name__ == '__main__':
    raise SystemExit(main())

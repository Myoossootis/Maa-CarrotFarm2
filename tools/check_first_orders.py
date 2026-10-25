"""Apply the user's product whitelist to slot 1 before daily order tasks."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import time
import traceback

if __package__ in {None, ''}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.order_preferences import default_allowed_products, validate_allowed_products
import cv2
from maa.resource import Resource
from maa.tasker import Tasker
from maa.pipeline import JOCR, JRecognitionType

from atom_cli_common import connect_controller, release_controller
from dismiss_offline_income import dismiss_offline_income
from ensure_ranch_feed import recognized_text
from maa_frame_normalize import post_click_normalized
from order_template_click import open_orders, template_hit
from refresh_order import card_center, card_pending, card_selected, refresh_order, LABELS
from scan_normal_orders import load_products, match_products, screenshot
from task_control import TaskCancelled, check_cancelled
import decoded_runtime as decoded


ROOT = Path(__file__).resolve().parents[1]
MAX_REFRESHES = 50


class RecipeNotFullyVisible(RuntimeError):
    pass


def read_names(tasker, frame, products, *, scale=2, allowed_products=None):
    if decoded.enabled():
        return [item['name'] for item in decoded.read_selected()]
    allowed_products = default_allowed_products() if allowed_products is None else allowed_products
    matches = match_products(frame, products)
    names = [item['name'] for item in matches]
    # One known forbidden ingredient is already enough to reject the recipe.
    if set(names) - allowed_products:
        return names
    if len(matches) > 4:
        # Existing order execution cannot handle a recipe extending past the
        # panel. Refresh rather than approving an unseen final ingredient.
        raise RecipeNotFullyVisible('配方超出可见区域，无法核对完整白名单')
    # Count every displayed requirement, including an unknown product icon.
    # Never approve a partial recipe merely because its recognized part is safe.
    patch = cv2.resize(frame[1010:1080, 50:670], None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    detail = tasker.post_recognition(JRecognitionType.OCR,
        JOCR(roi=(0, 0, patch.shape[1], patch.shape[0]), expected=[r'.+'], threshold=.02), patch).wait().get()
    recognition = detail.nodes[0].recognition if detail and detail.nodes else None
    results = list(recognition.filtered_results) if recognition else []
    # Maa can merge all counters into one text box, even after upscaling.
    # Count actual/required pairs rather than treating boxes as ingredients.
    count = sum(len(re.findall(r'\d+\s*/\s*\d+', item.text)) for item in results)
    if not 2 <= len(matches) <= 4 or count != len(matches):
        raise RuntimeError(f'first_recipe_incomplete: 图标{len(matches)}种，数量栏{count}项')
    return names


def enforce_first_order(controller, tasker, products, kind, evidence, progress, *, allowed_products=None):
    allowed_products = default_allowed_products() if allowed_products is None else validate_allowed_products(allowed_products)
    progress.update(stage='open_board', refresh_count=0, history=[])
    if not open_orders(controller, kind):
        raise RuntimeError('first_order_board_unavailable: 无法打开订单页签')
    if not post_click_normalized(controller, *card_center(1)).wait().succeeded:
        raise RuntimeError('first_order_select_failed')
    time.sleep(.4)
    for attempt in range(MAX_REFRESHES + 1):
        check_cancelled()
        progress['stage'] = 'read_first_order'
        if decoded.enabled():
            decoded.select_order(kind, 1, 1)
        frame = screenshot(controller)
        cv2.imwrite(str(evidence / 'current.png'), frame)
        pending = card_pending(frame, 1)
        if not pending:
            if not card_selected(frame, 1):
                raise RuntimeError('first_order_not_selected: 一号位未选中，未操作其他位置')
            if template_hit(frame, 'ClickNormalOrderReplace') is None:
                state = recognized_text(tasker, frame, (80, 750, 590, 460),
                                        expected=(r'进行中|领取奖励|已完成',))
                if state:
                    print(f'[一号位] {LABELS[kind]}订单已有配送/奖励：{state}，交回原领奖流程', flush=True)
                    return dict(status='existing_delivery', state=state)
                raise RuntimeError('first_order_not_replaceable: 一号位状态不明或无法更换')
            names = []
            reason = None
            for read_attempt in range(3):
                try:
                    names = read_names(tasker, frame, products, scale=2 if read_attempt == 0 else 4,
                                       allowed_products=allowed_products)
                    break
                except RecipeNotFullyVisible as error:
                    names, reason, message = None, 'recipe_not_fully_visible', str(error)
                    break
                except decoded.OrderUnavailable as error:
                    names, reason, message = None, 'dish_level_locked', str(error)
                    break
                except decoded.DecodeError:
                    raise
                except RuntimeError:
                    # A locked dish shows dishes/upgrade icons instead of raw
                    # ingredients. It must be refreshed, never upgraded here.
                    message = recognized_text(tasker, frame, (70, 875, 590, 190), expected=(r'.+',))
                    if '等级太低' in message or '无法接受' in message:
                        names, reason = None, 'dish_level_locked'
                        break
                    if read_attempt == 2:
                        raise
                    time.sleep(.25)
                    frame = screenshot(controller)
            if names is None:
                progress['history'].append({'reason': reason, 'message': message})
                print(f'[一号位] {LABELS[kind]}订单 {message}，免费刷新', flush=True)
            else:
                forbidden = sorted(set(names) - allowed_products)
                progress['history'].append({'products': names, 'forbidden': forbidden})
                print(f'[一号位] {LABELS[kind]}订单 需求={"、".join(names)} '
                      f'白名单外={"、".join(forbidden) or "无"}', flush=True)
                if not forbidden:
                    cv2.imwrite(str(evidence / 'allowed.png'), frame)
                    return dict(status='allowed', products=names)
        if attempt == MAX_REFRESHES:
            raise RuntimeError(f'first_order_refresh_limit: 连续刷新{MAX_REFRESHES}次仍未满足白名单')
        progress['stage'] = 'refresh_first_order'
        folder = evidence / f'refresh_{attempt + 1:02d}'
        folder.mkdir()
        step = {}
        try:
            refresh_order(controller, kind, 1, folder, step)
        finally:
            progress['last_refresh'] = step
        progress['refresh_count'] += 1
        print(f'[一号位] {LABELS[kind]}订单 已免费刷新{progress["refresh_count"]}次，重读需求', flush=True)


def main():
    parser = argparse.ArgumentParser(description='日常订单启动前检查一号位白名单并免费刷新')
    parser.add_argument('--order-type', choices=tuple(LABELS), required=True)
    parser.add_argument('--allowed-products', nargs='*', default=None, help='本次允许品类，默认读取 Maa 勾选预设')
    args = parser.parse_args()
    try:
        allowed_products = default_allowed_products() if args.allowed_products is None else validate_allowed_products(args.allowed_products)
    except ValueError as error:
        parser.error(str(error))
    evidence = ROOT / '.tmp/first_order_policy' / (args.order_type + '_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    evidence.mkdir(parents=True, exist_ok=True)
    progress = dict(order_type=args.order_type, order_number=1, stage='connect',
                    allowed_products=sorted(allowed_products), evidence_dir=str(evidence))
    controller = resource = tasker = None
    try:
        print(f'[一号位] 启动白名单检查：{LABELS[args.order_type]}订单；证据={evidence}', flush=True)
        controller = connect_controller()
        resource, tasker = Resource(), Tasker()
        tasker.set_save_draw(False)
        tasker.set_save_on_error(False)
        tasker.set_reco_image_cache_limit(4)
        if not resource.post_bundle(ROOT / 'assets/resource').wait().succeeded or not tasker.bind(resource, controller):
            raise RuntimeError('first_order_resource_binding_failed')
        dismiss_offline_income(controller, tasker)
        progress.update(enforce_first_order(controller, tasker, load_products(), args.order_type, evidence, progress,
                                           allowed_products=allowed_products))
        progress['stage'] = 'done'
        return 0
    except TaskCancelled:
        progress['status'] = 'cancelled'
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
        print(f'[错误] 一号位白名单 阶段={progress["stage"]}：{error}\n{trace}', flush=True)
        return 2
    finally:
        (evidence / 'result.json').write_text(json.dumps(progress, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(progress, ensure_ascii=False), flush=True)
        if tasker is not None:
            tasker.clear_cache()
        for value in (tasker, resource):
            if value is not None:
                value.__del__()
                value._handle = None
        if controller is not None:
            release_controller(controller)


if __name__ == '__main__':
    raise SystemExit(main())

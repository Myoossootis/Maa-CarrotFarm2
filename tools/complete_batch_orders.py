"""Scan/accept party & takeout orders on the shared notice board.

Same board as normal orders; switch top tab (聚会/外卖). Guest-grid UI shows one
current recipe; Accept/claim share the normal-order controls.
Decoded mode confirms actual status and material debit after handling prompts.
"""

from __future__ import annotations

import argparse
import json
import re
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import cv2
from maa.controller import Win32Controller
from maa.define import MaaWin32InputMethodEnum, MaaWin32ScreencapMethodEnum
from maa.resource import Resource
from maa.tasker import Tasker

try:
    from . import decoded_runtime as decoded
except ImportError:
    import decoded_runtime as decoded

from configure_field_employee import leave_employee_ui_if_visible, locate_nav
from dismiss_offline_income import dismiss_offline_income
from ensure_ranch_feed import recognized_text
from allocate_employee_work import allocate
from employee_capabilities import load_capabilities
from runtime_state import write_json
from order_board_helpers import ensure_returned_to_farm, confirm_sleep_or_close_shop, finish_order_acceptance
from complete_normal_orders import claim_completed_reward, reward_overlay_visible, dismiss_reward_overlay
from order_template_click import click_template_node, normalize_order_type, open_orders
from production_targets import (
    build_targets,
    order_contains_unsupported_product,
    order_signature,
)
from scan_normal_orders import (
    ROOT,
    completion_checked,
    completion_control_visible,
    require_decoded_card_selection,
    ensure_triple_checked,
    find_window,
    load_products,
    read_order,
    screenshot,
)

try:
    from .maa_frame_normalize import (
        build_runtime_resource,
        install_foreground_safe_inputs,
        measure_controller_offset,
        post_click_normalized,
    )
except ImportError:
    from maa_frame_normalize import (
        build_runtime_resource,
        install_foreground_safe_inputs,
        measure_controller_offset,
        post_click_normalized,
    )

RESULT = ROOT / "assets/catalog/normal_orders/latest_completion.json"
SETTLE = 0.35
TYPE_CN = {"party": "聚会订单", "takeout": "外卖订单"}


class UnavailableOrderError(RuntimeError):
    pass


def _log(msg: str) -> None:
    print(msg, flush=True)


def _write(payload: dict) -> None:
    payload['state_reader'] = 'decoded' if decoded.enabled() else 'ocr'
    RESULT.parent.mkdir(parents=True, exist_ok=True)
    RESULT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def _ensure_batch_checked(tasker: Tasker, controller: Win32Controller) -> bool:
    """Reuse normal checkbox templates (same control; label shows 完成6次)."""
    frame = screenshot(controller)
    if completion_checked(frame)[0]:
        return False
    ensure_triple_checked(tasker, controller)
    return True


def _click_accept(tasker: Tasker, controller: Win32Controller) -> None:
    frame = screenshot(controller)
    # Prefer bright OCR 接受, else template.
    try:
        from maa.pipeline import JOCR, JRecognitionType
    except ImportError:
        JOCR = None  # type: ignore
    if JOCR is not None:
        job = tasker.post_recognition(
            JRecognitionType.OCR,
            JOCR(roi=(300, 980, 400, 280), expected=[r"接受"], threshold=0.55),
            frame,
        ).wait()
        detail = job.get()
        recognition = detail.nodes[0].recognition if detail and detail.nodes else None
        best = None
        if recognition is not None:
            for result in list(getattr(recognition, "filtered_results", []) or []):
                box = list(getattr(result, "box", []) or [])
                if len(box) < 4:
                    continue
                x, y, w, h = box[:4]
                crop = frame[y : y + h, x : x + w]
                if crop.size == 0:
                    continue
                value = float(cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)[:, :, 2].mean())
                score = float(getattr(result, "score", 0))
                if value < 150:
                    continue
                if best is None or score > best[0]:
                    best = (score, x, y, w, h)
        if best is not None:
            _, x, y, w, h = best
            if not post_click_normalized(controller, x + w // 2, y + h // 2).wait().succeeded:
                raise RuntimeError("batch accept click failed")
            time.sleep(0.6)
            return
    if not click_template_node(controller, "AcceptNormalOrderStart", frame):
        raise RuntimeError("batch accept button not recognized")
    time.sleep(0.6)



def _completion_count(tasker, frame):
    text = recognized_text(tasker, frame, (475, 1175, 205, 75), expected=(r'.+',))
    match = re.search(r'完成\s*([1-6])', text)
    if match is None:
        raise RuntimeError(f'batch_count_unreadable: 无法读取实际完成次数 {text!r}')
    return int(match.group(1))


def _read_current(tasker: Tasker, controller: Win32Controller, products) -> list[dict]:
    if decoded.enabled():
        require_decoded_card_selection(screenshot(controller))
        try:
            return decoded.read_selected()
        except decoded.OrderUnavailable as error:
            raise UnavailableOrderError(str(error)) from error
    # Retry the same recipe: a loading/OCR failure must not replace an order.
    for attempt in range(3):
        frame = screenshot(controller)
        items = read_order(tasker, frame, products)
        if len(items) > 4:
            raise UnavailableOrderError('wide_recipe: 配方超过当前可见区域，跳过以免漏读材料')
        if 2 <= len(items) <= 4:
            return items
        if not items:
            message = recognized_text(tasker, frame, (50, 870, 620, 180), expected=(r'.+',))
            if '无法接受' in message or '去升级' in message:
                raise UnavailableOrderError(f'dish_level_locked: {message}')
        _log(f"[识别] 订单需求不完整：识别{len(items)}种，重读{attempt + 1}/3")
        time.sleep(SETTLE)
    raise RuntimeError("order_recipe_unreadable: 连续3次无法完整读取订单需求")


def _enrich(items: list[dict], order: int = 1, pin=None, batch_count=6) -> dict:
    order_signature(items)
    normalized = [{key: it[key] for key in ('name', 'actual', 'required', 'missing')} for it in items]
    missing = [it for it in normalized if it['missing'] > 0]
    return dict(order=order, items=normalized, missing_items=missing,
                total_missing=sum(it['missing'] for it in missing),
                selection='batch_card', board_pin=pin, batch_count=batch_count, production_targets=build_targets(missing))


def _select_recipe(tasker, controller, products, requested=None, order_type='party'):
    """Use six completions by default; otherwise choose the smallest shortfall."""
    if requested is not None:
        pin = requested.get('board_pin')
        if pin and not post_click_normalized(controller, *pin).wait().succeeded:
            raise RuntimeError('order card click failed')
        time.sleep(SETTLE)
        batch_count = requested.get('batch_count', 6)
        if batch_count == 1:
            if completion_checked(screenshot(controller))[0]:
                if not post_click_normalized(controller, 520, 1205).wait().succeeded:
                    raise RuntimeError('single-order toggle failed')
        else:
            _ensure_batch_checked(tasker, controller)
        if decoded.enabled():
            if batch_count == 1 and (not completion_control_visible(screenshot(controller)) or completion_checked(screenshot(controller))[0]):
                raise decoded.DecodeError('Single completion control is not confirmed unchecked')
            actual_count = 1 if batch_count == 1 else _completion_count(tasker, screenshot(controller))
            if actual_count != batch_count:
                raise decoded.DecodeError('Requested batch count differs from the selected UI count')
            decoded.select_order(order_type, int(requested['order']), batch_count)
            try:
                require_decoded_card_selection(screenshot(controller))
            except decoded.DecodeError:
                # Board-entry animation can swallow the first selection click.
                # Retry only the same card, before reading or accepting anything.
                time.sleep(1.5)
                if not pin or not post_click_normalized(controller, *pin).wait().succeeded:
                    raise
                time.sleep(.5)
                require_decoded_card_selection(screenshot(controller))
        items = _read_current(tasker, controller, products)
        if order_signature(items) != order_signature(requested['items']):
            _log(f"[订单] 选卡可能被加载动画吞掉，重选一次；实际={order_signature(items)}")
            if pin and post_click_normalized(controller, *pin).wait().succeeded:
                time.sleep(0.5)
                items = _read_current(tasker, controller, products)
            if order_signature(items) != order_signature(requested['items']):
                raise RuntimeError('order_rematch_failed: 原订单已变化，未交单')
        return items, requested['order'], pin, batch_count
    # Shared 720x1358 board: card centers, after the active tab was recognized.
    top = ((155, 470), (365, 470), (575, 470))
    capabilities = load_capabilities()
    best = None
    batch_verified = False
    batch_size = 6
    for row in range(3):
        for col, (x, y) in enumerate(top):
            number = row * 3 + col + 1
            pin = (x, y + 156 * row)
            if not post_click_normalized(controller, *pin).wait().succeeded:
                raise RuntimeError(f'order card {number} click failed')
            time.sleep(SETTLE)
            try:
                if decoded.enabled():
                    decoded.select_order(order_type, number, 1)
                    if not batch_verified:
                        _read_current(tasker, controller, products)
                        _ensure_batch_checked(tasker, controller)
                        batch_size = _completion_count(tasker, screenshot(controller))
                        if batch_size != 6:
                            raise decoded.DecodeError('Default party/takeout flow requires exactly six completions')
                        batch_verified = True
                    decoded.set_batch_count(batch_size)
                items = _read_current(tasker, controller, products)
            except UnavailableOrderError as error:
                _log(f'[订单] 卡片{number} 跳过：{error}')
                continue
            if not batch_verified:
                if _ensure_batch_checked(tasker, controller):
                    items = _read_current(tasker, controller, products)
                batch_size = _completion_count(tasker, screenshot(controller))
                if batch_size != 6:
                    raise RuntimeError('Default party/takeout flow requires exactly six completions')
                batch_verified = True
            if order_contains_unsupported_product(items):
                _log(f'[订单] 卡片{number} 含鱼肉，跳过')
                continue
            shortage = sum(it['missing'] for it in items)
            _log(f"[订单] 卡片{number} 总缺口={shortage} " + '、'.join(f"{it['name']}缺{it['missing']}" for it in items))
            if shortage == 0:
                return items, number, pin, batch_size
            target = _enrich(items, number, pin)
            allocation = allocate(target['production_targets'], capabilities['slots'], capabilities.get('product_aliases'))
            if not allocation['ready']:
                _log(f"[订单] 卡片{number} 本轮无法排产，跳过：{allocation['unmet']}")
                continue
            if best is None or shortage < best[0]:
                best = (shortage, items, number, pin)
    if best is None:
        raise RuntimeError('no_feasible_orders: 未找到库存足够或本轮可排产的订单，未更换或交单')
    _, items, number, pin = best
    if decoded.enabled():
        decoded.select_order(order_type, number, batch_size, items[0]['game_order_uid'])
    if not post_click_normalized(controller, *pin).wait().succeeded:
        raise RuntimeError(f'order card {number} click failed')
    return items, number, pin, batch_size


def _wait_and_claim_reward(tasker, controller, order_type, timeout_seconds=300, order_number=None):
    """Wait only for delivery/reward UI; never accept another order here."""
    evidence = ROOT / '.tmp' / 'batch_rewards' / (order_type + '_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    evidence.mkdir(parents=True, exist_ok=True)
    # Accept closes this board automatically; rewards live on the reopened tab.
    time.sleep(0.8)
    if not open_orders(controller, order_type):
        raise RuntimeError('reward_board_unavailable: 已提交订单，无法重新打开领奖页')
    if order_number:
        index = order_number - 1
        if not post_click_normalized(controller, 155 + 210 * (index % 3), 470 + 156 * (index // 3)).wait().succeeded:
            raise RuntimeError('reward card selection failed')
        time.sleep(0.4)
    deadline = time.monotonic() + timeout_seconds
    next_log = 0.0
    while time.monotonic() < deadline:
        frame = screenshot(controller)
        if reward_overlay_visible(frame):
            cv2.imwrite(str(evidence / 'reward.png'), frame)
            dismiss_reward_overlay(controller)
            cv2.imwrite(str(evidence / 'after_reward.png'), screenshot(controller))
            _log(f'[领奖] 类型={TYPE_CN[order_type]} 奖励到账弹层已关闭；证据={evidence}')
            return str(evidence)
        # The shared board has the same claim button as the normal-order page.
        if claim_completed_reward(controller, tasker):
            cv2.imwrite(str(evidence / 'before_claim.png'), frame)
            cv2.imwrite(str(evidence / 'after_reward.png'), screenshot(controller))
            _log(f'[领奖] 类型={TYPE_CN[order_type]} 已领取奖励；证据={evidence}')
            return str(evidence)
        confirm_sleep_or_close_shop(tasker, controller)
        now = time.monotonic()
        if now >= next_log:
            _log(f'[等待] 类型={TYPE_CN[order_type]} 等待配送/领奖，剩余约{int(deadline-now)}秒')
            next_log = now + 15
        time.sleep(10)
    cv2.imwrite(str(evidence / 'timeout.png'), screenshot(controller))
    raise RuntimeError(f'reward_timeout: 订单已提交，尚未看到可领取奖励；禁止重复接受；证据={evidence}')


def recover_accept_intent(delivery, path):
    """Reconcile an interrupted accept from its debit; never send it again."""
    if delivery.get('status') != 'accept_intent':
        return delivery
    if not decoded.enabled() or not delivery.get('submitted') or len(delivery['submitted']) != 1:
        raise decoded.DecodeError('Unresolved order acceptance; decoded receipt required, no retry')
    entry = delivery['submitted'][0]
    if not decoded.submission_confirmed(entry['items'], decoded.snapshot()):
        raise decoded.DecodeError('Unresolved order acceptance; preserve intent and do not reaccept')
    entry.update(acceptance_confirmed=True, reason='accepted', recovered_by_readback=True)
    delivery.update(status='accepted', submitted_count=1, target_order=None, reward_claimed=False)
    write_json(path, delivery)
    return delivery


def run_batch(
    *,
    order_type: str,
    prepare_only: bool,
    requested: dict | None,
) -> int:
    order_type = normalize_order_type(order_type)
    if order_type not in {"party", "takeout"}:
        raise ValueError("complete_batch_orders only handles party/takeout")
    label = TYPE_CN[order_type]
    batch_count = 6
    started = time.perf_counter()
    _write({"generated_at_utc": datetime.now(timezone.utc).isoformat(), "status": "running", "order_type": order_type})

    delivery_path = RESULT.with_name(f'latest_{order_type}_delivery.json')
    try:
        delivery = json.loads(delivery_path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        delivery = {}
    controller = resource = tasker = None
    stage = "connect"
    submitted: list[dict] = []
    skipped: list[dict] = []
    try:
        delivery = recover_accept_intent(delivery, delivery_path)
        products = load_products()
        controller = Win32Controller(
            hWnd=find_window(),
            screencap_method=MaaWin32ScreencapMethodEnum.FramePool,
            mouse_method=MaaWin32InputMethodEnum.PostMessage,
            keyboard_method=MaaWin32InputMethodEnum.PostMessage,
        )
        resource = Resource()
        tasker = Tasker()
        tasker.set_save_draw(False)
        tasker.set_save_on_error(False)
        tasker.set_reco_image_cache_limit(16)
        tasker.set_stdout_level(2)
        controller.set_screenshot_target_short_side(720)
        install_foreground_safe_inputs()
        if not controller.post_connection().wait().succeeded:
            raise RuntimeError("Maa Win32 connection failed")
        offset = measure_controller_offset(controller)
        runtime_resource = build_runtime_resource(offset)
        _log(f"[日常] {label} 打开订单板（共享告示牌+页签）offset={offset}")
        if not resource.post_bundle(runtime_resource).wait().succeeded:
            raise RuntimeError("Maa resource loading failed")
        if not tasker.bind(resource, controller):
            raise RuntimeError("Maa tasker binding failed")

        stage = "open_board"
        dismiss_offline_income(controller, tasker)
        leave_employee_ui_if_visible(controller)
        if not open_orders(controller, order_type):
            leave_employee_ui_if_visible(controller)
            if not open_orders(controller, order_type):
                raise RuntimeError(f"failed to open {order_type} order tab")
        _log(f"[日常] {label} 页签已就绪")

        if delivery.get('status') == 'accepted':
            stage = 'resume_reward'
            submitted = delivery['submitted']
            reward_evidence = _wait_and_claim_reward(tasker, controller, order_type, order_number=submitted[0]['order'])
            delivery.update(status='completed', reward_claimed=True, reward_evidence=reward_evidence)
            write_json(delivery_path, delivery)
            _write(delivery)
            return 0
        stage = "read_recipe"
        if decoded.enabled():
            items, order_number, pin, batch_count = _select_recipe(tasker, controller, products, requested, order_type=order_type)
        else:
            items, order_number, pin, batch_count = _select_recipe(tasker, controller, products, requested)
        target = _enrich(items, order_number, pin, batch_count)
        item_txt = "、".join(
            f"{it.get('name')} 需{it.get('required')}/有{it.get('actual')}/缺{it.get('missing')}"
            for it in items
        )
        _log(f"[订单] 类型={label} 订单号={order_number} 需求：{item_txt}")

        if prepare_only:
            skipped.append({
                "order": order_number,
                "reason": "ready" if all(it["satisfied"] for it in items) else "insufficient",
                "items": items,
            })
            stage = "return_to_farm"
            ensure_returned_to_farm(tasker, controller)
            has_gap = bool(target.get("missing_items") or target.get("production_targets"))
            _write({
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                "status": "prepared",
                "order_type": order_type,
                "submitted_count": 0,
                "submitted": [],
                "skipped": skipped,
                "no_acceptable_orders": False,
                "returned_to_farm": True,
                "next_stage": "planting_and_breeding" if has_gap else None,
                "target_order": target,
                "batch_count": batch_count,
            })
            return 0

        if not all(it["satisfied"] for it in items):
            skipped.append({"order": order_number, "reason": "insufficient", "items": items})
            stage = "return_to_farm"
            ensure_returned_to_farm(tasker, controller)
            _write({
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                "status": "completed",
                "order_type": order_type,
                "submitted_count": 0,
                "submitted": [],
                "skipped": skipped,
                "no_acceptable_orders": True,
                "returned_to_farm": True,
                "target_order": target,
                "batch_count": batch_count,
            })
            return 0

        _log(f"[交单] 类型={label} 点击接受（完成{batch_count}次）")
        stage = "accept"
        if decoded.enabled():
            accept_frame = screenshot(controller)
            require_decoded_card_selection(accept_frame)
            if (not completion_control_visible(accept_frame)
                    or completion_checked(accept_frame)[0] != (batch_count > 1)
                    or batch_count > 1 and _completion_count(tasker, accept_frame) != batch_count):
                raise decoded.DecodeError('Completion count changed before accept; no click sent')
            items = decoded.assert_order_unchanged(items)
            delivery = dict(status='accept_intent', order_type=order_type, batch_count=batch_count,
                            created_at_utc=datetime.now(timezone.utc).isoformat(),
                            submitted=[dict(order=order_number, items=items, batch_count=batch_count,
                                            acceptance_confirmed=False)], automatic_retry_allowed=False)
            write_json(delivery_path, delivery)
        _click_accept(tasker, controller)
        confirmation = finish_order_acceptance(tasker, controller, items) if decoded.enabled() else None
        submitted.append({
            "order": order_number,
            "items": items,
            "acceptance_confirmed": True,
            "batch_count": batch_count,
            "reason": "accepted",
            "decode_confirmation": confirmation,
        })
        _log(f"[交单] 类型={label} 结果=已提交（批量={batch_count}），等待领取奖励")
        delivery = dict(status='accepted', order_type=order_type, submitted=submitted,
                        submitted_count=1, batch_count=batch_count, target_order=None, reward_claimed=False)
        write_json(delivery_path, delivery)
        stage = 'claim_reward'
        reward_evidence = _wait_and_claim_reward(tasker, controller, order_type, order_number=order_number)
        delivery.update(status='completed', reward_claimed=True, reward_evidence=reward_evidence)
        write_json(delivery_path, delivery)
        try:
            stage = "return_to_farm_after_accept"
            ensure_returned_to_farm(tasker, controller)
            returned = True
        except RuntimeError as exc:
            _log(f"[日常] 回农场校验跳过：{exc}")
            returned = False

        _write({
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "status": "completed",
            "order_type": order_type,
            "submitted_count": len(submitted),
            "submitted": submitted,
            "reward_claimed": True,
            "reward_evidence": reward_evidence,
            "skipped": skipped,
            "no_acceptable_orders": False,
            "returned_to_farm": returned,
            "target_order": None,
            "batch_count": batch_count,
        })
        return 0
    except Exception as error:
        evidence = ROOT / ".tmp" / "batch_orders" / (order_type + "_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
        evidence.mkdir(parents=True, exist_ok=True)
        trace = traceback.format_exc()
        (evidence / "error.log").write_text(trace, encoding="utf-8")
        try:
            if controller is not None:
                cv2.imwrite(str(evidence / "failure.png"), screenshot(controller))
        except Exception as capture_error:
            _log(f"[错误] 失败截图保存失败：{capture_error}")
        _log(f"[错误] 类型={label} 阶段={stage} {type(error).__name__}: {error}；证据={evidence}")
        _log(trace)
        _write({
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "status": "failed",
            "order_type": order_type,
            "error": str(error),
            "stage": stage,
            "evidence_dir": str(evidence),
            "submitted": submitted,
            "skipped": skipped,
            "returned_to_farm": False,
            "target_order": None,
            "batch_count": batch_count,
        })
        raise
    finally:
        if tasker is not None:
            tasker.clear_cache()
        for value in (tasker, resource, controller):
            destructor = getattr(value, "__del__", None)
            if callable(destructor):
                destructor()
            if hasattr(value, "_handle"):
                value._handle = None


def main() -> int:
    parser = argparse.ArgumentParser(description="聚会/外卖订单扫单与交单")
    parser.add_argument("--order-type", required=True, help="party / takeout")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--target-file", type=Path)
    args = parser.parse_args()
    if args.prepare_only and args.target_file:
        parser.error("--prepare-only cannot be combined with --target-file")
    order_type = normalize_order_type(args.order_type)
    if order_type not in {"party", "takeout"}:
        parser.error("--order-type must be party or takeout")
    requested = None
    if args.target_file:
        payload = json.loads(args.target_file.read_text(encoding="utf-8"))
        requested = payload.get("target", payload)
        if not 2 <= len(requested.get("items") or []) <= 4:
            parser.error("target must contain order products")
        order_signature(requested["items"])
        if order_contains_unsupported_product(requested["items"]):
            parser.error("fish orders cannot be submitted")
    return run_batch(order_type=order_type, prepare_only=args.prepare_only, requested=requested)


if __name__ == "__main__":
    raise SystemExit(main())

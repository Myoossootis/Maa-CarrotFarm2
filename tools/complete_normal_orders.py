"""Submit every fully satisfied normal order through Maa background input."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import time
import re

import cv2
from maa.controller import Win32Controller
from maa.define import MaaWin32InputMethodEnum, MaaWin32ScreencapMethodEnum
from maa.resource import Resource
from maa.tasker import Tasker

try:
    from . import decoded_runtime as decoded
except ImportError:
    import decoded_runtime as decoded

from order_template_click import open_normal_orders, click_template_node, template_hit
from scan_normal_orders import (
    ROOT,
    RESOURCE,
    completion_checked,
    completion_control_visible,
    prepare_completion_control,
    find_window,
    load_products,
    read_order,
    match_products,
    recognize_pins,
    recover_order,
    screenshot,
)
from production_targets import (
    MAX_PREPARED_ORDERS,
    prepare_order_target,
    build_targets,
    choose_target_order,
    order_contains_unsupported_product,
    order_signature,
    order_matches_shortage_focus,
)
from order_board_helpers import (
    close_order_board,
    confirm_sleep_or_close_shop,
    finish_order_acceptance,
    order_record_reward_prompt,
    ensure_returned_to_farm,
)
from allocate_employee_work import CAPABILITIES, allocate
from configure_field_employee import leave_employee_ui_if_visible
from dismiss_offline_income import dismiss_offline_income
from ensure_ranch_feed import recognized_text
from refresh_order import card_selected
try:
    from . import normal_order_delivery as delivery
except ImportError:
    import normal_order_delivery as delivery
try:
    from .maa_frame_normalize import build_runtime_resource, measure_controller_offset, post_click_normalized, install_foreground_safe_inputs
except ImportError:
    from maa_frame_normalize import build_runtime_resource, measure_controller_offset, post_click_normalized, install_foreground_safe_inputs


RESULT = ROOT / "assets/catalog/normal_orders/latest_completion.json"
SETTLE_SECONDS = 0.3
IN_PROGRESS_WAIT_SECONDS = 60


def verify_normal_triple(tasker, frame):
    text = recognized_text(tasker, frame, (475, 1175, 205, 75), expected=(r'.+',))
    counts = re.findall(r'完成\s*([1-9])\s*次', text)
    if not completion_control_visible(frame) or not completion_checked(frame)[0] or counts != ['3']:
        raise decoded.DecodeError(f'Normal triple count not confirmed; no accept: {text!r}')


def finish_return(tasker, controller, receipt):
    if not ensure_returned_to_farm(tasker, controller):
        raise decoded.DecodeError('Normal completion return not confirmed')
    return delivery.confirm_return(receipt)


def finish_normal_delivery(tasker, controller, receipt, timeout=300):
    """Resume only this UID; claim once and require fresh post-reward state."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = decoded.snapshot()
        receipt = delivery.recover(receipt, data)
        if receipt['status'] == 'completed':
            dismiss_reward_overlay(controller)
            return finish_return(tasker, controller, receipt)
        order = next((row for row in data['orders'] if row['uid'] == receipt['uid']
                      and row['category'] == 'normal'), None)
        if not order or order['statusCode'] not in (2, 3, 99):
            raise decoded.DecodeError('Normal delivery UID/state changed; no new input')
        if order['statusCode'] == 3:
            # The map's order guest can award directly while opening the board.
            # Reserve before navigation and stop its clicks at the reward layer.
            receipt = delivery.reserve_reward(receipt, data)
            navigation = open_normal_orders(controller, stop_when=reward_overlay_visible)
            if reward_overlay_visible(screenshot(controller)):
                dismiss_reward_overlay(controller)
                for attempt in range(8):
                    try:
                        receipt = delivery.complete(receipt, decoded.snapshot())
                        break
                    except decoded.DecodeError:
                        if attempt == 7:
                            raise
                        time.sleep(.3)
                return finish_return(tasker, controller, receipt)
            if not navigation:
                raise decoded.DecodeError('Normal reward board unavailable')
            number = order['slot']
            pin = (155 + 210 * ((number - 1) % 3), 470 + 156 * ((number - 1) // 3))
            if not post_click_normalized(controller, *pin).wait().succeeded:
                raise decoded.DecodeError('Normal reward card not selected')
            time.sleep(.4)
            reward_frame = screenshot(controller)
            if not card_selected(reward_frame, number) or _claim_reward_button(reward_frame) is None:
                raise decoded.DecodeError('Normal reward button not confirmed; no claim sent')
            current = next((row for row in decoded.snapshot()['orders'] if row['category'] == 'normal'
                            and row['uid'] == receipt['uid'] and row['slot'] == number), None)
            if not current or current['statusCode'] != 3:
                raise decoded.DecodeError('Normal reward source changed after navigation; no claim sent')
            if not claim_completed_reward(controller, tasker):
                raise decoded.DecodeError('Normal reward input/outcome unresolved; no retry')
            # Storage can update after the confirmation animation. Read only.
            for attempt in range(5):
                try:
                    receipt = delivery.complete(receipt, decoded.snapshot())
                    break
                except decoded.DecodeError:
                    if attempt == 4:
                        raise
                    time.sleep(.3)
            return finish_return(tasker, controller, receipt)
        time.sleep(2)
    raise decoded.DecodeError('Normal reward timeout; preserve receipt and do not accept again')


def delivery_output(receipt):
    return dict(status='completed', order_type='normal', batch_count=3,
                operation_id=receipt['operation_id'], submitted_count=1,
                submitted=[dict(order=receipt['order'], items=receipt['items'], acceptance_confirmed=True)],
                target_order=None, reward_claimed=True, returned_to_farm=True,
                generated_at_utc=datetime.now(timezone.utc).isoformat())



def _order_delivery_ocr(tasker: Tasker, frame) -> bool:
    """True when board shows delivery/accelerate copy for an accepted order."""
    try:
        from maa.pipeline import JOCR, JRecognitionType
    except ImportError:
        return False
    job = tasker.post_recognition(
        JRecognitionType.OCR,
        JOCR(roi=(40, 700, 640, 500), expected=[r".+"], threshold=0.4),
        frame,
    ).wait()
    detail = job.get()
    recognition = detail.nodes[0].recognition if detail and detail.nodes else None
    if recognition is None:
        return False
    for result in list(getattr(recognition, "filtered_results", []) or []):
        text = getattr(result, "text", "") or ""
        if "进行中" in text:
            return True
    return False


def _bright_accept_box(tasker: Tasker, frame):
    """Return (score, x, y, w, h) for a bright 接受 control, else None."""
    try:
        from maa.pipeline import JOCR, JRecognitionType
    except ImportError:
        return None
    job = tasker.post_recognition(
        JRecognitionType.OCR,
        JOCR(roi=(300, 980, 400, 280), expected=[r"接受"], threshold=0.55),
        frame,
    ).wait()
    detail = job.get()
    recognition = detail.nodes[0].recognition if detail and detail.nodes else None
    if recognition is None:
        return None
    best = None
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
            best = (score, x, y, w, h, value)
    return best


def _acceptance_confirmed(tasker: Tasker, controller: Win32Controller) -> bool:
    """Require explicit in-progress evidence, not a closed board/missing button."""
    frame = screenshot(controller)
    if template_hit(frame, 'VerifyNormalOrderInProgress') is not None:
        return True
    if _order_delivery_ocr(tasker, frame):
        return True
    return False



def reward_overlay_visible(frame) -> bool:
    template = cv2.imread(
        str(RESOURCE / "image/navigation/normal_order_reward_continue.png")
    )
    if template is None:
        raise RuntimeError("missing normal-order reward overlay template")
    roi = frame[900:1250, 80:640]
    if roi.size == 0 or template.size == 0:
        return False
    return cv2.minMaxLoc(cv2.matchTemplate(roi, template, cv2.TM_CCOEFF_NORMED))[1] >= 0.85


def dismiss_reward_overlay(controller: Win32Controller) -> bool:
    if not reward_overlay_visible(screenshot(controller)):
        return False
    for _ in range(4):
        if not post_click_normalized(controller, 360, 1100).wait().succeeded:
            raise RuntimeError("reward overlay dismissal failed")
        # One blank-area click is enough. A second click can hit the phone
        # after the overlay disappears and leave another modal open.
        time.sleep(0.5)
        if not reward_overlay_visible(screenshot(controller)):
            return True
    raise RuntimeError("reward overlay remained visible")


def _claim_reward_button(frame):
    template = cv2.imread(str(RESOURCE / "image/navigation/normal_order_claim_reward.png"))
    if template is None:
        raise RuntimeError("missing normal-order reward template")
    x, y, width, height = 160, 1060, 400, 180
    scores = cv2.matchTemplate(
        frame[y : y + height, x : x + width], template, cv2.TM_CCOEFF_NORMED
    )
    _, score, _, position = cv2.minMaxLoc(scores)
    if score < 0.9:
        return None
    target_x = x + position[0] + template.shape[1] // 2
    target_y = y + position[1] + template.shape[0] // 2
    patch = frame[y + position[1]:y + position[1] + template.shape[0],
                  x + position[0]:x + position[0] + template.shape[1]]
    # Normalized template scores also match a dark button behind a modal.
    if float(cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)[:, :, 2].mean()) < 150:
        return None
    return target_x, target_y


def claim_order_record_reward(tasker, controller, frame, timeout=8):
    if not order_record_reward_prompt(tasker, frame):
        return False
    if not post_click_normalized(controller, 360, 875).wait().succeeded:
        raise RuntimeError('order-record reward click failed; no automatic retry')
    deadline = time.monotonic() + timeout
    while True:
        time.sleep(.3)
        after = screenshot(controller)
        if reward_overlay_visible(after):
            dismiss_reward_overlay(controller)
            return True
        if _claim_reward_button(after) is not None:
            return True
        if time.monotonic() >= deadline:
            raise RuntimeError('order-record reward outcome unconfirmed; no automatic retry')


def claim_completed_reward(controller: Win32Controller, tasker=None) -> bool:
    frame = screenshot(controller)
    if tasker is not None and claim_order_record_reward(tasker, controller, frame):
        frame = screenshot(controller)
    target = _claim_reward_button(frame)
    if target is None:
        return False
    target_x, target_y = target
    if not post_click_normalized(controller, target_x, target_y).wait().succeeded:
        raise RuntimeError("reward claim click failed")
    # The animation can appear after a variable delay. Wait for the explicit
    # blank-area prompt before tapping, so a too-early tap cannot be lost.
    for _ in range(12):
        time.sleep(0.4)
        if reward_overlay_visible(screenshot(controller)):
            break
    else:
        raise RuntimeError("reward claim did not produce its confirmation overlay")
    dismiss_reward_overlay(controller)
    after = screenshot(controller)
    if _claim_reward_button(after) is not None:
        raise RuntimeError("reward claim was not confirmed")
    return True


def duplicate_accept_blocked(previous, items, *, now=None):
    """True when this exact recipe and stock were accepted moments ago.

    A broken previous record raises. It is not permission to accept again.
    """
    if not isinstance(previous, dict):
        raise ValueError('Previous completion record must be an object')
    prev = previous
    submitted = prev.get('submitted', [])
    if not isinstance(submitted, list):
        raise ValueError('Previous submitted orders must be a list')
    prev_gen = prev.get('generated_at_utc')
    if not prev_gen:
        if submitted:
            raise ValueError('Previous submitted orders lack a timestamp')
        return False
    moment = now or datetime.now(timezone.utc)
    generated = datetime.fromisoformat(prev_gen)
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
    age = (moment - generated).total_seconds()
    if not 0 <= age <= 300:
        return False
    signature = order_signature(items)
    current = {(item['name'], int(item['actual'])) for item in items}
    for prev_sub in submitted:
        if order_signature(prev_sub.get('items') or []) != signature:
            continue
        previous_actual = {(item['name'], int(item['actual'])) for item in prev_sub.get('items') or []}
        if previous_actual == current:
            return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-file", type=Path, help="cycle/target JSON; rematch by shortage crops on a fresh board scan (not frozen order id)")
    parser.add_argument('--prepare-only', action='store_true', help='prepare one non-fish order among the first three; stop reading when found, do not accept')
    args = parser.parse_args()
    if args.prepare_only and args.target_file:
        parser.error('--prepare-only cannot be combined with --target-file')
    requested = None
    if args.target_file:
        payload = json.loads(args.target_file.read_text(encoding="utf-8"))
        requested = payload.get("target", payload)
        if not 1 <= int(requested['order']) <= MAX_PREPARED_ORDERS or not 2 <= len(requested['items']) <= 4:
            parser.error('target must contain a first-row position and every order product')
        order_signature(requested['items'])
        if order_contains_unsupported_product(requested['items']):
            parser.error('fish orders cannot be submitted')
    started = time.perf_counter()
    # Keep prior completion for duplicate-accept detection, then invalidate
    # any production target so a failed run cannot drive employee assignment.
    RESULT.parent.mkdir(parents=True, exist_ok=True)
    try:
        previous_completion = json.loads(RESULT.read_text(encoding="utf-8"))
    except FileNotFoundError:
        previous_completion = {}
    except (OSError, json.JSONDecodeError) as error:
        raise decoded.DecodeError('上次交单记录无法读取，未接受：' + str(error)) from error
    RESULT.write_text(
        json.dumps(
            {"generated_at_utc": datetime.now(timezone.utc).isoformat(), "status": "running"},
            ensure_ascii=False,
        ) + "\n",
        encoding="utf-8",
    )
    startup_data = decoded.snapshot() if decoded.enabled() else None
    if startup_data is not None:
        decoded.require_normal_package(startup_data)
    pending_delivery = delivery.load() if startup_data is not None else None
    if (pending_delivery and pending_delivery['status'] == 'completed'
            and pending_delivery.get('returned_to_farm', True) is not False):
        pending_delivery = None  # A prior terminal receipt cannot count as a new completion.
    if pending_delivery and pending_delivery['status'] != 'completed':
        pending_delivery = delivery.recover(pending_delivery, startup_data)
    products = load_products()
    controller = Win32Controller(
        hWnd=find_window(),
        screencap_method=MaaWin32ScreencapMethodEnum.FramePool,
        mouse_method=MaaWin32InputMethodEnum.PostMessage,
        keyboard_method=MaaWin32InputMethodEnum.PostMessage,
    )
    resource = Resource()
    tasker = Tasker()
    submitted: list[dict] = []
    skipped: list[dict] = []
    sleep_confirmed = False
    in_progress_waited = False
    returned_to_farm = False
    target_order: dict | None = None
    reward_claimed = False
    acceptance_pending = False
    try:
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
        print(f"frame top offset={offset}; pipeline resource={runtime_resource}", flush=True)
        if not resource.post_bundle(runtime_resource).wait().succeeded:
            raise RuntimeError("Maa resource loading failed")
        if not tasker.bind(resource, controller):
            raise RuntimeError("Maa tasker binding failed")

        dismiss_offline_income(controller, tasker)
        leave_employee_ui_if_visible(controller)
        dismiss_reward_overlay(controller)
        if pending_delivery and pending_delivery['status'] == 'completed':
            receipt = finish_return(tasker, controller, pending_delivery)
            output = delivery_output(receipt)
            RESULT.write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            print(json.dumps(output, ensure_ascii=False))
            return 0
        navigation = open_normal_orders(controller)
        if not navigation:
            # Reward "继续" overlay or a leftover popup can block the notice board.
            dismiss_reward_overlay(controller)
            # Do not guess blank positions: the lower-right map point is the
            # phone button and can open another blocking menu.
            leave_employee_ui_if_visible(controller)
            navigation = open_normal_orders(controller)
        if not navigation:
            raise RuntimeError("failed to open normal-order page")

        if pending_delivery and pending_delivery['status'] == 'accepted':
            receipt = finish_normal_delivery(tasker, controller, pending_delivery)
            output = delivery_output(receipt)
            RESULT.write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            print(json.dumps(output, ensure_ascii=False))
            return 0
        if decoded.enabled() and any(row['category'] == 'normal' and row['statusCode'] in (2, 3, 99)
                                     for row in decoded.snapshot()['orders']):
            raise decoded.DecodeError('Untracked normal delivery; reconcile before new input or reward claim')
        reward_claimed = claim_completed_reward(controller, tasker)

        # A previously accepted order can still be running when a new task
        # starts. Detect it before touching the triple control or any card.
        if template_hit(screenshot(controller), 'VerifyNormalOrderInProgress') is not None:
            in_progress_waited = True
            time.sleep(IN_PROGRESS_WAIT_SECONDS)
            # The next cycle must start on a verified map, not shared HUD icons.
            on_farm = ensure_returned_to_farm(tasker, controller)
            output = {
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                "submitted_count": 0,
                "submitted": [],
                "skipped": [],
                "sleep_prompt_confirmed": False,
                "in_progress_waited": True,
                "wait_seconds": IN_PROGRESS_WAIT_SECONDS,
                "status": "existing_order_in_progress",
                "reward_claimed": reward_claimed,
                "returned_to_farm": on_farm,
                "target_order": None,
            }
            RESULT.parent.mkdir(parents=True, exist_ok=True)
            RESULT.write_text(
                json.dumps(output, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(json.dumps(output, ensure_ascii=False))
            return 0

        # When rematching a production target, enable triple mode without
        # freezing the old grid index — board refresh may move the recipe.
        triple_frame = prepare_completion_control(tasker, controller, products, None)
        if decoded.enabled():
            verify_normal_triple(tasker, triple_frame)
        pins = recognize_pins(triple_frame)

        # Iterate stable grid positions once. A successful submit refreshes only
        # the selected card; later positions remain valid.
        # With --target-file: scan ALL pins and rematch by shortage crops
        # (same missing crop names), not a frozen order id / recipe signature.
        selected_pins = list(enumerate(pins[:MAX_PREPARED_ORDERS], 1))
        if requested:
            preferred = int(requested["order"])
            selected_pins.sort(key=lambda pair: 0 if pair[0] == preferred else 1)
        for index, pin in selected_pins:
            if not post_click_normalized(controller, *pin).wait().succeeded:
                raise RuntimeError(f"order {index} pin click failed")
            time.sleep(SETTLE_SECONDS)
            frame = screenshot(controller)
            if completion_control_visible(frame) and not completion_checked(frame)[0]:
                raise RuntimeError("complete-3-times changed before reading the order")
            # Fish excludes the entire order even if its quantity OCR fails.
            if decoded.enabled():
                decoded.select_order('normal', index, 3)
                visible_products = read_order(tasker, frame, products)
            else:
                visible_products = match_products(frame, products)
            if order_contains_unsupported_product(visible_products):
                skipped.append({'order': index, 'reason': 'unsupported_product',
                                'items': [{'name': item['name']} for item in visible_products]})
                continue
            # An unavailable recipe hides the entire checkbox control. Its
            # guarded recovery will restore and verify triple mode before OCR.
            items = (visible_products if decoded.enabled() else read_order(tasker, frame, products)) if completion_checked(frame)[0] else []
            recovery_actions: list[str] = []
            if not 2 <= len(items) <= 4:
                if requested:
                    # Rematch path: skip unreadable cards instead of aborting the board.
                    skipped.append({"order": index, "reason": "unreadable", "items": items})
                    continue
                if decoded.enabled():
                    items, recovery_actions = recover_order(tasker, controller, pin, products, order_number=index)
                else:
                    items, recovery_actions = recover_order(tasker, controller, pin, products)
            if not 2 <= len(items) <= 4:
                skipped.append({"order": index, "reason": "unreadable", "items": items})
                continue
            if requested and not order_matches_shortage_focus(requested, items):
                skipped.append({
                    "order": index,
                    "reason": "shortage_mismatch",
                    "items": items,
                })
                continue
            if order_contains_unsupported_product(items):
                skipped.append({"order": index, "reason": "unsupported_product", "items": items})
                continue
            if args.prepare_only:
                skipped.append({'order': index, 'reason': 'ready' if all(item['satisfied'] for item in items) else 'insufficient', 'items': items})
                break
            if not all(item["satisfied"] for item in items):
                skipped.append({"order": index, "reason": "insufficient", "items": items})
                continue

            # Refuse to re-accept the same recipe+stock snapshot within a few
            # minutes (false-accept loops leave inventory unchanged).
            if duplicate_accept_blocked(previous_completion, items):
                skipped.append({
                    "order": index,
                    "reason": "duplicate_uncconsumed_accept",
                    "items": items,
                })
                continue

            if decoded.enabled():
                items = decoded.assert_order_unchanged(items)
            before = [(item["name"], item["actual"], item["required"]) for item in items]
            # Click bright OCR 接受 (template match can latch onto a dark twin).
            accept_frame = screenshot(controller)
            if decoded.enabled():
                try:
                    from .scan_normal_orders import require_decoded_card_selection
                except ImportError:
                    from scan_normal_orders import require_decoded_card_selection
                require_decoded_card_selection(accept_frame)
                verify_normal_triple(tasker, accept_frame)
            accept_box = _bright_accept_box(tasker, accept_frame)
            if decoded.enabled():
                if accept_box is None and template_hit(accept_frame, 'AcceptNormalOrderStart') is None:
                    raise decoded.DecodeError('Normal accept control not recognized; no intent or input')
                receipt = delivery.reserve(items, index, decoded.snapshot())
            if accept_box is None:
                # Fall back to pipeline template click only when OCR misses.
                if not click_template_node(controller, "AcceptNormalOrderStart"):
                    raise RuntimeError(f"order {index}: accept button not recognized")
            else:
                _score, ax, ay, aw, ah, _value = accept_box
                if not post_click_normalized(
                    controller, ax + aw // 2, ay + ah // 2
                ).wait().succeeded:
                    raise RuntimeError(f"order {index}: accept click failed")
            time.sleep(max(SETTLE_SECONDS, 0.8))

            if decoded.enabled():
                finish_order_acceptance(tasker, controller, items)
                receipt = delivery.recover(receipt, decoded.snapshot())
                receipt = finish_normal_delivery(tasker, controller, receipt)
                output = delivery_output(receipt)
                RESULT.write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
                print(json.dumps(output, ensure_ascii=False))
                return 0
            elif confirm_sleep_or_close_shop(tasker, controller):
                sleep_confirmed = True
                time.sleep(0.5)

            # Accept frequently dismisses the notice board back to the farm map.
            in_progress_succeeded = True if decoded.enabled() else _acceptance_confirmed(tasker, controller)
            if not in_progress_succeeded:
                reopened = open_normal_orders(controller)
                if reopened:
                    time.sleep(0.5)
                    in_progress_succeeded = _acceptance_confirmed(tasker, controller)
            if not in_progress_succeeded:
                if sleep_confirmed:
                    # Accept near night often shows sleep/close-shop. Confirming
                    # that prompt advances time; in-progress chrome may never
                    # appear because the order is taken overnight. Count as
                    # accepted once sleep was confirmed after the accept click.
                    submitted.append(
                        {
                            "order": index,
                            "items": items,
                            "recovery_actions": recovery_actions,
                            "acceptance_confirmed": "sleep_prompt",
                        }
                    )
                    in_progress_waited = True
                    close_order_board(controller)
                    ensure_returned_to_farm(tasker, controller)
                    returned_to_farm = True
                    break
                raise RuntimeError(
                    f"order {index}: acceptance not confirmed after reopening the board"
                )
            submitted.append(
                {"order": index, "items": items, "recovery_actions": recovery_actions, "acceptance_confirmed": True}
            )
            # An accepted order locks the board. Do not inspect or submit any
            # other card using stale inventory. Wait without sending input,
            # then close the board so the next cycle pass reopens cleanly.
            in_progress_waited = True
            time.sleep(IN_PROGRESS_WAIT_SECONDS)
            close_order_board(controller)
            break

        all_orders_read = len(submitted) + len(skipped) == len(selected_pins) and all(
            entry["reason"] in {"insufficient", "unsupported_product", "duplicate_uncconsumed_accept"} for entry in skipped
        )
        no_acceptable_orders = not submitted and all_orders_read
        # Also pick a plant target when this pass submitted one ready card but the
        # rest of the board is only shortages (common after inventory covers a
        # single easy order). Previously in_progress_waited blocked that path.
        # After a submit the loop breaks, so later pins are unread and
        # all_orders_read is often false. Still plant from the shortages we
        # already classified on this pass.
        should_pick_target = (
            bool(skipped)
            and any(entry.get("reason") == "insufficient" for entry in skipped)
            and not sleep_confirmed
            and not acceptance_pending
            and (no_acceptable_orders or bool(submitted) or all_orders_read)
        )
        if args.prepare_only:
            target_order = prepare_order_target(skipped)
            no_acceptable_orders = target_order is None
            ensure_returned_to_farm(tasker, controller)
            returned_to_farm = True
        elif should_pick_target:
            slots = json.loads(CAPABILITIES.read_text(encoding="utf-8"))["slots"]

            def employee_feasible(entry: dict) -> bool:
                shortage = [item for item in entry["items"] if item["missing"] > 0]
                return allocate(build_targets(shortage), slots)["ready"]

            target_order = choose_target_order(skipped, is_feasible=employee_feasible)
            ensure_returned_to_farm(tasker, controller)
            returned_to_farm = True

        output = {
            "status": "sleep_confirmation_pending" if acceptance_pending else 'prepared' if args.prepare_only else "completed",
            'scan_limit': MAX_PREPARED_ORDERS,
            'state_reader': 'decoded' if decoded.enabled() else 'ocr',
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "submitted_count": len(submitted),
            "submitted": submitted,
            "skipped": skipped,
            "sleep_prompt_confirmed": sleep_confirmed,
            "in_progress_waited": in_progress_waited,
            "wait_seconds": IN_PROGRESS_WAIT_SECONDS if in_progress_waited else 0,
            "no_acceptable_orders": no_acceptable_orders,
            "returned_to_farm": returned_to_farm,
            "next_stage": "planting_and_breeding" if returned_to_farm and target_order else None,
            "target_order": target_order,
            "reward_claimed": reward_claimed,
            "acceptance_pending": acceptance_pending,
        }
        RESULT.parent.mkdir(parents=True, exist_ok=True)
        RESULT.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(output, ensure_ascii=False))
        return 2 if acceptance_pending else 0
    except Exception as error:
        RESULT.write_text(json.dumps({
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "status": "failed", "error": str(error),
            "submitted": submitted, "skipped": skipped,
            "returned_to_farm": False, "target_order": None,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        raise
    finally:
        tasker.clear_cache()
        for value in (tasker, resource, controller):
            destructor = getattr(value, "__del__", None)
            if callable(destructor):
                destructor()
            if hasattr(value, "_handle"):
                value._handle = None


if __name__ == "__main__":
    raise SystemExit(main())

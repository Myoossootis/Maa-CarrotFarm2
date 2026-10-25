"""Scan the first three normal orders using Maa background capture and input.

The task does not accept orders. Unavailable recipes can be replaced and
free-accelerated through the guarded recovery flow authorized by the user.
Product identity uses the immutable backpack template registry; quantities use
Maa OCR and are reported as actual/required.
"""

from __future__ import annotations

import ctypes
import json
from pathlib import Path
import re
import time

import cv2
from maa.controller import Win32Controller
from maa.define import MaaWin32InputMethodEnum, MaaWin32ScreencapMethodEnum
from maa.pipeline import JOCR, JRecognitionType
from maa.resource import Resource
from maa.tasker import Tasker

try:
    from . import decoded_runtime as decoded
except ImportError:
    import decoded_runtime as decoded

try:
    from .production_targets import MAX_PREPARED_ORDERS
except ImportError:
    from production_targets import MAX_PREPARED_ORDERS

try:
    from .order_template_click import click_template_node, open_normal_orders
except ImportError:
    from order_template_click import click_template_node, open_normal_orders


ROOT = Path(__file__).resolve().parents[1]
RESOURCE = ROOT / "assets/resource"
REGISTRY = ROOT / "assets/catalog/backpack_crops/registry.json"
RESULT = ROOT / "assets/catalog/normal_orders/latest_scan.json"
DETAIL_ROI = (50, 900, 610, 170)
QUANTITY_Y = (1015, 1070)
OCR_REPLACE = [["O", "0"], ["o", "0"], ["l", "1"], ["I", "1"], ["i", "1"]]
PRODUCT_THRESHOLD = 0.9
SETTLE_SECONDS = 0.22
RECOVERY_LIMIT = 8
RECOVERY_SETTLE_SECONDS = 0.45


def find_window() -> int:
    matches: list[int] = []
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    @callback_type
    def callback(hwnd, _):
        title = ctypes.create_unicode_buffer(user32.GetWindowTextLengthW(hwnd) + 1)
        class_name = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, title, len(title))
        user32.GetClassNameW(hwnd, class_name, len(class_name))
        if title.value == "萝卜家园2" and class_name.value == "Chrome_WidgetWin_0":
            matches.append(int(hwnd))
        return True

    user32.EnumWindows(callback, 0)
    if len(matches) != 1:
        raise RuntimeError(f"expected one game window, found {len(matches)}")
    return matches[0]


try:
    from .maa_frame_normalize import normalize_frame, build_runtime_resource, measure_controller_offset, post_click_normalized
except ImportError:
    from maa_frame_normalize import normalize_frame, build_runtime_resource, measure_controller_offset, post_click_normalized


def screenshot(controller: Win32Controller):
    job = controller.post_screencap().wait()
    if not job.succeeded:
        raise RuntimeError("Maa screenshot failed")
    return normalize_frame(job.get())


def load_products() -> list[dict]:
    data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    products = []
    for item in data["products"]:
        image = cv2.imread(str(ROOT / item["template"]), cv2.IMREAD_GRAYSCALE)
        if image is None:
            raise RuntimeError(f"missing product template: {item['template']}")
        products.append({**item, "image": image})
    return products


def recognize_pins(frame) -> list[tuple[int, int]]:
    template = cv2.imread(
        str(RESOURCE / "image/navigation/normal_order_pin.png"),
        cv2.IMREAD_GRAYSCALE,
    )
    # Only the top row matters; unavailable lower rows must not block this scan.
    roi_x, roi_y, roi_width, roi_height = (30, 380, 660, 85)
    roi = cv2.cvtColor(
        frame[roi_y : roi_y + roi_height, roi_x : roi_x + roi_width],
        cv2.COLOR_BGR2GRAY,
    )
    scores = cv2.matchTemplate(roi, template, cv2.TM_CCOEFF_NORMED)
    hits: list[tuple[float, int, int]] = []
    while True:
        _, score, _, location = cv2.minMaxLoc(scores)
        if score < 0.75:
            break
        x, y = location
        center_x = roi_x + x + template.shape[1] // 2
        center_y = roi_y + y + template.shape[0] // 2
        hits.append((float(score), center_x, center_y))
        cv2.rectangle(
            scores,
            (max(0, x - 20), max(0, y - 20)),
            (min(scores.shape[1] - 1, x + 20), min(scores.shape[0] - 1, y + 20)),
            -1,
            -1,
        )
    if len(hits) != MAX_PREPARED_ORDERS:
        raise RuntimeError(f"expected exactly 3 top-row pins, recognized {len(hits)}")
    return [(x, y) for _, x, y in sorted(hits, key=lambda item: item[1])]


def match_products(frame, products: list[dict]) -> list[dict]:
    x, y, width, height = DETAIL_ROI
    roi = cv2.cvtColor(frame[y : y + height, x : x + width], cv2.COLOR_BGR2GRAY)
    candidates = []
    for product in products:
        result = cv2.matchTemplate(roi, product["image"], cv2.TM_CCOEFF_NORMED)
        _, score, _, location = cv2.minMaxLoc(result)
        if score < PRODUCT_THRESHOLD:
            continue
        candidates.append(
            {
                "id": product["id"],
                "name": product["name"],
                "score": float(score),
                "center_x": x + location[0] + product["image"].shape[1] // 2,
            }
        )
    # Different crop icons can share small outlines. Retain one strongest
    # identity per displayed column and preserve the UI's left-to-right order.
    selected: list[dict] = []
    for candidate in sorted(candidates, key=lambda item: item["score"], reverse=True):
        if any(abs(candidate["center_x"] - item["center_x"]) < 45 for item in selected):
            continue
        selected.append(candidate)
    return sorted(selected, key=lambda item: item["center_x"])


def ocr_candidates(tasker: Tasker, image, only_rec: bool) -> list[dict]:
    job = tasker.post_recognition(
        JRecognitionType.OCR,
        JOCR(
            roi=(0, 0, image.shape[1], image.shape[0]),
            expected=[r"^\d+\s*/\s*\d+$"],
            threshold=0.02,
            only_rec=only_rec,
            replace=OCR_REPLACE,
        ),
        image,
    ).wait()
    detail = job.get()
    recognition = detail.nodes[0].recognition if detail and detail.nodes else None
    if not recognition:
        return []
    output = []
    for item in recognition.filtered_results:
        text = re.sub(r"\s+", "", getattr(item, "text", ""))
        if re.fullmatch(r"\d+/\d+", text):
            output.append({"text": text, "score": float(getattr(item, "score", 0))})
    return output


def recognize_quantity(tasker: Tasker, frame, center_x: int) -> dict | None:
    y1, y2 = QUANTITY_Y
    patch = frame[y1:y2, max(0, center_x - 55) : min(frame.shape[1], center_x + 55)]
    enlarged = cv2.resize(patch, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
    candidates = ocr_candidates(tasker, enlarged, False)
    # Retry on a thresholded image only when detection did not produce a
    # complete actual/required pair. This keeps the common path fast.
    if not candidates:
        gray = cv2.cvtColor(enlarged, cv2.COLOR_BGR2GRAY)
        _, binary = cv2.threshold(gray, 160, 255, cv2.THRESH_BINARY)
        candidates = ocr_candidates(tasker, cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR), True)
    if not candidates:
        return None
    best = max(candidates, key=lambda item: item["score"])
    actual_text, required_text = best["text"].split("/")
    actual, required = int(actual_text), int(required_text)
    missing = max(required - actual, 0)
    return {
        "actual": actual,
        "required": required,
        "satisfied": missing == 0,
        "missing": missing,
        "ocr_score": round(best["score"], 6),
    }


def require_decoded_card_selection(frame):
    """Retain the original cyan-card UI proof before associating cache with a click."""
    try:
        from .refresh_order import card_selected
    except ImportError:
        from refresh_order import card_selected
    if not card_selected(frame, decoded.selected_order()[1]):
        raise decoded.DecodeError('Decoded order card is not visibly selected; no recipe guessed')


def read_order(tasker: Tasker, frame, products: list[dict]) -> list[dict]:
    if decoded.enabled():
        require_decoded_card_selection(frame)
        try:
            return decoded.read_selected()
        except decoded.OrderUnavailable:
            return []
    items = []
    for product in match_products(frame, products):
        quantity = recognize_quantity(tasker, frame, product["center_x"])
        if quantity is None:
            # Partial recipes can hide an unmet product (including fish).
            # Do not let two readable items stand in for a three-item order.
            return []
        items.append(
            {
                "name": product["name"],
                **quantity,
                "template_score": round(product["score"], 6),
            }
        )
    return items


def recover_order(
    tasker: Tasker,
    controller: Win32Controller,
    pin: tuple[int, int],
    products: list[dict],
    order_number: int | None = None,
) -> tuple[list[dict], list[str]]:
    actions: list[str] = []
    if not post_click_normalized(controller, *pin).wait().succeeded:
        raise RuntimeError("recovery target pin click failed")
    time.sleep(RECOVERY_SETTLE_SECONDS)
    for _ in range(RECOVERY_LIMIT):
        recovered = None
        for name in ('ClickNormalOrderReplace', 'ClickNormalOrderFreeAccelerate'):
            if click_template_node(controller, name):
                recovered = name
                break
        if recovered:
            if recovered == "ClickNormalOrderReplace":
                actions.append("更换订单")
                # Rare orders ask for an extra confirmation before replacement.
                resolve_rare_abandon_dialog(controller, confirm=True)
            elif recovered == "ClickNormalOrderFreeAccelerate":
                actions.append("免费加速")
            time.sleep(RECOVERY_SETTLE_SECONDS)

        # A newly generated order may not remain selected, so always re-click
        # its grid position before checking the requirement panel.
        if not post_click_normalized(controller, *pin).wait().succeeded:
            raise RuntimeError("replacement order pin click failed")
        time.sleep(RECOVERY_SETTLE_SECONDS)
        frame = screenshot(controller)
        if not completion_control_visible(frame):
            # Unlearned/replacement-pending orders have no triple control.
            # Continue the guarded replace/free-accelerate state machine.
            continue
        if not completion_checked(frame)[0]:
            ensure_triple_checked(tasker, controller)
            frame = screenshot(controller)
        if decoded.enabled():
            if order_number is None:
                raise decoded.DecodeError('Recovery requires an explicit decoded slot number')
            decoded.select_order('normal', order_number, 3)
        items = read_order(tasker, frame, products)
        if 2 <= len(items) <= 4:
            return items, actions
        if not recovered:
            time.sleep(RECOVERY_SETTLE_SECONDS)
    return [], actions


def completion_checked(frame) -> tuple[bool, float]:
    # Bottom-only ROI intentionally excludes every order-card green check.
    roi_color = frame[1180:1250, 475:670]
    roi = cv2.cvtColor(roi_color, cv2.COLOR_BGR2GRAY)
    score, location, matched_width, matched_height = -2.0, (0, 0), 0, 0
    for name in ("normal_order_completion_checked_full.png", "normal_order_completion_checked_full_live.png"):
        template = cv2.imread(str(RESOURCE / "image/navigation" / name), cv2.IMREAD_GRAYSCALE)
        if template is None:
            continue
        _, candidate, _, point = cv2.minMaxLoc(cv2.matchTemplate(roi, template, cv2.TM_CCOEFF_NORMED))
        if candidate > score:
            score, location = float(candidate), point
            matched_height, matched_width = template.shape
    # Template score protects shape; explicit HSV green pixels protect against
    # the visually similar empty white square.
    x, y = location
    if matched_height == 0:
        raise RuntimeError("no checked-completion template was loaded")
    hsv = cv2.cvtColor(roi_color[y : y + matched_height, x : x + min(60, matched_width)], cv2.COLOR_BGR2HSV)
    green = cv2.inRange(hsv, (35, 80, 60), (90, 255, 255))
    green_pixels = int(cv2.countNonZero(green))
    return score >= 0.80 and green_pixels >= 80, float(score)


def completion_control_visible(frame) -> bool:
    roi = cv2.cvtColor(frame[1180:1250, 475:670], cv2.COLOR_BGR2GRAY)
    for name in ("normal_order_completion_checked_full.png", "normal_order_completion_checked_full_live.png",
                 "normal_order_completion_unchecked.png", "normal_order_completion_unchecked_live.png"):
        template = cv2.imread(str(RESOURCE / "image/navigation" / name), cv2.IMREAD_GRAYSCALE)
        if template is not None and template.shape[0] <= roi.shape[0] and template.shape[1] <= roi.shape[1]:
            if cv2.minMaxLoc(cv2.matchTemplate(roi, template, cv2.TM_CCOEFF_NORMED))[1] >= 0.84:
                return True
    return False





def rare_abandon_dialog_visible(frame) -> bool:
    """Detect the rare-order abandon confirmation dialog."""
    hsv = cv2.cvtColor(frame[620:750, 380:640], cv2.COLOR_BGR2HSV)
    yellow = cv2.inRange(hsv, (18, 100, 180), (35, 255, 255))
    return int(cv2.countNonZero(yellow)) >= 4000


def resolve_rare_abandon_dialog(controller: Win32Controller, confirm: bool) -> bool:
    """Click Confirm or Cancel on the rare-order abandon dialog."""
    frame = screenshot(controller)
    if not rare_abandon_dialog_visible(frame):
        return False
    # Measured on a live 720x1358 capture: Cancel ~(512,686), Confirm ~(230,686).
    target = (230, 686) if confirm else (512, 686)
    if not post_click_normalized(controller, *target).wait().succeeded:
        raise RuntimeError("rare-order dialog click failed")
    time.sleep(0.8)
    return True


def ensure_triple_checked(tasker: Tasker, controller: Win32Controller) -> None:
    """Ensure the complete-3-times control is checked using pipeline then Python."""
    frame = screenshot(controller)
    if completion_checked(frame)[0]:
        return
    if _enable_triple_by_template_click(controller, frame):
        return
    # Calibration click point for the live banner control.
    if not post_click_normalized(controller, 570, 1213).wait().succeeded:
        raise RuntimeError("complete-3-times click failed")
    time.sleep(0.5)
    frame = screenshot(controller)
    if completion_checked(frame)[0]:
        return
    if _enable_triple_by_template_click(controller, frame):
        frame = screenshot(controller)
        if completion_checked(frame)[0]:
            return
    raise RuntimeError("complete-3-times was not verified")


def _enable_triple_by_template_click(controller: Win32Controller, frame) -> bool:
    """Click the empty square on the left of the unchecked completion banner."""
    best_score = -1.0
    best_origin = None
    best_template = None
    for name in (
        "normal_order_completion_unchecked.png",
        "normal_order_completion_unchecked_live.png",
    ):
        template = cv2.imread(str(RESOURCE / "image/navigation" / name))
        if template is None:
            continue
        roi = frame[1170:1280, 470:680]
        if roi.shape[0] < template.shape[0] or roi.shape[1] < template.shape[1]:
            continue
        _, score, _, loc = cv2.minMaxLoc(cv2.matchTemplate(roi, template, cv2.TM_CCOEFF_NORMED))
        if score > best_score:
            best_score = float(score)
            best_origin = (470 + loc[0], 1170 + loc[1])
            best_template = template
    if best_origin is None or best_template is None or best_score < 0.80:
        return False
    # The live control is a white tip bubble; the empty square sits near the
    # left edge of the matched banner, not at the geometric center.
    ox, oy = best_origin
    candidates = [
        (ox + 18, oy + best_template.shape[0] // 2),
        (ox + 28, oy + best_template.shape[0] // 2),
        (ox + 38, oy + best_template.shape[0] // 2),
        (ox + 22, oy + best_template.shape[0] // 2 - 4),
        (ox + 22, oy + best_template.shape[0] // 2 + 4),
    ]
    for x, y in candidates:
        if not post_click_normalized(controller, x, y).wait().succeeded:
            return False
        time.sleep(0.35)
        if completion_checked(screenshot(controller))[0]:
            return True
    return False


def prepare_completion_control(tasker, controller, products, requested_order=None):
    """Select a readable card before verifying triple mode; never OCR unverified quantities."""
    frame = screenshot(controller)
    if rare_abandon_dialog_visible(frame):
        # Accidental replace confirmations should not discard rare orders here.
        resolve_rare_abandon_dialog(controller, confirm=False)
        frame = screenshot(controller)
    try:
        pins = recognize_pins(frame)
    except RuntimeError:
        # Product-detail / replace subpages hide the 3x3 pins; one back tap
        # usually restores the grid without leaving the notice board.
        post_click_normalized(controller, 57, 1280).wait()
        time.sleep(0.6)
        frame = screenshot(controller)
        if rare_abandon_dialog_visible(frame):
            resolve_rare_abandon_dialog(controller, confirm=False)
            frame = screenshot(controller)
        pins = recognize_pins(frame)
    if requested_order is not None and not 1 <= requested_order <= MAX_PREPARED_ORDERS:
        raise ValueError('only the first three order positions may be selected')
    selected = [pins[requested_order - 1]] if requested_order else pins[:MAX_PREPARED_ORDERS]
    for pin in selected:
        if not post_click_normalized(controller, *pin).wait().succeeded:
            raise RuntimeError("initial order pin click failed")
        time.sleep(SETTLE_SECONDS)
        frame = screenshot(controller)
        if completion_control_visible(frame):
            break
    else:
        if requested_order:
            raise RuntimeError("target order is unavailable; refusing to replace its recipe")
        if decoded.enabled():
            items, actions = recover_order(tasker, controller, selected[0], products, order_number=requested_order or 1)
        else:
            items, actions = recover_order(tasker, controller, selected[0], products)
        if not items:
            raise RuntimeError(f"no readable order after recovery: {actions}")
        frame = screenshot(controller)
    for _ in range(3):
        if completion_checked(frame)[0]:
            return frame
        ensure_triple_checked(tasker, controller)
        frame = screenshot(controller)
        if completion_checked(frame)[0]:
            return frame
    raise RuntimeError("complete-3-times is not checked")


def click_point_available(frame, x: int, y: int) -> bool:
    # Pin detection for one slot tolerates selected-card tilt but rejects the
    # empty hourglass slot that appears while a replacement order is pending.
    template = cv2.imread(
        str(RESOURCE / "image/navigation/normal_order_pin.png"),
        cv2.IMREAD_GRAYSCALE,
    )
    search = cv2.cvtColor(frame[y - 28 : y + 28, x - 28 : x + 28], cv2.COLOR_BGR2GRAY)
    _, score, _, _ = cv2.minMaxLoc(cv2.matchTemplate(search, template, cv2.TM_CCOEFF_NORMED))
    return score >= 0.7


def format_output(output: dict) -> str:
    completion = output["completion"]
    if completion["checked"]:
        completion_text = f"完成次数：{completion['required']}/{completion['required']}（已打勾）"
    else:
        completion_text = f"完成条件：完成{completion['required']}次（未打勾）"
    lines = [completion_text]
    for order in output["orders"]:
        descriptions = []
        for item in order["items"]:
            state = "已满足" if item["satisfied"] else f"差{item['missing']}个"
            descriptions.append(
                f"{item['name']}要求{item['required']}实际{item['actual']}（{state}）"
            )
        lines.append(f"订单{order['order']}：" + "，".join(descriptions))
    lines.append(f"耗时：{output['elapsed_seconds']:.3f}秒")
    return "\n".join(lines)


def main() -> int:
    started = time.perf_counter()
    products = load_products()
    controller = Win32Controller(
        hWnd=find_window(),
        screencap_method=MaaWin32ScreencapMethodEnum.FramePool,
        mouse_method=MaaWin32InputMethodEnum.PostMessage,
        keyboard_method=MaaWin32InputMethodEnum.PostMessage,
    )
    resource = Resource()
    tasker = Tasker()
    try:
        tasker.set_save_draw(False)
        tasker.set_save_on_error(False)
        tasker.set_reco_image_cache_limit(16)
        tasker.set_stdout_level(2)
        controller.set_screenshot_target_short_side(720)
        if not controller.post_connection().wait().succeeded:
            raise RuntimeError("Maa Win32 connection failed")
        offset = measure_controller_offset(controller)
        runtime_resource = build_runtime_resource(offset)
        print(f"frame top offset={offset}; pipeline resource={runtime_resource}", flush=True)
        if not resource.post_bundle(runtime_resource).wait().succeeded:
            raise RuntimeError("Maa resource loading failed")
        if not tasker.bind(resource, controller):
            raise RuntimeError("Maa tasker binding failed")
        navigation = None
        for _ in range(3):
            navigation = open_normal_orders(controller)
            if navigation:
                break
            time.sleep(0.6)
        if not navigation:
            raise RuntimeError("failed to open normal orders or completion box is not checked")

        # This must precede pin recognition and every quantity OCR. Otherwise
        # the UI displays one-third of the requested amounts.
        triple_frame = prepare_completion_control(tasker, controller, products)

        initial_frame = triple_frame
        try:
            pins = recognize_pins(initial_frame)
        except RuntimeError:
            # Recovery screens contain eight pins plus one pending slot. The
            # grid geometry is stable; missing locations are processed by the
            # same guarded recovery state machine below.
            pins = [(x, 418) for x in (157, 367, 577)]
        orders = []
        final_frame = None
        for index, pin in enumerate(pins[:MAX_PREPARED_ORDERS], 1):
            before_click = screenshot(controller)
            if click_point_available(before_click, *pin):
                if not post_click_normalized(controller, *pin).wait().succeeded:
                    raise RuntimeError(f"order {index} pin click failed")
                time.sleep(SETTLE_SECONDS)
            frame = screenshot(controller)
            final_frame = frame
            if decoded.enabled():
                decoded.select_order('normal', index, 3)
            items = read_order(tasker, frame, products)
            recovery_actions: list[str] = []
            if not 2 <= len(items) <= 4:
                if decoded.enabled():
                    items, recovery_actions = recover_order(tasker, controller, pin, products, order_number=index)
                else:
                    items, recovery_actions = recover_order(tasker, controller, pin, products)
                final_frame = screenshot(controller)
            if not 2 <= len(items) <= 4:
                raise RuntimeError(
                    f"order {index}: recovery exhausted after {recovery_actions}; "
                    f"recognized {len(items)} products"
                )
            orders.append(
                {"order": index, "items": items, "recovery_actions": recovery_actions}
            )
            del frame

        checked, checked_score = completion_checked(final_frame)
        output = {
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "completion": {
                "completed": 3 if checked else None,
                "required": 3,
                "checked": checked,
                "template_score": round(checked_score, 6),
            },
            "orders": orders,
        }
        RESULT.parent.mkdir(parents=True, exist_ok=True)
        RESULT.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(format_output(output))
        return 0 if checked and len(orders) == MAX_PREPARED_ORDERS else 2
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

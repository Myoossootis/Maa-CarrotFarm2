"""Fast, read-only inventory scan using Maa background capture/input.

The product registry is immutable during scans. Product identity is resolved by
template matching against stable IDs, so a newly visible product never shifts
the names of later slots. Quantity OCR is batched per page with Maa OCR.
"""

from __future__ import annotations

import ctypes
import json
from pathlib import Path
import time

import cv2
import numpy as np
from maa.controller import Win32Controller
from maa.define import MaaWin32InputMethodEnum, MaaWin32ScreencapMethodEnum
from maa.pipeline import JOCR, JRecognitionType
from maa.resource import Resource
from maa.tasker import Tasker

try:
    from . import decoded_runtime as decoded
except ImportError:
    import decoded_runtime as decoded
from maa_frame_normalize import post_click_normalized, post_swipe_normalized, install_foreground_safe_inputs


ROOT = Path(__file__).resolve().parents[1]
RESOURCE_DIR = ROOT / "assets/resource"
REGISTRY_PATH = ROOT / "assets/catalog/backpack_crops/registry.json"
RESULT_PATH = ROOT / "assets/catalog/backpack_crops/latest_scan.json"
WINDOW_TITLE = "萝卜家园2"
WINDOW_CLASS = "Chrome_WidgetWin_0"
CROP_TAB_CENTER = (136, 426)
GRID_ROI = (75, 458, 570, 480)
CELL_X = (142, 254, 366, 478, 590)
TOP_ROWS = (535, 671, 809)
BOTTOM_ROWS = (483, 617, 751)
SWIPE_DURATION_MS = 550
SETTLE_SECONDS = 0.8
OCR_REPLACE = [["O", "0"], ["o", "0"], ["l", "1"], ["I", "1"], ["i", "1"]]


def find_window() -> int:
    matches: list[int] = []
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    @callback_type
    def callback(hwnd, _lparam):
        title = ctypes.create_unicode_buffer(user32.GetWindowTextLengthW(hwnd) + 1)
        class_name = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, title, len(title))
        user32.GetClassNameW(hwnd, class_name, len(class_name))
        if title.value == WINDOW_TITLE and class_name.value == WINDOW_CLASS:
            matches.append(int(hwnd))
        return True

    user32.EnumWindows(callback, 0)
    if len(matches) != 1:
        raise RuntimeError(f"expected one {WINDOW_TITLE} window, found {len(matches)}")
    return matches[0]


def screenshot(controller: Win32Controller) -> np.ndarray:
    job = controller.post_screencap().wait()
    if not job.succeeded:
        raise RuntimeError("Maa screenshot failed")
    return job.get()


def occupied(patch: np.ndarray) -> bool:
    background = np.array([209, 248, 255], dtype=np.int16)
    distance = np.max(np.abs(patch.astype(np.int16) - background), axis=2)
    return int(np.count_nonzero(distance > 32)) >= 230


def slot_patch(image: np.ndarray, x: int, y: int) -> np.ndarray:
    return image[y - 48 : y + 48, x - 48 : x + 48]


def icon_patch(image: np.ndarray, x: int, y: int) -> np.ndarray:
    return image[y - 48 : y + 2, x - 42 : x + 42]


def normalized_score(left: np.ndarray, right: np.ndarray) -> float:
    return float(
        cv2.matchTemplate(
            cv2.cvtColor(left, cv2.COLOR_BGR2GRAY),
            cv2.cvtColor(right, cv2.COLOR_BGR2GRAY),
            cv2.TM_CCOEFF_NORMED,
        )[0, 0]
    )


def load_registry() -> tuple[list[dict], float]:
    data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    products = []
    for product in data["products"]:
        template = cv2.imread(str(ROOT / product["template"]), cv2.IMREAD_COLOR)
        if template is None:
            raise RuntimeError(f"missing product template: {product['template']}")
        products.append({**product, "image": template})
    return products, float(data["template_match_threshold"])


def local_ocr(tasker: Tasker, patch: np.ndarray) -> dict | None:
    prepared_images: list[tuple[str, np.ndarray, bool]] = []
    prepared_images.append(("detect_x20", patch[42:96, 20:96], False))
    quantity = patch[42:96, 38:96]
    gray = cv2.cvtColor(quantity, cv2.COLOR_BGR2GRAY)
    for threshold in (170,):
        _, binary = cv2.threshold(gray, threshold, 255, cv2.THRESH_BINARY)
        prepared_images.append(
            (f"threshold_{threshold}", cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR), True)
        )
    candidates: list[dict] = []
    for mode, prepared, only_rec in prepared_images:
        enlarged = cv2.resize(prepared, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
        job = tasker.post_recognition(
            JRecognitionType.OCR,
            JOCR(
                roi=(0, 0, enlarged.shape[1], enlarged.shape[0]),
                expected=[r"^\d+$"],
                threshold=0.05,
                only_rec=only_rec,
                replace=OCR_REPLACE,
            ),
            enlarged,
        ).wait()
        detail = job.get()
        recognition = detail.nodes[0].recognition if detail and detail.nodes else None
        if not recognition:
            continue
        for result in recognition.filtered_results:
            text = getattr(result, "text", "")
            if text.isascii() and text.isdecimal():
                candidates.append(
                    {
                        "text": text,
                        "score": float(getattr(result, "score", 0)),
                        "mode": mode,
                    }
                )
    # Three digits from the wide detector can include one icon stroke on the
    # left (e.g. beef 53 -> 153). Re-recognize a right-aligned strip only for
    # that exceptional shape instead of paying this cost for every item.
    if any(len(candidate["text"]) == 3 for candidate in candidates):
        narrow = cv2.resize(patch[42:96, 56:96], None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
        job = tasker.post_recognition(
            JRecognitionType.OCR,
            JOCR(
                roi=(0, 0, narrow.shape[1], narrow.shape[0]),
                expected=[r"^\d+$"],
                threshold=0.05,
                only_rec=False,
                replace=OCR_REPLACE,
            ),
            narrow,
        ).wait()
        detail = job.get()
        recognition = detail.nodes[0].recognition if detail and detail.nodes else None
        if recognition:
            for result in recognition.filtered_results:
                text = getattr(result, "text", "")
                if text.isascii() and text.isdecimal():
                    candidates.append(
                        {"text": text, "score": float(getattr(result, "score", 0)), "mode": "detect_right"}
                    )
    if not candidates:
        fallback = cv2.resize(patch[30:96, 30:96], None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
        job = tasker.post_recognition(
            JRecognitionType.OCR,
            JOCR(
                roi=(0, 0, fallback.shape[1], fallback.shape[0]),
                expected=[r"^\d+$"],
                threshold=0.05,
                only_rec=False,
                replace=OCR_REPLACE,
            ),
            fallback,
        ).wait()
        detail = job.get()
        recognition = detail.nodes[0].recognition if detail and detail.nodes else None
        if recognition:
            for result in recognition.filtered_results:
                text = getattr(result, "text", "")
                if text.isascii() and text.isdecimal():
                    candidates.append(
                        {"text": text, "score": float(getattr(result, "score", 0)), "mode": "fallback"}
                    )
        if not candidates:
            return None
    grouped: dict[str, list[dict]] = {}
    for candidate in candidates:
        grouped.setdefault(candidate["text"], []).append(candidate)
    right_detected = [
        candidate
        for candidate in candidates
        if candidate["mode"] == "detect_right" and candidate["score"] >= 0.5
    ]
    detected = [candidate for candidate in candidates if candidate["mode"].startswith("detect_")]
    right_aligned = [
        candidate
        for candidate in detected
        if candidate["mode"] == "detect_x48" and candidate["score"] >= 0.5
    ]
    strong_detected = [candidate for candidate in detected if candidate["score"] >= 0.8]
    preferred_detected = right_detected or right_aligned or strong_detected
    if right_detected:
        detector_text = max(right_detected, key=lambda item: item["score"])["text"]
        plausible = [pair for pair in grouped.items() if pair[0] == detector_text]
    elif preferred_detected:
        detector_text = max(preferred_detected, key=lambda item: item["score"])["text"]
        plausible = [
            pair
            for pair in grouped.items()
            if pair[0] == detector_text or detector_text in pair[0] or pair[0] in detector_text
        ]
    else:
        plausible = list(grouped.items())
    text, group = max(
        plausible,
        key=lambda pair: (len(pair[1]), len(pair[0]), max(item["score"] for item in pair[1])),
    )
    best = max(group, key=lambda item: item["score"])
    normalized_text = text.lstrip("0") or "0"
    return {"text": normalized_text, "score": best["score"], "candidates": candidates}


def scan_slots(
    page_name: str,
    image: np.ndarray,
    rows: tuple[int, ...],
    products: list[dict],
    threshold: float,
    tasker: Tasker,
    *,
    skip_first_row: bool = False,
) -> tuple[list[dict], list[dict]]:
    hits: list[dict] = []
    unknown: list[dict] = []
    for row_index, y in enumerate(rows):
        if skip_first_row and row_index == 0:
            continue
        for column_index, x in enumerate(CELL_X):
            patch = slot_patch(image, x, y)
            if not occupied(patch):
                continue
            search_x, search_y = x - 50, y - 64
            search = image[search_y : search_y + 104, search_x : search_x + 100]
            matches = []
            for product in products:
                result = cv2.matchTemplate(
                    cv2.cvtColor(search, cv2.COLOR_BGR2GRAY),
                    cv2.cvtColor(product["image"], cv2.COLOR_BGR2GRAY),
                    cv2.TM_CCOEFF_NORMED,
                )
                _, score, _, location = cv2.minMaxLoc(result)
                matches.append((float(score), location, product))
            score, location, product = max(matches, key=lambda match: match[0])
            actual_x = search_x + location[0] + 42
            actual_y = search_y + location[1] + 48
            actual_patch = slot_patch(image, actual_x, actual_y)
            quantity = local_ocr(tasker, actual_patch)
            record = {
                "page": page_name,
                "row": row_index,
                "column": column_index,
                "center": [actual_x, actual_y],
                "template_score": round(score, 6),
                "quantity": int(quantity["text"]) if quantity else None,
                "quantity_score": round(quantity["score"], 6) if quantity else None,
                "quantity_candidates": quantity["candidates"] if quantity else [],
            }
            if score < threshold:
                unknown.append(record)
                continue
            hits.append({**record, "id": product["id"], "name": product["name"]})
    return hits, unknown


def main() -> int:
    started = time.perf_counter()
    if decoded.enabled():
        data = decoded.snapshot()
        output = {**decoded.inventory(data), 'state_reader': 'decoded', 'generated_at_utc': data['capturedAt'],
                  'elapsed_seconds': round(time.perf_counter() - started, 3), 'background_input': False}
        RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
        RESULT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(output, ensure_ascii=False))
        return 0 if not output['unknown_slots'] else 2
    products, threshold = load_registry()
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
        controller.set_screenshot_target_short_side(720)
        if not controller.post_connection().wait().succeeded:
            raise RuntimeError("Maa Win32 connection failed")
        install_foreground_safe_inputs()
        if not resource.post_bundle(RESOURCE_DIR).wait().succeeded:
            raise RuntimeError("Maa resource loading failed")
        if not tasker.bind(resource, controller):
            raise RuntimeError("Maa tasker binding failed")
        install_foreground_safe_inputs()
        from order_template_click import click_template_node, template_hit
        _frame = screenshot(controller)
        if template_hit(_frame, "VerifyBackpackOpened") is None:
            if template_hit(_frame, "RecognizeBackpackIcon") is None:
                raise RuntimeError("backpack entry not recognized; no click")
            if not click_template_node(controller, "RecognizeBackpackIcon", _frame):
                raise RuntimeError("safe backpack open click failed")
            time.sleep(0.5)
            if template_hit(screenshot(controller), "VerifyBackpackOpened") is None:
                raise RuntimeError("backpack open not confirmed")
        post_click_normalized(controller, *CROP_TAB_CENTER).wait()
        time.sleep(SETTLE_SECONDS)

        # One downward swipe normalizes to top; one upward swipe reaches bottom
        # for the current 22-product category. No redundant stable-frame loops.
        post_swipe_normalized(controller, 360, 585, 360, 860, SWIPE_DURATION_MS).wait()
        time.sleep(SETTLE_SECONDS)
        top = screenshot(controller)
        post_swipe_normalized(controller, 360, 860, 360, 585, SWIPE_DURATION_MS).wait()
        time.sleep(SETTLE_SECONDS)
        bottom = screenshot(controller)

        post_swipe_normalized(controller, 360, 585, 360, 860, SWIPE_DURATION_MS).wait()
        top_hits, top_unknown = scan_slots("top", top, TOP_ROWS, products, threshold, tasker)
        bottom_hits, bottom_unknown = scan_slots(
            "bottom", bottom, BOTTOM_ROWS, products, threshold, tasker, skip_first_row=True
        )
        by_id = {item["id"]: item for item in top_hits + bottom_hits}
        elapsed = time.perf_counter() - started
        output = {
            "elapsed_seconds": round(elapsed, 3),
            "items": sorted(by_id.values(), key=lambda item: item["id"]),
            "unknown_slots": top_unknown + bottom_unknown,
            "background_input": True,
        }
        RESULT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(output, ensure_ascii=False))
        return 0 if not output["unknown_slots"] and all(item["quantity"] is not None for item in output["items"]) else 2
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

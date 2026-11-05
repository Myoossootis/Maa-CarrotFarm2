"""Configure an audited employee through Maa Win32 background input.

Only configured, unlocked products are supported. Formal Maa workers use
decoded saved plans and progress; navigation and input retain the original
Maa methods. Standalone legacy CLI keeps its OCR checks.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

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
    from .capture_current_frame_with_maa import find_window
    from .maa_frame_normalize import normalize_frame, post_click_normalized, post_swipe_normalized
    from .employee_capabilities import load_capabilities, normalize_product_name, slot_can_produce
except ImportError:
    from capture_current_frame_with_maa import find_window
    from maa_frame_normalize import normalize_frame, post_click_normalized, post_swipe_normalized
    from employee_capabilities import load_capabilities, normalize_product_name, slot_can_produce


ROOT = Path(__file__).resolve().parents[1]
IMAGES = ROOT / "assets/resource/image/production"
NAV_IMAGES = ROOT / "assets/resource/image/navigation"
RESOURCE = ROOT / "assets/resource"
CAPABILITIES = ROOT / "assets/catalog/employee_capabilities.json"
SETTLE = 0.25
MAX_QUANTITY = 50


class CollectionUnconfirmedError(RuntimeError):
    """Input may have been delivered; stop instead of retrying a harvest."""

# Product icons confirmed in 720 x 1358 Maa FramePool work-plan captures.
CROPS = {
    "番茄": ("crop_tomato.png", 128, 580),
    "小麦": ("crop_wheat.png", 245, 580),
    "玉米": ("crop_corn.png", 360, 580),
    "甘蔗": ("crop_sugarcane.png", 475, 580),
    "土豆": ("crop_potato.png", 590, 580),
    "玫瑰": ("crop_rose.png", 128, 710),
    "胡萝卜": ("crop_carrot.png", 245, 710),
    "牛肉": ("crop_beef.png", 128, 580),
    "鸡蛋": ("crop_egg.png", 245, 580),
    "猪肉": ("crop_pork.png", 360, 580),
    "羊肉": ("crop_mutton.png", 475, 580),
    "牛奶": ("crop_milk.png", 590, 580),
    "苹果": ("crop_apple.png", 128, 580),
    "黄桃": ("crop_peach.png", 245, 580),
    "柠檬": ("crop_lemon.png", 360, 580),
    "椰子": ("crop_coconut.png", 475, 580),
}

SLOTS = {
    slot["id"]: slot
    for slot in json.loads(CAPABILITIES.read_text(encoding="utf-8"))["slots"]
}


def screenshot(controller: Win32Controller):
    job = controller.post_screencap().wait()
    if not job.succeeded:
        raise RuntimeError("Maa screenshot failed")
    frame = job.get()
    # Win32 client chrome can add a top letterbox (e.g. 720x1410). Crop back to
    # the audited 720x1358 coordinate space used by templates and click targets.
    return normalize_frame(frame)


def locate(
    frame,
    template_name: str,
    roi: tuple[int, int, int, int],
    image_dir: Path | None = None,
):
    directory = IMAGES if image_dir is None else image_dir
    template = cv2.imread(str(directory / template_name))
    if template is None:
        raise RuntimeError(f"missing template: {directory / template_name}")
    x, y, width, height = roi
    patch = frame[y : y + height, x : x + width]
    if patch.shape[0] < template.shape[0] or patch.shape[1] < template.shape[1]:
        raise RuntimeError(f"ROI too small for {template_name}")
    result = cv2.matchTemplate(patch, template, cv2.TM_CCOEFF_NORMED)
    _, score, _, location = cv2.minMaxLoc(result)
    return float(score), (x + location[0], y + location[1])


def match(
    frame,
    template_name: str,
    roi: tuple[int, int, int, int],
    image_dir: Path | None = None,
) -> float:
    return locate(frame, template_name, roi, image_dir=image_dir)[0]


def locate_nav(frame, template_name: str, roi: tuple[int, int, int, int]):
    return locate(frame, template_name, roi, image_dir=NAV_IMAGES)


def match_nav(frame, template_name: str, roi: tuple[int, int, int, int]) -> float:
    return locate_nav(frame, template_name, roi)[0]


def slot_match(frame, slot_id: str):
    score, (x, y) = locate(frame, f"slot_{slot_id}.png", (130, 100, 230, 1080))
    if score < 0.88:
        return 0.0, (x, y)
    suffix = match(frame, f"slot_{slot_id}_suffix.png", (x + 60, y, 45, 44))
    return min(score, suffix), (x, y)


def click(controller: Win32Controller, x: int, y: int) -> None:
    if not post_click_normalized(controller, x, y).wait().succeeded:
        raise RuntimeError(f"background click failed at ({x}, {y})")
    time.sleep(SETTLE)


def _click_close_nav(controller: Win32Controller, x: int, y: int) -> None:
    click(controller, x, y)


def close_plan_dialog(controller: Win32Controller) -> None:
    """Click dismiss once when work-plan title is visible; trust the click."""
    frame = screenshot(controller)
    if match(frame, "work_plan_title.png", (235, 220, 270, 120)) < 0.75:
        return
    # Footer blank area; do not multi-frame score-gate the list return.
    click(controller, 360, 1220)
    time.sleep(0.35)


def close_employee_menu(controller: Win32Controller) -> None:
    """Close only recognized employee/phone pages and verify the map UI."""
    failure_dir = ROOT / ".tmp"
    failure_path = failure_dir / "close_employee_menu_failure.png"
    for attempt in range(24):
        frame = screenshot(controller)
        # 1) work-plan dialog
        if match(frame, "work_plan_title.png", (235, 220, 270, 120)) >= 0.75:
            close_plan_dialog(controller)
            continue
        # 1a) empty-plan schedule prompt
        if match(frame, 'empty_plan_prompt_text.png', (80, 490, 570, 130)) >= .80:
            _click_close_nav(controller, 230, 686)  # 取消
            time.sleep(0.5)
            continue
        # 1b) field assignment page (上阵员工 -> 农田N with 工作计划)
        if match(frame, "field_work_plan_button.png", (450, 360, 250, 100)) >= 0.70:
            _click_close_nav(controller, 57, 1280)
            time.sleep(1.0)
            continue
        # 2) employee list
        if any(slot_match(frame, slot_id)[0] >= 0.88 for slot_id in SLOTS) or max(
            match(frame, "work_plan_button.png", (115, 150, 240, 1010)),
            match(frame, "collect_supplies_button.png", (115, 150, 240, 1010)),
        ) >= 0.75:
            _click_close_nav(controller, 57, 1280)
            time.sleep(1.0)
            continue
        # 3) phone employee_config tile
        if match(frame, "employee_config.png", (270, 430, 190, 210)) >= 0.74:
            _click_close_nav(controller, 365, 1230)
            time.sleep(1.0)
            continue
        # 4) generic popup close (navigation template)
        popup_score, popup_xy = locate_nav(frame, "popup_close.png", (0, 1190, 130, 150))
        if popup_score >= 0.78:
            px, py = popup_xy
            # click near template center; fallback fixed coord if needed
            _click_close_nav(controller, px + 32, py + 38)
            time.sleep(1.0)
            continue
        # 5) map anchors: phone_icon (production) or backpack (navigation)
        phone_score = match(frame, "phone_icon.png", (610, 1210, 105, 140))
        backpack_score = match_nav(frame, "backpack.png", (600, 1150, 120, 120))
        on_map = phone_score >= 0.68 or backpack_score >= 0.70
        if on_map:
            plan_open = match(frame, "work_plan_title.png", (235, 220, 270, 120)) >= 0.75
            list_open = any(slot_match(frame, slot_id)[0] >= 0.88 for slot_id in SLOTS) or max(
                match(frame, "work_plan_button.png", (115, 150, 240, 1010)),
                match(frame, "collect_supplies_button.png", (115, 150, 240, 1010)),
            ) >= 0.75
            config_open = match(frame, "employee_config.png", (270, 430, 190, 210)) >= 0.74
            if not (plan_open or list_open or config_open):
                return
        # Unknown overlays never authorize a blank recovery click on the map.
        time.sleep(0.3)
    # dump diagnostic frame + scores before failing
    frame = screenshot(controller)
    failure_dir.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(failure_path), frame)
    scores = {
        "work_plan_title": match(frame, "work_plan_title.png", (235, 220, 270, 120)),
        "work_plan_button": match(frame, "work_plan_button.png", (115, 150, 240, 1010)),
        "collect_supplies_button": match(frame, "collect_supplies_button.png", (115, 150, 240, 1010)),
        "employee_config": match(frame, "employee_config.png", (270, 430, 190, 210)),
        "popup_close": match_nav(frame, "popup_close.png", (0, 1190, 130, 150)),
        "phone_icon": match(frame, "phone_icon.png", (610, 1210, 105, 140)),
        "backpack": match_nav(frame, "backpack.png", (600, 1150, 120, 120)),
    }
    for slot_id in SLOTS:
        scores[f"slot_{slot_id}"] = slot_match(frame, slot_id)[0]
    print(f"close_employee_menu failure frame: {failure_path}", flush=True)
    print(f"close_employee_menu template scores: {scores}", flush=True)
    # Soft success: map anchors present and no clear employee overlay.
    if scores["phone_icon"] >= 0.68 or scores["backpack"] >= 0.70:
        overlay = (
            scores["work_plan_title"] >= 0.75
            or scores["employee_config"] >= 0.74
            or max(scores["work_plan_button"], scores["collect_supplies_button"]) >= 0.75
            or any(scores[f"slot_{slot_id}"] >= 0.88 for slot_id in SLOTS)
        )
        if not overlay:
            print("close_employee_menu: soft-accept farm map after retries", flush=True)
            return
    raise RuntimeError("farm map was not verified after closing the employee menu")


def leave_employee_ui_if_visible(controller: Win32Controller) -> None:
    frame = screenshot(controller)
    on_employee_ui = (
        any(slot_match(frame, slot_id)[0] >= 0.88 for slot_id in SLOTS)
        or match(frame, "work_plan_title.png", (235, 220, 270, 120)) >= 0.75
        or match(frame, "employee_config.png", (270, 430, 190, 210)) >= 0.74
        or match(frame, "field_work_plan_button.png", (450, 360, 250, 100)) >= 0.70
        or _field_work_plan_button_visible(frame)
        or _schedule_plan_confirm_visible(frame)
    )
    if on_employee_ui:
        close_employee_menu(controller)


def capacity(tasker: Tasker, frame) -> tuple[int, int] | None:
    if decoded.enabled():
        raise decoded.DecodeError('Image-only capacity read is disabled; an explicit decoded employee slot is required')
    return _capacity_ocr(tasker, frame)


def editor_capacity(tasker, frame, slot_id):
    """Read the unsaved UI capacity only after an explicit idle slot check."""
    if slot_id not in SLOTS or not decoded.enabled() or decoded.assignment_blocker(slot_id):
        raise decoded.DecodeError('当前岗位不明或不空闲，不能读取排产容量')
    if match(frame, 'work_plan_title.png', (235, 220, 270, 120)) < .75:
        raise decoded.DecodeError('未识别空计划编辑器，不猜容量')
    return _capacity_ocr(tasker, frame)


def confirm_draft_capacity(controller, tasker, slot_id, total, limit):
    """Correct only a positively observed unsaved deficit; never repeat save."""
    for _ in range(4):
        time.sleep(.6)
        frame = screenshot(controller)
        current = editor_capacity(tasker, frame, slot_id)
        if current is None or current[1] != limit or not 0 <= current[0] <= total:
            raise RuntimeError('草稿数量或容量未确认，禁止保存')
        if current[0] == total:
            time.sleep(.3)
            if editor_capacity(tasker, screenshot(controller), slot_id) != current:
                raise RuntimeError('草稿数量不稳定，禁止保存')
            return frame
        for _ in range(total - current[0]):
            click(controller, 547, 953)
    raise RuntimeError('草稿补齐未确认，禁止保存；保留编辑意图')


def confirm_empty_editor_capacity(controller, tasker, slot_id, limit):
    """Opening animation/OCR uncertainty may be reread, never clicked through."""
    previous = None
    for _ in range(6):
        frame = screenshot(controller)
        observed = editor_capacity(tasker, frame, slot_id)
        if observed is not None and observed == previous:
            if observed != (0, limit):
                raise RuntimeError(f'当前员工容量或草稿不一致：{observed}，需要(0, {limit})，未编辑或保存')
            return frame
        previous = observed
        time.sleep(.3)
    raise RuntimeError('当前员工空计划容量未稳定确认，未编辑或保存')


def _capacity_ocr(tasker, frame):
    patch = frame[410:443, 289:378]
    image = cv2.resize(patch, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    job = tasker.post_recognition(
        JRecognitionType.OCR,
        JOCR(
            roi=(0, 0, image.shape[1], image.shape[0]),
            expected=[r"\d+\s*/\s*\d+"],
            threshold=0.02,
            replace=[["O", "0"], ["o", "0"], ["l", "1"], ["I", "1"]],
        ),
        image,
    ).wait()
    detail = job.get()
    recognition = detail.nodes[0].recognition if detail and detail.nodes else None
    if recognition is None:
        return None
    for result in sorted(
        recognition.filtered_results,
        key=lambda item: float(getattr(item, "score", 0)),
        reverse=True,
    ):
        value = re.sub(r"\s+", "", getattr(result, "text", ""))
        if re.fullmatch(r"\d+/\d+", value):
            count, limit = map(int, value.split("/"))
            return count, limit
    return None


def _progress_image_variants(patch, include_binary: bool = False):
    """Upscaled color variants; binary/OTSU only as a second pass."""
    variants = []
    for fx in (3, 4):
        image = cv2.resize(patch, None, fx=fx, fy=fx, interpolation=cv2.INTER_CUBIC)
        variants.append(image)
        if include_binary:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            _, binary = cv2.threshold(gray, 155, 255, cv2.THRESH_BINARY)
            _, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            variants.append(cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR))
            variants.append(cv2.cvtColor(otsu, cv2.COLOR_GRAY2BGR))
    return variants


def _rank_progress_candidates(
    candidates: list[tuple[int, int, float]],
    expected_planned: int | None,
    slot_capacity: int | None,
) -> tuple[int, int] | None:
    """Pick produced/planned from OCR hits.

    Prefer the expected plan denominator, then left>=right complete bars.
    Reject other denominators, including slot-capacity badges for smaller plans.
    """
    if not candidates:
        return None
    filtered: list[tuple[int, int, float]] = []
    for produced, planned, score in candidates:
        if planned < 1 or produced > planned + 5:
            continue
        if expected_planned is not None and planned != expected_planned:
            continue
        filtered.append((produced, planned, score))
    if not filtered:
        return None

    def sort_key(item: tuple[int, int, float]):
        produced, planned, score = item
        matches_expected = expected_planned is not None and planned == expected_planned
        complete = produced >= planned
        meets_required = expected_planned is not None and produced >= expected_planned
        planned_distance = (
            abs(planned - expected_planned) if expected_planned is not None else 0
        )
        return (
            matches_expected,
            complete,
            meets_required,
            -planned_distance,
            produced,
            score,
        )

    filtered.sort(key=sort_key, reverse=True)
    best = filtered[0]
    return best[0], best[1]


def list_progress(
    tasker: Tasker,
    frame,
    slot_id: str,
    expected_planned: int | None = None,
    debug_dump_dir: Path | None = None,
) -> tuple[int, int] | None:
    """Read produced/planned count from an employee card, not plan capacity.

    ROI includes dy=170..220 (live 22/22 sits near dy=175). Prefers readings
    that match expected_planned and left>=right so a stuck partial OCR (e.g.
    4/27) does not beat a real 27/27, and a capacity badge 50/50 does not beat
    a 22/22 plan when expected_planned is set.
    """
    if decoded.enabled():
        return decoded.progress(slot_id, expected_planned)
    score, (_, header_y) = slot_match(frame, slot_id)
    if score < 0.88 or not 100 <= header_y <= 1000:
        return None
    candidates: list[tuple[int, int, float]] = []
    dump_i = 0

    def _ocr_patch(patch, include_binary: bool) -> None:
        nonlocal dump_i
        if debug_dump_dir is not None:
            debug_dump_dir.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(
                str(debug_dump_dir / f"{slot_id}_dump_{dump_i}.png"),
                patch,
            )
            dump_i += 1
        for image in _progress_image_variants(patch, include_binary=include_binary):
            for only_rec in ((False,) if not include_binary else (False, True)):
                job = tasker.post_recognition(
                    JRecognitionType.OCR,
                    JOCR(
                        roi=(0, 0, image.shape[1], image.shape[0]),
                        expected=[r"\d+\s*/\s*\d+"],
                        threshold=0.02,
                        only_rec=only_rec,
                        replace=[["O", "0"], ["o", "0"], ["l", "1"], ["I", "1"]],
                    ),
                    image,
                ).wait()
                detail = job.get()
                recognition = (
                    detail.nodes[0].recognition if detail and detail.nodes else None
                )
                if recognition is None:
                    continue
                for result in recognition.filtered_results:
                    value = re.sub(r"\s+", "", getattr(result, "text", ""))
                    match_frac = re.search(r"(\d+)/(\d+)", value)
                    if not match_frac:
                        continue
                    produced, planned = map(int, match_frac.groups())
                    candidates.append(
                        (produced, planned, float(getattr(result, "score", 0)))
                    )

    # Sweet spot from offline probes: dy~175-190, left strip x0<=40.
    roi_grid = (
        (170, 0, 160),
        (180, 0, 160),
        (190, 20, 140),
        (200, 20, 180),
    )
    for dy, x0, x1 in roi_grid:
        patch = frame[header_y + dy : header_y + dy + 70, x0:x1]
        if patch.size == 0:
            continue
        _ocr_patch(patch, include_binary=False)
        ranked = _rank_progress_candidates(
            candidates,
            expected_planned,
            int(SLOTS[slot_id]["capacity"]) if slot_id in SLOTS else None,
        )
        if ranked is not None:
            produced, planned = ranked
            if expected_planned is not None and planned == expected_planned and (
                produced >= planned or produced >= expected_planned
            ):
                return ranked
            if expected_planned is None and produced >= planned:
                return ranked

    if _rank_progress_candidates(candidates, expected_planned, None) is None:
        for dy, x0, x1 in roi_grid:
            patch = frame[header_y + dy : header_y + dy + 70, x0:x1]
            if patch.size == 0:
                continue
            _ocr_patch(patch, include_binary=True)

    slot_capacity = int(SLOTS[slot_id]["capacity"]) if slot_id in SLOTS else None
    return _rank_progress_candidates(candidates, expected_planned, slot_capacity)


def icon_quantity(tasker: Tasker, frame, crop: str) -> int | None:
    """Read the small per-product counter under a work-plan icon."""
    if decoded.enabled():
        raise decoded.DecodeError('Unsaved editor counts are not stored; verify the saved decoded plan instead')
    _, x, y = CROPS[crop]
    # Current dialogs draw the digit above the older baseline; retain its top.
    patch = frame[y + 30 : y + 82, x - 24 : x + 25]
    image = cv2.resize(patch, None, fx=5, fy=5, interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, 155, 255, cv2.THRESH_BINARY)
    for candidate, only_rec in (
        (image, False),
        (image, True),
        (cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR), True),
    ):
        job = tasker.post_recognition(
            JRecognitionType.OCR,
            JOCR(
                roi=(0, 0, candidate.shape[1], candidate.shape[0]),
                expected=[r"^\d+$"],
                threshold=0.02,
                only_rec=only_rec,
                replace=[["O", "0"], ["o", "0"], ["l", "1"], ["I", "1"]],
            ),
            candidate,
        ).wait()
        detail = job.get()
        recognition = detail.nodes[0].recognition if detail and detail.nodes else None
        if recognition is None:
            continue
        values = [
            (float(getattr(result, "score", 0)), int(result.text))
            for result in recognition.filtered_results
            if re.fullmatch(r"\d+", getattr(result, "text", ""))
        ]
        if values:
            return max(values)[1]
    return None



def _phone_menu_open(frame) -> bool:
    """True when the phone app grid shows farm staff or staff-config tiles."""
    # Live 720x1348 places tiles near y~442-452; keep ROI top at 380+.
    if match(frame, "employee_config.png", (250, 380, 240, 280)) >= 0.74:
        return True
    staff = IMAGES / "employee_staff.png"
    if staff.exists() and match(frame, "employee_staff.png", (50, 380, 240, 280)) >= 0.74:
        return True
    return False


def open_phone(controller: Win32Controller):
    frame = screenshot(controller)
    if _phone_menu_open(frame):
        return frame
    # Peel known employee/assignment overlays before requiring the map phone icon.
    for _ in range(6):
        frame = screenshot(controller)
        if _phone_menu_open(frame):
            return frame
        if match(frame, "phone_icon.png", (610, 1210, 105, 140)) >= 0.68:
            break
        if match(frame, "work_plan_title.png", (235, 220, 270, 120)) >= 0.75:
            close_plan_dialog(controller)
            continue
        if _schedule_plan_confirm_visible(frame):
            click(controller, 230, 686)  # 取消
            time.sleep(0.5)
            continue
        if match(frame, "field_work_plan_button.png", (450, 360, 250, 100)) >= 0.70 or _field_work_plan_button_visible(frame):
            click(controller, 57, 1280)
            time.sleep(0.7)
            continue
        if any(slot_match(frame, slot_id)[0] >= 0.88 for slot_id in SLOTS):
            click(controller, 57, 1280)
            time.sleep(0.8)
            continue
        # blank / popup recovery
        click(controller, 680, 1250)
        time.sleep(0.4)
        frame = screenshot(controller)
        if match(frame, "phone_icon.png", (610, 1210, 105, 140)) >= 0.68:
            break
        click(controller, 57, 1280)
        time.sleep(0.5)
    frame = screenshot(controller)
    if match(frame, "phone_icon.png", (610, 1210, 105, 140)) < 0.68:
        raise RuntimeError("farm phone icon not visible after one popup recovery attempt")
    for _ in range(2):
        click(controller, 663, 1285)
        for _ in range(8):
            frame = screenshot(controller)
            if _phone_menu_open(frame):
                return frame
            time.sleep(0.3)
        if match(frame, "phone_icon.png", (610, 1210, 105, 140)) < 0.68:
            break
    raise RuntimeError("employee configuration tile not visible after opening phone")


# Third bottom tab. Selected hat is orange plus the 农场就职 label; unselected is gray.
HAT_TAB_ROI = (380, 1100, 330, 258)


def _hat_tab_hit(frame, template_name: str) -> tuple[float, tuple[int, int]] | None:
    score, (x, y) = locate(frame, template_name, HAT_TAB_ROI)
    template = cv2.imread(str(IMAGES / template_name))
    if template is None:
        raise RuntimeError(f"missing template: {template_name}")
    center = (x + template.shape[1] // 2, y + template.shape[0] // 2)
    return float(score), center


def employee_page_state(frame) -> str:
    """Classify mutually exclusive employee pages from positive evidence."""
    if match(frame, "work_plan_title.png", (235, 220, 270, 120)) >= 0.75:
        return "work_plan"
    if match(frame, "empty_plan_prompt_text.png", (80, 490, 570, 130)) >= 0.80:
        return "empty_plan_prompt"
    if match(frame, "employee_assignment_prompt.png", (110, 420, 520, 130)) >= 0.80:
        return "slot_detail"
    selected, _ = _hat_tab_hit(frame, "farm_hat_selected.png")
    has_slot = any(slot_match(frame, slot_id)[0] >= 0.88 for slot_id in SLOTS)
    if selected >= 0.85 and has_slot:
        return "employee_list"
    return "unknown"


def _farm_worker_list_visible(frame) -> bool:
    return employee_page_state(frame) == "employee_list"


def return_to_employee_list(controller: Win32Controller):
    """Leave one work-plan/detail layer and prove the employee list is back."""
    frame = screenshot(controller)
    state = employee_page_state(frame)
    if state == "employee_list":
        time.sleep(0.3)
        frame = screenshot(controller)
        if employee_page_state(frame) == "employee_list":
            return frame, 0
        raise RuntimeError("employee list state not confirmed; no click")
    clicks = 0
    if state == "work_plan":
        close_plan_dialog(controller)
        clicks += 1
        frame = screenshot(controller)
        state = employee_page_state(frame)
    if state == "empty_plan_prompt":
        click(controller, 230, 686)
        clicks += 1
    elif state == "slot_detail":
        click(controller, 57, 1280)
        clicks += 1
    else:
        raise RuntimeError(f"cannot return to employee list from {state}; no click")
    cancelled_prompt = state == "empty_plan_prompt"
    for _ in range(12):
        frame = screenshot(controller)
        state = employee_page_state(frame)
        if state == "employee_list":
            time.sleep(0.3)
            confirmed = screenshot(controller)
            if employee_page_state(confirmed) == "employee_list":
                return confirmed, clicks
        if state == "empty_plan_prompt" and not cancelled_prompt:
            click(controller, 230, 686)
            clicks += 1
            cancelled_prompt = True
        time.sleep(0.3)
    raise RuntimeError("employee list not confirmed after one back click")


def _open_farm_hat_tab(controller: Win32Controller, frame):
    """Click the gray hat only. An orange hat means 农场就职 is already open."""
    selected, _ = _hat_tab_hit(frame, "farm_hat_selected.png")
    if _farm_worker_list_visible(frame):
        return frame
    if selected >= 0.85:
        for _ in range(12):
            frame = screenshot(controller)
            if _farm_worker_list_visible(frame):
                return frame
            time.sleep(0.4)
        raise RuntimeError("orange farm tab visible but employee list not recognized")
    score, center = _hat_tab_hit(frame, "farm_hat_unselected.png")
    if score < 0.85:
        raise RuntimeError(f"farm hat tab not recognized (gray={score:.3f}, orange={selected:.3f})")
    click(controller, center[0], center[1])
    for _ in range(12):
        frame = screenshot(controller)
        if _farm_worker_list_visible(frame):
            return frame
        time.sleep(0.4)
    raise RuntimeError("employee list not recognized")


def open_employee_list(controller: Win32Controller):
    for _ in range(6):
        frame = screenshot(controller)
        state = employee_page_state(frame)
        if state == "employee_list":
            return frame
        if state in {"slot_detail", "work_plan"}:
            return return_to_employee_list(controller)[0]
        time.sleep(0.3)
    open_phone(controller)
    # 员工配置 opens the assignment UI. It sometimes lands directly on 农场就职.
    # Bottom tabs: bowtie, paper, hat. The hat is gray until that tab is selected.
    frame = screenshot(controller)
    score, (x, y) = locate(frame, "employee_config.png", (250, 380, 240, 280))
    if score < 0.74:
        raise RuntimeError("employee configuration tile not visible on phone menu")
    template = cv2.imread(str(IMAGES / "employee_config.png"))
    click(controller, x + template.shape[1] // 2, y + template.shape[0] // 2)
    time.sleep(1.0)
    frame = screenshot(controller)
    if _farm_worker_list_visible(frame):
        return frame
    return _open_farm_hat_tab(controller, frame)


def visible_slot_action(frame, slot_id: str) -> tuple[int, str] | None:
    score, (_, header_y) = slot_match(frame, slot_id)
    button_y = header_y + 292
    if score < 0.88 or not 350 <= button_y <= 1120:
        return None
    # Left yellow button = 收获物资; right button = 上阵员工 / work-plan entry.
    collect_roi = (115, button_y - 60, 240, 120)
    plan_roi = (450, button_y - 60, 230, 120)
    plan_score = match(frame, "work_plan_button.png", plan_roi)
    collect_score = match(frame, "collect_supplies_button.png", collect_roi)
    # Collect must win when both buttons are visible (finished work).
    if collect_score >= 0.75:
        return button_y, "collect_supplies"
    if plan_score >= 0.70:
        return button_y, "work_plan"
    return None


def find_slot_action(controller: Win32Controller, slot_id: str) -> tuple[int, str]:
    # A delayed opening animation must not leave a modal dialog over the list
    # while scrolling or locating another employee's action button.
    close_plan_dialog(controller)
    frame = open_employee_list(controller)
    visible = visible_slot_action(frame, slot_id)
    if visible is not None:
        return visible
    # All farm, ranch, and orchard work plans share the rightmost hat tab.
    # The left tab is the employee roster, not a ranch category.
    frame = _open_farm_hat_tab(controller, screenshot(controller))
    # Normalize the list to the top before scanning. Swiping from x=85 avoids
    # employee portraits and buttons, which can open other pages.
    for _ in range(8):
        # Stop returning to the top once it is visible; do not swipe eight times blindly.
        frame = screenshot(controller)
        visible = visible_slot_action(frame, slot_id)
        if visible is not None:
            return visible
        if slot_match(frame, 'field_1')[0] >= 0.88:
            break
        if not post_swipe_normalized(controller, 85, 340, 85, 1070, 550).wait().succeeded:
            raise RuntimeError('employee list scroll failed')
    time.sleep(0.6)
    for _ in range(24):
        frame = screenshot(controller)
        visible = visible_slot_action(frame, slot_id)
        if visible is not None:
            return visible
        if not post_swipe_normalized(controller, 85, 900, 85, 690, 550).wait().succeeded:
            raise RuntimeError("employee list scroll failed")
        time.sleep(0.45)
    raise RuntimeError(f"employee slot not found or button not visible: {slot_id}")


def find_slot(controller: Win32Controller, slot_id: str) -> int:
    button_y, state = find_slot_action(controller, slot_id)
    if state != "work_plan":
        raise RuntimeError(f"{SLOTS[slot_id]['label']} has completed supplies to collect")
    return button_y



def plan_cleared_on_list(frame, slot_id: str) -> dict:
    """Positive empty-plan evidence on the employee list card (no dialog open).

    Optional diagnostic helper. Collect path trusts the harvest click and does
    not require this score gate.
    """
    if decoded.enabled():
        return {'cleared': decoded.is_idle(slot_id), 'reason': 'decoded_plan_and_held_products', 'data_source': 'decoded'}
    score, (_, header_y) = slot_match(frame, slot_id)
    result = {
        "cleared": False,
        "slot_score": round(float(score), 3),
        "header_y": int(header_y),
        "no_work_plan": None,
        "visible": None,
        "reason": "slot_not_locked",
    }
    if score < 0.88 or not 100 <= header_y <= 1000:
        return result
    no_plan = float(match(frame, "no_work_plan.png", (100, header_y + 125, 300, 115)))
    visible = visible_slot_action(frame, slot_id)
    result["no_work_plan"] = round(no_plan, 3)
    result["visible"] = None if visible is None else visible[1]
    if visible is not None and visible[1] == "collect_supplies":
        result["reason"] = "collect_still_visible"
        return result
    if no_plan >= 0.75 and (visible is None or visible[1] == "work_plan"):
        result["cleared"] = True
        result["reason"] = "no_work_plan"
        return result
    result["reason"] = "empty_plan_not_confirmed"
    return result


def collect_finished_slot(
    controller: Win32Controller,
    slot_id: str,
    tasker: Tasker | None = None,
    required_progress: int | None = None,
    *,
    before_click=None,
) -> dict:
    """Click one stable, freshly located button; decoded mode confirms receipt."""
    if tasker is None or required_progress is None or not 1 <= required_progress <= SLOTS[slot_id]["capacity"]:
        raise ValueError("verified collection requires a tasker and positive target within capacity")
    button_y, state = find_slot_action(controller, slot_id)
    if state != "collect_supplies":
        progress = None
        if tasker is not None:
            progress = list_progress(
                tasker,
                screenshot(controller),
                slot_id,
                expected_planned=required_progress,
            )
        return {
            "slot": SLOTS[slot_id]["label"],
            "collected": False,
            "progress": progress,
            "reason": "collect_button_not_ready",
        }
    frame = screenshot(controller)
    visible = visible_slot_action(frame, slot_id)
    if visible is None or visible[1] != "collect_supplies":
        return {
            "slot": SLOTS[slot_id]["label"],
            "collected": False,
            "progress": None,
            "reason": "collect_button_not_ready",
        }
    button_y = visible[0]
    observed = list_progress(tasker, frame, slot_id, expected_planned=required_progress)
    if decoded.enabled() and observed is not None and observed[0] >= required_progress:
        time.sleep(.25)
        observed = list_progress(tasker, frame, slot_id, expected_planned=required_progress)
    if observed is None or observed[1] != required_progress or observed[0] < required_progress:
        return {
            "slot": SLOTS[slot_id]["label"],
            "collected": False,
            "progress": observed,
            "reason": "target_not_verified",
        }
    before = None
    if decoded.enabled():
        before = decoded.snapshot()
        if decoded.progress(slot_id, required_progress, before) != (required_progress, required_progress):
            raise decoded.DecodeError('Collection target changed before click: ' + slot_id)
        if not decoded.plan(slot_id, before)['heldProducts']:
            raise decoded.DecodeError('No held products before collection: ' + slot_id)
        # Decoding takes time; list inertia/loading can move the old row meanwhile.
        # Reacquire the actual button center on two stable frames after decoding.
        previous = None
        for _ in range(5):
            current = screenshot(controller)
            action = visible_slot_action(current, slot_id)
            center = None
            if action is not None and action[1] == 'collect_supplies':
                score, (x, y) = locate(current, 'collect_supplies_button.png', (115, action[0] - 60, 240, 120))
                if score >= .75:
                    image = cv2.imread(str(IMAGES / 'collect_supplies_button.png'))
                    center = (x + image.shape[1] // 2, y + image.shape[0] // 2)
            if center and previous and max(abs(a - b) for a, b in zip(center, previous)) <= 3:
                break
            previous = center
            time.sleep(.2)
        else:
            return dict(slot=SLOTS[slot_id]['label'], collected=False, progress=observed,
                        reason='collect_button_not_stable')
    else:
        center = (230, button_y)
    started = time.monotonic()
    if before_click is not None:
        if before is None:
            raise RuntimeError('Durable collection intent requires decoded readback')
        before_click(before)
    try:
        click(controller, *center)
        clearance = decoded.verify_collection(slot_id, before) if before is not None else {
            'cleared': True, 'reason': 'trust_collect_click'}
    except RuntimeError as error:
        raise CollectionUnconfirmedError(
            f"{SLOTS[slot_id]['label']} collect click failed; no automatic retry: {error}"
        ) from error
    time.sleep(0.25)
    return {
        "slot": SLOTS[slot_id]["label"],
        "collected": True,
        "progress": list(observed),
        "plan_cleared": True,
        "clearance": clearance,
        "confirmation_seconds": round(time.monotonic() - started, 3),
    }


def _schedule_plan_confirm_visible(frame) -> bool:
    """Yellow 确认 on the empty-plan schedule prompt."""
    patch = frame[620:750, 400:600]
    if patch.size == 0:
        return False
    b, g, r = [float(patch[:, :, i].mean()) for i in range(3)]
    return r > 170 and g > 130 and b < 110 and (r - b) > 80


def open_plan(controller: Win32Controller, slot_id: str):
    """Open a work plan directly from the list's blue 工作计划 button."""
    # Supplies may be partial. Looking at a plan must never harvest or clear it.
    try:
        visible = find_slot_action(controller, slot_id)
    except RuntimeError:
        visible = None
    if visible is not None and visible[1] == "collect_supplies":
        raise RuntimeError(f"{SLOTS[slot_id]['label']} has uncollected supplies; refusing to open or collect")
    button_y = find_slot(controller, slot_id)
    # Left blue button is 工作计划. Green 上阵员工 must never be used here.
    click(controller, 230, button_y)
    for _ in range(16):
        frame = screenshot(controller)
        if employee_page_state(frame) == "work_plan":
            return frame, button_y
        time.sleep(0.25)
    raise RuntimeError(f"work-plan dialog not recognized for {slot_id}")


def _field_work_plan_button_visible(frame) -> bool:
    """Heuristic: yellow 工作计划 chip on the field assignment header."""
    patch = frame[385:435, 480:670]
    if patch.size == 0:
        return False
    b, g, r = [float(patch[:, :, i].mean()) for i in range(3)]
    return r > 160 and g > 120 and b < 100 and (r - b) > 80


def reset_plan(controller: Win32Controller, tasker: Tasker, slot_id: str):
    frame, button_y = open_plan(controller, slot_id)
    score = match(frame, "rearrange_button.png", (150, 1010, 410, 150))
    if score < 0.75:
        raise RuntimeError("no active plan or rearrange button not recognized")
    click(controller, 360, 1090)
    frame = screenshot(controller)
    if decoded.enabled():
        decoded.verify_plan(slot_id, {}, repeat=None)
    elif capacity(tasker, frame) != (0, SLOTS[slot_id]["capacity"]):
        raise RuntimeError("plan reset did not clear employee capacity")
    click(controller, 57, 1280)
    time.sleep(0.5)
    # Dismiss empty-plan prompt if it appeared after rearrange.
    frame = screenshot(controller)
    if _schedule_plan_confirm_visible(frame):
        click(controller, 230, 686)  # 取消
        time.sleep(0.5)
        click(controller, 57, 1280)
        time.sleep(0.5)
        frame = screenshot(controller)
    header_y = button_y - 292
    no_plan = match(frame, "no_work_plan.png", (100, header_y + 125, 300, 115))
    # Accept either explicit empty text or absence of collect/progress when score is soft.
    if no_plan < 0.60 and match(frame, "collect_supplies_button.png", (115, button_y - 60, 240, 120)) >= 0.75:
        raise RuntimeError("employee list still shows a work plan after reset")
    return {"slot": SLOTS[slot_id]["label"], "reset": True, "no_work_plan_score": round(float(no_plan), 3)}



REPEAT_CHECKBOX_ROI = (480, 1110, 80, 75)
REPEAT_CHECKBOX_MIN_SCORE = 0.78
REPEAT_CHECKBOX_SCORE_MARGIN = 0.05
REARRANGE_BUTTON_ROI = (150, 1010, 410, 150)


def saved_plan_rearrange_view(frame) -> bool:
    """True when reopen shows the post-save summary (重新安排), not the edit form.

    The one-shot「完成后重复」checkbox only exists on the edit form (安排工作计划).
    After save, opening 工作计划 lands on the rearrange summary where that control
    is absent — template/OCR reads there are unknown, not "checked".
    """
    return match(frame, "rearrange_button.png", REARRANGE_BUTTON_ROI) >= 0.75


def repeat_checkbox_scores(frame) -> tuple[float, float]:
    """Return (checked_score, unchecked_score) in the audited checkbox ROI."""
    checked = match(frame, "repeat_checked.png", REPEAT_CHECKBOX_ROI)
    unchecked = match(frame, "repeat_unchecked.png", REPEAT_CHECKBOX_ROI)
    return float(checked), float(unchecked)


def repeat_checkbox_state(tasker: Tasker, frame) -> bool | None:
    """Require the full control's text; an absent/ambiguous check is unknown.

    Decide by comparative template scores: the unchecked and checked crops cross-
    talk around ~0.70, so a single lenient checked threshold (0.72) or a strict
    unchecked threshold (0.90) alone is brittle. Prefer the clearly higher score
    above REPEAT_CHECKBOX_MIN_SCORE with a margin.
    """
    job = tasker.post_recognition(
        JRecognitionType.OCR,
        JOCR(roi=(150, 1110, 440, 75), expected=[r".*继续使用当前计划.*"], threshold=.3),
        frame,
    ).wait()
    detail = job.get()
    recognition = detail.nodes[0].recognition if detail and detail.nodes else None
    if not recognition or not recognition.filtered_results:
        return None
    checked_score, unchecked_score = repeat_checkbox_scores(frame)
    if (
        unchecked_score >= REPEAT_CHECKBOX_MIN_SCORE
        and unchecked_score > checked_score + REPEAT_CHECKBOX_SCORE_MARGIN
    ):
        return False
    if (
        checked_score >= REPEAT_CHECKBOX_MIN_SCORE
        and checked_score > unchecked_score + REPEAT_CHECKBOX_SCORE_MARGIN
    ):
        return True
    return None


def ensure_repeat_checkbox(
    controller: Win32Controller,
    frame,
    *,
    repeat: bool,
    tasker: Tasker,
) -> object:
    """Default is one-shot: leave the repeat-after-complete checkbox unchecked.

    Locate state once; if a toggle is needed, click once and trust it.
    """
    checked = repeat_checkbox_state(tasker, frame)
    if checked is None:
        raise RuntimeError('repeat-plan control not verified; refusing to save')
    if repeat == checked:
        return frame
    # Checkbox center audited near (520, 1148) in 720x1358 space.
    click(controller, 520, 1148)
    # Toggle click = success; do not re-score the checkbox.
    time.sleep(0.25)
    return screenshot(controller)


def configure_items(
    controller: Win32Controller,
    tasker: Tasker,
    slot_id: str,
    items: dict[str, int],
    *,
    repeat: bool = False,
    before_save=None,
    capacity_limit=None,
):
    slot = SLOTS[slot_id]
    if not items or any(count <= 0 for count in items.values()):
        raise RuntimeError("work plan requires positive quantities")
    limit = slot['capacity'] if capacity_limit is None else capacity_limit
    if type(limit) is not int or not 1 <= limit <= 1000 or sum(items.values()) > limit:
        raise RuntimeError(f"work plan exceeds {slot['label']} capacity")
    aliases = load_capabilities().get("product_aliases", {})
    normalized_items: dict[str, int] = {}
    for crop, quantity in items.items():
        canonical = normalize_product_name(crop, aliases)
        if not slot_can_produce(slot, canonical, aliases=aliases):
            raise RuntimeError(
                f"{slot['label']} cannot produce {canonical} under level-aware capability rules"
            )
        normalized_items[canonical] = normalized_items.get(canonical, 0) + int(quantity)
    items = normalized_items
    if decoded.enabled():
        blocker = decoded.assignment_blocker(slot_id)
        if blocker:
            raise decoded.DecodeError(f'{slot_id}: {blocker}; preserving existing employee and physical production')
    frame, button_y = open_plan(controller, slot_id)
    if capacity_limit is not None:
        frame = confirm_empty_editor_capacity(controller, tasker, slot_id, capacity_limit)
    if not decoded.enabled():
        current_capacity = capacity(tasker, frame)
        if current_capacity is None:
            raise RuntimeError("unable to OCR employee capacity")
        if current_capacity != (0, slot["capacity"]):
            raise RuntimeError("employee has an existing plan; refusing to overwrite it")

    expected_total = 0
    for crop, quantity in items.items():
        template, x, y = CROPS[crop]
        icon_roi = (x - 54, y - 60, 108, 105)
        score = match(frame, template, icon_roi)
        if score < 0.78:
            raise RuntimeError(f"{crop} template did not match work-plan slot: {score:.3f}")
        click(controller, x, y)
        for _ in range(quantity):
            click(controller, 547, 953)
        expected_total += quantity
        frame = screenshot(controller)
        if decoded.enabled():
            # Draft counts are not persisted in MMKV. Do not manufacture a readback;
            # exact quantities are verified from the saved plan after the one save click.
            continue
        observed = capacity(tasker, frame)
        if observed != (expected_total, slot["capacity"]):
            raise RuntimeError(
                f"capacity verification failed: expected {expected_total}/{slot['capacity']}, got {observed}"
            )
        observed_item = icon_quantity(tasker, frame, crop)
        if observed_item != quantity:
            raise RuntimeError(
                f"{crop} quantity verification failed: expected {quantity}, got {observed_item}"
            )
    if not decoded.enabled():
        for crop, quantity in items.items():
            observed_item = icon_quantity(tasker, frame, crop)
            if observed_item != quantity:
                raise RuntimeError(f"{crop} changed unexpectedly: expected {quantity}, got {observed_item}")
    if capacity_limit is not None:
        frame = confirm_draft_capacity(controller, tasker, slot_id, expected_total, capacity_limit)
    frame = ensure_repeat_checkbox(controller, frame, repeat=repeat, tasker=tasker)
    if match(frame, "arrange_button.png", (360, 985, 290, 145)) < 0.75:
        raise RuntimeError("arrange button not recognized")
    if before_save is not None:
        if not decoded.enabled():
            raise RuntimeError('Durable save intent requires decoded state')
        before_save(decoded.snapshot())
    click(controller, 500, 1060)
    frame = screenshot(controller)
    if match(frame, "work_plan_button.png", (450, button_y - 60, 230, 120)) < 0.70:
        raise RuntimeError("plan save did not return to employee list")
    if decoded.enabled():
        decoded.verify_plan(slot_id, items, repeat=repeat)
    return {"items": items, "slot": slot["label"], "saved": True, "repeat": repeat, "one_shot": not repeat}


def configure(
    controller: Win32Controller, tasker: Tasker, slot_id: str, crop: str, quantity: int
):
    return configure_items(controller, tasker, slot_id, {crop: quantity})


def verify_saved_plan(
    controller: Win32Controller, tasker: Tasker, slot_id: str, items: dict[str, int]
) -> None:
    if decoded.enabled():
        decoded.verify_plan(slot_id, items, repeat=False)
        return
    frame, _ = open_plan(controller, slot_id)
    try:
        expected = (sum(items.values()), SLOTS[slot_id]["capacity"])
        observed = capacity(tasker, frame)
        if observed != expected:
            raise RuntimeError(
                f"{SLOTS[slot_id]['label']} saved capacity mismatch: expected {expected}, got {observed}"
            )
        for crop, quantity in items.items():
            actual = icon_quantity(tasker, frame, crop)
            if actual != quantity:
                raise RuntimeError(
                    f"{SLOTS[slot_id]['label']} saved {crop} mismatch: expected {quantity}, got {actual}"
                )
        # Post-save reopen is usually the rearrange summary: the one-shot checkbox
        # is not on that screen. Capacity/items are the reopen checks; unchecked
        # was enforced by ensure_repeat_checkbox immediately before arrange.
        if saved_plan_rearrange_view(frame):
            return
        state = repeat_checkbox_state(tasker, frame)
        if state is not False:
            checked_score, unchecked_score = repeat_checkbox_scores(frame)
            raise RuntimeError(
                f"{SLOTS[slot_id]['label']} saved one-shot checkbox was not verified unchecked "
                f"(state={state!r}, checked={checked_score:.3f}, unchecked={unchecked_score:.3f})"
            )
    finally:
        close_plan_dialog(controller)


def parse_items(text: str) -> dict[str, int]:
    """Parse repeated product=quantity pairs without silently merging duplicates."""
    items: dict[str, int] = {}
    for part in text.split(","):
        name, separator, raw_quantity = part.strip().partition("=")
        if not separator or name not in CROPS or name in items:
            raise ValueError(f"invalid or duplicate work-plan item: {part!r}")
        try:
            count = int(raw_quantity)
        except ValueError as error:
            raise ValueError(f"invalid quantity: {part!r}") from error
        if count < 1:
            raise ValueError(f"quantity must be positive: {part!r}")
        items[name] = count
    return items


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--slot", choices=sorted(SLOTS), default="field_1")
    parser.add_argument("--crop", choices=sorted(CROPS))
    parser.add_argument("--quantity", type=int)
    parser.add_argument("--items", help="comma-separated product=quantity pairs")
    parser.add_argument("--reset", action="store_true", help="clear the current farm-1 plan")
    parser.add_argument("--repeat", action="store_true", help="keep repeat-after-complete checked; default is one-shot (unchecked)")
    parser.add_argument("--collect", action="store_true", help="collect finished supplies")
    parser.add_argument("--target", type=int, help="required plan total for --collect; never collect partial supplies")
    parser.add_argument("--apply", action="store_true", help="actually save a work plan")
    args = parser.parse_args()
    if args.collect:
        if args.target is None or not 1 <= args.target <= SLOTS[args.slot]["capacity"]:
            parser.error("--collect requires --target within the employee capacity")
    elif args.target is not None:
        parser.error("--target is only valid with --collect")
    if args.items is not None:
        if args.crop is not None or args.quantity is not None or args.reset or args.collect:
            parser.error("--items cannot be combined with --crop, --quantity, --reset, or --collect")
        try:
            items = parse_items(args.items)
        except ValueError as error:
            parser.error(str(error))
    else:
        items = None
    if args.reset or args.collect:
        if args.crop is not None or args.quantity is not None:
            parser.error("--reset/--collect cannot be combined with --crop or --quantity")
        if args.reset and args.collect:
            parser.error("--reset and --collect are mutually exclusive")
    elif items is None and (args.crop is None or args.quantity is None or not 1 <= args.quantity <= MAX_QUANTITY):
        parser.error(f"--crop and --quantity (1 to {MAX_QUANTITY}) are required")
    if items is None and not args.reset and not args.collect:
        items = {args.crop: args.quantity}
    if items and sum(items.values()) > SLOTS[args.slot]["capacity"]:
        parser.error(f"quantity exceeds {args.slot} capacity {SLOTS[args.slot]['capacity']}")
    if not args.apply:
        action = "reset" if args.reset else "collect" if args.collect else str(items)
        print(f"dry run: {SLOTS[args.slot]['label']}, {action}; add --apply to save")
        return 0

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
        if not resource.post_bundle(RESOURCE).wait().succeeded:
            raise RuntimeError("Maa resource load failed")
        if not tasker.bind(resource, controller):
            raise RuntimeError("Maa tasker binding failed")
        if args.reset:
            result = reset_plan(controller, tasker, args.slot)
        elif args.collect:
            result = collect_finished_slot(controller, args.slot, tasker, required_progress=args.target)
        else:
            result = configure_items(controller, tasker, args.slot, items, repeat=args.repeat)
        print(result)
        if args.collect:
            return 0 if result.get("collected") else 2
        return 0
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

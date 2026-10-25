"""Build the unlabeled crop template catalog from the live Maa backpack.

The collector deliberately keeps naming separate from acquisition. It opens the
crop tab, moves to the top, captures every stable scroll page, returns to the
top, splits the known five-column grid, deduplicates overlap with template
matching, and recognizes the quantities with Maa OCR.
"""

from __future__ import annotations

import ctypes
import gc
import json
from pathlib import Path
import shutil
import time

import cv2
import numpy as np
from maa.controller import Win32Controller
from maa.define import MaaWin32InputMethodEnum, MaaWin32ScreencapMethodEnum
from maa.pipeline import JOCR, JRecognitionType
from maa.resource import Resource
from maa.tasker import Tasker
from maa_frame_normalize import post_click_normalized, post_swipe_normalized, install_foreground_safe_inputs


ROOT = Path(__file__).resolve().parents[1]
RESOURCE_DIR = ROOT / "assets/resource"
LEGACY_TEMPLATE_DIR = RESOURCE_DIR / "image/crops/named"
TEMPLATE_DIR = RESOURCE_DIR / "image/crops/stable"
CATALOG_DIR = ROOT / "assets/catalog/backpack_crops"
CAPTURE_DIR = CATALOG_DIR / "pages"
SEGMENT_DIR = CATALOG_DIR / "segments"
MANIFEST_PATH = CATALOG_DIR / "catalog.json"
INDEX_PATH = CATALOG_DIR / "index.png"
TEMP_DIR = ROOT / ".tmp/backpack_crop_catalog"

WINDOW_TITLE = "萝卜家园2"
WINDOW_CLASS = "Chrome_WidgetWin_0"
CROP_TAB_CENTER = (136, 426)
SWIPE_CENTER_X = 360
SWIPE_TOP_Y = 585
SWIPE_BOTTOM_Y = 860
SWIPE_DURATION_MS = 360
SETTLE_SECONDS = 0.12
FRAME_STABILITY_GAP_SECONDS = 0.14
MAX_SCROLLS = 12
STABLE_THRESHOLD = 0.995
IDENTITY_THRESHOLD = 0.82
IDENTITY_MARGIN = 0.025
GRID_ROI = (75, 458, 570, 480)
PAGE_CROP_ROI = (31, 277, 658, 765)

# The game snaps the inventory rows after swiping. These anchors were measured
# on the lossless 720-short-side Maa FramePool images.
PAGE_ROWS = {
    "top": ((535, True), (671, True), (809, True)),
    # The first bottom row is deliberately represented by the clean top-page
    # copy. It overlaps the tab strip here and is useful only as a completeness
    # check, not as a source template.
    "bottom": ((483, False), (617, True), (751, True)),
}
CELL_CENTER_X = (142, 254, 366, 478, 590)
ITEM_PATCH_SIZE = (96, 96)
ICON_TEMPLATE_SIZE = (84, 50)
OCR_REPLACE = [["O", "0"], ["o", "0"], ["l", "1"], ["I", "1"], ["i", "1"]]
OCR_PROFILES = {
    "crop_003": (4, 0, 60, 42, None),
    "crop_005": (12, 1, 58, 51, 150),
    "crop_010": (12, 1, 58, 51, 170),
    "crop_011": (12, 1, 58, 51, 170),
    "crop_015": (4, 4, 60, 42, None),
    "crop_016": (0, 0, 64, 46, None),
    "crop_018": (0, 0, 64, 46, None),
    "crop_020": (12, 1, 58, 51, 150),
    "crop_021": (12, 1, 58, 51, 130),
}
OCR_RETRY_PROFILES = {
    "crop_003": (4, 0, 60, 42, None),
    "crop_005": (12, 1, 58, 51, 170),
    "crop_010": (12, 1, 58, 51, 150),
    "crop_011": (12, 1, 58, 51, 150),
    "crop_015": (4, 4, 60, 42, None),
    "crop_016": (0, 0, 64, 46, 170),
    "crop_018": (0, 0, 64, 46, 170),
    "crop_020": (12, 1, 58, 51, 170),
    "crop_021": (12, 1, 58, 51, 150),
}
CROP_NAMES = {
    "crop_001": "红皮土豆",
    "crop_002": "白玉草莓",
    "crop_003": "山鸡肉",
    "crop_004": "青苹果",
    "crop_005": "番茄",
    "crop_006": "小麦",
    "crop_007": "玉米",
    "crop_008": "土豆",
    "crop_009": "玫瑰",
    "crop_010": "胡萝卜",
    "crop_011": "红薯",
    "crop_012": "苹果",
    "crop_013": "黄桃",
    "crop_014": "柠檬",
    "crop_015": "椰子",
    "crop_016": "牛肉",
    "crop_017": "鸡蛋",
    "crop_018": "猪肉",
    "crop_019": "羊肉",
    "crop_020": "牛奶",
    "crop_021": "鱼肉",
    # Sugarcane used to be absent because its quantity was zero.  It is
    # deliberately appended instead of inserted at its visual slot, so the
    # stable IDs of potato and every later item never change.
    "crop_022": "甘蔗",
}

# A failed legacy positional collector wrote the current 22 templates before
# it could write its manifest.  This one-time bootstrap mapping recovers their
# semantic IDs.  All successful runs publish canonical templates under
# ``image/crops/stable`` and no longer depend on this mapping.
LEGACY_TEMPLATE_REMAP = {
    **{f"crop_{index:03d}": f"crop_{index:03d}" for index in range(1, 8)},
    "crop_008": "crop_009",
    "crop_009": "crop_010",
    "crop_010": "crop_011",
    "crop_011": "crop_012",
    "crop_012": "crop_013",
    "crop_013": "crop_014",
    "crop_014": "crop_015",
    "crop_015": "crop_016",
    "crop_016": "crop_017",
    "crop_017": "crop_018",
    "crop_018": "crop_019",
    "crop_019": "crop_020",
    "crop_020": "crop_021",
    "crop_021": "crop_022",
    "crop_022": "crop_008",
}


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


def destroy_native(*objects: object) -> None:
    for value in objects:
        destructor = getattr(value, "__del__", None)
        if callable(destructor):
            destructor()
        if hasattr(value, "_handle"):
            value._handle = None


def screenshot(controller: Win32Controller) -> np.ndarray:
    job = controller.post_screencap().wait()
    if not job.succeeded:
        raise RuntimeError("Maa screenshot failed")
    return job.get()


def roi(image: np.ndarray, box: tuple[int, int, int, int]) -> np.ndarray:
    x, y, width, height = box
    return image[y : y + height, x : x + width]


def similarity(left: np.ndarray, right: np.ndarray, box: tuple[int, int, int, int]) -> float:
    lhs = cv2.cvtColor(roi(left, box), cv2.COLOR_BGR2GRAY)
    rhs = cv2.cvtColor(roi(right, box), cv2.COLOR_BGR2GRAY)
    return float(cv2.matchTemplate(lhs, rhs, cv2.TM_CCOEFF_NORMED)[0, 0])


def write_png(path: Path, image: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), image, [cv2.IMWRITE_PNG_COMPRESSION, 9]):
        raise RuntimeError(f"failed to write image: {path}")


def move_until_stable(
    controller: Win32Controller,
    current: np.ndarray,
    *,
    upward: bool,
    retain_pages: bool,
) -> tuple[np.ndarray, list[np.ndarray], list[float]]:
    pages = [current] if retain_pages else []
    scores: list[float] = []
    stable_hits = 0
    for _ in range(MAX_SCROLLS):
        y1, y2 = (
            (SWIPE_BOTTOM_Y, SWIPE_TOP_Y)
            if upward
            else (SWIPE_TOP_Y, SWIPE_BOTTOM_Y)
        )
        job = post_swipe_normalized(controller, SWIPE_CENTER_X, y1, SWIPE_CENTER_X, y2, SWIPE_DURATION_MS).wait()
        if not job.succeeded:
            raise RuntimeError("Maa backpack swipe failed")
        time.sleep(SETTLE_SECONDS)
        candidate = screenshot(controller)
        time.sleep(FRAME_STABILITY_GAP_SECONDS)
        next_image = screenshot(controller)
        if similarity(candidate, next_image, GRID_ROI) < STABLE_THRESHOLD:
            time.sleep(FRAME_STABILITY_GAP_SECONDS)
            next_image = screenshot(controller)
        score = similarity(current, next_image, GRID_ROI)
        scores.append(score)
        if score >= STABLE_THRESHOLD:
            stable_hits += 1
        else:
            stable_hits = 0
            if retain_pages:
                pages.append(next_image)
        current = next_image
        # The inventory is a snapping list. One unchanged post-swipe frame is
        # enough to prove the endpoint and saves one full swipe in each of the
        # three seek phases.
        if stable_hits >= 1:
            return current, pages, scores
    direction = "bottom" if upward else "top"
    raise RuntimeError(f"backpack did not reach stable {direction} within {MAX_SCROLLS} swipes")


def item_patch(image: np.ndarray, center_x: int, center_y: int) -> np.ndarray:
    width, height = ITEM_PATCH_SIZE
    return image[center_y - height // 2 : center_y + height // 2, center_x - width // 2 : center_x + width // 2]


def icon_template(image: np.ndarray, center_x: int, center_y: int) -> np.ndarray:
    width, height = ICON_TEMPLATE_SIZE
    # Quantity digits are below the crop. Keeping only the icon and its static
    # cream background gives the later Maa TemplateMatch a clean template.
    return image[center_y - 48 : center_y - 48 + height, center_x - width // 2 : center_x + width // 2]


def occupied(patch: np.ndarray) -> bool:
    background = np.array([209, 248, 255], dtype=np.int16)
    distance = np.max(np.abs(patch.astype(np.int16) - background), axis=2)
    return int(np.count_nonzero(distance > 32)) >= 230


def template_similarity(left: np.ndarray, right: np.ndarray) -> float:
    lhs = cv2.cvtColor(left, cv2.COLOR_BGR2GRAY)
    rhs = cv2.cvtColor(right, cv2.COLOR_BGR2GRAY)
    return float(cv2.matchTemplate(lhs, rhs, cv2.TM_CCOEFF_NORMED)[0, 0])


def page_quantities(
    tasker: Tasker,
    image: np.ndarray,
    slots: list[tuple[int, int, str]],
    *,
    profiles: dict[str, tuple[int, int, int, int, int | None]] = OCR_PROFILES,
) -> dict[tuple[int, int], dict]:
    """Post exactly one OCR job per occupied cell, then wait as a batch."""

    pending: list[tuple[tuple[int, int], str, int | None, object]] = []
    for center_x, center_y, item_id in slots:
        offset_x, offset_y, width, height, threshold = profiles.get(
            item_id,
            (0, 0, 64, 46, None),
        )
        crop = image[
            center_y - 4 + offset_y : center_y - 4 + offset_y + height,
            center_x - 22 + offset_x : center_x - 22 + offset_x + width,
        ]
        if threshold is not None:
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            _, crop = cv2.threshold(gray, threshold, 255, cv2.THRESH_BINARY)
            crop = cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR)
        prepared = cv2.resize(crop, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
        job = tasker.post_recognition(
            JRecognitionType.OCR,
            JOCR(
                roi=(0, 0, prepared.shape[1], prepared.shape[0]),
                expected=[r"^\d+$"],
                threshold=0.1,
                only_rec=True,
                replace=OCR_REPLACE,
            ),
            prepared,
        )
        pending.append(((center_x, center_y), item_id, threshold, job))

    output: dict[tuple[int, int], dict] = {}
    for slot, item_id, threshold, job in pending:
        completed = job.wait()
        detail = completed.get()
        recognition = detail.nodes[0].recognition if detail and detail.nodes else None
        best = recognition.best_result if recognition else None
        text = getattr(best, "text", None)
        score = getattr(best, "score", None)
        if text is None or not text.isdigit():
            continue
        candidate = {
            "text": text,
            "score": round(float(score), 6),
            "mode": "batched_single_cell",
            "profile": item_id,
            "threshold": threshold,
        }
        output[slot] = {
            "text": text,
            "value": int(text),
            "score": candidate["score"],
            "status": "ok",
            "selection_rule": "one_ocr_job_for_stable_identity_profile",
            "candidates": [candidate],
        }
    return output


def load_identity_templates() -> list[dict]:
    """Load immutable stable templates, or migrate the one legacy snapshot."""

    identities: list[dict] = []
    use_stable = all((TEMPLATE_DIR / f"{item_id}.png").exists() for item_id in CROP_NAMES)
    for item_id, name in CROP_NAMES.items():
        source_id = item_id if use_stable else LEGACY_TEMPLATE_REMAP[item_id]
        path = (TEMPLATE_DIR if use_stable else LEGACY_TEMPLATE_DIR) / f"{source_id}.png"
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None or image.shape[:2] != (ICON_TEMPLATE_SIZE[1], ICON_TEMPLATE_SIZE[0]):
            raise RuntimeError(f"missing or invalid identity template: {path}")
        identities.append({"id": item_id, "name": name, "template": image})
    return identities


def match_identity(template: np.ndarray, identities: list[dict]) -> tuple[dict, float, float]:
    ranked = sorted(
        ((identity, template_similarity(template, identity["template"])) for identity in identities),
        key=lambda pair: pair[1],
        reverse=True,
    )
    best, best_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else -1.0
    if best_score < IDENTITY_THRESHOLD or best_score - second_score < IDENTITY_MARGIN:
        raise RuntimeError(
            "ambiguous crop identity: "
            f"best={best['id']} score={best_score:.6f} margin={best_score - second_score:.6f}"
        )
    return best, best_score, best_score - second_score


def put_label(canvas: np.ndarray, text: str, origin: tuple[int, int], scale: float = 0.75) -> None:
    cv2.putText(canvas, text, origin, cv2.FONT_HERSHEY_SIMPLEX, scale, (93, 58, 82), 2, cv2.LINE_AA)


def build_index(items: list[dict]) -> np.ndarray:
    columns = 5
    tile_width, tile_height = 150, 150
    rows = (len(items) + columns - 1) // columns
    canvas = np.full((rows * tile_height + 50, columns * tile_width, 3), (246, 250, 255), np.uint8)
    put_label(canvas, f"Backpack crop catalog - {len(items)} named items", (16, 32), 0.72)
    for index, item in enumerate(items):
        row, column = divmod(index, columns)
        x, y = column * tile_width, 50 + row * tile_height
        segment = cv2.imread(str(ROOT / item["segment"]), cv2.IMREAD_COLOR)
        if segment is not None:
            image_x = x + (tile_width - segment.shape[1]) // 2
            canvas[y + 4 : y + 4 + segment.shape[0], image_x : image_x + segment.shape[1]] = segment
        quantity = item["quantity"]["text"] or "?"
        put_label(canvas, item["id"], (x + 18, y + 107), 0.58)
        put_label(canvas, f"qty {quantity}", (x + 25, y + 135), 0.58)
    return canvas


def collect_slots(
    tasker: Tasker,
    endpoint_pages: dict[str, np.ndarray],
    identities: list[dict],
    template_dir: Path,
    segment_dir: Path,
) -> tuple[list[dict], list[dict]]:
    """Identify every occupied slot by template and OCR each page once."""

    items: list[dict] = []
    duplicate_matches: list[dict] = []
    seen_ids: set[str] = set()
    for page_name, image in endpoint_pages.items():
        recognized_slots: dict[tuple[int, int], tuple[dict, float, float]] = {}
        for center_y, should_export in PAGE_ROWS[page_name]:
            if not should_export:
                continue
            for center_x in CELL_CENTER_X:
                if not occupied(item_patch(image, center_x, center_y)):
                    continue
                identity, score, margin = match_identity(
                    icon_template(image, center_x, center_y),
                    identities,
                )
                recognized_slots[(center_x, center_y)] = (identity, score, margin)
        quantities = page_quantities(
            tasker,
            image,
            [
                (center_x, center_y, identity["id"])
                for (center_x, center_y), (identity, _, _) in recognized_slots.items()
            ],
        )
        missed = [
            (center_x, center_y, identity["id"])
            for (center_x, center_y), (identity, _, _) in recognized_slots.items()
            if (center_x, center_y) not in quantities
        ]
        if missed:
            quantities.update(
                page_quantities(
                    tasker,
                    image,
                    missed,
                    profiles=OCR_RETRY_PROFILES,
                )
            )
        for row_index, (center_y, should_export) in enumerate(PAGE_ROWS[page_name]):
            for column_index, center_x in enumerate(CELL_CENTER_X):
                patch = item_patch(image, center_x, center_y)
                if not occupied(patch):
                    continue
                if not should_export:
                    # This row is intentionally clipped by the tab strip. It
                    # is a geometric overlap marker, not valid identity input.
                    # Stable IDs are deduplicated from the two clean endpoint
                    # row sets below, never inferred from column offsets.
                    duplicate_matches.append(
                        {
                            "page": page_name,
                            "row": row_index,
                            "column": column_index,
                            "matched_id": None,
                            "reason": "clipped_overlap_row_not_used_for_identity",
                        }
                    )
                    continue
                template = icon_template(image, center_x, center_y)
                identity, score, margin = recognized_slots[(center_x, center_y)]
                item_id = identity["id"]
                match = {
                    "page": page_name,
                    "row": row_index,
                    "column": column_index,
                    "matched_id": item_id,
                    "score": round(score, 6),
                    "margin": round(margin, 6),
                }
                if item_id in seen_ids:
                    duplicate_matches.append(match)
                    continue
                seen_ids.add(item_id)
                template_path = template_dir / f"{item_id}.png"
                segment_path = segment_dir / f"{item_id}.png"
                write_png(template_path, template)
                write_png(segment_path, patch)
                quantity = quantities.get((center_x, center_y))
                if quantity is None:
                    quantity = {
                        "text": None,
                        "value": None,
                        "score": None,
                        "status": "unrecognized",
                        "candidates": [],
                    }
                items.append(
                    {
                        "id": item_id,
                        "name": identity["name"],
                        "template": (TEMPLATE_DIR / f"{item_id}.png").relative_to(ROOT).as_posix(),
                        "segment": (SEGMENT_DIR / f"{item_id}.png").relative_to(ROOT).as_posix(),
                        "identity": {
                            "method": "TemplateMatch",
                            "score": round(score, 6),
                            "margin": round(margin, 6),
                        },
                        "quantity": quantity,
                        "source": {
                            "page": page_name,
                            "row": row_index,
                            "column": column_index,
                            "center": [center_x, center_y],
                        },
                    }
                )
    missing = sorted(set(CROP_NAMES) - seen_ids)
    if missing:
        raise RuntimeError(f"catalog is missing stable identities: {missing}")
    return sorted(items, key=lambda item: item["id"]), duplicate_matches


def main() -> int:
    shutil.rmtree(TEMP_DIR, ignore_errors=True)
    TEMP_DIR.mkdir(parents=True)
    identities = load_identity_templates()
    staged_templates = TEMP_DIR / "templates"
    staged_captures = TEMP_DIR / "pages"
    staged_segments = TEMP_DIR / "segments"
    staged_templates.mkdir()
    staged_captures.mkdir()
    staged_segments.mkdir()

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
        if not post_click_normalized(controller, *CROP_TAB_CENTER).wait().succeeded:
            raise RuntimeError("Maa could not select the crop backpack tab")
        time.sleep(SETTLE_SECONDS)

        current = screenshot(controller)
        time.sleep(FRAME_STABILITY_GAP_SECONDS)
        current = screenshot(controller)
        top_image, _, top_scores = move_until_stable(controller, current, upward=False, retain_pages=False)
        bottom_image, pages, down_scores = move_until_stable(controller, top_image, upward=True, retain_pages=True)
        restored_image, _, restore_scores = move_until_stable(controller, bottom_image, upward=False, retain_pages=False)
        restored_score = similarity(top_image, restored_image, GRID_ROI)
        if restored_score < STABLE_THRESHOLD:
            raise RuntimeError(f"backpack failed to return to top: similarity={restored_score:.6f}")

        # The current game has exactly one moving page between top and bottom;
        # retain the endpoints because they cover every full item row.
        endpoint_pages = {"top": top_image, "bottom": bottom_image}
        for name, image in endpoint_pages.items():
            write_png(staged_captures / f"{name}.png", roi(image, PAGE_CROP_ROI))

        items, duplicate_matches = collect_slots(
            tasker,
            endpoint_pages,
            identities,
            staged_templates,
            staged_segments,
        )

        manifest = {
            "schema_version": 2,
            "display_short_side": 720,
            "window": {"title": WINDOW_TITLE, "class": WINDOW_CLASS},
            "capture": {
                "method": "MaaFramework.Win32.FramePool",
                "input": "MaaFramework.PostMessage.Swipe",
                "foreground_policy": "never_activate_window_or_move_system_cursor",
                "pages": [f"pages/{name}.png" for name in endpoint_pages],
                "top_seek_similarity": top_scores,
                "bottom_seek_similarity": down_scores,
                "restore_similarity": restore_scores,
                "top_restored_score": round(restored_score, 6),
            },
            "recognition": {
                "item_identity": "TemplateMatch",
                "quantity": "MaaFramework.OCR",
                "identity_threshold": IDENTITY_THRESHOLD,
                "identity_margin": IDENTITY_MARGIN,
                "ocr_jobs": "one recognition-only job per occupied cell; one sparse retry batch for misses",
            },
            "item_count": len(items),
            "duplicate_matches": duplicate_matches,
            "items": items,
        }
        unresolved = [item["id"] for item in items if item["quantity"]["status"] != "ok"]
        if unresolved:
            raise RuntimeError(f"unresolved quantities: {unresolved}")

        # Publish only after every identity and quantity has passed. A failed
        # run leaves the last known-good catalog untouched.
        for target, staged in (
            (TEMPLATE_DIR, staged_templates),
            (CAPTURE_DIR, staged_captures),
            (SEGMENT_DIR, staged_segments),
        ):
            shutil.rmtree(target, ignore_errors=True)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(staged), str(target))
        MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
        MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        write_png(INDEX_PATH, build_index(items))
        print(
            json.dumps(
                {
                    "items": len(items),
                    "duplicates": len(duplicate_matches),
                    "unresolved_quantities": unresolved,
                    "top_restored_score": round(restored_score, 6),
                    "manifest": str(MANIFEST_PATH.relative_to(ROOT)),
                    "index": str(INDEX_PATH.relative_to(ROOT)),
                },
                ensure_ascii=False,
            )
        )
        return 0
    finally:
        tasker.clear_cache()
        destroy_native(tasker, resource, controller)
        del tasker, resource, controller
        gc.collect()
        shutil.rmtree(TEMP_DIR, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())

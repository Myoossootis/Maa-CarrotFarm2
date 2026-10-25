"""Check the farm feed mill before starting ranch employee production.

The mill and feed sack are identified from saved image templates. OCR confirms
the empty-mill state before spending ingredients on a new batch. All input uses
Maa Win32 PostMessage and all screenshots use FramePool.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import re
import time
import traceback
from pathlib import Path

import cv2
from maa.controller import Win32Controller
from maa.define import MaaWin32InputMethodEnum, MaaWin32ScreencapMethodEnum
from maa.pipeline import JOCR, JRecognitionType
from maa.resource import Resource
from maa.tasker import Tasker

try:
    from .capture_current_frame_with_maa import find_window
    from .maa_frame_normalize import post_click_normalized, post_swipe_normalized
    from .configure_field_employee import RESOURCE, screenshot, close_employee_menu, match
    from .task_control import TaskCancelled, check_cancelled
except ImportError:
    from capture_current_frame_with_maa import find_window
    from maa_frame_normalize import post_click_normalized, post_swipe_normalized
    from configure_field_employee import RESOURCE, screenshot, close_employee_menu, match
    from task_control import TaskCancelled, check_cancelled


ROOT = Path(__file__).resolve().parents[1]
IMAGES = ROOT / "assets/resource/image/production"
POLL_SECONDS = 10
FEED_TIMEOUT_SECONDS = 1800
MAP_SCALES = tuple(round(0.7 + index * 0.1, 2) for index in range(13))
SACK_SCALES = tuple(round(0.8 + index * 0.1, 2) for index in range(13))
BUTTON_SCALES = (1.65, 1.75, 1.85, 1.95, 2.05)


def mill_remaining_seconds(text: str) -> int | None:
    """Parse the labelled duration, not a '0秒' suffix of e.g. 1分30秒."""
    compact = re.sub(r"\s+", "", text)
    match = re.search(r"预计可生产[:：]?((?:\d+(?:小时|时|分|秒))+)", compact)
    if match is None:
        return None
    units = {"小时": 3600, "时": 3600, "分": 60, "秒": 1}
    return sum(int(count) * units[unit] for count, unit in re.findall(r"(\d+)(小时|时|分|秒)", match[1]))


def template(name: str):
    image = cv2.imread(str(IMAGES / name), cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError(f"missing feed template: {name}")
    return image


def locate_scaled(
    frame,
    image,
    scales: tuple[float, ...],
    threshold: float,
    roi: tuple[int, int, int, int] | None = None,
):
    if roi is None:
        x0, y0, x1, y1 = 0, 0, frame.shape[1], frame.shape[0]
    else:
        x0, y0, width, height = roi
        x1, y1 = min(frame.shape[1], x0 + width), min(frame.shape[0], y0 + height)
    area = frame[y0:y1, x0:x1]
    best = None
    for scale in scales:
        width = max(1, round(image.shape[1] * scale))
        height = max(1, round(image.shape[0] * scale))
        if width > area.shape[1] or height > area.shape[0]:
            continue
        resized = cv2.resize(image, (width, height), interpolation=cv2.INTER_CUBIC)
        scores = cv2.matchTemplate(area, resized, cv2.TM_CCOEFF_NORMED)
        _, score, _, point = cv2.minMaxLoc(scores)
        candidate = (float(score), x0 + point[0], y0 + point[1], width, height)
        if best is None or candidate[0] > best[0]:
            best = candidate
    if best is None or best[0] < threshold:
        return None
    return best


def recognized_text(
    tasker: Tasker,
    frame,
    roi: tuple[int, int, int, int],
    expected: tuple[str, ...] = (
        r"预计可生产.*",
        r"生产中.*",
        r"补货.*",
        r"饿了",
    ),
) -> str:
    x, y, width, height = roi
    patch = frame[y : y + height, x : x + width]
    if patch.size == 0:
        return ""
    enlarged = cv2.resize(patch, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    job = tasker.post_recognition(
        JRecognitionType.OCR,
        JOCR(
            roi=(0, 0, enlarged.shape[1], enlarged.shape[0]),
            expected=list(expected),
            threshold=0.02,
        ),
        enlarged,
    ).wait()
    detail = job.get()
    recognition = detail.nodes[0].recognition if detail and detail.nodes else None
    if recognition is None:
        return ""
    return "".join(
        re.sub(r"\s+", "", getattr(result, "text", ""))
        for result in recognition.filtered_results
    )


def click(controller: Win32Controller, x: int, y: int) -> None:
    if not post_click_normalized(controller, x, y).wait().succeeded:
        raise RuntimeError(f"feed UI click failed at ({x}, {y})")
    time.sleep(0.5)


def feed_match(frame, name: str, scales: tuple[float, ...], threshold: float, roi=None):
    # Normalized 720-wide live captures usually match at 1x. The mill body
    # anchor excludes snow, the changing badge, and the fixed avatar overlay.
    if name == 'feed_silo_structure.png':
        live = IMAGES / 'feed_silo_structure_live.png'
        if live.is_file():
            hit = locate_scaled(frame, template(live.name), (1.0, .5, .6, .7, .8, .9), .90, roi)
            if hit is not None:
                return hit
    best = locate_scaled(frame, template(name), scales, threshold, roi)
    if name in {"feed_silo_structure.png", "feed_sack_icon.png"}:
        live_name = name.replace(".png", "_live.png")
        if (IMAGES / live_name).is_file():
            hit = locate_scaled(frame, template(live_name), scales, max(threshold, 0.90), roi)
            if hit is not None and (best is None or hit[0] > best[0]):
                best = hit
    return best


def find_silo(controller: Win32Controller, *, evidence_dir=None):
    """Search toward the user-confirmed bottom-right mill, with bounded pans."""
    shortage_image = template("feed_silo_shortage.png")
    frame = screenshot(controller)
    # Dragging the map up/left reveals its bottom/right region. A vertical-only
    # search misses the mill when the camera was moved horizontally.
    pans = [(540, 1000, 220, 500)] * 3 + [(450, 650, 450, 980)] + [(540, 900, 220, 550)] * 2
    for swipe in range(len(pans) + 1):
        check_cancelled()
        structure = feed_match(frame, "feed_silo_structure.png", MAP_SCALES, 0.72)
        if structure is not None:
            print(f'[磨坊] 定位成功：滑动{swipe}次，主体={structure}', flush=True)
            return frame, None, structure
        shortage = locate_scaled(
            frame,
            shortage_image,
            MAP_SCALES,
            0.78,
        )
        if shortage is not None:
            # Derive the silo body from the known reference geometry. The
            # center of the whole shortage marker can land on its speech bubble.
            score, x, y, width, height = shortage
            structure = (score, x + round(width * 20 / 205), y + round(height * 43 / 234),
                         round(width * 120 / 205), round(height * 180 / 234))
            print(f'[磨坊] 识别缺货标记，定位主体：{structure}', flush=True)
            return frame, shortage, structure
        # The output sack remains identifiable even when the tall roof is
        # clipped by the window edge. Its counter must be verified separately
        # before any collection; this never authorizes a structure/refill click.
        if feed_match(frame, "feed_sack_icon.png", SACK_SCALES, 0.78) is not None:
            return frame, None, None
        if swipe == len(pans):
            break
        print(f'[磨坊] 当前画面未找到，滑动搜索{swipe + 1}/{len(pans)}：{pans[swipe]}', flush=True)
        if not post_swipe_normalized(controller, *pans[swipe], 650).wait().succeeded:
            raise RuntimeError("farm-map pan failed while locating feed mill")
        time.sleep(0.4)
        frame = screenshot(controller)
    evidence = Path(evidence_dir) if evidence_dir else _navigation_evidence()
    _save_navigation_frame(evidence / 'search_failed.png', frame)
    scores = {name: locate_scaled(frame, template(name), MAP_SCALES, 0) for name in
              ('feed_silo_structure.png', 'feed_silo_structure_live.png', 'feed_silo_shortage.png')}
    (evidence / 'search.json').write_text(json.dumps(dict(swipes=len(pans), best_matches=scores), indent=2), encoding='utf-8')
    raise RuntimeError(f'feed silo landmark was not found on the farm map；证据={evidence}')


def _navigation_evidence():
    folder = ROOT / '.tmp/mill_navigation' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _save_navigation_frame(path, frame):
    success, data = cv2.imencode('.png', frame)
    if not success:
        raise RuntimeError(f'磨坊证据截图编码失败：{path}')
    Path(path).write_bytes(data.tobytes())


def read_mill_state(tasker, frame):
    text = recognized_text(tasker, frame, (70, 320, 590, 450),
                           expected=(r'磨坊.*', r'预计可生产.*'))
    return text if '磨坊' in text else None


def open_mill(controller, tasker, *, location=None):
    """Find and click the silo body once; require the actual mill panel."""
    evidence = _navigation_evidence()
    result = dict(task='open_mill', stage='locate', status='running', clicks=0, evidence_dir=str(evidence))
    try:
        frame = screenshot(controller)
        text = read_mill_state(tasker, frame)
        if text is not None:
            result.update(status='already_open', mill_text=text, remaining_seconds=mill_remaining_seconds(text))
        else:
            frame, shortage, structure = location if location is not None else find_silo(controller, evidence_dir=evidence)
            if structure is None:
                raise RuntimeError('mill_body_not_visible: 只看到饲料袋，未点击其他建筑')
            _, x, y, width, height = structure
            point = (x + width // 2, y + height // 2)
            result.update(stage='click_body', structure=structure, click_point=point)
            _save_navigation_frame(evidence / 'before.png', frame)
            print(f'[磨坊] 点击主体：{point}；证据={evidence}', flush=True)
            click(controller, *point)
            result.update(stage='verify_panel', clicks=1)
            for _ in range(4):
                check_cancelled()
                frame = screenshot(controller)
                text = read_mill_state(tasker, frame)
                if text is not None:
                    result.update(status='opened', mill_text=text, remaining_seconds=mill_remaining_seconds(text))
                    break
                time.sleep(.25)
            else:
                raise RuntimeError('mill_panel_not_opened: 点击后未识别到磨坊标题，未重复点击')
        _save_navigation_frame(evidence / 'opened.png', frame)
        result['stage'] = 'done'
        return result
    except TaskCancelled:
        result['status'] = 'cancelled'
        raise
    except Exception as error:
        result.update(status='failed', error=str(error))
        (evidence / 'error.log').write_text(traceback.format_exc(), encoding='utf-8')
        try:
            _save_navigation_frame(evidence / 'failure.png', screenshot(controller))
        except Exception as capture_error:
            result['screenshot_error'] = str(capture_error)
        raise RuntimeError(f'磨坊定位/点击失败 stage={result["stage"]}: {error}；证据={evidence}') from error
    finally:
        (evidence / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(result, ensure_ascii=False), flush=True)


def find_ready_sack(frame, silo_box):
    _, silo_x, silo_y, silo_w, silo_h = silo_box
    roi = (max(0, silo_x - 80), max(80, silo_y - 160), silo_w + 240, silo_h + 260)
    return feed_match(
        frame,
        "feed_sack_icon.png",
        SACK_SCALES,
        0.72,
        roi,
    )


def parse_sack_readings(readings) -> int | None:
    """Require two high-confidence complete readings, including the × prefix."""
    values = []
    prefix_seen = False
    for text, score in readings:
        # This badge's stylized zero is consistently read as lowercase u by
        # the OCR model. Apply this substitution only inside this numeric ROI.
        text = re.sub(r"\s+", "", text).translate(str.maketrans("uUOo", "0000"))
        value = re.fullmatch(r"([xX×]?)(\d{1,3})", text)
        if score < 0.75 or value is None:
            continue
        prefix_seen |= bool(value[1])
        values.append(int(value[2]))
    if prefix_seen and len(values) >= 2 and len(set(values)) == 1:
        return values[0]
    return None


def read_sack_count(tasker: Tasker, frame, sack_box) -> int | None:
    _, x, y, width, height = sack_box
    readings = []
    for fx, fy, fw, fh in ((0.75, 0.56, 0.54, 0.32), (0.81, 0.61, 0.46, 0.25)):
        rx, ry = x + round(width * fx), y + round(height * fy)
        patch = frame[ry:ry + round(height * fh), rx:rx + round(width * fw)]
        if patch.size == 0:
            continue
        image = cv2.resize(patch, None, fx=6, fy=6, interpolation=cv2.INTER_CUBIC)
        image = cv2.copyMakeBorder(image, 8, 8, 8, 8, cv2.BORDER_CONSTANT, value=(255, 255, 255))
        job = tasker.post_recognition(JRecognitionType.OCR, JOCR(
            roi=(0, 0, image.shape[1], image.shape[0]), expected=[r".*"],
            only_rec=True, threshold=0.02), image).wait()
        detail = job.get()
        recognition = detail.nodes[0].recognition if detail and detail.nodes else None
        if recognition:
            readings.extend((value.text, float(value.score)) for value in recognition.filtered_results)
    return parse_sack_readings(readings)


def has_hungry_marker(tasker: Tasker, frame) -> bool:
    text = recognized_text(tasker, frame, (0, 880, 465, 440))
    return "饿了" in text


def close_mill_popup(controller: Win32Controller) -> None:
    # Maa reference screenshots show the standard “tap blank area” footer.
    click(controller, 365, 1230)


def start_refill(controller: Win32Controller, tasker: Tasker) -> None:
    frame = screenshot(controller)
    button = feed_match(
        frame,
        "feed_refill_button.png",
        BUTTON_SCALES,
        0.76,
    )
    if button is None:
        raise RuntimeError("enabled feed refill button template was not found")
    text = recognized_text(tasker, frame, (70, 680, 590, 300))
    if mill_remaining_seconds(text) != 0:
        raise RuntimeError(f"OCR did not confirm an empty feed mill: {text!r}")
    _, x, y, width, height = button
    click(controller, x + width // 2, y + height // 2)
    after = screenshot(controller)
    status = recognized_text(tasker, after, (70, 680, 590, 300))
    duration = mill_remaining_seconds(status)
    if duration is None or duration <= 0:
        raise RuntimeError(f"feed refill was not confirmed by OCR: {status!r}")
    print(f"feed refill started: {status}", flush=True)
    close_mill_popup(controller)


def collect_ready_sack(controller: Win32Controller, tasker: Tasker, frame, box) -> dict:
    _, x, y, width, height = box
    count_before = read_sack_count(tasker, frame, box)
    if count_before is None or count_before <= 0:
        raise RuntimeError("feed sack counter is unreadable or empty; refusing collection")
    if not post_click_normalized(controller, x + width // 2, y + height // 2).wait().succeeded:
        raise RuntimeError("feed sack collection click failed")
    time.sleep(0.7)
    after = screenshot(controller)
    remaining = find_ready_sack(after, box)
    if remaining is None:
        # Clicking the structure instead of the sack can open its popup. A
        # hidden marker is not collection evidence on an obstructed screen.
        if mill_remaining_seconds(recognized_text(tasker, after, (70, 680, 590, 300))) is not None or match(after, "phone_icon.png", (610, 1210, 105, 140)) < 0.68:
            raise RuntimeError("sack disappeared behind a popup; collection is not confirmed")
        return {"status": "collected", "sacks_before": count_before, "sack_marker_score": round(box[0], 3)}
    count_after = read_sack_count(tasker, after, remaining)
    # Verify the new counter on a second frame; a partially rendered badge
    # must not make a real 50 look like 0 and falsely confirm supplementation.
    time.sleep(0.5)
    stable_frame = screenshot(controller)
    stable_box = find_ready_sack(stable_frame, remaining)
    if stable_box is not None and read_sack_count(tasker, stable_frame, stable_box) != count_after:
        count_after = None
    if (
        count_before is not None
        and count_after is not None
        and count_after < count_before
    ):
        return {
            "status": "fed",
            "sacks_before": count_before,
            "sacks_after": count_after,
            "sack_marker_score": round(box[0], 3),
        }
    raise RuntimeError(
        f"feed action was not confirmed: sacks {count_before!r} -> {count_after!r}"
    )


def wait_and_collect(
    controller: Win32Controller,
    tasker: Tasker,
    timeout_seconds: int = FEED_TIMEOUT_SECONDS,
) -> dict:
    started = time.monotonic()
    while time.monotonic() - started <= timeout_seconds:
        frame, shortage, structure = find_silo(controller)
        if structure is None:
            ready = feed_match(frame, "feed_sack_icon.png", SACK_SCALES, 0.78)
            if ready is not None:
                result = collect_ready_sack(controller, tasker, frame, ready)
                result["wait_seconds"] = round(time.monotonic() - started, 1)
                return result
            structure = feed_match(
                frame,
                "feed_silo_structure.png",
                MAP_SCALES,
                0.70,
            )
        if structure is not None:
            ready = find_ready_sack(frame, structure)
            if ready is not None:
                result = collect_ready_sack(controller, tasker, frame, ready)
                result["wait_seconds"] = round(time.monotonic() - started, 1)
                return result
            if has_hungry_marker(tasker, frame):
                print("feed: livestock still hungry; waiting for the first sack", flush=True)
        tasker.clear_cache()
        time.sleep(POLL_SECONDS)
    raise TimeoutError(f"feed sack did not become ready in {timeout_seconds}s")


def ensure_ranch_feed(
    controller: Win32Controller,
    tasker: Tasker,
    timeout_seconds: int = FEED_TIMEOUT_SECONDS,
) -> dict:
    frame, shortage, structure = find_silo(controller)
    if structure is None:
        structure = feed_match(
            frame,
            "feed_silo_structure.png",
            MAP_SCALES,
            0.70,
        )
    if structure is None:
        ready = feed_match(frame, "feed_sack_icon.png", SACK_SCALES, 0.78)
        if ready is not None:
            return collect_ready_sack(controller, tasker, frame, ready)
        raise RuntimeError("feed silo could not be located")

    ready = find_ready_sack(frame, structure)
    if ready is not None:
        return collect_ready_sack(controller, tasker, frame, ready)

    opened = open_mill(controller, tasker, location=(frame, shortage, structure))
    text = opened['mill_text']
    duration = opened['remaining_seconds']
    if duration == 0:
        start_refill(controller, tasker)
    else:
        close_mill_popup(controller)
        if duration is None or duration <= 0:
            raise RuntimeError(f"mill has neither a refill action nor active production: {text!r}")
        print(f"feed production already active: {text}", flush=True)
    return wait_and_collect(controller, tasker, timeout_seconds)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout-seconds", type=int, default=FEED_TIMEOUT_SECONDS)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--inspect", action="store_true", help="locate the mill and save one Maa frame without refilling or collecting")
    parser.add_argument('--open-only', action='store_true', help='只定位并打开磨坊，确认标题后结束')
    args = parser.parse_args()
    if args.timeout_seconds < POLL_SECONDS:
        parser.error("timeout must be at least the polling interval")
    if sum((args.inspect, args.apply, args.open_only)) > 1:
        parser.error('choose inspect, open-only or apply, not more than one')
    if not args.apply and not args.inspect and not args.open_only:
        print("dry run: check the feed mill; add --apply to refill/collect when needed")
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
        controller.set_screenshot_target_short_side(720)
        if not controller.post_connection().wait().succeeded:
            raise RuntimeError("Maa Win32 connection failed")
        if not resource.post_bundle(RESOURCE).wait().succeeded:
            raise RuntimeError("Maa resource loading failed")
        if not tasker.bind(resource, controller):
            raise RuntimeError("Maa tasker binding failed")
        if not args.open_only or read_mill_state(tasker, screenshot(controller)) is None:
            close_employee_menu(controller)
        if args.open_only:
            open_mill(controller, tasker)
            return 0
        if args.inspect:
            frame, shortage, structure = find_silo(controller)
            structure = structure or feed_match(frame, "feed_silo_structure.png", MAP_SCALES, 0.70)
            sack = find_ready_sack(frame, structure) if structure else feed_match(frame, "feed_sack_icon.png", SACK_SCALES, 0.78)
            output = ROOT / ".tmp/current.png"
            output.parent.mkdir(parents=True, exist_ok=True)
            if not cv2.imwrite(str(output), frame):
                raise RuntimeError("could not save inspection frame")
            print(json.dumps({"shortage": shortage, "structure": structure, "sack": sack,
                "sack_count": read_sack_count(tasker, frame, sack) if sack else None}, ensure_ascii=False))
            return 0
        print(json.dumps(ensure_ranch_feed(controller, tasker, args.timeout_seconds), ensure_ascii=False))
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

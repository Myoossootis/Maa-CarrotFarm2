"""Wait for audited employee plans and collect finished products in background.

This does not move the physical cursor or bring the game to the foreground.
Only two consecutive on-target progress readings authorize a collection click.
Apply/collect defaults to the decoded state reader so a held recipe mismatch
becomes recipe_conflict_skip without exporting CARROT_STATE_READER.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
import time
from pathlib import Path

from maa.controller import Win32Controller
from maa.define import MaaWin32InputMethodEnum, MaaWin32ScreencapMethodEnum
from maa.resource import Resource
from maa.tasker import Tasker

try:
    from . import decoded_runtime as decoded
    from . import employee_operation as operations
except ImportError:
    import decoded_runtime as decoded
    import employee_operation as operations

try:
    from .runtime_state import write_json
    from .capture_current_frame_with_maa import find_window
    from .configure_field_employee import RESOURCE, SLOTS, CollectionUnconfirmedError, collect_finished_slot, close_employee_menu
    from .maa_frame_normalize import install_foreground_safe_inputs
    from .enter_farm import enter_farm
except ImportError:
    from runtime_state import write_json
    from capture_current_frame_with_maa import find_window
    from configure_field_employee import RESOURCE, SLOTS, CollectionUnconfirmedError, collect_finished_slot, close_employee_menu
    from maa_frame_normalize import install_foreground_safe_inputs
    from enter_farm import enter_farm



RESULT = Path(__file__).resolve().parents[1] / "assets/catalog/employee_work/latest_monitor.json"


# Slow field crops: rose/sugarcane-style growth needs a longer monitor window
# than the default 2700s short-cycle budget when plans are large.
CROP_MONITOR_TIMEOUT_HINTS = {
    "玫瑰": 3600,
    "甘蔗": 3600,
}


def format_order_need(allocation):
    """One line: which order, each item's need / stock / gap."""
    items = (allocation or {}).get('target_items') or []
    order = (allocation or {}).get('target_order')
    if not items:
        return f'[订单] 订单{order} 没有需求明细'
    parts = [
        f"{row.get('name')} 需{row.get('required')} 有{row.get('actual')} 缺{row.get('missing')}"
        for row in items
    ]
    return f"[订单] 订单{order}：" + '；'.join(parts)


def current_cycle_remaining(slot_id, data):
    """Longest reliable timer on slots already growing. None when unread."""
    if not data:
        return None
    try:
        entry = decoded.plan(slot_id, data)
    except decoded.DecodeError:
        return {'state': 'unread'}
    areas = [area for area in (data.get('production') or {}).get('buildings') or []
             if area.get('buildingUid') == entry.get('buildingUid')]
    if len(areas) != 1:
        return {'state': 'unread'}
    growing = 0
    seconds = []
    for slot in areas[0].get('slots') or []:
        if slot.get('status') not in ('growing', 'needs_feed', 'mature'):
            continue
        growing += 1
        remaining = slot.get('remainingSeconds')
        if slot.get('timingReliable') is True and type(remaining) is int and remaining >= 0:
            seconds.append(remaining)
    if not growing:
        return {'state': 'not_started'}
    if not seconds:
        return {'state': 'unknown', 'growing': growing}
    return {'state': 'timed', 'seconds': max(seconds), 'growing': growing}


def format_cycle_time(info):
    if not info or info.get('state') != 'timed':
        return '剩余时间未判定'
    seconds = info['seconds']
    if seconds <= 0:
        return '这轮已到时'
    return f"这轮剩余约{(seconds + 59) // 60}分钟"


def format_watch_line(slot_id, items, progress, cycle):
    label = SLOTS.get(slot_id, {}).get('label', slot_id)
    plan = '、'.join(f'{name}×{qty}' for name, qty in (items or {}).items()) or '空'
    if progress:
        done, total = progress
        count = f'{done}/{total} 还差{max(0, total - done)}'
    else:
        count = '进度未知'
    return f'[进度] {label} {plan} {count}；{format_cycle_time(cycle)}'


def suggested_timeout_seconds(
    items_by_slot: dict[str, dict[str, int]] | None,
    default: int = 2700,
) -> int:
    """Raise timeout when any assigned crop is known-slow."""
    if not items_by_slot:
        return default
    timeout = default
    for items in items_by_slot.values():
        for crop, qty in items.items():
            hint = CROP_MONITOR_TIMEOUT_HINTS.get(crop)
            if hint is None:
                continue
            # Scale gently with quantity; keep a sane upper bound.
            timeout = max(timeout, min(7200, hint + max(0, qty - 10) * 30))
    return timeout




def select_collect_reader():
    """Use decoded state for collect/resume unless the operator opts into OCR.

    An unset ``CARROT_STATE_READER`` used to mean OCR. A saved recipe that no
    longer matched a slot still holding products then never became
    ``recipe_conflict_skip``, and that slot kept OCR-polling until timeout.
    Explicit ``CARROT_STATE_READER=ocr`` keeps recognition and does not arm
    the skip. Explicit ``decoded`` is unchanged.
    """
    mode = os.environ.get('CARROT_STATE_READER')
    if mode is None or mode == '':
        os.environ['CARROT_STATE_READER'] = 'decoded'
        return 'decoded'
    if mode not in ('ocr', 'decoded'):
        raise decoded.DecodeError('Unknown CARROT_STATE_READER: ' + mode)
    return mode


def _close_employee_menu_with_retry(controller) -> None:
    """Close employee UI; retry once if the first pass fails map verification."""
    try:
        close_employee_menu(controller)
    except RuntimeError as first_error:
        print(f"close_employee_menu retry after: {first_error}", flush=True)
        time.sleep(0.5)
        close_employee_menu(controller)
    # Phone/employee UI may return to the restaurant, where farm production
    # does not advance. Wait on the actual farm rather than any map chrome.
    enter_farm(controller)


def _collect_monitored_plans(plans: dict[str, int], poll_seconds: int, timeout_seconds: int, expected_items: dict | None = None, operation=None) -> dict:
    controller = Win32Controller(
        hWnd=find_window(),
        screencap_method=MaaWin32ScreencapMethodEnum.FramePool,
        mouse_method=MaaWin32InputMethodEnum.PostMessage,
        keyboard_method=MaaWin32InputMethodEnum.PostMessage,
    )
    controller.set_screenshot_target_short_side(720)
    install_foreground_safe_inputs()
    resource = Resource()
    tasker = Tasker()
    collected: dict[str, str] = {}
    started = time.monotonic()
    errors: dict[str, int] = {}
    reset_slots: list[str] = []
    cleared_slots: list[str] = []
    conflict_slots: list[str] = []
    observations = {}
    def checkpoint(status):
        RESULT.parent.mkdir(parents=True, exist_ok=True)
        write_json(RESULT, {"status": status, "plans": plans, "collected_at_utc": collected,
            "assignment_id": operation['assignment_id'] if operation else None,
            "reset_slots": reset_slots, "cleared_slots": cleared_slots, "observations": observations,
            "recipe_conflict_slots": list(conflict_slots),
            "pending": [slot for slot in plans if slot not in collected and slot not in conflict_slots],
            "elapsed_seconds": round(time.monotonic() - started, 1)})
    try:
        tasker.set_save_draw(False)
        tasker.set_save_on_error(False)
        tasker.set_reco_image_cache_limit(16)
        tasker.set_stdout_level(2)
        if not controller.post_connection().wait().succeeded:
            raise RuntimeError("Maa Win32 connection failed")
        if not resource.post_bundle(RESOURCE).wait().succeeded:
            raise RuntimeError("Maa OCR resources failed to load")
        if not tasker.bind(resource, controller):
            raise RuntimeError("Maa tasker binding failed")
        checkpoint("monitoring")
        reported = {}
        if operation:
            print(format_order_need(operation.get('allocation')), flush=True)
        while len(collected) < len(plans):
            if time.monotonic() - started > timeout_seconds:
                break
            conflict_slots.clear()
            for slot_id, required in plans.items():
                if slot_id in collected:
                    continue
                data = None
                try:
                    if decoded.enabled() and expected_items is not None:
                        data = decoded.snapshot()
                        pending_claim = operations.collection_record(operation, slot_id) if operation else None
                        if operation:
                            operations.verify_owner(operation, slot_id, data)
                        if pending_claim and pending_claim['status'] in ('intent', 'unconfirmed', 'confirmed'):
                            result = operations.collect(operation, slot_id, decoded.snapshot,
                                lambda *args: (_ for _ in ()).throw(decoded.DecodeError('Recovery cannot send new collection input')))
                            collected[slot_id] = datetime.now(timezone.utc).isoformat()
                            cleared_slots.append(slot_id)
                            observations[slot_id] = result
                            checkpoint('monitoring')
                            continue
                        current_items = decoded.plan_items(slot_id, data)
                        if current_items != expected_items[slot_id]:
                            entry = decoded.plan(slot_id, data)
                            if operations.recipe_conflict_held(entry, current_items, expected_items[slot_id]):
                                # Another saved slot may still match. Do not click or abort the batch.
                                result = dict(collected=False, reason='recipe_conflict_skip',
                                              game_operations=0, progress=None)
                                errors[slot_id] = 0
                                observations[slot_id] = result
                                conflict_slots.append(slot_id)
                                label = SLOTS.get(slot_id, {}).get('label', slot_id)
                                line = f'[进度] {label} 配方与已保存计划不一致且仍持有产物，跳过收取且不点击'
                                if reported.get(slot_id) != line:
                                    reported[slot_id] = line
                                    print(line, flush=True)
                                checkpoint('monitoring')
                                continue
                            raise decoded.DecodeError(f'{slot_id}: decoded plan identity differs from the saved assignment; no collection click')
                        progress = decoded.progress(slot_id, required, data)
                    else:
                        progress = None
                    if progress is not None and progress[0] < required:
                        # Stay on the farm while unfinished; opening the list
                        # pauses production and can leave it scrolling at claim time.
                        result = dict(collected=False, progress=progress, reason='decoded_not_finished')
                    else:
                        if operation:
                            def claim(candidate, reserve, evidence):
                                return collect_finished_slot(controller, slot_id, tasker,
                                    required_progress=required, before_click=reserve)
                            result = operations.collect(operation, slot_id, decoded.snapshot, claim)
                        else:
                            result = collect_finished_slot(controller, slot_id, tasker, required_progress=required)
                except CollectionUnconfirmedError:
                    # Harvest may already have cleared a one-shot plan. Do not
                    # retry an input whose outcome is unknown.
                    raise
                except decoded.DecodeError:
                    raise
                except RuntimeError as error:
                    errors[slot_id] = errors.get(slot_id, 0) + 1
                    print(f"{slot_id}: recognition retry {errors[slot_id]}: {error}", flush=True)
                    if errors[slot_id] >= 3:
                        raise
                    continue
                errors[slot_id] = 0
                observations[slot_id] = result
                progress = result.get("progress")
                cycle = current_cycle_remaining(slot_id, data) if data else None
                label = SLOTS.get(slot_id, {}).get('label', slot_id)
                line = (f'[进度] {label} 已收取' if result.get('collected') else
                        format_watch_line(slot_id, (expected_items or {}).get(slot_id), progress, cycle))
                if reported.get(slot_id) != line:
                    reported[slot_id] = line
                    print(line, flush=True)
                checkpoint("monitoring")
                if result["collected"]:
                    collected[slot_id] = datetime.now(timezone.utc).isoformat()
                    checkpoint("collected")
                    # Decoded mode already confirmed both clearance and inventory.
                    # Never open/reset a plan after harvesting.
                    cleared_slots.append(slot_id)
                    checkpoint("monitoring")
            tasker.clear_cache()
            if not [slot for slot in plans if slot not in collected and slot not in conflict_slots]:
                break
            if len(collected) < len(plans):
                # The employee work page stops the farm simulation timer. Leave
                # it before sleeping so crops and livestock keep producing.
                _close_employee_menu_with_retry(controller)
                time.sleep(poll_seconds)
        # Leave the list closed on completion as well; this keeps the next
        # order scan on the farm map and avoids a redundant UI action.
        _close_employee_menu_with_retry(controller)
        if operation and len(collected) == len(plans):
            operations.mark(operation, 'collected')
        if len(collected) == len(plans):
            final_status = "complete"
        elif conflict_slots and all(slot in collected or slot in conflict_slots for slot in plans):
            final_status = "recipe_conflict_skip"
        else:
            final_status = "timed_out"
        checkpoint(final_status)
        return {
            "complete": len(collected) == len(plans),
            "collected_at_utc": collected,
            "pending": [slot_id for slot_id in plans if slot_id not in collected and slot_id not in conflict_slots],
            "recipe_conflict_slots": list(conflict_slots),
            "reset_slots": reset_slots,
            "cleared_slots": cleared_slots,
            "elapsed_seconds": round(time.monotonic() - started, 1),
        }
    except Exception as error:
        failed = {
            "status": "failed",
            "plans": plans,
            "collected_at_utc": collected,
            "reset_slots": reset_slots,
            "cleared_slots": cleared_slots,
            "observations": observations,
            "recipe_conflict_slots": list(conflict_slots),
            "pending": [slot for slot in plans if slot not in collected and slot not in conflict_slots],
            "elapsed_seconds": round(time.monotonic() - started, 1),
            "error": str(error),
            "error_type": type(error).__name__,
        }
        RESULT.parent.mkdir(parents=True, exist_ok=True)
        write_json(RESULT, failed)
        raise
    finally:
        tasker.clear_cache()
        for value in (tasker, resource, controller):
            destructor = getattr(value, "__del__", None)
            if callable(destructor):
                destructor()
            if hasattr(value, "_handle"):
                value._handle = None


def monitor(plans: dict[str, int], poll_seconds: int, timeout_seconds: int, expected_items: dict | None = None, operation=None) -> dict:
    """Collect/resume entry. Decoded state is the default so conflict skips arm."""
    previous_reader = os.environ.get('CARROT_STATE_READER')
    reader_was_unset = previous_reader is None or previous_reader == ''
    select_collect_reader()
    try:
        return _collect_monitored_plans(plans, poll_seconds, timeout_seconds, expected_items, operation)
    finally:
        if reader_was_unset:
            if previous_reader is None:
                os.environ.pop('CARROT_STATE_READER', None)
            else:
                os.environ['CARROT_STATE_READER'] = previous_reader


def main() -> int:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--plans", nargs="+", help="slot=planned quantity for one-shot plans; collect only on target")
    source.add_argument("--assignment-file", type=Path, help="verified one-shot assignment; collect and verify automatic clearance")
    parser.add_argument("--poll-seconds", type=int, default=30)
    parser.add_argument("--timeout-seconds", type=int, default=2700)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.apply:
        # Bind the operation and arm recipe_conflict_skip before any collect click.
        # Callers such as run_normal_order_loop inherit this; they need not export
        # CARROT_STATE_READER. OCR remains an explicit opt-in.
        mode = select_collect_reader()
        source_label = '解码' if mode == 'decoded' else '识图'
        print(f'[数据源] 监控收取使用{source_label}（CARROT_STATE_READER={mode}）', flush=True)
        if mode == 'ocr':
            print('[数据源] 显式识图不会启用 recipe_conflict_skip；配方冲突岗位会继续轮询。', flush=True)
    plans: dict[str, int] = {}
    expected_items = None
    operation = None
    if args.assignment_file:
        assignment = json.loads(args.assignment_file.read_text(encoding="utf-8"))
        if assignment.get("status") != "saved" or not assignment.get("saved") or not all(job.get("verified") for job in assignment["saved"]):
            parser.error("assignment must contain only verified saved plans")
        if any(job.get("repeat") is not False or job.get("one_shot") is not True for job in assignment["saved"]):
            parser.error("assignment must explicitly contain one-shot plans (repeat=false, one_shot=true)")
        expected_items = {job["slot_id"]: job["items"] for job in assignment["saved"]}
        if decoded.enabled():
            operation = operations.active()
            if (not operation or assignment.get('assignment_id') != operation['assignment_id']
                    or assignment.get('operation_dir') != operation['operation_dir']
                    or expected_items != {job['slot_id']: job['items'] for job in operation.get('saved', [])}):
                parser.error('Decoded monitoring requires the active bound employee assignment')
        entries = [f"{slot}={sum(items.values())}" for slot, items in expected_items.items()]
    else:
        entries = args.plans
    for entry in entries:
        slot_id, separator, raw_count = entry.partition("=")
        if not separator or slot_id not in SLOTS or slot_id in plans:
            parser.error(f"invalid or duplicate slot plan: {entry}")
        try:
            count = int(raw_count)
        except ValueError:
            parser.error(f"invalid planned quantity: {entry}")
        if not 1 <= count <= SLOTS[slot_id]["capacity"]:
            parser.error(f"planned quantity out of range: {entry}")
        plans[slot_id] = count
    if not 5 <= args.poll_seconds <= 300 or args.timeout_seconds < args.poll_seconds:
        parser.error("invalid polling or timeout interval")
    hint = suggested_timeout_seconds(expected_items, args.timeout_seconds)
    if expected_items and hint > args.timeout_seconds:
        print(
            f"timeout hint for slow crops: {hint}s (current --timeout-seconds={args.timeout_seconds})",
            flush=True,
        )
    if not args.apply:
        print(f"dry run: monitor {plans}; add --apply to collect finished supplies")
        return 0
    if decoded.enabled() and operation is None:
        parser.error('Decoded collection requires --assignment-file with bound operation identity')
    timeout_seconds = args.timeout_seconds
    if expected_items and hint > timeout_seconds:
        # Auto-raise only when assignment carries known-slow crops.
        timeout_seconds = hint
        print(f"using raised timeout_seconds={timeout_seconds}", flush=True)
    result = monitor(plans, args.poll_seconds, timeout_seconds, expected_items, operation)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return 0 if result["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

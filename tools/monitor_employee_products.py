"""Wait for audited employee plans and collect finished products in background.

This does not move the physical cursor or bring the game to the foreground.
Only two consecutive on-target progress readings authorize a collection click.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import time
from pathlib import Path

from maa.controller import Win32Controller
from maa.define import MaaWin32InputMethodEnum, MaaWin32ScreencapMethodEnum
from maa.resource import Resource
from maa.tasker import Tasker

try:
    from . import decoded_runtime as decoded
except ImportError:
    import decoded_runtime as decoded

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


def monitor(plans: dict[str, int], poll_seconds: int, timeout_seconds: int, expected_items: dict | None = None) -> dict:
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
    observations = {}
    def checkpoint(status):
        RESULT.parent.mkdir(parents=True, exist_ok=True)
        write_json(RESULT, {"status": status, "plans": plans, "collected_at_utc": collected,
            "reset_slots": reset_slots, "cleared_slots": cleared_slots, "observations": observations,
            "pending": [slot for slot in plans if slot not in collected],
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
        while len(collected) < len(plans):
            if time.monotonic() - started > timeout_seconds:
                break
            for slot_id, required in plans.items():
                if slot_id in collected:
                    continue
                try:
                    if decoded.enabled() and expected_items is not None:
                        data = decoded.snapshot()
                        if decoded.plan_items(slot_id, data) != expected_items[slot_id]:
                            raise decoded.DecodeError(f'{slot_id}: decoded plan identity differs from the saved assignment; no collection click')
                        progress = decoded.progress(slot_id, required, data)
                    else:
                        progress = None
                    if progress is not None and progress[0] < required:
                        # Stay on the farm while unfinished; opening the list
                        # pauses production and can leave it scrolling at claim time.
                        result = dict(collected=False, progress=progress, reason='decoded_not_finished')
                    else:
                        result = collect_finished_slot(
                            controller, slot_id, tasker, required_progress=required
                        )
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
                if progress is not None:
                    print(
                        f"{slot_id}: progress {progress[0]}/{progress[1]} collected={result['collected']}",
                        flush=True,
                    )
                else:
                    print(
                        f"{slot_id}: progress unknown collected={result['collected']} reason={result.get('reason')}",
                        flush=True,
                    )
                checkpoint("monitoring")
                if result["collected"]:
                    collected[slot_id] = datetime.now(timezone.utc).isoformat()
                    checkpoint("collected")
                    print(f"{slot_id}: collected", flush=True)
                    # Decoded mode already confirmed both clearance and inventory.
                    # Never open/reset a plan after harvesting.
                    cleared_slots.append(slot_id)
                    checkpoint("monitoring")
            tasker.clear_cache()
            if len(collected) < len(plans):
                # The employee work page stops the farm simulation timer. Leave
                # it before sleeping so crops and livestock keep producing.
                _close_employee_menu_with_retry(controller)
                time.sleep(poll_seconds)
        # Leave the list closed on completion as well; this keeps the next
        # order scan on the farm map and avoids a redundant UI action.
        _close_employee_menu_with_retry(controller)
        checkpoint("complete" if len(collected) == len(plans) else "timed_out")
        return {
            "complete": len(collected) == len(plans),
            "collected_at_utc": collected,
            "pending": [slot_id for slot_id in plans if slot_id not in collected],
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
            "pending": [slot for slot in plans if slot not in collected],
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


def main() -> int:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--plans", nargs="+", help="slot=planned quantity for one-shot plans; collect only on target")
    source.add_argument("--assignment-file", type=Path, help="verified one-shot assignment; collect and verify automatic clearance")
    parser.add_argument("--poll-seconds", type=int, default=30)
    parser.add_argument("--timeout-seconds", type=int, default=2700)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    plans: dict[str, int] = {}
    expected_items = None
    if args.assignment_file:
        assignment = json.loads(args.assignment_file.read_text(encoding="utf-8"))
        if assignment.get("status") != "saved" or not assignment.get("saved") or not all(job.get("verified") for job in assignment["saved"]):
            parser.error("assignment must contain only verified saved plans")
        if any(job.get("repeat") is not False or job.get("one_shot") is not True for job in assignment["saved"]):
            parser.error("assignment must explicitly contain one-shot plans (repeat=false, one_shot=true)")
        expected_items = {job["slot_id"]: job["items"] for job in assignment["saved"]}
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
    timeout_seconds = args.timeout_seconds
    if expected_items and hint > timeout_seconds:
        # Auto-raise only when assignment carries known-slow crops.
        timeout_seconds = hint
        print(f"using raised timeout_seconds={timeout_seconds}", flush=True)
    result = monitor(plans, args.poll_seconds, timeout_seconds, expected_items)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return 0 if result["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

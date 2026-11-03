"""Apply a freshly scanned normal-order shortage to audited employees.

Default mode prints a plan only. --apply first checks every affected employee
has an empty plan, then uses Maa FramePool/PostMessage to save each plan.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
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
    from .allocate_employee_work import plan_latest, allocate
    from .employee_capabilities import normalize_product_name, slot_can_produce, load_capabilities
    from .configure_field_employee import (
        RESOURCE,
        screenshot,
        SLOTS,
        capacity,
        close_plan_dialog,
        close_employee_menu,
        configure_items,
        open_plan,
        find_slot_action,
        verify_saved_plan,
    )
    from .capture_current_frame_with_maa import find_window
    from .ensure_ranch_feed import ensure_ranch_feed, has_hungry_marker
    from .enter_farm import enter_farm
    from .maa_frame_normalize import install_foreground_safe_inputs
except ImportError:
    from runtime_state import write_json
    from allocate_employee_work import plan_latest, allocate
    from employee_capabilities import normalize_product_name, slot_can_produce, load_capabilities
    from configure_field_employee import (
        RESOURCE,
        screenshot,
        SLOTS,
        capacity,
        close_plan_dialog,
        close_employee_menu,
        configure_items,
        open_plan,
        find_slot_action,
        verify_saved_plan,
    )
    from capture_current_frame_with_maa import find_window
    from ensure_ranch_feed import ensure_ranch_feed, has_hungry_marker
    from enter_farm import enter_farm
    from maa_frame_normalize import install_foreground_safe_inputs


def check_all_idle(controller: Win32Controller, tasker: Tasker, jobs: list[dict]) -> None:
    if decoded.enabled():
        data = decoded.snapshot()
        if any(decoded.assignment_blocker(job['slot_id'], data) for job in jobs):
            raise decoded.DecodeError('Decoded employee or production slot is unavailable; preserving existing work')
        return
    for job in jobs:
        slot_id = job["slot_id"]
        frame, _ = open_plan(controller, slot_id)
        observed = capacity(tasker, frame)
        if observed != (0, SLOTS[slot_id]["capacity"]):
            raise RuntimeError(
                f"{SLOTS[slot_id]['label']} has an active or unreadable plan: {observed}"
            )
        # Confirmed in-game: a blank tap outside the dialog closes it without
        # saving. The previous plan remains untouched.
        close_plan_dialog(controller)


def idle_allocation(controller, tasker, targets, *, allow_partial=False):
    """Read candidate plans without collecting, resetting or overwriting them."""
    aliases = load_capabilities().get("product_aliases", {})
    products = {normalize_product_name(target["name"], aliases) for target in targets}
    idle = []
    busy = []
    data = decoded.snapshot() if decoded.enabled() else None
    for slot_id, slot in SLOTS.items():
        if not any(slot_can_produce(slot, name, aliases=aliases) for name in products):
            continue
        print(f'[排产] 检查空闲岗位 {slot_id}', flush=True)
        if decoded.enabled():
            blocker = decoded.assignment_blocker(slot_id, data)
            if blocker is None:
                idle.append(slot)
            else:
                busy.append({'slot_id': slot_id, 'state': blocker})
            continue
        _, state = find_slot_action(controller, slot_id)
        if state == "collect_supplies":
            busy.append({"slot_id": slot_id, "state": state})
            continue
        frame, _ = open_plan(controller, slot_id)
        observed = capacity(tasker, frame)
        close_plan_dialog(controller)
        if observed == (0, slot["capacity"]):
            idle.append(slot)
        elif observed is None:
            raise RuntimeError(f"cannot verify availability of {slot['label']}")
        else:
            busy.append({"slot_id": slot_id, "state": "active", "capacity": observed})
    result = allocate(targets, idle)
    result["busy_slots_preserved"] = busy
    if (allow_partial and not result['ready'] and result['jobs'] and result['unmet']
            and all(entry['reason'] == 'insufficient_employee_capacity' for entry in result['unmet'])):
        # Ready describes this bounded wave, never completion of the full order.
        result.update(ready=True, partial_wave=True, order_fulfilled_after_wave=False)
        print(f"[排产] 本批按空闲容量补料，剩余缺口收取后重新扫描：{result['unmet']}", flush=True)
    if not result["ready"]:
        # allocate() only sees idle slots; if capable workers exist but are busy,
        # it reports no_employee_capability — rewrite for clarity.
        aliases = load_capabilities().get("product_aliases", {})
        capable = {
            normalize_product_name(name, aliases)
            for slot in SLOTS.values()
            for name in (slot.get("products") or [])
            if slot_can_produce(slot, name, aliases=aliases)
        }
        for entry in result.get("unmet", []):
            if entry.get("reason") == "no_employee_capability" and normalize_product_name(entry.get("name", ""), aliases) in capable:
                entry["reason"] = "all_capable_slots_busy"
        raise RuntimeError(f"not enough idle employee capacity; existing plans preserved: {result}")
    return result


def apply_jobs(allocation: dict, checkpoint=None, operation=None, *, allow_partial=False) -> dict:
    if not allocation["ready"] and not allow_partial:
        raise RuntimeError(f"order cannot be fully allocated: {allocation['unmet']}")
    controller = Win32Controller(
        hWnd=find_window(),
        screencap_method=MaaWin32ScreencapMethodEnum.FramePool,
        mouse_method=MaaWin32InputMethodEnum.PostMessage,
        keyboard_method=MaaWin32InputMethodEnum.PostMessage,
    )
    resource = Resource()
    tasker = Tasker()
    saved: list[dict] = []
    ranch_feed = None
    try:
        tasker.set_save_draw(False)
        tasker.set_save_on_error(False)
        tasker.set_reco_image_cache_limit(16)
        tasker.set_stdout_level(2)
        controller.set_screenshot_target_short_side(720)
        install_foreground_safe_inputs()
        if not controller.post_connection().wait().succeeded:
            raise RuntimeError("Maa Win32 connection failed")
        if not resource.post_bundle(RESOURCE).wait().succeeded:
            raise RuntimeError("Maa resource loading failed")
        if not tasker.bind(resource, controller):
            raise RuntimeError("Maa tasker binding failed")
        if operation is None:
            if decoded.enabled():
                operations.validate_target(allocation, decoded.snapshot())
            available = idle_allocation(controller, tasker, allocation["production_targets"], allow_partial=allow_partial)
            allocation.update(available)
            print(f"preserved existing plans: {available['busy_slots_preserved']}", flush=True)
            if decoded.enabled():
                operation = operations.begin(allocation)
        if operation:
            allocation.update(assignment_id=operation['assignment_id'], operation_dir=operation['operation_dir'])
        # idle_allocation already checked every chosen slot; configure_items rechecks on save.
        close_employee_menu(controller)
        if any(job["slot_id"].startswith("ranch_") for job in allocation["jobs"]):
            enter_farm(controller)
            from_frame = screenshot(controller)
            if has_hungry_marker(tasker, from_frame):
                if operation:
                    raise decoded.DecodeError('Ranch feed requires separate reconciliation; no unjournaled refill')
                print('[牧场] 识别到饿了，补充饲料', flush=True)
                ranch_feed = ensure_ranch_feed(controller, tasker)
            else:
                ranch_feed = {'status': 'no_hunger_observed'}
                print('[牧场] 未出现饿了提示，直接排产', flush=True)
        for job in allocation["jobs"]:
            print(f"[排产] {job['slot_id']} items={job['items']}", flush=True)
            previous = operations.read(operations.job_path(operation, job['slot_id'])) if operation else None
            if previous:
                result = operations.reconcile_save(operation, job['slot_id'], decoded.snapshot())
                saved.append({**result, 'verified': True})
                if checkpoint:
                    checkpoint(saved, ranch_feed)
                continue
            if operation:
                operations.validate_target(allocation, decoded.snapshot())
                operations.begin_edit(operation, job['slot_id'], job['items'], decoded.snapshot())
            result = configure_items(
                controller, tasker, job["slot_id"], job["items"], repeat=False,
                **({'before_save': lambda data, slot=job['slot_id']: operations.reserve_save(operation, slot, data)}
                   if operation else {})
            )
            if operation:
                operations.reconcile_save(operation, job['slot_id'], decoded.snapshot())
            saved.append({"slot_id": job["slot_id"], **result, "verified": False})
            if checkpoint:
                checkpoint(saved, ranch_feed)
            if result.get('repeat') is not False or result.get('one_shot') is not True:
                raise RuntimeError(f"{job['slot_id']}: one-shot plan was not confirmed")
            verify_saved_plan(controller, tasker, job["slot_id"], job["items"])
            saved[-1]["verified"] = True
            if checkpoint:
                checkpoint(saved, ranch_feed)
            tasker.clear_cache()
        close_employee_menu(controller)
        enter_farm(controller)
        if operation:
            operations.mark(operation, 'saved')
        return {
            "target_order": allocation["target_order"],
            "ranch_feed": ranch_feed,
            "busy_slots_preserved": allocation.get("busy_slots_preserved", []),
            "saved": saved,
            "assignment_id": allocation.get('assignment_id'),
            "operation_dir": allocation.get('operation_dir'),
        }
    except Exception as error:
        raise RuntimeError(
            f"employee plan stopped: {error}; confirmed saved slots: "
            f"{[entry['slot_id'] for entry in saved]}"
        ) from error
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
    parser.add_argument("--apply", action="store_true", help="save plans in the game")
    parser.add_argument('--resume', action='store_true', help='resume the bound active assignment, never replay a save')
    parser.add_argument('--allow-partial', action='store_true', help='one capacity-limited wave; rescan after collection before further production')
    args = parser.parse_args()
    if args.allow_partial and not decoded.enabled():
        parser.error('Partial waves require the formal decoded worker and bound operation records')
    operation = operations.unfinished_active() if args.apply and decoded.enabled() else None
    if args.resume and not operation:
        parser.error('No unfinished bound employee operation')
    allocation = operation['allocation'] if operation else plan_latest()
    if not args.apply:
        print(json.dumps(allocation, ensure_ascii=False, indent=2))
        return 0 if allocation["ready"] else 2
    record = {
        'state_reader': 'decoded' if decoded.enabled() else 'ocr',
        "saved_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_generated_at_utc": allocation["source_generated_at_utc"],
        "target_order": allocation["target_order"],
        "target_items": allocation["target_items"],
        "production_targets": allocation["production_targets"],
        "status": "applying",
        "ranch_feed": None,
        "saved": [],
    }
    destination = Path(__file__).resolve().parents[1] / "assets/catalog/employee_work/latest_assignment.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    def checkpoint(saved, feed):
        record.update(saved=saved, ranch_feed=feed)
        record.update(assignment_id=allocation.get('assignment_id'), operation_dir=allocation.get('operation_dir'))
        write_json(destination, record)
    checkpoint([], None)
    try:
        result = apply_jobs(allocation, checkpoint, operation, allow_partial=args.allow_partial)
    except Exception as error:
        record.update(status="failed", error=str(error))
        checkpoint(record["saved"], record["ranch_feed"])
        raise
    record["status"] = "saved"
    record["busy_slots_preserved"] = result.get("busy_slots_preserved", [])
    checkpoint(result["saved"], result["ranch_feed"])
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

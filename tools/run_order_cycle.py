"""DEPRECATED for long hangs — prefer tools/run_short_cycle.py.

Kept for compatibility. Short cycle rematches submit by shortage crops and
checkpoints under .tmp/short_cycle/. This script still runs a bounded
orders -> plan -> monitor -> complete loop via Maa FramePool/PostMessage.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone

try:
    from .production_targets import order_signature, shortage_focus_was_submitted, target_shortage_names
    from .runtime_state import write_json
except ImportError:
    from production_targets import order_signature, shortage_focus_was_submitted, target_shortage_names
    from runtime_state import write_json

ROOT = Path(__file__).resolve().parents[1]
COMPLETION = ROOT / "assets/catalog/normal_orders/latest_completion.json"
CYCLE = ROOT / "assets/catalog/order_cycle/latest_cycle.json"
ASSIGNMENT = ROOT / "assets/catalog/employee_work/latest_assignment.json"


def target_was_submitted(state: dict, target: dict) -> bool:
    return any(
        entry["order"] == target["order"]
        and entry.get("acceptance_confirmed") is True
        and order_signature(entry["items"]) == order_signature(target["items"])
        for entry in state.get("submitted", [])
    )


def run_script(name: str, *arguments: str) -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / name), *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"{name} failed: {result.stderr.strip() or result.stdout.strip()}")


def read_completion() -> dict:
    return json.loads(COMPLETION.read_text(encoding="utf-8"))


def scan_until_target(max_cycles: int) -> dict:
    stagnant = 0
    for attempt in range(1, max_cycles + 1):
        run_script("complete_normal_orders.py")
        state = read_completion()
        print(
            f"order pass {attempt}: submitted={state.get('submitted_count', 0)} "
            f"status={state.get('status')} "
            f"target={state.get('target_order', {}).get('order') if state.get('target_order') else None}",
            flush=True,
        )
        if state.get("returned_to_farm") and state.get("target_order"):
            return state
        if state.get("returned_to_farm") and state.get("no_acceptable_orders") and not state.get("target_order"):
            # Inventory covers nothing employee-feasible; surface that cleanly.
            return state
        if state.get("status") == "existing_order_in_progress":
            stagnant += 1
        elif state.get("submitted_count"):
            stagnant += 1
        else:
            stagnant = 0
        # Same board kept reporting submit/in-progress without a plant target.
        if stagnant >= 3 and state.get("returned_to_farm"):
            return state
    raise RuntimeError(f"no production target after {max_cycles} order passes")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-order-passes", type=int, default=12)
    parser.add_argument("--production-timeout-seconds", type=int, default=2700)
    parser.add_argument("--max-production-rounds", type=int, default=3)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.max_order_passes <= 100:
        parser.error("--max-order-passes must be 1..100")
    if not 60 <= args.production_timeout_seconds <= 86400:
        parser.error("--production-timeout-seconds must be 60..86400")
    if not 1 <= args.max_production_rounds <= 10:
        parser.error("--max-production-rounds must be 1..10")
    if not args.apply:
        print("DEPRECATED long-cycle entry; prefer tools/run_short_cycle.py\n"
              "dry run: add --apply to scan, assign employees, collect, and recheck orders")
        return 0

    state = scan_until_target(args.max_order_passes)
    target = state.get("target_order")
    if not target:
        raise RuntimeError("no employee-feasible target order is available")
    record = {"started_at_utc": datetime.now(timezone.utc).isoformat(), "target": target,
              "status": "selected", "production": []}
    write_json(CYCLE, record)
    try:
        for round_number in range(1, args.max_production_rounds + 1):
            record["status"] = "assigning"
            write_json(CYCLE, record)
            print(f"production round {round_number}: target order {target['order']}", flush=True)
            run_script("run_employee_order_plan.py", "--apply")
            assignment = json.loads(ASSIGNMENT.read_text(encoding="utf-8"))
            if assignment.get("status") != "saved" or not all(job.get("verified") for job in assignment["saved"]):
                raise RuntimeError("employee assignments were not all verified")
            record["production"].append({"round": round_number, "assignment": assignment})
            record["status"] = "producing"
            write_json(CYCLE, record)
            run_script("monitor_employee_products.py", "--assignment-file", str(ASSIGNMENT),
                       "--timeout-seconds", str(args.production_timeout_seconds), "--apply")
            monitor_result = json.loads((ROOT / "assets/catalog/employee_work/latest_monitor.json").read_text(encoding="utf-8"))
            record["production"][-1]["monitor"] = monitor_result
            record["status"] = "confirming"
            write_json(CYCLE, record)
            run_script("complete_normal_orders.py", "--target-file", str(CYCLE))
            result = read_completion()
            if shortage_focus_was_submitted(result, target) or target_was_submitted(result, target):
                record.update(status="complete", submission=result["submitted"],
                              completed_at_utc=datetime.now(timezone.utc).isoformat())
                write_json(CYCLE, record)
                print(json.dumps({"target_order": target["order"], "accepted": True, "match": "shortage_or_signature"}, ensure_ascii=False))
                return 0
            next_target = result.get("target_order")
            # Continue only when the fresh board still needs overlapping shortage crops.
            if (
                not result.get("returned_to_farm")
                or not next_target
                or not (target_shortage_names(next_target) & target_shortage_names(target))
            ):
                raise RuntimeError(
                    "shortage focus could not be safely continued after production "
                    f"(prev={sorted(target_shortage_names(target))} "
                    f"next={sorted(target_shortage_names(next_target) if next_target else [])})"
                )
            target = next_target
            record["target"] = target
            print(
                "overlapping shortage crops remain; producing the freshly observed remainder",
                flush=True,
            )
        raise RuntimeError(f"target order was not accepted after {args.max_production_rounds} production rounds")
    except Exception as error:
        record.update(status="failed", error=str(error))
        write_json(CYCLE, record)
        raise


if __name__ == "__main__":
    raise SystemExit(main())

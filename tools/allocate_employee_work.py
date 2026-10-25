"""Split a fresh order shortage across verified employee work-plan slots."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

try:
    from .production_targets import UNSUPPORTED_ORDER_PRODUCTS
    from .employee_capabilities import (
        load_capabilities,
        normalize_product_name,
        slot_can_produce,
        capable_slots,
    )
except ImportError:
    from production_targets import UNSUPPORTED_ORDER_PRODUCTS
    from employee_capabilities import (
        load_capabilities,
        normalize_product_name,
        slot_can_produce,
        capable_slots,
    )


ROOT = Path(__file__).resolve().parents[1]
CAPABILITIES = ROOT / "assets/catalog/employee_capabilities.json"
LATEST_ORDER = ROOT / "assets/catalog/normal_orders/latest_completion.json"
MAX_ORDER_AGE_SECONDS = 300


def allocate(targets: list[dict], slots: list[dict], aliases: dict[str, str] | None = None) -> dict:
    """Split shortages across slots that pass level-aware capability checks."""
    if aliases is None:
        aliases = load_capabilities().get("product_aliases", {})
    assignments = {slot["id"]: {"slot": slot["label"], "items": {}} for slot in slots}
    remaining_capacity = {slot["id"]: int(slot["capacity"]) for slot in slots}
    unmet = []
    ordered_targets = sorted(
        targets,
        key=lambda target: (
            len(capable_slots(slots, target["name"], aliases=aliases)),
            -int(target["needed"]),
            normalize_product_name(target["name"], aliases),
        ),
    )
    for target in ordered_targets:
        raw_name = target["name"]
        name = normalize_product_name(raw_name, aliases)
        if name in UNSUPPORTED_ORDER_PRODUCTS or raw_name in UNSUPPORTED_ORDER_PRODUCTS:
            raise RuntimeError("fish orders must be ignored before production allocation")
        needed = int(target["needed"])
        if needed <= 0:
            raise RuntimeError(f"invalid shortage for {name}: {needed}")
        for _ in range(needed):
            eligible = [
                slot
                for slot in slots
                if slot_can_produce(slot, name, aliases=aliases)
                and remaining_capacity[slot["id"]] > 0
            ]
            if not eligible:
                reason = (
                    "no_employee_capability"
                    if not capable_slots(slots, name, aliases=aliases)
                    else "insufficient_employee_capacity"
                )
                previous = next((entry for entry in unmet if entry["name"] == name), None)
                if previous is None:
                    previous = {"name": name, "missing": 0, "reason": reason}
                    unmet.append(previous)
                previous["missing"] += 1
                continue
            slot = min(
                eligible,
                key=lambda candidate: (
                    sum(assignments[candidate["id"]]["items"].values()),
                    len(candidate.get("products") or []),
                    -int(candidate.get("observed_energy") or 0),
                    candidate["id"],
                ),
            )
            # Store canonical name so configure/CROPS keys match.
            assignments[slot["id"]]["items"][name] = (
                assignments[slot["id"]]["items"].get(name, 0) + 1
            )
            remaining_capacity[slot["id"]] -= 1
    jobs = [
        {"slot_id": slot_id, **entry}
        for slot_id, entry in assignments.items()
        if entry["items"]
    ]
    return {"ready": not unmet, "jobs": jobs, "unmet": unmet}


def plan_latest(now: datetime | None = None) -> dict:
    source = json.loads(LATEST_ORDER.read_text(encoding="utf-8"))
    try:
        from . import decoded_runtime as decoded
    except ImportError:
        import decoded_runtime as decoded
    if decoded.enabled() and source.get('state_reader') != 'decoded':
        raise decoded.DecodeError('Formal decoded worker refuses an old OCR production checkpoint; rescan first')
    generated = source.get("generated_at_utc")
    if not generated:
        raise RuntimeError("order result has no timestamp; run a fresh order scan")
    observed_at = datetime.fromisoformat(generated)
    if observed_at.tzinfo is None:
        raise RuntimeError("order result timestamp has no timezone")
    current = now or datetime.now(timezone.utc)
    age = (current - observed_at).total_seconds()
    if age < 0 or age > MAX_ORDER_AGE_SECONDS:
        raise RuntimeError(f"order result is stale ({age:.0f}s old); run a fresh scan")
    if not source.get("returned_to_farm") or not source.get("target_order"):
        raise RuntimeError("order result has no production target returned to farm")
    targets = source["target_order"]["production_targets"]
    capabilities = load_capabilities()
    slots = capabilities["slots"]
    allocation = allocate(targets, slots, aliases=capabilities.get("product_aliases"))
    return {
        "target_order": source["target_order"]["order"],
        "target_items": source["target_order"].get("items", []),
        "bound_target": source['target_order'],
        "source_generated_at_utc": generated,
        "production_targets": targets,
        "order_age_seconds": round(age, 1),
        **allocation,
    }


def plan_decoded(source: dict, batch_count: int) -> dict:
    """Preview decoded normal orders through existing pure planning functions."""
    try:
        from .decoded_state_adapter import adapt_snapshot
        from .production_targets import prepare_order_target
    except ImportError:
        from decoded_state_adapter import adapt_snapshot
        from production_targets import prepare_order_target
    adapted = adapt_snapshot(source, {'normal': batch_count, 'party': 1, 'takeout': 1})
    target = prepare_order_target(adapted['orders']['normal'])
    capabilities = load_capabilities()
    try:
        from .decoded_runtime import assignment_blocker
    except ImportError:
        from decoded_runtime import assignment_blocker
    blockers = {slot['id']: assignment_blocker(slot['id'], source) for slot in capabilities['slots']}
    slots = [slot for slot in capabilities['slots'] if blockers[slot['id']] is None]
    allocation = allocate(target['production_targets'], slots, aliases=capabilities.get('product_aliases')) if target else None
    if allocation:
        for entry in allocation['unmet']:
            if entry['reason'] == 'no_employee_capability' and capable_slots(
                    capabilities['slots'], entry['name'], aliases=capabilities.get('product_aliases')):
                entry['reason'] = 'all_capable_slots_busy'
    return {'mode': 'decoded_shadow_preview', 'execution_eligible': False,
            'source_generated_at_utc': adapted['generated_at_utc'],
            'batch_count_assumed': batch_count, 'batch_control_verified': False,
            'capabilities_source': 'existing_catalog_accepted_by_user',
            'target_order': target, 'allocation': allocation,
            'cached_busy_slots_preserved': [entry['slot_id'] for entry in adapted['employees'] if entry['cached_state'] == 'busy'],
            'assignment_blockers': {slot: reason for slot, reason in blockers.items() if reason},
            'mapping_issues': adapted['mapping_issues']}


def main() -> int:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--file", type=Path, help="optional JSON list of production targets")
    source.add_argument('--decoded-state', type=Path, help='read-only shadow preview from decoded JSON; does not apply')
    parser.add_argument('--batch-count', type=int, choices=(1, 2, 3), help='explicit normal-order quantity multiplier for shadow preview')
    args = parser.parse_args()
    if args.decoded_state:
        if args.batch_count is None:
            parser.error('--decoded-state requires an explicit --batch-count (UI checkbox is not read)')
        result = plan_decoded(json.loads(args.decoded_state.read_text(encoding='utf-8')), args.batch_count)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.batch_count is not None:
        parser.error('--batch-count requires --decoded-state')
    if args.file:
        targets = json.loads(args.file.read_text(encoding="utf-8"))
        capabilities = load_capabilities()
        slots = capabilities["slots"]
        result = allocate(targets, slots, aliases=capabilities.get("product_aliases"))
    else:
        result = plan_latest()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

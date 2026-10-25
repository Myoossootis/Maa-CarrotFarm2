"""Translate order shortages into stable, named farm production targets."""

from __future__ import annotations

import json
from pathlib import Path
import random


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "assets/catalog/backpack_crops/registry.json"
DECODED_PRODUCTS = ROOT / 'assets/catalog/decoded_products.json'


def load_decoded_products() -> list[dict]:
    """Read ID mappings that do not require or fabricate OCR templates."""
    return json.loads(DECODED_PRODUCTS.read_text(encoding='utf-8'))['products']

# These are production routes, not click coordinates. Each route still needs
# in-game verification before a controller is allowed to operate it.
ROUTE_BY_ID = {
    "crop_001": "field",
    "crop_002": "field",
    "crop_003": "livestock",
    "crop_004": "orchard",
    "crop_005": "field",
    "crop_006": "field",
    "crop_007": "field",
    "crop_008": "field",
    "crop_009": "field",
    "crop_010": "field",
    "crop_011": "field",
    "crop_012": "orchard",
    "crop_013": "orchard",
    "crop_014": "orchard",
    "crop_015": "orchard",
    "crop_016": "livestock",
    "crop_017": "livestock",
    "crop_018": "livestock",
    "crop_019": "livestock",
    "crop_020": "livestock",
    "crop_021": "unsupported",
    "crop_022": "field",
    "crop_023": "field",
    "crop_024": "field",
}

UNSUPPORTED_ORDER_PRODUCTS = frozenset({"鱼肉"})
MAX_PREPARED_ORDERS = 3


def order_signature(items: list[dict]) -> tuple[tuple[str, int], ...]:
    """Identify an order by every product and triple requirement, not stock."""
    pairs = [(item["name"], int(item["required"])) for item in items]
    if not pairs or len({name for name, _ in pairs}) != len(pairs) or any(count < 1 for _, count in pairs):
        raise ValueError("order products must be unique with positive requirements")
    return tuple(sorted(pairs))




def target_shortage_names(target: dict) -> frozenset[str]:
    """Crop names this production target was trying to fill."""
    if not target:
        return frozenset()
    missing = target.get("missing_items")
    if missing:
        return frozenset(str(item["name"]) for item in missing)
    production = target.get("production_targets")
    if production:
        return frozenset(str(item["name"]) for item in production)
    items = target.get("items") or []
    named = [
        str(item["name"])
        for item in items
        if int(item.get("missing", max(0, int(item.get("required", 0)) - int(item.get("actual", 0))))) > 0
    ]
    if named:
        return frozenset(named)
    return frozenset(str(item["name"]) for item in items)


def order_matches_shortage_focus(requested: dict, items: list[dict]) -> bool:
    """True when a freshly scanned order still covers the crops we planned for.

    Rematch by shortage crop names — do not require the same grid order id or
    an exact frozen recipe signature (quantities/board refresh may change).
    """
    if requested.get('selection') in {'first_non_fish_of_first_three', 'batch_card'}:
        return order_signature(requested['items']) == order_signature(items)
    focus = target_shortage_names(requested)
    if not focus:
        return False
    order_names = frozenset(str(item["name"]) for item in items)
    return focus <= order_names


def shortage_focus_was_submitted(state: dict, target: dict) -> bool:
    """Acceptance confirmed for any submitted card covering the shortage crops."""
    focus = target_shortage_names(target)
    if not focus:
        # Fully stocked target: any confirmed accept counts (party/takeout batch path).
        return any(
            entry.get("acceptance_confirmed") is True
            for entry in (state.get("submitted") or [])
        )
    for entry in state.get("submitted") or []:
        if entry.get("acceptance_confirmed") is not True:
            continue
        if target.get('selection') in {'first_non_fish_of_first_three', 'batch_card'}:
            if order_signature(entry.get('items') or []) == order_signature(target['items']):
                return True
            continue
        names = frozenset(str(item["name"]) for item in entry.get("items") or [])
        if focus <= names:
            return True
        try:
            if order_signature(entry.get("items") or []) == order_signature(target.get("items") or []):
                return True
        except ValueError:
            continue
    return False

def order_contains_unsupported_product(items: list[dict]) -> bool:
    return any(item["name"] in UNSUPPORTED_ORDER_PRODUCTS for item in items)


def build_targets(missing_items: list[dict]) -> list[dict]:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    by_name = {item["name"]: item["id"] for item in registry["products"]}
    if set(by_name.values()) != set(ROUTE_BY_ID):
        raise RuntimeError("production route table does not cover the crop registry")
    decoded = load_decoded_products()
    routes = {**ROUTE_BY_ID, **{item['id']: item['route'] for item in decoded}}
    by_name.update({item['name']: item['id'] for item in decoded})
    result = []
    for item in missing_items:
        if item["name"] in UNSUPPORTED_ORDER_PRODUCTS:
            raise RuntimeError(f"unsupported order product: {item['name']}")
        item_id = by_name.get(item["name"])
        if item_id is None:
            raise RuntimeError(f"unknown order product: {item['name']}")
        missing = int(item["missing"])
        if missing <= 0 or int(item["required"]) - int(item["actual"]) != missing:
            raise RuntimeError(f"invalid shortage for {item['name']}")
        result.append(
            {
                "id": item_id,
                "name": item["name"],
                "route": routes[item_id],
                "needed": missing,
                "observed_inventory": int(item["actual"]),
                "required_inventory": int(item["required"]),
            }
        )
    return result


def prepare_order_target(entries: list[dict]) -> dict | None:
    """Prepare the first readable non-fish order, within the first three."""
    for entry in sorted(entries, key=lambda value: int(value['order'])):
        number = int(entry['order'])
        if not 1 <= number <= MAX_PREPARED_ORDERS or entry.get('reason') not in {'ready', 'insufficient'}:
            continue
        items = entry['items']
        if order_contains_unsupported_product(items):
            continue
        if not 2 <= len(items) <= 4:
            raise RuntimeError(f'order {number}: incomplete recipe')
        order_signature(items)
        normalized = [{key: item[key] for key in ('name', 'actual', 'required')} for item in items]
        for item in normalized:
            if int(item['actual']) < 0:
                raise RuntimeError(f"invalid inventory for {item['name']}")
            item['missing'] = max(int(item['required']) - int(item['actual']), 0)
        missing = [item for item in normalized if item['missing'] > 0]
        return dict(order=number, items=normalized, missing_items=missing,
                    total_missing=sum(item['missing'] for item in missing),
                    selection='first_non_fish_of_first_three', production_targets=build_targets(missing))
    return None


def choose_target_order(skipped: list[dict], rng=None, is_feasible=None) -> dict | None:
    """Choose the smallest non-fish shortfall that the caller can work on."""
    eligible = [entry for entry in skipped if entry["reason"] == "insufficient"]
    if not eligible:
        return None
    for entry in eligible:
        if order_contains_unsupported_product(entry["items"]):
            raise RuntimeError("unsupported product entered production selection")
    if is_feasible is not None:
        eligible = [entry for entry in eligible if is_feasible(entry)]
    if not eligible:
        return None
    minimum_missing = min(
        sum(int(item["missing"]) for item in entry["items"]) for entry in eligible
    )
    closest = [
        entry
        for entry in eligible
        if sum(int(item["missing"]) for item in entry["items"]) == minimum_missing
    ]
    selected = (rng or random.SystemRandom()).choice(closest)
    missing_items = [
        {
            "name": item["name"],
            "actual": item["actual"],
            "required": item["required"],
            "missing": item["missing"],
        }
        for item in selected["items"]
        if item["missing"] > 0
    ]
    return {
        "order": selected["order"],
        "items": [
            {key: item[key] for key in ("name", "actual", "required", "missing")}
            for item in selected["items"]
        ],
        "total_missing": minimum_missing,
        "missing_items": missing_items,
        "tie_count": len(closest),
        "selection": "random_among_minimum_total_missing",
        "production_targets": build_targets(missing_items),
    }

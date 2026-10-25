"""Level-aware employee capability helpers.

Product owner rule: what a worker can plant is gated by employee LEVEL, not a
crop-only allowlist. Numeric thresholds stay null until live-recorded; products
captured as unlocked on a slot keep eligibility=observed_unlocked for that slot.
"""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CAPABILITIES_PATH = ROOT / "assets/catalog/employee_capabilities.json"


def load_capabilities(path: Path | None = None) -> dict:
    return json.loads((path or CAPABILITIES_PATH).read_text(encoding="utf-8"))


def normalize_product_name(name: str, aliases: dict[str, str] | None = None) -> str:
    """NFKC + strip + alias map so wheat OCR variants still match 小麦."""
    text = unicodedata.normalize("NFKC", str(name or "")).strip()
    # Collapse internal whitespace.
    text = "".join(text.split())
    table = aliases if aliases is not None else load_capabilities().get("product_aliases", {})
    # Apply repeatedly for short chains; unknown names stay as normalized text.
    for _ in range(3):
        nxt = table.get(text, text)
        if nxt == text:
            break
        text = nxt
    return text


def slot_product_gates(slot: dict) -> dict[str, dict]:
    gates = dict(slot.get("product_gates") or {})
    # Legacy/flat products list: treat as observed_unlocked on this slot.
    for name in slot.get("products") or []:
        if isinstance(name, str) and name not in gates:
            gates[name] = {"min_level": None, "eligibility": "observed_unlocked"}
        elif isinstance(name, dict):
            gates.setdefault(name["name"], name)
    return gates


def slot_can_produce(
    slot: dict,
    crop_name: str,
    *,
    aliases: dict[str, str] | None = None,
) -> bool:
    """True when this worker slot may be assigned crop_name under level rules."""
    want = normalize_product_name(crop_name, aliases)
    worker_level = slot.get("worker_level")
    gates = slot_product_gates(slot)
    for raw_name, gate in gates.items():
        if normalize_product_name(raw_name, aliases) != want:
            continue
        eligibility = str(gate.get("eligibility") or "observed_unlocked")
        if eligibility in {"needs_live_recording", "locked_or_level_gated", "unsupported"}:
            return False
        min_level = gate.get("min_level")
        if min_level is None:
            return eligibility in {"observed_unlocked", "verified"}
        if worker_level is None:
            # Numeric gate exists but worker level unknown: only honour prior
            # observed unlock on this same slot.
            return eligibility == "observed_unlocked"
        try:
            return int(worker_level) >= int(min_level)
        except (TypeError, ValueError):
            return False
    return False


def capable_slots(
    slots: list[dict],
    crop_name: str,
    *,
    aliases: dict[str, str] | None = None,
) -> list[dict]:
    return [slot for slot in slots if slot_can_produce(slot, crop_name, aliases=aliases)]


def slot_schedulable_names(slot: dict, *, aliases: dict[str, str] | None = None) -> set[str]:
    names: set[str] = set()
    for raw_name, gate in slot_product_gates(slot).items():
        eligibility = str(gate.get("eligibility") or "observed_unlocked")
        if eligibility in {"needs_live_recording", "locked_or_level_gated", "unsupported"}:
            continue
        names.add(normalize_product_name(raw_name, aliases))
    return names

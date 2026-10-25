"""Audit a completed fast scan for identity, timing and background policy."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "assets/catalog/backpack_crops/latest_scan.json"
EXPECTED_IDS = {f"crop_{index:03d}" for index in range(1, 23)}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> int:
    data = json.loads(RESULT.read_text(encoding="utf-8"))
    items = data["items"]
    ids = {item["id"] for item in items}
    require(ids == EXPECTED_IDS, f"identity mismatch: {sorted(EXPECTED_IDS - ids)}")
    require(data["elapsed_seconds"] <= 10.0, f"scan too slow: {data['elapsed_seconds']}s")
    require(data["background_input"] is True, "background policy missing")
    require(not data["unknown_slots"], "unknown product slots remain")
    require(all(isinstance(item["quantity"], int) for item in items), "unresolved quantity")
    require(next(item for item in items if item["id"] == "crop_022")["name"] == "甘蔗", "sugarcane missing")
    print(
        json.dumps(
            {
                "audit": "passed",
                "elapsed_seconds": data["elapsed_seconds"],
                "items": len(items),
                "sugarcane_quantity": next(
                    item["quantity"] for item in items if item["id"] == "crop_022"
                ),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

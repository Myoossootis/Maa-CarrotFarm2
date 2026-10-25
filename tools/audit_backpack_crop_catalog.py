"""Audit the generated backpack crop catalog without mutating game state."""

from __future__ import annotations

import json
from pathlib import Path

import cv2


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "assets/catalog/backpack_crops/catalog.json"
EXPECTED_QUANTITIES = {
    "crop_001": 3,
    "crop_002": 2,
    "crop_003": 14,
    "crop_004": 23,
    "crop_005": 10,
    "crop_006": 39,
    "crop_007": 25,
    "crop_008": 31,
    "crop_009": 37,
    "crop_010": 10,
    "crop_011": 11,
    "crop_012": 80,
    "crop_013": 49,
    "crop_014": 28,
    "crop_015": 72,
    "crop_016": 83,
    "crop_017": 30,
    "crop_018": 62,
    "crop_019": 25,
    "crop_020": 5,
    "crop_021": 2,
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> int:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    items = manifest["items"]
    require(manifest["item_count"] == 21, "expected 21 unique backpack items")
    require(len(items) == 21, "manifest item count mismatch")
    require(len(manifest["duplicate_matches"]) == 5, "expected five overlapping slots")
    require(manifest["capture"]["top_restored_score"] >= 0.995, "backpack was not restored to top")
    require(manifest["capture"]["input"] == "MaaFramework.PostMessage.Swipe", "foreground mouse input is forbidden")
    require(
        manifest["capture"]["foreground_policy"] == "never_activate_window_or_move_system_cursor",
        "missing background-only policy",
    )
    require(len({item["id"] for item in items}) == len(items), "duplicate item id")

    quantities: dict[str, int] = {}
    for item in items:
        require(isinstance(item["name"], str) and item["name"], f"{item['id']} is missing a name")
        require(item["quantity"]["status"] == "ok", f"{item['id']} quantity is unresolved")
        require(isinstance(item["quantity"]["value"], int), f"{item['id']} quantity is not an integer")
        require(
            item["quantity"]["value"] == EXPECTED_QUANTITIES[item["id"]],
            f"{item['id']} quantity regression: expected {EXPECTED_QUANTITIES[item['id']]}, got {item['quantity']['value']}",
        )
        template_path = ROOT / item["template"]
        segment_path = ROOT / item["segment"]
        template = cv2.imread(str(template_path), cv2.IMREAD_COLOR)
        segment = cv2.imread(str(segment_path), cv2.IMREAD_COLOR)
        require(template is not None, f"missing template: {template_path}")
        require(segment is not None, f"missing segment: {segment_path}")
        require(template.shape[:2] == (50, 84), f"unexpected template size: {template_path}")
        require(segment.shape[:2] == (96, 96), f"unexpected segment size: {segment_path}")
        quantities[item["id"]] = item["quantity"]["value"]

    require(len(list((ROOT / "assets/resource/image/crops/named").glob("*.png"))) == 21, "template file count mismatch")
    require(len(list((ROOT / "assets/catalog/backpack_crops/segments").glob("*.png"))) == 21, "segment file count mismatch")
    require(not (ROOT / ".tmp/backpack_crop_catalog").exists(), "collector temp directory was not released")
    print(json.dumps({"audit": "passed", "items": len(items), "quantities": quantities}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

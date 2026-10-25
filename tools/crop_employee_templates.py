"""Crop Maa FramePool captures into employee-configuration templates."""

from pathlib import Path

import cv2


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / ".tmp/employee_capture"
DEST = ROOT / "assets/resource/image/production"

# Rectangles are in the 720 x 1358 Maa screenshot coordinate system.
REGIONS = {
    "farm_clear.png": {
        "phone_icon.png": (628, 1247, 683, 1327),
    },
    "farm.png": {
        "employee_config.png": (298, 463, 424, 621),
    },
    "list.png": {
        "work_plan_button.png": (150, 397, 318, 457),
        "farm_worker_header.png": (35, 137, 236, 235),
        "no_work_plan.png": (129, 283, 345, 340),
        "slot_field_1.png": (143, 137, 248, 181),
        "slot_field_2.png": (143, 526, 248, 570),
    },
    "ranch_list.png": {
        "slot_field_3.png": (143, 229, 248, 273),
        "slot_ranch_1.png": (143, 617, 248, 661),
        "slot_ranch_2.png": (143, 1006, 248, 1050),
    },
    "orchard_list.png": {
        "slot_orchard_2.png": (143, 408, 248, 452),
    },
    "ranch_lower.png": {
        "slot_orchard_1.png": (143, 710, 248, 754),
    },
    "running.png": {
        "rearrange_button.png": (187, 1043, 530, 1137),
    },
    "plan.png": {
        "work_plan_title.png": (272, 252, 461, 314),
        "arrange_button.png": (399, 1017, 597, 1101),
        "repeat_checked.png": (502, 1132, 540, 1168),
        "crop_tomato.png": (99, 542, 158, 609),
        "crop_wheat.png": (214, 540, 281, 615),
        "crop_corn.png": (333, 540, 388, 615),
        "crop_sugarcane.png": (443, 543, 504, 611),
        "crop_potato.png": (554, 549, 615, 607),
        "crop_rose.png": (99, 676, 156, 750),
        "crop_carrot.png": (215, 675, 279, 747),
        "crop_strawberry.png": (326, 675, 390, 745),
        "crop_cabbage.png": (439, 674, 513, 750),
    },
    "ranch2_plan.png": {
        "crop_beef.png": (92, 541, 164, 617),
        "crop_egg.png": (213, 543, 276, 615),
        "crop_pork.png": (327, 543, 393, 614),
        "crop_mutton.png": (439, 544, 512, 615),
        "crop_milk.png": (561, 540, 617, 616),
    },
    "orchard1_plan.png": {
        "crop_apple.png": (96, 543, 163, 614),
        "crop_peach.png": (212, 542, 277, 615),
        "crop_lemon.png": (329, 539, 391, 615),
        "crop_coconut.png": (439, 540, 514, 615),
    },
}


def main() -> int:
    DEST.mkdir(parents=True, exist_ok=True)
    for source_name, crops in REGIONS.items():
        image = cv2.imread(str(SOURCE / source_name))
        if image is None or image.shape[:2] != (1358, 720):
            raise RuntimeError(f"invalid Maa source screenshot: {source_name}")
        for name, (x1, y1, x2, y2) in crops.items():
            result = image[y1:y2, x1:x2]
            if result.size == 0 or not cv2.imwrite(str(DEST / name), result):
                raise RuntimeError(f"failed to crop {name}")
            print(f"{name}: {result.shape[1]}x{result.shape[0]}")
            if name.startswith("slot_"):
                suffix = result[:, 60:105]
                suffix_name = name.replace(".png", "_suffix.png")
                if not cv2.imwrite(str(DEST / suffix_name), suffix):
                    raise RuntimeError(f"failed to crop {suffix_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

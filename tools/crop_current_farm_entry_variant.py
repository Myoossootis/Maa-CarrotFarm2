"""Crop the current notification-badged farm entry from a Maa 720 frame."""

from pathlib import Path
import argparse

import cv2


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / ".tmp/restaurant_capture/restaurant_loaded.png"
TARGET = ROOT / "assets/resource/image/navigation/farm_entry_alert.png"
BOX = (19, 1218, 96, 92)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--sprout-core", action="store_true", help="crop a background-independent sprout from a verified restaurant frame")
    parser.add_argument("--day-variant", action="store_true")
    args = parser.parse_args()
    image = cv2.imread(str(args.source), cv2.IMREAD_COLOR)
    if image is None or image.shape[1] != 720:
        raise RuntimeError("invalid Maa frame")
    x, y, width, height = (35, 1233, 60, 75) if args.sprout_core else BOX
    target = TARGET.with_name("farm_entry_sprout.png") if args.sprout_core else TARGET
    if args.day_variant:
        target = target.with_name(target.stem + "_day.png")
    patch = image[y : y + height, x : x + width]
    if patch.shape[:2] != (height, width) or not cv2.imwrite(str(target), patch):
        raise RuntimeError("failed to write farm entry variant")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

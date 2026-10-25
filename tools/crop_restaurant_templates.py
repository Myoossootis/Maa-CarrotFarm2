"""Crop restaurant templates from lossless Maa 720-short-side screenshots."""

import argparse
from pathlib import Path

import cv2


ROOT = Path(__file__).resolve().parents[1]
HOME = ROOT / ".tmp/restaurant_capture/home.png"
INTERIOR = ROOT / ".tmp/restaurant_capture/restaurant_loaded.png"
OUTPUT = ROOT / "assets/resource/image/navigation"
ENTRY_BOX = (10, 1231, 96, 94)
FARM_ENTRY_BOX = (19, 1218, 96, 92)


def crop(source: Path, target: Path, box: tuple[int, int, int, int]) -> None:
    image = cv2.imread(str(source), cv2.IMREAD_COLOR)
    if image is None or image.shape[1] != 720:
        raise RuntimeError(f"invalid Maa screenshot: {source}")
    x, y, width, height = box
    patch = image[y : y + height, x : x + width]
    target.parent.mkdir(parents=True, exist_ok=True)
    if patch.shape[:2] != (height, width) or not cv2.imwrite(str(target), patch):
        raise RuntimeError(f"failed to crop {target}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--entry-lid-source", type=Path, help="Maa 720p farm screenshot for the background-independent lid template")
    parser.add_argument("--lid-box", nargs=4, type=int, default=(35, 1255, 57, 41), metavar=("X", "Y", "W", "H"))
    args = parser.parse_args()
    if args.entry_lid_source:
        crop(args.entry_lid_source, OUTPUT / "restaurant_entry_lid.png", tuple(args.lid_box))
        return 0
    crop(HOME, OUTPUT / "restaurant_entry.png", ENTRY_BOX)
    crop(INTERIOR, OUTPUT / "farm_entry.png", FARM_ENTRY_BOX)
    print(f"restaurant={list(ENTRY_BOX)} farm={list(FARM_ENTRY_BOX)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

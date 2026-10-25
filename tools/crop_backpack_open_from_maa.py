"""Crop the stable backpack-open marker from a Maa 720p screenshot."""

from pathlib import Path
import sys

import cv2


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "deps/tools/ImageCropper/src/backpack_open.png"
DESTINATION = ROOT / "assets/resource/image/navigation/backpack_open.png"
BACKPACK_OPEN_BOX = (281, 278, 160, 90)


def main() -> int:
    image = cv2.imread(str(SOURCE), cv2.IMREAD_COLOR)
    if image is None:
        print(f"missing source screenshot: {SOURCE}", file=sys.stderr)
        return 2
    height, width = image.shape[:2]
    if width != 720:
        print(f"expected Maa 720p short-side screenshot, got {width}x{height}", file=sys.stderr)
        return 2

    x, y, crop_width, crop_height = BACKPACK_OPEN_BOX
    crop = image[y : y + crop_height, x : x + crop_width]
    if crop.shape[:2] != (crop_height, crop_width):
        print("backpack-open ROI lies outside source screenshot", file=sys.stderr)
        return 2

    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(DESTINATION), crop):
        print(f"failed to write template: {DESTINATION}", file=sys.stderr)
        return 2
    print(f"template: {DESTINATION}")
    print(f"template box: {list(BACKPACK_OPEN_BOX)}")
    print("recognition roi: [230, 230, 260, 190]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

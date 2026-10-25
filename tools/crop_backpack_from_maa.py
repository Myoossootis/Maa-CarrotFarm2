"""Crop the backpack template from a Maa ImageCropper-standardized screenshot.

The source must be a lossless screenshot produced by Maa with
set_screenshot_target_short_side(720). The coordinates are the ROI selected
and checked with MaaFramework's bundled ImageCropper.
"""

from pathlib import Path
import sys

import cv2


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "deps/tools/ImageCropper/src/carrot_home.png"
DESTINATION = ROOT / "assets/resource/image/navigation/backpack.png"
# Keep the crop inside the static red backpack glyph. The old box included a
# background NPC whose animation lowered the live template score.
BACKPACK_BOX = (638, 1170, 64, 53)


def main() -> int:
    image = cv2.imread(str(SOURCE), cv2.IMREAD_COLOR)
    if image is None:
        print(f"missing source screenshot: {SOURCE}", file=sys.stderr)
        return 2
    height, width = image.shape[:2]
    if width != 720:
        print(f"expected Maa 720p short-side screenshot, got {width}x{height}", file=sys.stderr)
        return 2

    x, y, crop_width, crop_height = BACKPACK_BOX
    crop = image[y : y + crop_height, x : x + crop_width]
    if crop.shape[:2] != (crop_height, crop_width):
        print("backpack ROI lies outside source screenshot", file=sys.stderr)
        return 2

    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(DESTINATION), crop):
        print(f"failed to write template: {DESTINATION}", file=sys.stderr)
        return 2
    print(f"template: {DESTINATION}")
    print(f"template box: {list(BACKPACK_BOX)}")
    print("recognition roi: [560, 1080, 160, 180]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

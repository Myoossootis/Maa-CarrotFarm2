"""Crop the verified bottom-right mill from a lossless Maa 720 frame."""

import argparse
from pathlib import Path

import cv2


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=ROOT / ".tmp/current.png")
    args = parser.parse_args()
    frame = cv2.imread(str(args.source))
    if frame is None or frame.shape[1] != 720 or abs(frame.shape[0] - 1358) > 8:
        raise RuntimeError("expected lossless Maa 720-short-side capture")
    for name, (x, y, width, height) in {
        "feed_silo_structure_live.png": (256, 240, 92, 150),
        "feed_sack_icon_live.png": (382, 116, 57, 71),
    }.items():
        target = ROOT / "assets/resource/image/production" / name
        if not cv2.imwrite(str(target), frame[y:y + height, x:x + width]):
            raise RuntimeError(f"could not save {target}")


if __name__ == "__main__":
    main()

"""Crop the empty-paper notice-board variant from a Maa restaurant capture."""

from pathlib import Path
import argparse

import cv2


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=ROOT / ".tmp/current.png")
    parser.add_argument("--day-variant", action="store_true")
    args = parser.parse_args()
    frame = cv2.imread(str(args.source))
    if frame is None or frame.shape[1] != 720 or abs(frame.shape[0] - 1358) > 8:
        raise RuntimeError("expected live Maa 720-short-side screenshot")
    name = "notice_board_empty_day.png" if args.day_variant else "notice_board_empty.png"
    target = ROOT / "assets/resource/image/navigation" / name
    if not cv2.imwrite(str(target), frame[675:905, 225:465]):
        raise RuntimeError("failed to save empty notice-board template")
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

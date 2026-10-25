"""Crop the completion checkbox from a live 720x1358 Maa screenshot."""

import argparse
from pathlib import Path

import cv2


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("state", choices=("checked", "unchecked"))
    parser.add_argument("--source", type=Path, default=ROOT / ".tmp/current.png")
    args = parser.parse_args()
    frame = cv2.imread(str(args.source))
    if frame is None or frame.shape[:2] != (1358, 720):
        raise RuntimeError("expected live Maa 720x1358 screenshot")
    target_name = (
        "normal_order_completion_checked_full.png"
        if args.state == "checked"
        else "normal_order_completion_unchecked.png"
    )
    target = ROOT / "assets/resource/image/navigation" / target_name
    patch = frame[1192:1246, 480:660]
    if patch.shape[:2] != (54, 180) or not cv2.imwrite(str(target), patch):
        raise RuntimeError(f"failed to write {target}")
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

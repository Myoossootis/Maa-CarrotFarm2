"""Crop the post-claim 'tap blank area' prompt from a Maa screenshot."""

from pathlib import Path

import cv2


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    frame = cv2.imread(str(ROOT / ".tmp/current.png"))
    if frame is None or frame.shape[:2] != (1358, 720):
        raise RuntimeError("expected live Maa 720x1358 screenshot")
    target = ROOT / "assets/resource/image/navigation/normal_order_reward_continue.png"
    if not cv2.imwrite(str(target), frame[1045:1115, 195:530]):
        raise RuntimeError("failed to save reward overlay prompt")
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

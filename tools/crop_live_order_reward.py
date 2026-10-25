"""Crop the normal-order reward-claim button from a live Maa screenshot."""

from pathlib import Path

import cv2


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / ".tmp/current.png"
TARGET = ROOT / "assets/resource/image/navigation/normal_order_claim_reward.png"


def main() -> int:
    frame = cv2.imread(str(SOURCE))
    if frame is None or frame.shape[:2] != (1358, 720):
        raise RuntimeError("expected live Maa 720x1358 screenshot")
    patch = frame[1108:1205, 214:512]
    if not cv2.imwrite(str(TARGET), patch):
        raise RuntimeError("failed to save reward button template")
    print(TARGET)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

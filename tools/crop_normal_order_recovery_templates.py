"""Crop normal-order recovery buttons into Maa 720 coordinate templates."""

from pathlib import Path
import os

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "assets/resource/image/navigation"
LIVE_FRAME = ROOT / ".tmp/restaurant_capture/restaurant_loaded.png"
REPLACE_SOURCE = Path(
    os.environ.get(
        "NORMAL_ORDER_REPLACE_SOURCE",
        r"C:\Users\杜锦鸿\AppData\Local\Temp\codex-clipboard-72d2dbfb-6373-4dd1-b5fc-d6b46d790459.png",
    )
)
FREE_ACCELERATE_BOX = (195, 1095, 335, 113)
# Source is 415x780; resize to the Maa 720x1353 short-side standard first.
REPLACE_BOX = (267, 1090, 190, 120)


def read_unicode(path: Path):
    image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError(f"failed to read {path}")
    return image


def write(path: Path, image) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), image):
        raise RuntimeError(f"failed to write {path}")


def main() -> int:
    live = cv2.imread(str(LIVE_FRAME), cv2.IMREAD_COLOR)
    if live is None or live.shape[1] != 720:
        raise RuntimeError("invalid live Maa frame")
    x, y, width, height = FREE_ACCELERATE_BOX
    write(OUTPUT / "normal_order_free_accelerate.png", live[y : y + height, x : x + width])

    source = read_unicode(REPLACE_SOURCE)
    standard = cv2.resize(source, (720, 1353), interpolation=cv2.INTER_CUBIC)
    x, y, width, height = REPLACE_BOX
    write(OUTPUT / "normal_order_replace.png", standard[y : y + height, x : x + width])
    print(f"replace={list(REPLACE_BOX)} free_accelerate={list(FREE_ACCELERATE_BOX)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

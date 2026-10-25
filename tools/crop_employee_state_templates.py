"""Extract employee completion-state templates from a Maa capture."""

from pathlib import Path

import cv2


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / ".tmp/current.png"
DEST = ROOT / "assets/resource/image/production"


def main() -> int:
    frame = cv2.imread(str(SOURCE))
    if frame is None or frame.shape[:2] != (1358, 720):
        raise RuntimeError("expected a 720x1358 Maa employee-list capture")
    regions = {
        "collect_supplies_button.png": (150, 397, 318, 457),
        "work_complete_text.png": (199, 195, 469, 228),
    }
    DEST.mkdir(parents=True, exist_ok=True)
    for name, (left, top, right, bottom) in regions.items():
        if not cv2.imwrite(str(DEST / name), frame[top:bottom, left:right]):
            raise RuntimeError(f"failed to save {name}")
        print(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

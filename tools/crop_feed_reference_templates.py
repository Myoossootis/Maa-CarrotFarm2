"""Extract stable animal-feed UI templates from the user's reference captures."""

from __future__ import annotations

from pathlib import Path

import cv2
ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "assets/resource/image/production"
SILO_REFERENCE = DEST / "feed_silo_shortage_reference.png"
MILL_REFERENCE = DEST / "feed_mill_empty_reference.png"


def load_reference(path: Path) -> np.ndarray:
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError(f"could not decode reference image: {path.name}")
    return image


def save_crop(image: np.ndarray, box: tuple[int, int, int, int], name: str) -> None:
    x1, y1, x2, y2 = box
    crop = image[y1:y2, x1:x2]
    if crop.size == 0 or not cv2.imwrite(str(DEST / name), crop):
        raise RuntimeError(f"could not write feed template: {name}")
    print(f"{name}: {crop.shape[1]}x{crop.shape[0]}")


def main() -> int:
    DEST.mkdir(parents=True, exist_ok=True)
    silo = load_reference(SILO_REFERENCE)
    mill = load_reference(MILL_REFERENCE)
    if silo.shape[:2] != (234, 205) or mill.shape[:2] != (426, 354):
        raise RuntimeError("feed reference screenshots have unexpected dimensions")

    # The complete shortage marker includes the clickable silo and its badge.
    save_crop(silo, (0, 0, 205, 234), "feed_silo_shortage.png")
    # Remove the changing shortage speech bubble while retaining the silo art.
    save_crop(silo, (20, 43, 140, 223), "feed_silo_structure.png")
    # The button and sack glyphs are the stable action/output markers in the
    # supplied empty-mill panel. The runtime matcher searches at multiple scales.
    save_crop(mill, (203, 247, 344, 313), "feed_refill_button.png")
    save_crop(mill, (51, 99, 113, 164), "feed_sack_count.png")
    save_crop(mill, (53, 100, 94, 151), "feed_sack_icon.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

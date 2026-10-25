"""Crop stable notice-board templates from lossless Maa 720 screenshots."""

from pathlib import Path

import cv2


ROOT = Path(__file__).resolve().parents[1]
CAPTURE = ROOT / ".tmp/notice_board_capture"
OUTPUT = ROOT / "assets/resource/image/navigation"

# Exclude the animated exclamation badge, speech bubble, counters and order goods.
BOARD_BOX = (452, 930, 188, 174)
OPEN_TITLE_BOX = (77, 215, 218, 86)
POPUP_CLOSE_BOX = (25, 1237, 64, 77)


def crop(source: Path, target: Path, box: tuple[int, int, int, int]) -> None:
    image = cv2.imread(str(source), cv2.IMREAD_COLOR)
    if image is None or image.shape[1] != 720:
        raise RuntimeError(f"invalid Maa screenshot: {source}")
    x, y, width, height = box
    patch = image[y : y + height, x : x + width]
    target.parent.mkdir(parents=True, exist_ok=True)
    if patch.shape[:2] != (height, width) or not cv2.imwrite(str(target), patch):
        raise RuntimeError(f"failed to crop {target}")


def main() -> int:
    crop(CAPTURE / "restaurant.png", OUTPUT / "notice_board.png", BOARD_BOX)
    crop(CAPTURE / "notice_open.png", OUTPUT / "notice_board_open.png", OPEN_TITLE_BOX)
    crop(CAPTURE / "notice_open.png", OUTPUT / "popup_close.png", POPUP_CLOSE_BOX)
    print(
        f"board={list(BOARD_BOX)} open_title={list(OPEN_TITLE_BOX)} "
        f"popup_close={list(POPUP_CLOSE_BOX)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

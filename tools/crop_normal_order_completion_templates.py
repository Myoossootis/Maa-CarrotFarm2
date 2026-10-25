"""Build accept/sleep-dialog templates in Maa's 720-short-side coordinate space."""

from pathlib import Path
import os

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "assets/resource/image/navigation"
ACCEPT_SOURCE = Path(
    os.environ.get(
        "NORMAL_ORDER_ACCEPT_SOURCE",
        r"C:\Users\杜锦鸿\AppData\Local\Temp\codex-clipboard-2baa8324-ea74-470e-9ed8-0c2919210c39.png",
    )
)
SLEEP_SOURCE = Path(
    os.environ.get(
        "NORMAL_ORDER_SLEEP_SOURCE",
        r"C:\Users\杜锦鸿\AppData\Local\Temp\codex-clipboard-acf57ccb-266c-42c5-bb7e-ba9d4f9f7a21.png",
    )
)
IN_PROGRESS_SOURCE = Path(
    os.environ.get(
        "NORMAL_ORDER_IN_PROGRESS_SOURCE",
        r"C:\Users\杜锦鸿\AppData\Local\Temp\codex-clipboard-9f54f5a2-d4d4-4c90-8036-984052d5cece.png",
    )
)
ACCEPT_BOX = (472, 1074, 185, 110)
SLEEP_TITLE_BOX = (300, 424, 120, 62)
SLEEP_CONFIRM_BOX = (400, 646, 250, 115)
IN_PROGRESS_BOX = (220, 925, 300, 105)


def read_standard(path: Path):
    image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError(f"failed to read {path}")
    return cv2.resize(image, (720, 1353), interpolation=cv2.INTER_CUBIC)


def crop(source, target: Path, box: tuple[int, int, int, int]) -> None:
    x, y, width, height = box
    patch = source[y : y + height, x : x + width]
    target.parent.mkdir(parents=True, exist_ok=True)
    if patch.shape[:2] != (height, width) or not cv2.imwrite(str(target), patch):
        raise RuntimeError(f"failed to write {target}")


def main() -> int:
    accept = read_standard(ACCEPT_SOURCE)
    sleep = read_standard(SLEEP_SOURCE)
    in_progress = read_standard(IN_PROGRESS_SOURCE)
    crop(accept, OUTPUT / "normal_order_accept.png", ACCEPT_BOX)
    crop(sleep, OUTPUT / "sleep_prompt_title.png", SLEEP_TITLE_BOX)
    crop(sleep, OUTPUT / "sleep_prompt_confirm.png", SLEEP_CONFIRM_BOX)
    crop(in_progress, OUTPUT / "normal_order_in_progress.png", IN_PROGRESS_BOX)
    print(
        f"accept={list(ACCEPT_BOX)} sleep_title={list(SLEEP_TITLE_BOX)} "
        f"sleep_confirm={list(SLEEP_CONFIRM_BOX)} in_progress={list(IN_PROGRESS_BOX)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

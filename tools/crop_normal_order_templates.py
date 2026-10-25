"""Crop stable normal-order templates from Maa 720-short-side screenshots."""

from pathlib import Path

import cv2


ROOT = Path(__file__).resolve().parents[1]
CAPTURE = ROOT / ".tmp/normal_orders_capture"
PROBE = ROOT / ".tmp/normal_order_probe/order_9.png"
OUTPUT = ROOT / "assets/resource/image/navigation"
ACTIVE_TITLE_BOX = (445, 230, 190, 60)
INACTIVE_TAB_BOX = (485, 230, 145, 58)
PIN_BOX = (143, 404, 28, 28)
# Match only the check icon. The border and “完成3次” text are identical when
# unchecked and previously caused a high-scoring false positive.
COMPLETION_CHECKED_BOX = (498, 1200, 38, 27)


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
    normal_orders = CAPTURE / "normal_orders.png"
    inactive_tab = CAPTURE / "normal_tab_inactive.png"
    if normal_orders.exists():
        crop(normal_orders, OUTPUT / "normal_order_active.png", ACTIVE_TITLE_BOX)
    if inactive_tab.exists():
        crop(inactive_tab, OUTPUT / "normal_order_tab.png", INACTIVE_TAB_BOX)
    if PROBE.exists():
        crop(PROBE, OUTPUT / "normal_order_pin.png", PIN_BOX)
    checked_target = OUTPUT / "normal_order_completion_checked.png"
    if PROBE.exists():
        crop(PROBE, checked_target, COMPLETION_CHECKED_BOX)
    else:
        # One-time migration for workspaces where development captures were
        # already cleaned: derive the check-only patch from the old full box.
        previous = cv2.imread(str(checked_target), cv2.IMREAD_COLOR)
        if previous is None or previous.shape[:2] != (42, 162):
            raise RuntimeError("checked reference frame is unavailable")
        patch = previous[6:33, 10:48]
        if not cv2.imwrite(str(checked_target), patch):
            raise RuntimeError(f"failed to migrate {checked_target}")
    print(
        f"active={list(ACTIVE_TITLE_BOX)} inactive={list(INACTIVE_TAB_BOX)} "
        f"pin={list(PIN_BOX)} checked={list(COMPLETION_CHECKED_BOX)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

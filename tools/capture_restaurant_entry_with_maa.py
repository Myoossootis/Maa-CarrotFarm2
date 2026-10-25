"""Capture the restaurant entrance and interior through Maa background Win32 control."""

from __future__ import annotations

import ctypes
from pathlib import Path
import shutil
import time

import cv2
from maa.controller import Win32Controller
from maa.define import MaaWin32InputMethodEnum, MaaWin32ScreencapMethodEnum
from maa_frame_normalize import post_click_normalized


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / ".tmp/restaurant_capture"
WINDOW_TITLE = "萝卜家园2"
WINDOW_CLASS = "Chrome_WidgetWin_0"
BACK_BUTTON = (58, 1278)
RESTAURANT_CENTER = (58, 1270)
BACKPACK_OPEN_TEMPLATE = ROOT / "assets/resource/image/navigation/backpack_open.png"


def find_window() -> int:
    matches: list[int] = []
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    @callback_type
    def callback(hwnd, _lparam):
        title = ctypes.create_unicode_buffer(user32.GetWindowTextLengthW(hwnd) + 1)
        class_name = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, title, len(title))
        user32.GetClassNameW(hwnd, class_name, len(class_name))
        if title.value == WINDOW_TITLE and class_name.value == WINDOW_CLASS:
            matches.append(int(hwnd))
        return True

    user32.EnumWindows(callback, 0)
    if len(matches) != 1:
        raise RuntimeError(f"expected one game window, found {len(matches)}")
    return matches[0]


def capture(controller: Win32Controller, name: str):
    job = controller.post_screencap().wait()
    if not job.succeeded:
        raise RuntimeError("Maa screenshot failed")
    image = job.get()
    if not cv2.imwrite(str(OUTPUT_DIR / f"{name}.png"), image):
        raise RuntimeError(f"failed to save {name}")
    return image


def backpack_open(image) -> bool:
    template = cv2.imread(str(BACKPACK_OPEN_TEMPLATE), cv2.IMREAD_COLOR)
    area = image[230:420, 230:490]
    return float(cv2.matchTemplate(area, template, cv2.TM_CCOEFF_NORMED).max()) >= 0.8


def main() -> int:
    shutil.rmtree(OUTPUT_DIR, ignore_errors=True)
    OUTPUT_DIR.mkdir(parents=True)
    controller = Win32Controller(
        hWnd=find_window(),
        screencap_method=MaaWin32ScreencapMethodEnum.FramePool,
        mouse_method=MaaWin32InputMethodEnum.PostMessage,
        keyboard_method=MaaWin32InputMethodEnum.PostMessage,
    )
    try:
        controller.set_screenshot_target_short_side(720)
        if not controller.post_connection().wait().succeeded:
            raise RuntimeError("Maa Win32 connection failed")
        image = capture(controller, "before")
        if backpack_open(image):
            post_click_normalized(controller, *BACK_BUTTON).wait()
            time.sleep(0.8)
            image = capture(controller, "home")
        else:
            cv2.imwrite(str(OUTPUT_DIR / "home.png"), image)
        post_click_normalized(controller, *RESTAURANT_CENTER).wait()
        time.sleep(1.2)
        capture(controller, "restaurant")
        print(str(OUTPUT_DIR))
        return 0
    finally:
        controller.__del__()
        controller._handle = None


if __name__ == "__main__":
    raise SystemExit(main())

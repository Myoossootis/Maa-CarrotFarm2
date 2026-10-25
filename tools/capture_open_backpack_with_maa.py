"""Open the backpack through Maa Win32 and save a standardized screenshot."""

from __future__ import annotations

import ctypes
from pathlib import Path
import time

import cv2
from maa.controller import Win32Controller
from maa.define import MaaWin32InputMethodEnum, MaaWin32ScreencapMethodEnum
from maa_frame_normalize import post_click_normalized


OUTPUT = Path("deps/tools/ImageCropper/src/backpack_open.png")
WINDOW_TITLE = "萝卜家园2"
WINDOW_CLASS = "Chrome_WidgetWin_0"
BACKPACK_CENTER = (657, 1181)


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
        raise RuntimeError(f"expected one {WINDOW_TITLE} window, found {len(matches)}")
    return matches[0]


def main() -> int:
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
        if not post_click_normalized(controller, *BACKPACK_CENTER).wait().succeeded:
            raise RuntimeError("Maa backpack click failed")
        time.sleep(1.2)
        capture = controller.post_screencap().wait()
        if not capture.succeeded:
            raise RuntimeError("Maa screenshot failed")
        image = capture.get()
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(OUTPUT), image):
            raise RuntimeError("failed to save backpack screenshot")
        print(f"saved {OUTPUT}: {image.shape[1]}x{image.shape[0]}")
        return 0
    finally:
        controller.__del__()
        controller._handle = None


if __name__ == "__main__":
    raise SystemExit(main())

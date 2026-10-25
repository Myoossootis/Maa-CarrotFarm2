"""Save one current 720-short-side Maa FramePool screenshot without input."""

from __future__ import annotations

import argparse
import ctypes
from pathlib import Path

import cv2
from maa.controller import Win32Controller
from maa.define import MaaWin32InputMethodEnum, MaaWin32ScreencapMethodEnum


OUTPUT = Path(".tmp/restaurant_capture/restaurant_loaded.png")


def find_window() -> int:
    found = []
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    @callback_type
    def callback(hwnd, _):
        title = ctypes.create_unicode_buffer(user32.GetWindowTextLengthW(hwnd) + 1)
        class_name = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, title, len(title))
        user32.GetClassNameW(hwnd, class_name, len(class_name))
        if title.value == "萝卜家园2" and class_name.value == "Chrome_WidgetWin_0":
            found.append(int(hwnd))
        return True

    user32.EnumWindows(callback, 0)
    if len(found) != 1:
        raise RuntimeError(f"expected one game window, found {len(found)}")
    return found[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    controller = Win32Controller(
        hWnd=find_window(),
        screencap_method=MaaWin32ScreencapMethodEnum.FramePool,
        mouse_method=MaaWin32InputMethodEnum.PostMessage,
        keyboard_method=MaaWin32InputMethodEnum.PostMessage,
    )
    try:
        controller.set_screenshot_target_short_side(720)
        controller.post_connection().wait()
        job = controller.post_screencap().wait()
        image = job.get()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(args.output), image):
            raise RuntimeError(f"failed to save screenshot: {args.output}")
        print(f"{args.output}: {image.shape[1]}x{image.shape[0]}")
        return 0
    finally:
        controller.__del__()
        controller._handle = None


if __name__ == "__main__":
    raise SystemExit(main())

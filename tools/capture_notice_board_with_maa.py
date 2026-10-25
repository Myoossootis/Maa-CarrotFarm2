"""Enter the restaurant, capture and click its notice board using Maa background input."""

from __future__ import annotations

import ctypes
from pathlib import Path
import shutil
import time

import cv2
from maa.controller import Win32Controller
from maa.define import MaaWin32InputMethodEnum, MaaWin32ScreencapMethodEnum
from maa.resource import Resource
from maa.tasker import Tasker
from maa_frame_normalize import post_click_normalized


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / ".tmp/notice_board_capture"
WINDOW_TITLE = "萝卜家园2"
WINDOW_CLASS = "Chrome_WidgetWin_0"
BOARD_CENTER = (545, 969)


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
        if title.value == WINDOW_TITLE and class_name.value == WINDOW_CLASS:
            found.append(int(hwnd))
        return True

    user32.EnumWindows(callback, 0)
    if len(found) != 1:
        raise RuntimeError(f"expected one game window, found {len(found)}")
    return found[0]


def capture(controller: Win32Controller, name: str):
    job = controller.post_screencap().wait()
    if not job.succeeded:
        raise RuntimeError("Maa screenshot failed")
    image = job.get()
    if not cv2.imwrite(str(OUTPUT / f"{name}.png"), image):
        raise RuntimeError(f"failed to save {name}")
    return image


def main() -> int:
    shutil.rmtree(OUTPUT, ignore_errors=True)
    OUTPUT.mkdir(parents=True)
    controller = Win32Controller(
        hWnd=find_window(),
        screencap_method=MaaWin32ScreencapMethodEnum.FramePool,
        mouse_method=MaaWin32InputMethodEnum.PostMessage,
        keyboard_method=MaaWin32InputMethodEnum.PostMessage,
    )
    resource = Resource()
    tasker = Tasker()
    try:
        controller.set_screenshot_target_short_side(720)
        controller.post_connection().wait()
    # patched below
        resource.post_bundle(ROOT / "assets/resource").wait()
        tasker.bind(resource, controller)
        raise RuntimeError("native pipeline Click blocked for FG safety; use tools that call post_click_normalized / order_template_click")
        time.sleep(0.5)
        capture(controller, "restaurant")
        if not post_click_normalized(controller, *BOARD_CENTER).wait().succeeded:
            raise RuntimeError("notice board click failed")
        time.sleep(0.8)
        capture(controller, "notice_open")
        return 0
    finally:
        tasker.clear_cache()
        for value in (tasker, resource, controller):
            destructor = getattr(value, "__del__", None)
            if callable(destructor):
                destructor()
            if hasattr(value, "_handle"):
                value._handle = None


if __name__ == "__main__":
    raise SystemExit(main())

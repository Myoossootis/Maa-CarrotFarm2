"""Capture active/inactive normal-order tabs through Maa background input."""

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
OUTPUT = ROOT / ".tmp/normal_orders_capture"
GATHERING_TAB_CENTER = (165, 255)
NORMAL_TAB_CENTER = (540, 255)


def find_window() -> int:
    found: list[int] = []
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


def capture(controller: Win32Controller, name: str) -> None:
    job = controller.post_screencap().wait()
    if not job.succeeded or not cv2.imwrite(str(OUTPUT / f"{name}.png"), job.get()):
        raise RuntimeError(f"failed to capture {name}")


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
        if not controller.post_connection().wait().succeeded:
            raise RuntimeError("Maa Win32 connection failed")
        if not resource.post_bundle(ROOT / "assets/resource").wait().succeeded:
            raise RuntimeError("Maa resource loading failed")
        if not tasker.bind(resource, controller):
            raise RuntimeError("Maa tasker binding failed")
        # This calibration helper is intentionally run while the normal-order
        # panel is open. It only switches tabs and never accepts/replaces an order.
        capture(controller, "normal_orders")
        if not post_click_normalized(controller, *GATHERING_TAB_CENTER).wait().succeeded:
            raise RuntimeError("failed to click gathering-order tab")
        time.sleep(0.5)
        capture(controller, "normal_tab_inactive")
        if not post_click_normalized(controller, *NORMAL_TAB_CENTER).wait().succeeded:
            raise RuntimeError("failed to click normal-order tab")
        time.sleep(0.5)
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

"""Capture full unchecked/checked 'complete 3 times' controls from Maa frames."""

from __future__ import annotations

import ctypes
from pathlib import Path
import time

import cv2
from maa.controller import Win32Controller
from maa.define import MaaWin32InputMethodEnum, MaaWin32ScreencapMethodEnum
from maa.resource import Resource
from maa.tasker import Tasker
from maa_frame_normalize import post_click_normalized, install_foreground_safe_inputs


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "assets/resource/image/navigation"
CONTROL_BOX = (480, 1186, 180, 54)
CONTROL_CENTER = (570, 1213)


def find_window() -> int:
    matches: list[int] = []
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    @callback_type
    def callback(hwnd, _):
        title = ctypes.create_unicode_buffer(user32.GetWindowTextLengthW(hwnd) + 1)
        class_name = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, title, len(title))
        user32.GetClassNameW(hwnd, class_name, len(class_name))
        if title.value == "萝卜家园2" and class_name.value == "Chrome_WidgetWin_0":
            matches.append(int(hwnd))
        return True

    user32.EnumWindows(callback, 0)
    if len(matches) != 1:
        raise RuntimeError(f"expected one game window, found {len(matches)}")
    return matches[0]


def screenshot(controller: Win32Controller):
    job = controller.post_screencap().wait()
    if not job.succeeded:
        raise RuntimeError("Maa screenshot failed")
    return job.get()


def crop(frame, name: str) -> None:
    x, y, width, height = CONTROL_BOX
    patch = frame[y : y + height, x : x + width]
    if patch.shape[:2] != (height, width) or not cv2.imwrite(str(OUTPUT / name), patch):
        raise RuntimeError(f"failed to write {name}")


def checkbox_green_pixels(frame) -> int:
    x, y, _, height = CONTROL_BOX
    hsv = cv2.cvtColor(frame[y : y + height, x : x + 60], cv2.COLOR_BGR2HSV)
    return int(cv2.countNonZero(cv2.inRange(hsv, (35, 80, 60), (90, 255, 255))))


def main() -> int:
    controller = Win32Controller(
        hWnd=find_window(),
        screencap_method=MaaWin32ScreencapMethodEnum.FramePool,
        mouse_method=MaaWin32InputMethodEnum.PostMessage,
        keyboard_method=MaaWin32InputMethodEnum.PostMessage,
    )
    resource = Resource()
    tasker = Tasker()
    try:
        tasker.set_save_draw(False)
        tasker.set_save_on_error(False)
        tasker.set_reco_image_cache_limit(8)
        controller.set_screenshot_target_short_side(720)
        if not controller.post_connection().wait().succeeded:
            raise RuntimeError("Maa Win32 connection failed")
        install_foreground_safe_inputs()
        if not resource.post_bundle(ROOT / "assets/resource").wait().succeeded or not tasker.bind(resource, controller):
            raise RuntimeError("Maa resource binding failed")
        raise RuntimeError("native pipeline Click blocked for FG safety; use tools that call post_click_normalized / order_template_click")
        before = screenshot(controller)
        active = cv2.imread(str(OUTPUT / "normal_order_active.png"))
        if active is None or cv2.minMaxLoc(cv2.matchTemplate(before[180:330, 400:700], active, cv2.TM_CCOEFF_NORMED))[1] < 0.8:
            raise RuntimeError("normal-order page was not verified; no calibration click sent")
        if checkbox_green_pixels(before) >= 80:
            if not post_click_normalized(controller, *CONTROL_CENTER).wait().succeeded:
                raise RuntimeError("could not capture the unchecked calibration state")
            time.sleep(0.8)
            before = screenshot(controller)
            if checkbox_green_pixels(before) >= 80:
                raise RuntimeError("checkbox did not become unchecked; no templates saved")
        if not post_click_normalized(controller, *CONTROL_CENTER).wait().succeeded:
            raise RuntimeError("completion control click failed")
        time.sleep(0.8)
        after = screenshot(controller)
        if checkbox_green_pixels(after) < 80:
            raise RuntimeError("completion click did not produce a checked control; templates were not saved")
        crop(before, "normal_order_completion_unchecked_live.png")
        crop(after, "normal_order_completion_checked_full_live.png")
        print(f"captured verified unchecked/checked controls from {before.shape[1]}x{before.shape[0]} Maa frames")
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

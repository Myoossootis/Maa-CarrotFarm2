# -*- coding: utf-8 -*-
"""Resize the 萝卜家园2 window so its client area matches the template frame.

Templates and clicks are audited at 720x1358. This sets the Win32 client
area (the game surface, not the title bar) through SetWindowPos. It does not
call SetForegroundWindow. WeChat or the game host can still reject the size;
the script prints the client size before and after.

The game does not rebuild its render resolution when the window is resized.
Quit and reopen the mini program once; the next launch picks up the new
client size. The host process is WeChatAppEx.exe, so this script does not
kill it.
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes


WINDOW_TITLE = "萝卜家园2"
WINDOW_CLASS = "Chrome_WidgetWin_0"
DEFAULT_CLIENT = (720, 1358)

GWL_STYLE = -16
GWL_EXSTYLE = -20
SW_RESTORE = 9
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
MONITOR_DEFAULTTONEAREST = 2


class WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [
        ("length", wintypes.UINT),
        ("flags", wintypes.UINT),
        ("showCmd", wintypes.UINT),
        ("ptMinPosition", wintypes.POINT),
        ("ptMaxPosition", wintypes.POINT),
        ("rcNormalPosition", wintypes.RECT),
    ]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


def enable_dpi_awareness() -> None:
    """Measure and resize in physical pixels, not a virtualized DPI space."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    set_context = getattr(user32, "SetProcessDpiAwarenessContext", None)
    if set_context is not None:
        set_context.restype = wintypes.BOOL
        set_context.argtypes = [ctypes.c_void_p]
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        if set_context(ctypes.c_void_p(-4)):
            return
    user32.SetProcessDPIAware()


def find_window() -> int:
    found: list[int] = []
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

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
        raise RuntimeError(f"expected one {WINDOW_TITLE} window, found {len(found)}")
    return found[0]


def _user32():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_ulong
    user32.GetMenu.argtypes = [wintypes.HWND]
    user32.GetMenu.restype = wintypes.HMENU
    user32.AdjustWindowRectEx.argtypes = [
        ctypes.POINTER(wintypes.RECT),
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    ]
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsZoomed.argtypes = [wintypes.HWND]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetWindowPos.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.MonitorFromWindow.restype = wintypes.HMONITOR
    user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]
    return user32


def rect_size(rect: wintypes.RECT) -> tuple[int, int]:
    return int(rect.right - rect.left), int(rect.bottom - rect.top)


def window_metrics(hwnd: int) -> dict:
    user32 = _user32()
    outer = wintypes.RECT()
    client = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(outer)):
        raise RuntimeError(f"GetWindowRect failed: {ctypes.get_last_error()}")
    if not user32.GetClientRect(hwnd, ctypes.byref(client)):
        raise RuntimeError(f"GetClientRect failed: {ctypes.get_last_error()}")
    outer_w, outer_h = rect_size(outer)
    client_w, client_h = rect_size(client)
    return {
        "hwnd": hwnd,
        "outer": (int(outer.left), int(outer.top), outer_w, outer_h),
        "client": (client_w, client_h),
        "minimized": bool(user32.IsIconic(hwnd)),
        "maximized": bool(user32.IsZoomed(hwnd)),
    }


def work_area(hwnd: int) -> tuple[int, int, int, int]:
    user32 = _user32()
    monitor = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        raise RuntimeError(f"GetMonitorInfoW failed: {ctypes.get_last_error()}")
    return (
        int(info.rcWork.left),
        int(info.rcWork.top),
        int(info.rcWork.right),
        int(info.rcWork.bottom),
    )


def chrome_size(hwnd: int) -> tuple[int, int]:
    """Outer-minus-client from the live window.

    AdjustWindowRectEx assumes a standard caption. This host is a thin
    Chrome_WidgetWin_0 frame, so the style-based estimate is too tall and
    leaves unused space on the work area.
    """
    metrics = window_metrics(hwnd)
    outer_w, outer_h = metrics["outer"][2], metrics["outer"][3]
    client_w, client_h = metrics["client"]
    if client_w > 0 and client_h > 0 and outer_w >= client_w and outer_h >= client_h:
        return outer_w - client_w, outer_h - client_h
    user32 = _user32()
    style = user32.GetWindowLongW(hwnd, GWL_STYLE)
    ex_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    rect = wintypes.RECT(0, 0, 200, 200)
    if not user32.AdjustWindowRectEx(
        ctypes.byref(rect),
        style,
        bool(user32.GetMenu(hwnd)),
        ex_style,
    ):
        raise RuntimeError(f"AdjustWindowRectEx failed: {ctypes.get_last_error()}")
    return rect.right - 200, rect.bottom - 200


def outer_size_for_client(hwnd: int, client_w: int, client_h: int) -> tuple[int, int]:
    chrome_w, chrome_h = chrome_size(hwnd)
    return int(client_w) + chrome_w, int(client_h) + chrome_h


def fit_client(
    hwnd: int,
    client_w: int,
    client_h: int,
) -> tuple[int, int, bool]:
    """Shrink the requested client so the outer window stays inside the work area."""
    left, top, right, bottom = work_area(hwnd)
    work_w = right - left
    work_h = bottom - top
    outer_w, outer_h = outer_size_for_client(hwnd, client_w, client_h)
    if outer_w <= work_w and outer_h <= work_h:
        return client_w, client_h, False
    chrome_w = outer_w - client_w
    chrome_h = outer_h - client_h
    avail_w = max(1, work_w - chrome_w)
    avail_h = max(1, work_h - chrome_h)
    scale = min(avail_w / client_w, avail_h / client_h)
    fitted_w = max(1, int(client_w * scale))
    fitted_h = max(1, int(client_h * scale))
    return fitted_w, fitted_h, True


def resize_client(hwnd: int, client_w: int, client_h: int) -> None:
    user32 = _user32()
    if user32.IsIconic(hwnd) or user32.IsZoomed(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    outer = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(outer)):
        raise RuntimeError(f"GetWindowRect failed: {ctypes.get_last_error()}")
    outer_w, outer_h = outer_size_for_client(hwnd, client_w, client_h)
    left, top, right, bottom = work_area(hwnd)
    x = int(outer.left)
    y = int(outer.top)
    if x + outer_w > right:
        x = max(left, right - outer_w)
    if y + outer_h > bottom:
        y = max(top, bottom - outer_h)
    if x < left:
        x = left
    if y < top:
        y = top
    ok = user32.SetWindowPos(
        hwnd,
        None,
        x,
        y,
        outer_w,
        outer_h,
        SWP_NOZORDER | SWP_NOACTIVATE,
    )
    if not ok:
        raise RuntimeError(f"SetWindowPos failed: {ctypes.get_last_error()}")


def format_metrics(label: str, metrics: dict) -> str:
    outer = metrics["outer"]
    client = metrics["client"]
    flags = []
    if metrics["minimized"]:
        flags.append("minimized")
    if metrics["maximized"]:
        flags.append("maximized")
    state = f" ({', '.join(flags)})" if flags else ""
    return (
        f"{label}: client {client[0]}x{client[1]}, "
        f"outer {outer[2]}x{outer[3]} at ({outer[0]},{outer[1]}){state}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Set the 萝卜家园2 client size. Default target is 720x1358."
    )
    parser.add_argument("--width", type=int, default=DEFAULT_CLIENT[0], help="client width")
    parser.add_argument("--height", type=int, default=DEFAULT_CLIENT[1], help="client height")
    parser.add_argument(
        "--status",
        action="store_true",
        help="print the current size and do not resize",
    )
    parser.add_argument(
        "--exact",
        action="store_true",
        help="do not shrink the target when it is taller than the work area",
    )
    args = parser.parse_args()
    if args.width < 200 or args.height < 200:
        parser.error("client width and height must be at least 200")

    enable_dpi_awareness()
    hwnd = find_window()
    before = window_metrics(hwnd)
    print(format_metrics("before", before), flush=True)
    if args.status:
        return 0

    target_w, target_h = args.width, args.height
    if not args.exact:
        target_w, target_h, fitted = fit_client(hwnd, target_w, target_h)
        if fitted:
            print(
                f"target {args.width}x{args.height} does not fit the work area; "
                f"using {target_w}x{target_h}",
                flush=True,
            )
    resize_client(hwnd, target_w, target_h)
    after = window_metrics(hwnd)
    print(format_metrics("after", after), flush=True)
    print(
        "window size is updated; quit and reopen 萝卜家园2 once so the game "
        "rebuilds its render resolution",
        flush=True,
    )
    got_w, got_h = after["client"]
    if abs(got_w - target_w) > 2 or abs(got_h - target_h) > 2:
        print(
            f"host kept client {got_w}x{got_h}; requested {target_w}x{target_h}",
            flush=True,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
